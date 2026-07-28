/**
 * Bordereau refresh — replaces the cache-prone IMPORTDATA formula with a
 * direct UrlFetchApp fetch.
 *
 * Why this exists
 * ---------------
 * Bordereau!A1 was originally `=IMPORTDATA("https://…/bordereau.csv?key=…")`,
 * which Google Sheets caches for ~1 hour. When new products land in the DB
 * via the Streamlit app, you used to have to delete A1 and re-enter the
 * formula to force a refresh.
 *
 * This script fetches the CSV directly (no cache), parses it, and writes
 * the rows to the Bordereau tab. You can run it:
 *   - manually via the menu  🌿 Merci Raymond → ↻ Rafraîchir le Bordereau
 *   - automatically every N minutes (one click to enable from the menu)
 *
 * Installation
 * ------------
 * 1. Open the master Sheet → Extensions → Apps Script.
 * 2. Add this file alongside mr_cascade.gs (+ → Script).
 * 3. Save. Reload the Sheet. A new menu “🌿 Merci Raymond” appears.
 * 4. First time: click  🌿 Merci Raymond → ⚙ Configurer l’URL Bordereau
 *    and paste the full URL (including ?key=…).  The script stores it in
 *    document properties so you only do this once.
 * 5. (Optional) Enable auto-refresh: click ↻ Auto-refresh : activer
 *    → choose an interval (5 / 15 / 30 / 60 min).
 *
 * Notes
 * -----
 * - On the first manual refresh after installation, you'll get an
 *   authorization prompt for UrlFetchApp + DocumentProperties scopes —
 *   normal, accept it.
 * - The script keeps the same Bordereau column layout (writes rows starting
 *   from A1), so all formulas elsewhere keep working unchanged.
 * - This co-exists peacefully with mr_cascade.gs — the onEdit cascade
 *   reads the Bordereau values, this script writes them.
 */

const REFRESH_DOC_PROP_URL = 'BORDEREAU_URL';
const REFRESH_DOC_PROP_TAXO_URL = 'TAXONOMY_URL';
const REFRESH_DOC_PROP_INTERVAL = 'BORDEREAU_AUTO_INTERVAL';
const REFRESH_MENU_NAME = '🌿 Merci Raymond';

/* ───────────────────────────────────────────────────────────────────
   Simple trigger — runs on Sheet open. Adds our menu.
   ─────────────────────────────────────────────────────────────────── */
function onOpen() {
  const ui = SpreadsheetApp.getUi();
  const menu = ui.createMenu(REFRESH_MENU_NAME)
    .addItem('↻ Rafraîchir (Bordereau + Taxonomy)', 'refreshAll')
    .addSeparator();

  const interval = PropertiesService.getDocumentProperties()
    .getProperty(REFRESH_DOC_PROP_INTERVAL);
  if (interval) {
    menu.addItem(
      '↻ Auto-refresh : ACTIF (' + interval + ' min) — désactiver',
      'disableAutoRefresh'
    );
  } else {
    menu.addSubMenu(
      ui.createMenu('↻ Auto-refresh : activer')
        .addItem('Toutes les 5 min', 'enableAutoRefresh5')
        .addItem('Toutes les 15 min', 'enableAutoRefresh15')
        .addItem('Toutes les 30 min', 'enableAutoRefresh30')
        .addItem('Toutes les heures', 'enableAutoRefresh60')
    );
  }

  menu.addSeparator()
    // applyRentabilite is defined in mr_cascade.gs — same bound-script
    // namespace, so it's callable from here. Installs / refreshes the
    // "Pilotage de rentabilité" recap + the SST / hidden-id columns.
    .addItem('📊 Installer / MAJ rentabilité', 'applyRentabilite')
    .addItem('⚙ Configurer l\'URL Bordereau…', 'setBordereauUrl')
    .addItem('⚙ Configurer l\'URL Taxonomy…', 'setTaxonomyUrl')
    .addToUi();
}

/* ───────────────────────────────────────────────────────────────────
   Manual URL configuration.
   ─────────────────────────────────────────────────────────────────── */
function setBordereauUrl() { _setUrlProp_(REFRESH_DOC_PROP_URL, 'Bordereau'); }
function setTaxonomyUrl()  { _setUrlProp_(REFRESH_DOC_PROP_TAXO_URL, 'Taxonomy'); }

function _setUrlProp_(propKey, label) {
  const ui = SpreadsheetApp.getUi();
  const props = PropertiesService.getDocumentProperties();
  const current = props.getProperty(propKey) || '(aucune)';
  const resp = ui.prompt(
    'URL du ' + label,
    'Colle ici l\'URL complète (incluant ?key=… si présente) :\n\n' +
    'Actuelle : ' + current,
    ui.ButtonSet.OK_CANCEL
  );
  if (resp.getSelectedButton() !== ui.Button.OK) return;
  const url = resp.getResponseText().trim();
  if (!url) { ui.alert('URL vide — pas de changement.'); return; }
  if (!/^https?:\/\//i.test(url)) {
    ui.alert('URL invalide — elle doit commencer par http:// ou https://.');
    return;
  }
  props.setProperty(propKey, url);
  ui.alert('URL ' + label + ' enregistrée. Tu peux lancer ↻ Rafraîchir (Bordereau + Taxonomy).');
}

/* ───────────────────────────────────────────────────────────────────
   Core refresh — fetches a CSV endpoint and writes it to a tab. Generic
   over (tab, stored-URL property) so the SAME logic refreshes both the
   Bordereau and the Taxonomy tabs cache-free.

   TRIGGER-SAFE: never calls getUi() (which throws in a time-driven trigger
   context). Returns the data-row count on success, or a negative status:
     -1 = no URL configured, -2 = HTTP/fetch error, -3 = empty CSV.
   ─────────────────────────────────────────────────────────────────── */
function _refreshTab_(tabName, propKey) {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const sheet = ss.getSheetByName(tabName);
  if (!sheet) return -1;

  // Resolve the URL. First-run convenience: if the stored property is empty
  // and A1 holds an IMPORTDATA formula, lift the URL out of it and persist it
  // (so it survives even after we overwrite A1 with raw data).
  const props = PropertiesService.getDocumentProperties();
  let url = props.getProperty(propKey);
  if (!url) {
    const formula = sheet.getRange('A1').getFormula();
    const m = formula && formula.match(/IMPORTDATA\(\s*["']([^"']+)["']/i);
    if (m) { url = m[1]; props.setProperty(propKey, url); }
    else return -1;
  }

  // Cache-busting timestamp so no intermediary serves stale data.
  const fetchUrl = url + (url.indexOf('?') >= 0 ? '&' : '?') + '_t=' + Date.now();

  // Fetch. The ngrok-skip-browser-warning header is harmless against the VPS
  // domain and stops ngrok-free from returning its interstitial HTML page.
  let csv;
  try {
    const resp = UrlFetchApp.fetch(fetchUrl, {
      muteHttpExceptions: true,
      headers: {'ngrok-skip-browser-warning': 'true'},
    });
    if (resp.getResponseCode() !== 200) return -2;
    csv = resp.getContentText();
  } catch (err) { return -2; }

  const rows = Utilities.parseCsv(csv);
  if (!rows || rows.length === 0) return -3;

  // Blank the previous range first so a SHRINKING dataset (deleted row)
  // doesn't leave stale rows at the bottom.
  const lastRow = sheet.getLastRow();
  const lastCol = sheet.getLastColumn();
  if (lastRow > 0 && lastCol > 0) sheet.getRange(1, 1, lastRow, lastCol).clearContent();
  sheet.getRange(1, 1, rows.length, rows[0].length).setValues(rows);
  return rows.length - 1;   // data rows (header excluded)
}

/* Per-tab wrappers. Callable from the menu (toast feedback) or other code. */
function refreshBordereau() { return _toastRefresh_('Bordereau', _refreshTab_('Bordereau', REFRESH_DOC_PROP_URL)); }
function refreshTaxonomy()  { return _toastRefresh_('Taxonomy',  _refreshTab_('Taxonomy',  REFRESH_DOC_PROP_TAXO_URL)); }

/* Both tabs in one go — this is what the time-driven trigger calls so the
   Bordereau AND the Taxonomy stay fresh on the same interval. */
function refreshAll() {
  const b = _refreshTab_('Bordereau', REFRESH_DOC_PROP_URL);
  const t = _refreshTab_('Taxonomy',  REFRESH_DOC_PROP_TAXO_URL);
  SpreadsheetApp.getActive().toast(
    '↻ Bordereau : ' + _statusText_(b) + '  ·  Taxonomy : ' + _statusText_(t),
    REFRESH_MENU_NAME, 5
  );
  return {bordereau: b, taxonomy: t};
}

function _toastRefresh_(label, n) {
  SpreadsheetApp.getActive().toast(label + ' : ' + _statusText_(n), REFRESH_MENU_NAME, 4);
  return n;
}

function _statusText_(n) {
  if (n >= 0) return '✓ ' + n + ' lignes';
  if (n === -1) return '⚠ URL non configurée';
  if (n === -2) return '⚠ injoignable (HTTP)';
  return '⚠ vide';
}

/* ───────────────────────────────────────────────────────────────────
   Time-driven auto-refresh — installable triggers.
   ─────────────────────────────────────────────────────────────────── */
function enableAutoRefresh5()  { _enableAutoRefresh(5);  }
function enableAutoRefresh15() { _enableAutoRefresh(15); }
function enableAutoRefresh30() { _enableAutoRefresh(30); }
function enableAutoRefresh60() { _enableAutoRefresh(60); }

function _enableAutoRefresh(minutes) {
  _removeRefreshTriggers();
  // refreshAll → both Bordereau + Taxonomy on every tick.
  ScriptApp.newTrigger('refreshAll')
    .timeBased()
    .everyMinutes(minutes)
    .create();
  PropertiesService.getDocumentProperties()
    .setProperty(REFRESH_DOC_PROP_INTERVAL, String(minutes));
  SpreadsheetApp.getUi().alert(
    '✓ Rafraîchissement automatique (Bordereau + Taxonomy) activé toutes les ' +
    minutes + ' min.\nRecharge la page pour voir le menu mis à jour.'
  );
}

function disableAutoRefresh() {
  const removed = _removeRefreshTriggers();
  PropertiesService.getDocumentProperties()
    .deleteProperty(REFRESH_DOC_PROP_INTERVAL);
  SpreadsheetApp.getUi().alert(
    '✓ Auto-refresh désactivé (' + removed + ' déclencheur(s) supprimé(s)).\n' +
    'Recharge la page pour voir le menu mis à jour.'
  );
}

function _removeRefreshTriggers() {
  let removed = 0;
  ScriptApp.getProjectTriggers().forEach(function(t) {
    var fn = t.getHandlerFunction();
    // Remove the current 'refreshAll' triggers AND any legacy
    // 'refreshBordereau' trigger from before the Taxonomy extension.
    if (fn === 'refreshAll' || fn === 'refreshBordereau') {
      ScriptApp.deleteTrigger(t);
      removed++;
    }
  });
  return removed;
}

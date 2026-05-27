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
const REFRESH_DOC_PROP_INTERVAL = 'BORDEREAU_AUTO_INTERVAL';
const REFRESH_MENU_NAME = '🌿 Merci Raymond';

/* ───────────────────────────────────────────────────────────────────
   Simple trigger — runs on Sheet open. Adds our menu.
   ─────────────────────────────────────────────────────────────────── */
function onOpen() {
  const ui = SpreadsheetApp.getUi();
  const menu = ui.createMenu(REFRESH_MENU_NAME)
    .addItem('↻ Rafraîchir le Bordereau', 'refreshBordereau')
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
    .addItem('⚙ Configurer l\'URL Bordereau…', 'setBordereauUrl')
    .addToUi();
}

/* ───────────────────────────────────────────────────────────────────
   Manual URL configuration.
   ─────────────────────────────────────────────────────────────────── */
function setBordereauUrl() {
  const ui = SpreadsheetApp.getUi();
  const props = PropertiesService.getDocumentProperties();
  const current = props.getProperty(REFRESH_DOC_PROP_URL) || '(aucune)';
  const resp = ui.prompt(
    'URL du Bordereau',
    'Colle ici l\'URL complète (incluant ?key=… si présente) :\n\n' +
    'Actuelle : ' + current,
    ui.ButtonSet.OK_CANCEL
  );
  if (resp.getSelectedButton() !== ui.Button.OK) return;
  const url = resp.getResponseText().trim();
  if (!url) {
    ui.alert('URL vide — pas de changement.');
    return;
  }
  if (!/^https?:\/\//i.test(url)) {
    ui.alert('URL invalide — elle doit commencer par http:// ou https://.');
    return;
  }
  props.setProperty(REFRESH_DOC_PROP_URL, url);
  ui.alert(
    'URL enregistrée. Tu peux maintenant lancer ↻ Rafraîchir le Bordereau.'
  );
}

/* ───────────────────────────────────────────────────────────────────
   Core refresh — fetches the CSV and writes it to the Bordereau tab.
   Callable from the menu, from a time-driven trigger, or by another
   script. Returns the number of data rows written (header row excluded)
   for logging.
   ─────────────────────────────────────────────────────────────────── */
function refreshBordereau() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const sheet = ss.getSheetByName('Bordereau');
  if (!sheet) {
    SpreadsheetApp.getUi().alert('Onglet « Bordereau » introuvable.');
    return 0;
  }

  // 1. Resolve the URL. First-run convenience: if document property is
  // empty and Bordereau!A1 holds an IMPORTDATA formula, lift the URL out
  // of it and persist it so subsequent runs work even after we
  // overwrite A1 with raw data.
  const props = PropertiesService.getDocumentProperties();
  let url = props.getProperty(REFRESH_DOC_PROP_URL);
  if (!url) {
    const formula = sheet.getRange('A1').getFormula();
    const m = formula && formula.match(/IMPORTDATA\(\s*["']([^"']+)["']/i);
    if (m) {
      url = m[1];
      props.setProperty(REFRESH_DOC_PROP_URL, url);
    } else {
      SpreadsheetApp.getUi().alert(
        'URL introuvable. Va dans  ' + REFRESH_MENU_NAME +
        '  → ⚙ Configurer l\'URL Bordereau pour la définir.'
      );
      return 0;
    }
  }

  // 2. Append a cache-busting timestamp so any intermediary (CDN, etc.)
  // can't serve stale data.
  const fetchUrl = url + (url.indexOf('?') >= 0 ? '&' : '?')
    + '_t=' + Date.now();

  // 3. Fetch.
  let csv;
  try {
    const resp = UrlFetchApp.fetch(fetchUrl, {muteHttpExceptions: true});
    const code = resp.getResponseCode();
    if (code !== 200) {
      throw new Error('HTTP ' + code + ' — ' + resp.getContentText().substr(0, 200));
    }
    csv = resp.getContentText();
  } catch (err) {
    SpreadsheetApp.getUi().alert('Erreur de fetch : ' + err.message);
    return 0;
  }

  // 4. Parse the CSV.
  const rows = Utilities.parseCsv(csv);
  if (!rows || rows.length === 0) {
    SpreadsheetApp.getUi().alert('Le bordereau renvoyé est vide.');
    return 0;
  }

  // 5. Write. We blank the previous range first so a SHRINKING bordereau
  // (= a deleted product) doesn't leave stale rows at the bottom.
  const lastRow = sheet.getLastRow();
  const lastCol = sheet.getLastColumn();
  if (lastRow > 0 && lastCol > 0) {
    sheet.getRange(1, 1, lastRow, lastCol).clearContent();
  }
  sheet.getRange(1, 1, rows.length, rows[0].length).setValues(rows);

  ss.toast(
    '✓ Bordereau rafraîchi (' + (rows.length - 1) + ' produits)',
    REFRESH_MENU_NAME, 4
  );
  return rows.length - 1;
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
  ScriptApp.newTrigger('refreshBordereau')
    .timeBased()
    .everyMinutes(minutes)
    .create();
  PropertiesService.getDocumentProperties()
    .setProperty(REFRESH_DOC_PROP_INTERVAL, String(minutes));
  SpreadsheetApp.getUi().alert(
    '✓ Rafraîchissement automatique activé toutes les ' + minutes + ' min.\n' +
    'Recharge la page pour voir le menu mis à jour.'
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
    if (t.getHandlerFunction() === 'refreshBordereau') {
      ScriptApp.deleteTrigger(t);
      removed++;
    }
  });
  return removed;
}

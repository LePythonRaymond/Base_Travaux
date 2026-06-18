/**
 * Merci Raymond — Progressive cascade filter for the DPGF template (v2.4).
 *
 * Bind this script to the MASTER Google Sheet so every "Faire une copie"
 * inherits it. Vincent authorises it once per copy.
 *
 * Layout (4-column cascade on the DPGF tab):
 *   AD: Famille (static dropdown — 16 families, baked into the template)
 *   AE: Sous-catégorie (dynamic dropdown — narrows on AD; "Tous" = no filter)
 *   AF: Conditionnement (dynamic dropdown — narrows on AD + AE; "Tous" = no filter)
 *   AG: Produit (dynamic dropdown — narrows on AD + AE + AF; full list under Famille if AE/AF=Tous)
 *
 * v2.3 changes (2026-05-19):
 *   - Each filter level has a visible "— Tous —" option meaning "no filter
 *     on this dimension". Equivalent to leaving the cell blank but more
 *     discoverable. Vincent can pick Famille=Arbuste, leave AE/AF blank
 *     OR pick "Tous" — either way AG lists every arbuste we have.
 *   - Bordereau/Taxonomy reads are cached once per onEdit invocation
 *     instead of re-reading the tab on each refresh function. Halves the
 *     round-trips per cascade event.
 *
 * AI (Fournisseur) and AJ (Fraîcheur) are pure spreadsheet formulas; they
 * auto-update when AG changes without help from this script.
 */

// ----- Layout constants — must stay in sync with the master DPGF tab ----
const DPGF_SHEET = 'DPGF';
const BORDEREAU_SHEET = 'Bordereau';
const TAXONOMY_SHEET = 'Taxonomy';
const DATA_FIRST_ROW = 3;
const COL_AD = 30;  // Famille
const COL_AE = 31;  // Sous-catégorie
const COL_AF = 32;  // Conditionnement
const COL_AG = 33;  // Produit
// COL_BE (57) = hidden stable product-id column. It's a passive lookup
// formula (=INDEX(Bordereau!A.., MATCH(AG.., Helpers!A.., 0))) the cascade
// doesn't touch — see google_sheets/CELL_FORMULAS.md §3. Reverse-ingestion
// reads it to match a line to its DB product exactly.
const COL_BE = 57;  // hidden product DB id (resolved from the picker)
const COL_SST = 56; // BD — "SST ?" tick-box: TRUE = sous-traitant line, excluded
                    // from the Hors-SST rentability block on the Pilotage tab.
const COL_COMMENT = 58; // BF — wide free-text "Commentaire" column (yellow input
                        // colour) for per-line / per-section notes by Vincent.

// The project-settings tab was renamed "Paramètres" → "Pilotage de rentabilité"
// (it now hosts coefficients + the rentability recap). _getParamsSheet_() below
// renames an old-named tab in place so existing copies upgrade cleanly.
const PARAMS_SHEET_OLD = 'Paramètres';
const PARAMS_SHEET     = 'Pilotage de rentabilité';

// Visible "no-filter" sentinel. Picking it is equivalent to leaving blank.
const ALL = '— Tous —';

// AG sentinel — when picked, the DPGF cost-chain formulas switch to
// AVERAGEIFS mode and compute the mean over whatever filters AD/AE/AF
// currently match. Must stay character-for-character in sync with the
// formula constant in build_dpgf_template_v2.py (PRIX_MOYEN).
// No parens inside the string — Sheets' French-locale xlsx-import parser
// chokes on parens that follow non-ASCII chars in string literals.
const PRIX_MOYEN = '💰 Prix moyen';

// Bordereau CSV columns (1-indexed). Must stay in sync with bordereau_api/main.py.
const BORDEREAU_REFERENCE_NAME = 2;   // B
const BORDEREAU_FAMILY_NAME    = 3;   // C
const BORDEREAU_SUBCATEGORY    = 4;   // D
const BORDEREAU_PACKAGING      = 7;   // G
const BORDEREAU_LAST_ROW       = 502;

// Taxonomy CSV columns (1-indexed). Must stay in sync with bordereau_api/main.py.
const TAXONOMY_FAMILY_NAME = 2;       // B
const TAXONOMY_SUBCATEGORY = 3;       // C
const TAXONOMY_PACKAGING   = 4;       // D
const TAXONOMY_LAST_ROW    = 1002;


/**
 * Spreadsheet-installed simple onEdit trigger. Fires on every cell edit.
 *
 * Reads the Bordereau and Taxonomy tabs once at the top so every cascade
 * refresh below uses the same cached snapshot — keeps the latency to one
 * tab-read of each, not three.
 */
function onEdit(e) {
  const sheet = e.source.getActiveSheet();
  if (sheet.getName() !== DPGF_SHEET) return;

  const row = e.range.getRow();
  if (row < DATA_FIRST_ROW) return;

  const col = e.range.getColumn();
  if (col !== COL_AD && col !== COL_AE && col !== COL_AF && col !== COL_AG) return;

  // Cache reads for this invocation.
  const taxo = readTaxonomy_();
  const bord = readBordereau_();

  if (col === COL_AD) {
    refreshSousCatDropdown_(sheet, row, taxo);
    refreshCondDropdown_(sheet, row, taxo);
    refreshProduitDropdown_(sheet, row, bord);
    sheet.getRange(row, COL_AE).clearContent();
    sheet.getRange(row, COL_AF).clearContent();
    sheet.getRange(row, COL_AG).clearContent();
  } else if (col === COL_AE) {
    refreshCondDropdown_(sheet, row, taxo);
    refreshProduitDropdown_(sheet, row, bord);
    sheet.getRange(row, COL_AF).clearContent();
    sheet.getRange(row, COL_AG).clearContent();
  } else if (col === COL_AF) {
    refreshProduitDropdown_(sheet, row, bord);
    sheet.getRange(row, COL_AG).clearContent();
  } else if (col === COL_AG) {
    // Reverse cascade — when the user picks (or pastes) a product directly
    // in AG without going through AD/AE/AF, parse the picker string and
    // back-fill the upstream cascade. Also re-refreshes the AG dropdown so
    // the chosen value is officially in the validated list (kills the red
    // "Non valide" triangle).
    handleProduitDirectEdit_(sheet, row, bord, taxo);
  }
}


/**
 * Reverse cascade: triggered when the user edits AG directly.
 *
 * The AG value is expected to be one of:
 *   • a 4-part picker string `Famille — Sous-cat — Référence — Cond.`
 *     (the canonical format the dropdown produces);
 *   • a 3-part legacy `Famille — Référence — Cond.` (from older sheets);
 *   • the Prix-moyen sentinel — nothing to back-fill;
 *   • a free-text product name — we look it up in the Bordereau; if it
 *     uniquely matches a single product, we back-fill from there;
 *   • garbage — we leave the cascade as-is (warning triangle persists,
 *     telling the user the input wasn't recognised).
 *
 * After back-fill, all three dropdowns and the AG dropdown are refreshed
 * so the cascade is in a consistent state.
 */
function handleProduitDirectEdit_(sheet, row, bord, taxo) {
  const raw = String(sheet.getRange(row, COL_AG).getValue() || '').trim();
  if (!raw || raw === PRIX_MOYEN) return;

  let famille = null, sousCat = null, ref = null, packaging = null;

  // 1. Try the 4-part picker format ("Famille — Sous-cat — Ref — Cond.").
  const parts = raw.split(' — ').map(function (s) { return s.trim(); });
  if (parts.length === 4) {
    famille  = parts[0];
    sousCat  = parts[1];
    ref      = parts[2];
    packaging = parts[3];
  } else if (parts.length === 3) {
    // Legacy 3-part picker — no sous-cat. Look up the Bordereau row to
    // recover it.
    famille  = parts[0];
    ref      = parts[1];
    packaging = parts[2];
    var match = _findBordereauRow(bord, {
      family_name: famille, reference_name: ref, packaging: packaging,
    });
    if (match) sousCat = match.subcategory;
  } else {
    // Free text — try to match exactly on reference_name (case-sensitive,
    // exact). Multiple matches ⇒ ambiguous, give up.
    var matches = bord.filter(function (r) { return r.reference_name === raw; });
    if (matches.length === 1) {
      famille  = matches[0].family_name;
      sousCat  = matches[0].subcategory;
      ref      = matches[0].reference_name;
      packaging = matches[0].packaging;
      // Rewrite the AG cell with the canonical picker string so AI/AJ
      // formulas (which match against Helpers!A) resolve correctly.
      var canonical = famille + ' — ' + sousCat + ' — ' + ref + ' — ' + packaging;
      sheet.getRange(row, COL_AG).setValue(canonical);
    } else {
      // Couldn't recognise — leave cascade as-is.
      return;
    }
  }

  // 2. Verify the triplet exists in the Bordereau, otherwise the AI/AJ
  // formulas won't find it and the back-fill would be misleading.
  if (!famille || !sousCat || !ref || !packaging) return;
  var bordMatch = _findBordereauRow(bord, {
    family_name: famille,
    subcategory: sousCat,
    reference_name: ref,
    packaging: packaging,
  });
  if (!bordMatch) {
    // The product on this picker string isn't in our DB. Leave the
    // cascade as-is and let the warning triangle stay — that's the right
    // signal to Vincent that the product isn't (yet) in the catalogue.
    return;
  }

  // 3. Back-fill the cascade cells WITHOUT triggering onEdit (the
  // recursion would clear AG again right after we set it). Apps Script
  // setValue inside onEdit doesn't re-fire onEdit on simple triggers, so
  // this is safe.
  sheet.getRange(row, COL_AD).setValue(famille);
  sheet.getRange(row, COL_AE).setValue(sousCat);
  sheet.getRange(row, COL_AF).setValue(packaging);

  // 4. Rebuild every dropdown for the row so AE/AF/AG are populated with
  // the correct option lists (and AG now includes our chosen value, so
  // the red "Non valide" triangle disappears).
  refreshSousCatDropdown_(sheet, row, taxo);
  refreshCondDropdown_(sheet, row, taxo);
  refreshProduitDropdown_(sheet, row, bord);
}


/**
 * Find the first Bordereau row matching every provided key (exact text
 * comparison). Returns null if no row matches. Keys whose value is
 * undefined or null are skipped.
 */
function _findBordereauRow(bord, criteria) {
  for (var i = 0; i < bord.length; i++) {
    var row = bord[i];
    var hit = true;
    for (var k in criteria) {
      if (criteria[k] != null && row[k] !== criteria[k]) { hit = false; break; }
    }
    if (hit) return row;
  }
  return null;
}


/**
 * Treat the "Tous" sentinel as "no filter on this dimension". Returns
 * the user's actual filter or empty string if the value is blank or Tous.
 */
function effectiveFilter_(v) {
  const s = String(v || '').trim();
  return (s === '' || s === ALL) ? '' : s;
}


/**
 * AE (Sous-catégorie) dropdown = unique subcategories for the chosen
 * Famille (from Taxonomy tab), with "— Tous —" prepended.
 */
function refreshSousCatDropdown_(sheet, row, taxo) {
  const famille = effectiveFilter_(sheet.getRange(row, COL_AD).getValue());
  const target = sheet.getRange(row, COL_AE);
  if (!famille) {
    target.clearDataValidations();
    return;
  }
  const subs = new Set();
  for (const r of taxo) {
    if (r.family_name === famille && r.subcategory && r.subcategory !== 'À classifier') {
      subs.add(r.subcategory);
    }
  }
  const list = Array.from(subs).sort();
  if (list.length > 0) list.unshift(ALL);
  applyListValidation_(target, list);
}


/**
 * AF (Conditionnement) dropdown = unique packagings for the chosen
 * (Famille, Sous-cat) combo (from Taxonomy tab), with "— Tous —" prepended.
 * If AE = blank or Tous, list every packaging seen in that Famille.
 */
function refreshCondDropdown_(sheet, row, taxo) {
  const famille = effectiveFilter_(sheet.getRange(row, COL_AD).getValue());
  const sousCat = effectiveFilter_(sheet.getRange(row, COL_AE).getValue());
  const target = sheet.getRange(row, COL_AF);
  if (!famille) {
    target.clearDataValidations();
    return;
  }
  const packs = new Set();
  for (const r of taxo) {
    if (r.family_name !== famille) continue;
    if (r.subcategory === 'À classifier') continue;
    if (sousCat && r.subcategory !== sousCat) continue;
    if (r.packaging) packs.add(r.packaging);
  }
  const list = Array.from(packs).sort();
  if (list.length > 0) list.unshift(ALL);
  applyListValidation_(target, list);
}


/**
 * AG (Produit) dropdown = picker strings for products in the bordereau
 * matching the non-empty/non-Tous filters. With all three filters set to
 * Tous/blank, AG lists every product (skipping the "À classifier" bucket).
 */
function refreshProduitDropdown_(sheet, row, bord) {
  const famille = effectiveFilter_(sheet.getRange(row, COL_AD).getValue());
  const sousCat = effectiveFilter_(sheet.getRange(row, COL_AE).getValue());
  const condit  = effectiveFilter_(sheet.getRange(row, COL_AF).getValue());
  const target = sheet.getRange(row, COL_AG);

  const refs = [];
  for (const r of bord) {
    if (famille && r.family_name !== famille) continue;
    if (sousCat && r.subcategory !== sousCat) continue;
    if (condit  && r.packaging   !== condit ) continue;
    refs.push(
      r.family_name + ' — ' +
      r.subcategory + ' — ' +
      r.reference_name + ' — ' +
      r.packaging
    );
  }
  refs.sort();
  // Prepend the Prix moyen option only when there's at least one matching
  // product — otherwise picking it would produce a 0 € average and confuse.
  if (refs.length > 0) refs.unshift(PRIX_MOYEN);
  applyListValidation_(target, refs);
}


/**
 * Apply or clear list-style validation on `target` based on whether
 * `values` is non-empty.
 */
function applyListValidation_(target, values) {
  if (!values || values.length === 0) {
    target.clearDataValidations();
    return;
  }
  // `setAllowInvalid(true)` = show the list as a dropdown helper, but
  // don't reject values typed/pasted in. Combined with the reverse
  // cascade in handleProduitDirectEdit_(), the user can paste a picker
  // string straight into AG and the upstream cells back-fill on the
  // next onEdit tick — no more "Non valide" red triangle for legitimate
  // products that simply weren't reachable via the current cascade.
  const rule = SpreadsheetApp.newDataValidation()
                 .requireValueInList(values, true)
                 .setAllowInvalid(true)
                 .build();
  target.setDataValidation(rule);
}


/**
 * Convert a formula's argument separators from `,` to whatever the
 * spreadsheet's locale uses (`;` for most non-English locales).
 * Only converts commas OUTSIDE quoted strings. Apps Script's setFormula
 * is supposed to auto-translate, but it doesn't always — most reliably
 * we just do the translation ourselves before the write.
 */
function _formulaForLocale(formula) {
  const locale = SpreadsheetApp.getActiveSpreadsheet().getSpreadsheetLocale() || '';
  if (locale.indexOf('en_') === 0) return formula;  // English locale — leave commas
  let out = '';
  let inString = false;
  for (let i = 0; i < formula.length; i++) {
    const c = formula.charAt(i);
    if (c === '"') {
      inString = !inString;
      out += c;
    } else if (c === ',' && !inString) {
      out += ';';
    } else {
      out += c;
    }
  }
  return out;
}


/**
 * One-shot patch — apply the v2.4 changes to an existing Sheet that was
 * built from an earlier v2 import (so you don't have to re-import the
 * fresh xlsx).
 *
 * USAGE: in the Apps Script editor's function picker (top toolbar),
 * select `applyV24Patch` and click Run. Authorise if prompted. A toast
 * confirms when done.
 *
 * What it does:
 *   1. Adds the 6 numeric-mirror columns (B-G) to the Helpers tab as
 *      ARRAYFORMULAs sourced from the Bordereau dot-decimal columns.
 *   2. Rewrites the 6 cost-chain formulas (AI, AJ, AK, AL, AM, AQ) in
 *      rows 3..502 of the DPGF tab to include the Prix moyen branch.
 *
 * Idempotent — safe to run twice.
 */
function applyV24Patch() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const dpgf = ss.getSheetByName(DPGF_SHEET);
  const helpers = ss.getSheetByName('Helpers');
  const params = _getParamsSheet_(ss);   // renames "Paramètres" → "Pilotage de rentabilité" if needed
  if (!dpgf || !helpers || !params) {
    SpreadsheetApp.getUi().alert('DPGF, Helpers ou Pilotage de rentabilité introuvable. Avorté.');
    return;
  }

  // --- 0. Workbook-level named ranges -------------------------------
  // These live at workbook scope, not inside any tab — Copier vers
  // doesn't carry them. The formulas in AN/AO/AS/AT/AU/AV/AW/AX/AY/AZ
  // /BA/BB use them. We remove stale entries FIRST then create fresh,
  // because pre-existing broken named ranges (from a partial earlier
  // copy) can shadow the new setNamedRange call.
  const NAMED = [
    ['Taux_horaire',    'B6'],   // €/h labor rate
    ['Securite_humain', 'B7'],   // labor safety margin (0.10)
    ['Install_chantier','B8'],   // install chantier markup (1.02)
    ['Log_gestion',     'B9'],   // logistics + gestion markup (1.06)
    ['Loc_livr_marge',  'B10'],  // rental/delivery margin (1.5)
    ['Humain_marge',    'B11'],  // labor margin (1.8)
    ['Fourn_marge',     'B12'],  // supply + gestion margin (1.375)
    ['Col_Designation', 'B16'],  // user-mapped client zone column
    ['Col_Unite',       'B17'],
    ['Col_Quantite',    'B18'],
  ];
  const targetNames = new Set(NAMED.map(function(p) { return p[0]; }));
  // Strip any "TabName!" prefix so we catch both workbook-scoped and
  // tab-scoped duplicates (Copier vers creates the latter automatically).
  const simpleName = function(qualified) {
    const bang = qualified.lastIndexOf('!');
    return (bang === -1) ? qualified : qualified.substring(bang + 1);
  };
  const existing = ss.getNamedRanges();
  for (const nr of existing) {
    if (targetNames.has(simpleName(nr.getName()))) nr.remove();
  }
  for (const [name, cell] of NAMED) {
    ss.setNamedRange(name, params.getRange(cell));
  }

  // --- 1. Helpers numeric mirrors -----------------------------------
  const HELP_HEADERS = [
    ['cost_ht (num)', 'heure_u_pose (num)', 'nombre_uth (num)',
     'tier_1_h_decharge (num)', 'tier_2_h_decharge (num)', 'tier_3_h_decharge (num)']
  ];
  helpers.getRange('B1:G1').setValues(HELP_HEADERS);

  const numericMirror = function(srcCol) {
    return '=ARRAYFORMULA(IF(LEN(Bordereau!B2:B501)=0, "", ' +
           'IFERROR(VALUE(SUBSTITUTE(Bordereau!' + srcCol + '2:' + srcCol + '501, ".", ",")), "")))';
  };
  const HELP_MAP = [
    ['B2', 'J'], ['C2', 'O'], ['D2', 'P'],
    ['E2', 'R'], ['F2', 'T'], ['G2', 'V'],
  ];
  for (const [cell, srcCol] of HELP_MAP) {
    helpers.getRange(cell).setFormula(_formulaForLocale(numericMirror(srcCol)));
  }

  // --- 2. DPGF cost-chain formulas, rows 3..502 ---------------------
  const ALL_LBL = ALL;
  const PM = PRIX_MOYEN;

  // Standard AVERAGEIFS criteria block (family/sub/packaging + is_average=False).
  const avgCriteria = function(r) {
    return ('Bordereau!$C$2:$C$501, IF(OR(AD' + r + '="",AD' + r + '="' + ALL_LBL + '"),"*",AD' + r + '), ' +
            'Bordereau!$D$2:$D$501, IF(OR(AE' + r + '="",AE' + r + '="' + ALL_LBL + '"),"*",AE' + r + '), ' +
            'Bordereau!$G$2:$G$501, IF(OR(AF' + r + '="",AF' + r + '="' + ALL_LBL + '"),"*",AF' + r + '), ' +
            'Bordereau!$AB$2:$AB$501, "False"');
  };

  const f_AI = function(r) {
    // No parens in the string — Sheets' French-locale xlsx-import parser
    // mis-parses formulas with parens-after-non-ASCII inside string literals.
    return '=IF(AG' + r + '="", "", IF(AG' + r + '="' + PM + '", "Catalogue moyen", ' +
           'IFERROR(INDEX(Bordereau!$L$2:$L$501, MATCH(AG' + r + ', Helpers!$A$2:$A$501, 0)), "")))';
  };
  const f_AJ = function(r) {
    // No array literal — Sheets' French-locale conversion doesn't reliably
    // translate `{"a","b","c"}`. Use nested IFs against the status text.
    const idx = 'INDEX(Bordereau!$Z$2:$Z$501, MATCH(AG' + r + ', Helpers!$A$2:$A$501, 0))';
    return '=IF(AG' + r + '="", "", IF(AG' + r + '="' + PM + '", "🟡 Catalogue moyen", ' +
           'IFERROR(' +
             'IF(' + idx + '="fresh", "🟢 Frais", ' +
               'IF(' + idx + '="stale_6mo", "🟡 6-9 mois", ' +
                 'IF(' + idx + '="stale_9mo", "🔴 plus de 9 mois", ""))), "")))';
  };
  const f_AK = function(r) {
    const crit = avgCriteria(r);
    return '=IF(AG' + r + '="", "", IF(AG' + r + '="' + PM + '", ' +
           'IFERROR(CHOOSE(AH' + r + ', ' +
           'AVERAGEIFS(Helpers!$E$2:$E$501, ' + crit + '), ' +
           'AVERAGEIFS(Helpers!$F$2:$F$501, ' + crit + '), ' +
           'AVERAGEIFS(Helpers!$G$2:$G$501, ' + crit + ')), 0), ' +
           'IFERROR(CHOOSE(AH' + r + ', ' +
           'VALUE(SUBSTITUTE(INDEX(Bordereau!$R$2:$R$501, MATCH(AG' + r + ', Helpers!$A$2:$A$501, 0)), ".", ",")), ' +
           'VALUE(SUBSTITUTE(INDEX(Bordereau!$T$2:$T$501, MATCH(AG' + r + ', Helpers!$A$2:$A$501, 0)), ".", ",")), ' +
           'VALUE(SUBSTITUTE(INDEX(Bordereau!$V$2:$V$501, MATCH(AG' + r + ', Helpers!$A$2:$A$501, 0)), ".", ","))), 0)))';
  };
  const f_AL = function(r) {
    return '=IF(AG' + r + '="", "", IF(AG' + r + '="' + PM + '", ' +
           'IFERROR(AVERAGEIFS(Helpers!$C$2:$C$501, ' + avgCriteria(r) + '), 0), ' +
           'IFERROR(VALUE(SUBSTITUTE(INDEX(Bordereau!$O$2:$O$501, MATCH(AG' + r + ', Helpers!$A$2:$A$501, 0)), ".", ",")), 0)))';
  };
  const f_AM = function(r) {
    return '=IF(AG' + r + '="", "", IF(AG' + r + '="' + PM + '", ' +
           'IFERROR(AVERAGEIFS(Helpers!$D$2:$D$501, ' + avgCriteria(r) + '), 0), ' +
           'IFERROR(VALUE(SUBSTITUTE(INDEX(Bordereau!$P$2:$P$501, MATCH(AG' + r + ', Helpers!$A$2:$A$501, 0)), ".", ",")), 0)))';
  };
  const f_AQ = function(r) {
    return '=IF(AG' + r + '="", "", IF(AG' + r + '="' + PM + '", ' +
           'IFERROR(AVERAGEIFS(Helpers!$B$2:$B$501, ' + avgCriteria(r) + '), 0), ' +
           'IFERROR(VALUE(SUBSTITUTE(INDEX(Bordereau!$J$2:$J$501, MATCH(AG' + r + ', Helpers!$A$2:$A$501, 0)), ".", ",")), 0)))';
  };

  // The 6 Prix-moyen-aware lookup formulas (set by previous patcher version).
  const f_AN = function(r) { return '=IF(AG' + r + '="", "", (AK' + r + '+AL' + r + ')*AM' + r + '*Securite_humain)'; };
  const f_AO = function(r) { return '=IF(AG' + r + '="", "", ((AK' + r + '+AL' + r + ')*AM' + r + '+AN' + r + ')*Taux_horaire)'; };
  const f_AP = function(r) { return '=IF(OR(AG' + r + '="", NOT(ISNUMBER(AC' + r + '))), "", AO' + r + '*AC' + r + ')'; };
  const f_AR = function(r) { return '=IF(OR(AG' + r + '="", NOT(ISNUMBER(AC' + r + '))), "", AQ' + r + '*AC' + r + ')'; };
  const f_AS = function(r) { return '=IF(AG' + r + '="", "", AO' + r + '+AQ' + r + ')'; };
  const f_AT = function(r) { return '=IF(OR(AG' + r + '="", NOT(ISNUMBER(AC' + r + '))), "", AS' + r + '*AC' + r + ')'; };
  const f_AW = function(r) { return '=IF(AG' + r + '="", "", AT' + r + '*(Install_chantier-1))'; };
  const f_AX = function(r) { return '=IF(AG' + r + '="", "", (AT' + r + '+AU' + r + '+AV' + r + '+AW' + r + ')*(Log_gestion-1))'; };
  const f_AY = function(r) { return '=IF(AG' + r + '="", "", (AU' + r + '+AV' + r + ')*(Loc_livr_marge-1))'; };
  const f_AZ = function(r) { return '=IF(AG' + r + '="", "", AP' + r + '*(Humain_marge-1))'; };
  const f_BA = function(r) { return '=IF(AG' + r + '="", "", (AR' + r + '+AW' + r + '+AX' + r + ')*(Fourn_marge-1))'; };
  const f_BB = function(r) { return '=IF(AG' + r + '="", "", AT' + r + '+AU' + r + '+AV' + r + '+AW' + r + '+AX' + r + '+AY' + r + '+AZ' + r + '+BA' + r + ')'; };
  const f_BC = function(r) { return '=IF(OR(AG' + r + '="", NOT(ISNUMBER(AC' + r + ')), AC' + r + '=0), "", BB' + r + '/AC' + r + ')'; };

  // Build full 500-row × N-col formula matrix and apply in one call per
  // target column (fewer round-trips than per-cell writes).
  // The AN-BC formulas need to be rewritten too — Sheets cached the
  // xlsx-imported versions before the named ranges existed, so the
  // refs are stuck. Re-writing forces Sheets to re-parse and resolve.
  const colSpec = [
    ['AI', f_AI], ['AJ', f_AJ], ['AK', f_AK],
    ['AL', f_AL], ['AM', f_AM], ['AQ', f_AQ],
    ['AN', f_AN], ['AO', f_AO], ['AP', f_AP],
    ['AR', f_AR], ['AS', f_AS], ['AT', f_AT],
    ['AW', f_AW], ['AX', f_AX], ['AY', f_AY], ['AZ', f_AZ],
    ['BA', f_BA], ['BB', f_BB], ['BC', f_BC],
  ];
  const FIRST = 3, LAST = 502;
  for (const [col, fn] of colSpec) {
    const formulas = [];
    for (let r = FIRST; r <= LAST; r++) formulas.push([_formulaForLocale(fn(r))]);
    dpgf.getRange(col + FIRST + ':' + col + LAST).setFormulas(formulas);
  }

  // --- 3. Restore mirror formulas (AA/AB/AC) -----------------------
  // These can get clobbered by accidental typing or paste-over. Re-apply
  // the IFERROR(IF(ISNUMBER(...))) pattern so every data row pulls from
  // the configured client-zone columns.
  _restoreMirrorFormulas_(dpgf, FIRST, LAST);

  // --- 4. Rebuild conditional formatting ---------------------------
  // New policy: yellow ONLY on cells Vincent inputs manually — the
  // cascade picks (AD-AH) and the Loc / Liv numeric inputs (AU-AV).
  // The client mirror (AA-AC) and the auto-computed cost chain stay
  // unhighlighted so the visual distinction "I type here" vs "this is
  // computed for me" is obvious.
  _rebuildConditionalFormatting_(dpgf, FIRST, LAST);

  // --- 5. Rentability recap + SST/BE columns -----------------------
  // Installs the "Pilotage de rentabilité" recap block, the SST tick-box
  // column (BD) and the hidden product-id column (BE). Idempotent.
  applyRentabilite();

  SpreadsheetApp.getActive().toast('Patch v2.4 appliqué — Helpers + DPGF + miroir + couleurs + rentabilité.', 'OK', 6);
}


/**
 * Return the project-settings tab. Prefers the new name; if only the legacy
 * "Paramètres" tab exists, renames it in place (setName keeps the gridId, so
 * cell content + named-range targets survive). Idempotent.
 */
function _getParamsSheet_(ss) {
  var s = ss.getSheetByName(PARAMS_SHEET);
  if (s) return s;
  var old = ss.getSheetByName(PARAMS_SHEET_OLD);
  if (old) { old.setName(PARAMS_SHEET); return old; }
  return null;
}


/**
 * Set a human label (col A) + machine identifier (col C, hidden) and, only if
 * the value cell (col B) is empty, a default value. Used for the rentability
 * INPUT cells so a re-run never clobbers Vincent's tuned Personnes / heures.
 */
function _setIfBlank_(sh, row, ident, defVal, label) {
  sh.getRange(row, 1).setValue(label);
  sh.getRange(row, 3).setValue(ident);
  var c = sh.getRange(row, 2);
  var v = c.getValue();
  if (v === '' || v === null) c.setValue(defVal);
}


/**
 * Install / refresh the rentability recap on the "Pilotage de rentabilité" tab
 * (GLOBAL + Hors-SST financials + the Tps-chantier planning line), plus the
 * SST tick-box column (BD) and the hidden product-id column (BE) on the DPGF
 * tab. Idempotent — run once on the MASTER; "Faire une copie" inherits the
 * cells, and Vincent re-runs the menu item once per copy to rebind the
 * workbook-scoped named ranges (same as the v2.4 patch).
 */
function applyRentabilite() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const dpgf = ss.getSheetByName(DPGF_SHEET);
  const params = _getParamsSheet_(ss);
  if (!dpgf || !params) {
    SpreadsheetApp.getUi().alert('DPGF ou Pilotage de rentabilité introuvable. Avorté.');
    return;
  }
  const FIRST = 3, LAST = 502;
  const YELLOW = '#FFF2CC';   // "Vincent writes here" input colour
  const GREEN  = '#1F5132';   // section-header band (brand green)
  const ACCENT = '#2E7D52';   // KV / marge accent
  const INK    = '#1A1A1A';

  // --- A. SST tick-box column (BD) on the DPGF tab -----------------
  dpgf.getRange(2, COL_SST).setValue('SST ?');
  dpgf.getRange(FIRST, COL_SST, LAST - FIRST + 1, 1)
      .setDataValidation(SpreadsheetApp.newDataValidation().requireCheckbox().build());

  // --- B. Hidden product-id column (BE) on the DPGF tab ------------
  dpgf.getRange(2, COL_BE).setValue('product_id');
  const beFormulas = [];
  for (let r = FIRST; r <= LAST; r++) {
    beFormulas.push([_formulaForLocale(
      '=IFERROR(INDEX(Bordereau!$A$2:$A$501, MATCH(AG' + r + ', Helpers!$A$2:$A$501, 0)), "")'
    )]);
  }
  dpgf.getRange(FIRST, COL_BE, LAST - FIRST + 1, 1).setFormulas(beFormulas);
  dpgf.hideColumns(COL_BE);

  // --- B2. Wide free-text "Commentaire" column (BF) ---------------
  // Open notes per line / section, in the yellow input colour so it reads
  // as "write here". Last visible column on the DPGF tab.
  dpgf.getRange(2, COL_COMMENT)
      .setValue('Commentaire')
      .setFontWeight('bold');
  dpgf.setColumnWidth(COL_COMMENT, 340);
  dpgf.getRange(FIRST, COL_COMMENT, LAST - FIRST + 1, 1)
      .setBackground(YELLOW)
      .setWrap(true)
      .setVerticalAlignment('top')
      .setHorizontalAlignment('left')
      .setBorder(true, true, true, true, false, false, '#E6D9A8', SpreadsheetApp.BorderStyle.SOLID);

  // --- C. Recap layout — LABEL (A) · VALUE (B) · id (C, hidden) ----
  // INPUT cells (value set only if blank — preserve Vincent's tuning).
  _setIfBlank_(params, 22, 'Personnes',         5,    'Personnes (équipe)');
  _setIfBlank_(params, 23, 'Heures_par_jour',   7,    'Heures / jour');
  _setIfBlank_(params, 24, 'Jours_par_semaine', 5,    'Jours / semaine');
  _setIfBlank_(params, 25, 'Semaines_par_mois', 4.48, 'Semaines / mois');

  // Label (A) + identifier (C, hidden) for every recap row — always rewrite.
  const LBLID = [
    [21, 'Tps chantier (h)',            'Tps_chantier'],
    [26, 'Jours',                       'Jours'],
    [27, 'Semaines',                    'Semaines'],
    [28, 'Mois',                        'Mois'],
    [31, 'Prix de vente',               'Rent_prix_vente'],
    [32, 'Prix de revient',             'Rent_prix_revient'],
    [33, 'Marge €',                     'Rent_marge_eur'],
    [34, 'Marge %',                     'Rent_marge_pct'],
    [35, 'KV (vente / revient)',        'Rent_kv'],
    [38, 'Prix de vente',               'Rent_hs_prix_vente'],
    [39, 'Prix de revient',             'Rent_hs_prix_revient'],
    [40, 'Marge €',                     'Rent_hs_marge_eur'],
    [41, 'Marge %',                     'Rent_hs_marge_pct'],
    [42, 'KV (vente / revient)',        'Rent_hs_kv'],
  ];
  LBLID.forEach(function(t) {
    params.getRange(t[0], 1).setValue(t[1]).setFontColor('#3A3A3A').setFontWeight('normal');
    params.getRange(t[0], 3).setValue(t[2]);
  });

  // --- D. Recap formulas (always rewrite, locale-safe) ------------
  const FORMULAS = [
    // IFERROR wrap: empty rows return "" in AK/AL/AM/AC, which would make a
    // bare SUMPRODUCT throw #VALUE!; element-wise IFERROR coerces them to 0.
    [21, '=SUMPRODUCT(IFERROR((DPGF!AK3:AK502+DPGF!AL3:AL502)*DPGF!AM3:AM502*DPGF!AC3:AC502, 0))'],
    [26, '=IF(Personnes*Heures_par_jour=0,"",Tps_chantier/(Personnes*Heures_par_jour))'],
    [27, '=IF(Jours_par_semaine=0,"",Jours/Jours_par_semaine)'],
    [28, '=IF(Semaines_par_mois=0,"",Semaines/Semaines_par_mois)'],
    [31, '=SUM(DPGF!BB3:BB502)'],
    [32, '=SUM(DPGF!AT3:AT502)+SUM(DPGF!AU3:AU502)+SUM(DPGF!AV3:AV502)+SUM(DPGF!AW3:AW502)+SUM(DPGF!AX3:AX502)'],
    [33, '=Rent_prix_vente-Rent_prix_revient'],
    [34, '=IF(Rent_prix_vente=0,"",Rent_marge_eur/Rent_prix_vente*100)'],
    [35, '=IF(Rent_prix_revient=0,"",Rent_prix_vente/Rent_prix_revient)'],
    [38, '=SUMIFS(DPGF!BB3:BB502, DPGF!BD3:BD502, FALSE)'],
    [39, '=SUMIFS(DPGF!AT3:AT502, DPGF!BD3:BD502, FALSE)+SUMIFS(DPGF!AU3:AU502, DPGF!BD3:BD502, FALSE)+SUMIFS(DPGF!AV3:AV502, DPGF!BD3:BD502, FALSE)+SUMIFS(DPGF!AW3:AW502, DPGF!BD3:BD502, FALSE)+SUMIFS(DPGF!AX3:AX502, DPGF!BD3:BD502, FALSE)'],
    [40, '=Rent_hs_prix_vente-Rent_hs_prix_revient'],
    [41, '=IF(Rent_hs_prix_vente=0,"",Rent_hs_marge_eur/Rent_hs_prix_vente*100)'],
    [42, '=IF(Rent_hs_prix_revient=0,"",Rent_hs_prix_vente/Rent_hs_prix_revient)'],
  ];
  FORMULAS.forEach(function(t) {
    params.getRange(t[0], 2).setFormula(_formulaForLocale(t[1]));
  });

  // --- E. Value styling + number formats --------------------------
  // All values bold + right-aligned in col B.
  params.getRange('B21:B42').setFontWeight('bold').setHorizontalAlignment('right').setFontColor(INK);
  // Money / % / KV formats.
  params.getRange('B31:B33').setNumberFormat('# ##0,00 €');
  params.getRange('B38:B40').setNumberFormat('# ##0,00 €');
  params.getRange('B34').setNumberFormat('0,00"%"');
  params.getRange('B41').setNumberFormat('0,00"%"');
  params.getRange('B35').setNumberFormat('0,000');
  params.getRange('B42').setNumberFormat('0,000');
  params.getRange('B21').setNumberFormat('0');
  params.getRange('B26').setNumberFormat('0');
  params.getRange('B27:B28').setNumberFormat('0,0');
  // Accent the KV + marge figures.
  params.getRangeList(['B33', 'B34', 'B40', 'B41']).setFontColor(ACCENT);
  params.getRangeList(['B35', 'B42']).setFontColor(ACCENT).setFontSize(12);
  // INPUT cells: yellow + border so they read as editable.
  params.getRange('B22:B25')
        .setBackground(YELLOW).setFontWeight('normal').setFontColor(INK)
        .setBorder(true, true, true, true, true, true, '#E6D9A8', SpreadsheetApp.BorderStyle.SOLID);
  // Card outline + column widths + hide the identifier column.
  params.getRange('A20:B42').setBorder(true, true, true, true, false, false, '#D9D9D9', SpreadsheetApp.BorderStyle.SOLID);
  params.setColumnWidth(1, 210);
  params.setColumnWidth(2, 150);
  params.hideColumns(3);

  // Section header bands — applied LAST so white-on-green wins over the
  // col-B value styling that overlaps the merged header cells (B30/B37).
  [[20, 'TEMPS CHANTIER'], [30, 'RENTABILITÉ — GLOBAL'], [37, 'RENTABILITÉ — HORS SST']]
    .forEach(function(h) {
      params.getRange(h[0], 3).setValue('');
      params.getRange(h[0], 1, 1, 2).breakApart().merge()
          .setValue(h[1])
          .setBackground(GREEN).setFontColor('#FFFFFF').setFontWeight('bold')
          .setFontSize(10).setHorizontalAlignment('left').setVerticalAlignment('middle');
      params.setRowHeight(h[0], 24);
    });

  // --- F. Named ranges for the recap cells (workbook scope) -------
  const RENT_NAMED = [
    ['Tps_chantier','B21'], ['Personnes','B22'], ['Heures_par_jour','B23'],
    ['Jours_par_semaine','B24'], ['Semaines_par_mois','B25'],
    ['Jours','B26'], ['Semaines','B27'],
    ['Rent_prix_vente','B31'], ['Rent_prix_revient','B32'], ['Rent_marge_eur','B33'],
    ['Rent_hs_prix_vente','B38'], ['Rent_hs_prix_revient','B39'], ['Rent_hs_marge_eur','B40'],
  ];
  const want = {};
  RENT_NAMED.forEach(function(p) { want[p[0]] = 1; });
  ss.getNamedRanges().forEach(function(nr) {
    var n = nr.getName(); var b = n.lastIndexOf('!'); if (b >= 0) n = n.substring(b + 1);
    if (want[n]) nr.remove();
  });
  RENT_NAMED.forEach(function(p) { ss.setNamedRange(p[0], params.getRange(p[1])); });

  SpreadsheetApp.getActive().toast('Bloc rentabilité installé sur « Pilotage de rentabilité ».', 'OK', 5);
}


/**
 * Re-apply the three client-mirror formulas (AA/AB/AC) for every data row.
 * Idempotent — if the formulas are already correct, this is a no-op write.
 */
function _restoreMirrorFormulas_(dpgf, first, last) {
  const fAA = function(r) { return '=IFERROR(IF(ISNUMBER(INDIRECT(Col_Quantite&ROW())), INDIRECT(Col_Designation&ROW()), ""), "")'; };
  const fAB = function(r) { return '=IFERROR(IF(ISNUMBER(INDIRECT(Col_Quantite&ROW())), INDIRECT(Col_Unite&ROW()),       ""), "")'; };
  const fAC = function(r) { return '=IFERROR(IF(ISNUMBER(INDIRECT(Col_Quantite&ROW())), INDIRECT(Col_Quantite&ROW()),    ""), "")'; };
  const aa = [], ab = [], ac = [];
  for (let r = first; r <= last; r++) {
    aa.push([_formulaForLocale(fAA(r))]);
    ab.push([_formulaForLocale(fAB(r))]);
    ac.push([_formulaForLocale(fAC(r))]);
  }
  dpgf.getRange('AA' + first + ':AA' + last).setFormulas(aa);
  dpgf.getRange('AB' + first + ':AB' + last).setFormulas(ab);
  dpgf.getRange('AC' + first + ':AC' + last).setFormulas(ac);
}


/**
 * Rebuild conditional formatting on the DPGF tab.
 *
 * New rules (yellow = manual input only):
 *   1. AD3:AH502  — cascade + Tier picks
 *   2. AU3:AV502  — Loc / Liv numeric inputs
 *
 * Both trigger on ISNUMBER($AC{row}) — i.e. "this row has a quantity in
 * the client mirror" — so highlighting only shows up on the rows Vincent
 * is actively working on.
 */
function _rebuildConditionalFormatting_(dpgf, first, last) {
  // Drop any existing rules first.
  dpgf.setConditionalFormatRules([]);

  const YELLOW = '#FFF2CC';

  const rule1 = SpreadsheetApp.newConditionalFormatRule()
    .whenFormulaSatisfied('=ISNUMBER($AC' + first + ')')
    .setBackground(YELLOW)
    .setRanges([dpgf.getRange('AD' + first + ':AH' + last)])
    .build();

  const rule2 = SpreadsheetApp.newConditionalFormatRule()
    .whenFormulaSatisfied('=ISNUMBER($AC' + first + ')')
    .setBackground(YELLOW)
    .setRanges([dpgf.getRange('AU' + first + ':AV' + last)])
    .build();

  dpgf.setConditionalFormatRules([rule1, rule2]);
}


/**
 * Diagnostic — lists every named range in the workbook with its sheet,
 * A1 ref, current value, and whether it's one of the 10 we need for
 * the cost chain. Writes to the Debug tab.
 *
 * Run after applyV24Patch to verify the named ranges are bound.
 */
function inspectNamedRanges() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  let dbg = ss.getSheetByName('Debug');
  if (!dbg) dbg = ss.insertSheet('Debug');

  // Find a fresh block in the Debug tab so we don't overwrite test results.
  let startRow = (dbg.getLastRow() || 0) + 3;
  dbg.getRange(startRow, 1, 1, 5).setValues([['Named Range', 'Sheet', 'Range', 'Value', 'Required?']]);
  dbg.getRange(startRow, 1, 1, 5).setFontWeight('bold');
  startRow++;

  const REQUIRED = new Set([
    'Taux_horaire', 'Securite_humain', 'Install_chantier', 'Log_gestion',
    'Loc_livr_marge', 'Humain_marge', 'Fourn_marge',
    'Col_Designation', 'Col_Unite', 'Col_Quantite'
  ]);
  const seen = new Set();
  const ranges = ss.getNamedRanges();

  for (const nr of ranges) {
    const name = nr.getName();
    seen.add(name);
    let sheet = '?', a1 = '?', value = '?';
    try {
      const r = nr.getRange();
      sheet = r.getSheet().getName();
      a1 = r.getA1Notation();
      value = String(r.getValue());
    } catch (e) {
      sheet = '(error)';
      a1 = e.message;
    }
    dbg.getRange(startRow, 1, 1, 5).setValues([[name, sheet, a1, value, REQUIRED.has(name) ? 'YES' : 'no']]);
    startRow++;
  }

  // Flag missing required named ranges
  for (const need of REQUIRED) {
    if (!seen.has(need)) {
      dbg.getRange(startRow, 1, 1, 5).setValues([[need, '(MISSING)', '', '', 'YES']]);
      dbg.getRange(startRow, 1, 1, 5).setBackground('#fde7e9');
      startRow++;
    }
  }

  dbg.autoResizeColumns(1, 5);
  SpreadsheetApp.getActive().toast('Liste des named ranges écrite dans l\'onglet Debug.', 'OK', 5);
}


/**
 * Diagnostic — writes 9 progressively-more-complex formulas to a Debug
 * tab. Run it manually (function picker → diagnoseFormulas → Run), then
 * screenshot the Debug tab. Tells me exactly which construct breaks.
 *
 * Why: AI/AJ/Helpers!B are showing "Erreur d'analyse de formule" even
 * after the parens-in-string / array-literal fixes. There's another
 * culprit and we need empirical signal to find it.
 */
function diagnoseFormulas() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  let dbg = ss.getSheetByName('Debug');
  if (dbg) dbg.clear(); else dbg = ss.insertSheet('Debug');

  // Pick the FIRST picker string from Helpers!A2 as a known-valid lookup key.
  const helpers = ss.getSheetByName('Helpers');
  const lookupKey = helpers ? String(helpers.getRange('A2').getValue() || '') : '';
  dbg.getRange('D1').setValue('Lookup key from Helpers!A2:');
  dbg.getRange('E1').setValue(lookupKey);

  const tests = [
    ['T1  arithmetic',           '=1+1'],
    ['T2  cross-tab read',       '=Bordereau!L2'],
    ['T3  INDEX',                '=INDEX(Bordereau!$L$2:$L$501, 1)'],
    ['T4  MATCH',                '=MATCH("' + lookupKey + '", Helpers!$A$2:$A$501, 0)'],
    ['T5  INDEX+MATCH bare',     '=INDEX(Bordereau!$L$2:$L$501, MATCH("' + lookupKey + '", Helpers!$A$2:$A$501, 0))'],
    ['T6  + IFERROR wrap',       '=IFERROR(INDEX(Bordereau!$L$2:$L$501, MATCH("' + lookupKey + '", Helpers!$A$2:$A$501, 0)), "fallback")'],
    ['T7  + IF wrap no emoji',   '=IF(1=1, "OK", IFERROR(INDEX(Bordereau!$L$2:$L$501, MATCH("' + lookupKey + '", Helpers!$A$2:$A$501, 0)), "fb"))'],
    ['T8  nested IF no emoji',   '=IF(1=1, "OK", IF(2=2, "OK2", IFERROR(INDEX(Bordereau!$L$2:$L$501, MATCH("' + lookupKey + '", Helpers!$A$2:$A$501, 0)), "fb")))'],
    ['T9  with emoji string',    '=IF("x"="💰 Prix moyen", "match", "no-match")'],
    ['T10 SUBSTITUTE+VALUE',     '=VALUE(SUBSTITUTE("12.50", ".", ","))'],
    ['T11 ARRAYFORMULA + LEN',   '=ARRAYFORMULA(IF(LEN(Bordereau!B2:B5)=0, "", "ok"))'],
    ['T12 ARRAYFORMULA + SUBST', '=ARRAYFORMULA(SUBSTITUTE(Bordereau!J2:J5, ".", ","))'],
    ['T13 full AI formula',
        '=IF("x"="", "", IF("x"="💰 Prix moyen", "Catalogue moyen", IFERROR(INDEX(Bordereau!$L$2:$L$501, MATCH("' + lookupKey + '", Helpers!$A$2:$A$501, 0)), "")))'],
    ['T14 direct: Taux_horaire',    '=Taux_horaire'],
    ['T15 direct: Securite_humain', '=Securite_humain'],
    ['T16 direct: Install_chantier','=Install_chantier'],
  ];

  dbg.getRange('A1:B1').setValues([['Test', 'Formula or result']]);
  for (let i = 0; i < tests.length; i++) {
    const [label, formula] = tests[i];
    const row = i + 2;
    dbg.getRange(row, 1).setValue(label);
    dbg.getRange(row, 2).setFormula(_formulaForLocale(formula));
  }
  // Also log the detected locale for confirmation.
  dbg.getRange('D2').setValue('Detected locale:');
  dbg.getRange('E2').setValue(SpreadsheetApp.getActiveSpreadsheet().getSpreadsheetLocale() || '(unknown)');

  dbg.autoResizeColumns(1, 5);
  SpreadsheetApp.getActive().toast('Diagnostic écrit dans l\'onglet Debug — screenshote-le.', 'OK', 6);
}


/**
 * Read the Bordereau tab once. Returns an array of {reference_name,
 * family_name, subcategory, packaging}. Skips "À classifier" rows.
 */
function readBordereau_() {
  const sheet = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(BORDEREAU_SHEET);
  if (!sheet) return [];
  const rng = sheet.getRange(2, 1, BORDEREAU_LAST_ROW - 1, BORDEREAU_PACKAGING);
  const values = rng.getValues();
  const out = [];
  for (const r of values) {
    const reference_name = r[BORDEREAU_REFERENCE_NAME - 1];
    if (!reference_name) continue;
    const subcategory = String(r[BORDEREAU_SUBCATEGORY - 1] || '');
    if (subcategory === 'À classifier') continue;
    out.push({
      reference_name: String(reference_name),
      family_name:    String(r[BORDEREAU_FAMILY_NAME - 1] || ''),
      subcategory:    subcategory,
      packaging:      String(r[BORDEREAU_PACKAGING - 1] || ''),
    });
  }
  return out;
}


/**
 * Read the Taxonomy tab once. Returns an array of {family_name,
 * subcategory, packaging}. No filtering — caller decides what to do
 * with the "À classifier" rows.
 */
function readTaxonomy_() {
  const sheet = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(TAXONOMY_SHEET);
  if (!sheet) return [];
  const rng = sheet.getRange(2, 1, TAXONOMY_LAST_ROW - 1, TAXONOMY_PACKAGING);
  const values = rng.getValues();
  const out = [];
  for (const r of values) {
    const fam = r[TAXONOMY_FAMILY_NAME - 1];
    if (!fam) continue;
    out.push({
      family_name: String(fam),
      subcategory: String(r[TAXONOMY_SUBCATEGORY - 1] || ''),
      packaging:   String(r[TAXONOMY_PACKAGING - 1] || ''),
    });
  }
  return out;
}

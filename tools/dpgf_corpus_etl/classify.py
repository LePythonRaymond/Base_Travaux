"""Semantic classification — the subtle, non-column step.

Given a parsed product (désignation, unité, coût, comment/CCTP, section path), assign
the family + sous-catégorie and the labor-norm task, and infer brand/material/
attributes. Two backends:

  - ``llm``      : reuses the project's Gemini config (lib/gemini pattern). Picks an
                   existing taxonomy node or proposes a new one. This is the path the
                   real run uses (requires google-generativeai + GEMINI_API_KEY).
  - ``fallback`` : deterministic keyword/section mapping. Always available; flags
                   low-confidence rows to 'À classifier'. Used when the LLM backend
                   is unavailable, so ``extract`` always produces a full review sheet.

Results are cached by line-hash so re-runs are free and the artifact is reproducible.
"""

from __future__ import annotations

import json
import os
import re

from .config import normalize
from .genus import classify_plant
from .models import RawLine

# --- classification cache (the resumable checkpoint) ---------------------------
# Keyed by canonical(designation). Populated by a human/LLM (Claude here) so the
# pipeline uses real judgment without a live API. Surviving across runs = resume.
_PKG_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_PATH = os.path.join(_PKG_DIR, "classification_cache.json")
_cache: dict | None = None


def cache_key(designation: str) -> str:
    return normalize(designation)


def load_cache(path: str | None = None) -> dict:
    global _cache, CACHE_PATH
    if path:
        CACHE_PATH = path
    if _cache is None or path:
        try:
            with open(CACHE_PATH, encoding="utf-8") as fh:
                _cache = json.load(fh)
        except (FileNotFoundError, json.JSONDecodeError):
            _cache = {}
    return _cache

# (family, default_subcategory, action_verb, [keywords]) — order = priority (specific first)
_FAMILY_RULES: list[tuple[str, str, str, list[str]]] = [
    ("Études & honoraires", "Études d'exécution", "Réaliser", [
        "plan d'exe", "plans d'exe", "plan exe", "doe", "dossier des ouvrages", "étude", "etude",
        "récolement", "recolement", "implantation", "piquetage", "analyse de sol", "analyses de sol",
        "visite en pépinière", "marquage"]),
    ("Installation de chantier", "Installation", "Installer", [
        "installation de chantier", "installation du chantier", "amenée", "amenee", "repli",
        "signalisation", "préparation du chantier", "preparation du chantier", "état des lieux", "etat des lieux"]),
    ("Dépose / démolition", "Dépose", "Déposer", [
        "dépose", "depose", "démolition", "demolition", "évacuation", "evacuation", "mise en décharge",
        "mise en decharge", "déchet", "dechet", "nettoyage", "débroussaillage", "debroussaillage", "curage"]),
    ("Terrassement / VRD", "Terrassement", "Réaliser", [
        "terrassement", "fosse de plantation", "fosse ", "fosses", "remblai", "déblai", "deblai",
        "nivellement", "décompactage", "decompactage", "fond de forme", "fondation", "tranchée", "tranchee",
        "mouvement de terre", "réglage", "reglage"]),
    ("Étanchéité / protection toiture", "Protection", "Poser", [
        "étanchéité", "etancheite", "protection d'étanchéité", "relevé d'étanch", "releve d'etanch",
        "zone stérile", "zone sterile", "bande stérile", "pare grève", "pare greve",
        "protection vegetalisee", "protection végétalisée", "vegetalisee", "protection lourde",
        "anti-rejaillissement", "rejaillissement"]),
    ("Drainage", "Couche drainante", "Poser", [
        "drain", "drainage", "agrodrain", "couche drainante", "couche de drainage", "rétention", "retention",
        "nidaplast", "delta", "natte de", "mèche drainante", "meche drainante", "élément drainant",
        "polystyrène", "polystyrene", "bille d'argile", "billes d'argile", "argile expansée",
        "argile expansee", "bille >", "billes >"]),
    ("Géotextile", "Anti-racinaire", "Poser", [
        "géotextile", "geotextile", "anti-racinaire", "antiracinaire", "feutre", "bidim", "filtre géo",
        "filtre geo", "complexe filtrant", "nappe filtrante", "nappe drainante", "nappe filtre"]),
    ("Terre végétale", "Standard", "Mettre en œuvre", [
        "terre végétale", "terre vegetale", "terre fertile", "sol fertile", "terre en place",
        "terre allégée", "terre allegee", "terre de bruyère", "terre de bruyere"]),
    ("Substrat / amendement", "Amendement", "Mettre en œuvre", [
        "substrat", "amendement", "compost", "engrais", "fertilisant", "terreau", "jauge",
        "corne de boeuf", "corne broyée", "fumier", "billes d'argile", "argile expansée", "argile expansee"]),
    ("Paillage", "Végétal", "Mettre en œuvre", [
        "paillage", "mulch", "brf", "écorce", "ecorce", "broyat", "copeaux", "paillis"]),
    ("Accessoire de plantation", "Tuteurage", "Poser", [
        "tuteur", "hauban", "aubanage", "haubanage", "ancrage", "corset", "collier", "attache",
        "protection des arbres", "protection tronc", "gaine de protection", "palissage",
        "accessoires de plantation", "accessoire de plantation"]),
    ("Arrosage / irrigation", "Goutte-à-goutte", "Poser", [
        "arrosage", "goutte à goutte", "goutte a goutte", "goutte-à-goutte", "irrigation", "électrovanne",
        "electrovanne", "fourreau", "robinet", "programmateur", "asperseur", "réseau d'arrosage",
        "reseau d'arrosage", "pvc", "pehd", "tuyau", "pluviomètre", "pluviometre", "sonde", "crepine",
        "crépine", "nourrice", "récupérateur d'eau", "recuperateur d'eau", "oya", "hygrometr",
        "tensiometr", "centrale d'acquisition", "bouche d'arrosage", "station de controle",
        "reseau de distribution", "récupérateur d'eau de pluie"]),
    ("Bac / jardinière", "Bac", "Poser", [
        "bac ", "bacs", "jardinière", "jardiniere", "vasque", "adezz", "mysteel", "corten", "pot acier",
        "bac en", "bac alu", "jardipolys", "pot ", "poterie", "bac sur mesure"]),
    ("Mobilier extérieur", "Jeux", "Poser", [
        "jeu à grimper", "jeu a grimper", "aire de jeux", "jeux", "structure de jeu", "toboggan",
        "balançoire", "balancoire"]),
    ("Bordure / élément linéaire", "Bordure", "Poser", [
        "bordure", "cornière", "corniere", "élément linéaire"]),
    ("Clôture / treillage / support", "Treillage", "Poser", [
        "clôture", "cloture", "treillage", "treillis", "câble", "cable inox", "garde-corps", "grille",
        "support de végétaux", "support de vegetaux", "ganivelle", "gabion", "enrochement", "traverse",
        "portillon", "barrière", "barriere", "caillebotis"]),
    ("Biodiversité / habitats", "Habitat", "Poser", [
        "nichoir", "gîte", "gite", "chauve-souris", "chauve souris", "hôtel à insectes", "hotel a insectes",
        "hôtel insectes", "hotel insectes", "abri", "abreuvoir", "tas de bois", "bois mort", "cavité",
        "cavite", "oiseau", "panneau pedagogique", "panneau pédagogique", "sensibilisation",
        "pedagogique", "micro-habitat", "micro habitat", "pierrier", "totem", "mangeoire", "spirale"]),
    ("Mobilier extérieur", "Mobilier", "Poser", [
        "mobilier", "banc", "assise", "banquette", "platelage", "table", "éclairage", "eclairage",
        "luminaire", "borne", "pergola", "corbeille", "signaletique", "signalétique", "pique-nique",
        "pique nique", "agriculture urbaine"]),
    ("Revêtement de sol / maçonnerie", "Revêtement", "Réaliser", [
        "béton", "beton", "dalle", "plot réglable", "plot reglable", "plot à vérin", "plot a verin",
        "plots à vérin", "plots a verin", "sur plot", "maçonnerie", "maconnerie", "muret", "pavé",
        "pave", "allée", "allee", "platine", "voie pompier", "pas japonais", "emmarchement",
        "bande d'eveil", "podotactile", "stabilisé renforcé", "stabilise renforce", "granit", "grès cérame"]),
    ("Bordure / élément linéaire", "Bordure", "Poser", [
        "bordure", "bordurette", "cornière", "corniere", "retenue de terre", "retenues de terre",
        "élément linéaire", "élément courbe", "element courbe", "element droit", "element droits"]),
    ("Minéral (gravier, pierre)", "Gravier", "Mettre en œuvre", [
        "gravier", "gravillon", "concassé", "concasse", "galet", "minéral", "mineral", "stabilisé",
        "stabilise", "sable"]),
    ("Semis / engazonnement", "Prairie", "Semer", [
        "semis", "gazon", "engazonnement", "prairie", "pelouse", "ensemencement", "hydromulching"]),
    # Plants — usually disambiguated by section path; designation genus as backup.
    ("Arbre", "Tige", "Planter", [
        "arbre", "tige", "cépée", "cepee", "quercus", "acer", "tilia", "prunus", "platane", "betula",
        "carpinus", "fruitier", "fastigié", "fastigie"]),
    ("Grimpante", "Sur support", "Planter", [
        "grimpante", "grimpant", "clematis", "clématite", "clematite", "akebia", "lierre", "hedera",
        "wisteria", "glycine", "jasmin", "vigne"]),
    ("Graminée", "Persistante", "Planter", [
        "graminée", "graminee", "carex", "stipa", "miscanthus", "pennisetum", "poa", "festuca",
        "molinia", "deschampsia", "calamagrostis"]),
    ("Couvre-sol", "Persistant", "Planter", [
        "couvre-sol", "couvre sol", "tapissant", "vinca", "pachysandra", "sedum"]),
    ("Vivace", "Persistante", "Planter", [
        "vivace", "vivaces"]),
    ("Arbuste", "Caduc", "Planter", [
        "arbuste", "arbustive", "haie", "cornus", "viburnum", "spiraea", "photinia", "hydrangea",
        "rosier", "buis", "lavande", "lavandula"]),
    ("Bulbe", "Printemps", "Planter", ["bulbe", "bulbes"]),
    ("Entretien / garantie / suivi cultural", "Suivi cultural", "Assurer", [
        "entretien", "garantie", "suivi cultural", "parachèvement", "parachevement", "confortement",
        "reprise", "arrosage de suivi", "façon culturale", "facon culturale"]),
]

# Clean labor-task name per family (the labor-norm grouping key). Plants get a more
# specific task from genus.py; this is the fallback for non-plant families.
_FAMILY_TASK = {
    "Arbre": "Plantation arbre tige", "Arbuste": "Plantation arbuste", "Vivace": "Plantation vivace",
    "Graminée": "Plantation graminée", "Couvre-sol": "Plantation couvre-sol",
    "Grimpante": "Plantation grimpante", "Bulbe": "Plantation bulbe",
    "Semis / engazonnement": "Engazonnement / semis", "Terre végétale": "Mise en œuvre terre végétale",
    "Substrat / amendement": "Mise en œuvre substrat", "Paillage": "Mise en œuvre paillage",
    "Géotextile": "Pose géotextile", "Drainage": "Pose drainage",
    "Accessoire de plantation": "Pose accessoire de plantation", "Arrosage / irrigation": "Pose arrosage",
    "Minéral (gravier, pierre)": "Mise en œuvre minéral", "Bac / jardinière": "Pose bac / jardinière",
    "Bordure / élément linéaire": "Pose bordure", "Clôture / treillage / support": "Pose clôture / treillage",
    "Biodiversité / habitats": "Pose habitat biodiversité", "Mobilier extérieur": "Pose mobilier",
    "Revêtement de sol / maçonnerie": "Réalisation revêtement de sol", "Études & honoraires": "Études & honoraires",
    "Installation de chantier": "Installation de chantier", "Terrassement / VRD": "Terrassement",
    "Dépose / démolition": "Dépose / démolition", "Étanchéité / protection toiture": "Pose protection / étanchéité",
    "Entretien / garantie / suivi cultural": "Entretien / suivi cultural",
}

# Section-path tokens that strongly imply a plant family (override designation).
_SECTION_FAMILY = {
    "arbre": ("Arbre", "Tige", "Planter"), "arbres": ("Arbre", "Tige", "Planter"),
    "arbuste": ("Arbuste", "Caduc", "Planter"), "arbustes": ("Arbuste", "Caduc", "Planter"),
    "vivace": ("Vivace", "Persistante", "Planter"), "vivaces": ("Vivace", "Persistante", "Planter"),
    "graminee": ("Graminée", "Persistante", "Planter"), "graminees": ("Graminée", "Persistante", "Planter"),
    "grimpante": ("Grimpante", "Sur support", "Planter"), "grimpantes": ("Grimpante", "Sur support", "Planter"),
    "couvre-sol": ("Couvre-sol", "Persistant", "Planter"), "couvre sol": ("Couvre-sol", "Persistant", "Planter"),
    "bulbe": ("Bulbe", "Printemps", "Planter"), "bulbes": ("Bulbe", "Printemps", "Planter"),
}

# Brands seen in the corpus (comment/designation) → brand + material hint.
_BRANDS = {
    "adezz": ("ADEZZ", "métal"), "mysteel": ("MySteel", "métal"), "atech": ("ATECH", "métal"),
    "zinco": ("ZinCo", "végétal"), "agrodrain": ("Agrodrain", None), "terralgreen": ("Terralgreen", "végétal"),
    "nidaplast": ("Nidaplast", "plastique"), "delta": ("Dörken Delta", "plastique"),
    "allavoine": ("Pépinières Allavoine", "végétal"), "carrez": ("Carrez", "métal"),
}
_MATERIALS = [
    ("inox", "métal"), ("acier", "métal"), ("alu", "métal"), ("aluminium", "métal"), ("corten", "métal"),
    ("métal", "métal"), ("metal", "métal"), ("bois", "bois"), ("mélèze", "bois"), ("meleze", "bois"),
    ("chêne", "bois"), ("végétal", "végétal"), ("vegetal", "végétal"), ("minéral", "minéral"),
    ("mineral", "minéral"), ("pierre", "minéral"), ("béton", "minéral"), ("beton", "minéral"),
]
_SIZE_RE = re.compile(r"\b(\d{1,3}\s*/\s*\d{1,3})\b")            # 10/12, 250/300, 18/20
_CONT_RE = re.compile(r"\b(godet|gdt|c\s?\d+\s?l|conteneur\s?\d+\s?l|container\s?\d+\s?l|motte|racines? nues?)\b", re.I)


def _infer_attributes(text: str) -> dict:
    attrs: dict[str, str] = {}
    m = _SIZE_RE.search(text)
    if m:
        attrs["taille"] = m.group(1).replace(" ", "")
    m = _CONT_RE.search(text)
    if m:
        attrs["conditionnement"] = m.group(1).strip()
    return attrs


def _infer_brand_material(text_norm: str) -> tuple[str | None, str | None]:
    brand = material = None
    for kw, (b, mat) in _BRANDS.items():
        if kw in text_norm:
            brand, material = b, mat
            break
    if material is None:
        for kw, mat in _MATERIALS:
            if kw in text_norm:
                material = mat
                break
    return brand, material


# One canonical labor task per family — so the task vocabulary can't diverge
# (e.g. 'Planter vivace' vs 'Plantation vivace'). Arbre keeps its forme (tige/cépée),
# which genuinely changes planting time; leaf-type (caduc/persistant) does not.
_FAMILY_TASK = {
    "Arbuste": "Plantation arbuste", "Vivace": "Plantation vivace", "Graminée": "Plantation graminée",
    "Couvre-sol": "Plantation couvre-sol", "Grimpante": "Plantation grimpante", "Bulbe": "Plantation bulbe",
    "Semis / engazonnement": "Engazonnement / semis",
    "Terre végétale": "Mise en œuvre terre végétale", "Substrat / amendement": "Mise en œuvre substrat",
    "Paillage": "Mise en œuvre paillage", "Minéral (gravier, pierre)": "Mise en œuvre minéral",
    "Drainage": "Pose drainage", "Géotextile": "Pose géotextile",
    "Étanchéité / protection toiture": "Pose protection / étanchéité",
    "Accessoire de plantation": "Pose accessoire de plantation", "Tuteur / piquet": "Pose accessoire de plantation",
    "Arrosage / irrigation": "Pose arrosage", "Bac / jardinière": "Pose bac / jardinière",
    "Bordure / élément linéaire": "Pose bordure", "Clôture / treillage / support": "Pose clôture / treillage",
    "Biodiversité / habitats": "Pose habitat biodiversité", "Mobilier extérieur": "Pose mobilier",
    "Revêtement de sol / maçonnerie": "Réalisation revêtement de sol",
    "Études & honoraires": "Études & honoraires", "Installation de chantier": "Installation de chantier",
    "Terrassement / VRD": "Terrassement", "Dépose / démolition": "Dépose / démolition",
    "Entretien / garantie / suivi cultural": "Entretien / suivi cultural",
}


def task_from_family(family: str | None, subcategory: str | None) -> str:
    """Deterministic labor-task identity from the family (no verb/subcat drift)."""
    if not family:
        return "Norme par défaut (à classifier)"
    if family == "Arbre":
        forme = "cépée" if "cep" in normalize(subcategory or "") else "tige"
        return f"Plantation arbre {forme}"
    return _FAMILY_TASK.get(family, "Norme par défaut (à classifier)")


def classify_fallback(line: RawLine) -> dict:
    """Deterministic classification: section path first, then designation keywords."""
    des_norm = normalize(line.designation)
    com_norm = normalize(line.comment)
    text = f"{des_norm} {com_norm}"
    section_norm = [normalize(s) for s in line.section_path]

    # 0) GENUS first — a recognised plant genus overrides the section path
    #    (so a Vivace priced inside a "bacs" section is still a Vivace).
    plant = classify_plant(line.designation)
    if plant:
        fam, sub, task = plant
        brand, material = _infer_brand_material(text)
        return {"family": fam, "subcategory": sub, "labor_task": task, "brand": brand,
                "material": material, "attributes": _infer_attributes(line.designation),
                "confidence": 0.85, "method": "genus"}

    family = subcat = action = None
    # 1) keyword rules on the DESIGNATION (+comment) — the ouvrage/material wins over
    #    the section (so 'Apport de compost' in an Arbres section is still Substrat).
    for fam, sub, act, kws in _FAMILY_RULES:
        if any(kw in text for kw in kws):
            family, subcat, action = fam, sub, act
            break
    # 2) fallback: the section path, when the designation itself was generic.
    if not family:
        for s in reversed(section_norm):
            for tok, (fam, sub, act) in _SECTION_FAMILY.items():
                if tok in s:
                    family, subcat, action = fam, sub, act
                    break
            if family:
                break
    if not family:
        sec = " ".join(section_norm)
        for fam, sub, act, kws in _FAMILY_RULES:
            if any(kw in sec for kw in kws):
                family, subcat, action = fam, sub, act
                break

    confidence = 0.6 if family else 0.2
    if not family:
        family, subcat = None, "À classifier"
        action = "Poser"

    brand, material = _infer_brand_material(text)
    attrs = _infer_attributes(line.designation)
    task = _FAMILY_TASK.get(family, "Norme par défaut (à classifier)") if family \
        else "Norme par défaut (à classifier)"

    return {
        "family": family,
        "subcategory": subcat,
        "labor_task": task,
        "brand": brand,
        "material": material,
        "attributes": attrs,
        "confidence": confidence,
        "method": "fallback",
    }


# --- LLM backend (wired; runs in Vincent's env with google-generativeai + key) ----
def llm_available() -> bool:
    import importlib.util
    import os
    return (
        importlib.util.find_spec("google.generativeai") is not None
        and bool(os.environ.get("GEMINI_API_KEY"))
    )


def classify(line: RawLine, *, backend: str = "auto") -> dict:
    """Classify one line. Priority: human/Claude cache → LLM (if available) → fallback.

    The cache (classification_cache.json) holds real judgment keyed by canonical
    designation, so a populated cache makes the pipeline use that judgment with no
    API call. Attributes captured from columns (taille/forme) are merged on top.
    """
    cache = load_cache()
    hit = cache.get(cache_key(line.designation))
    if hit:
        out = {
            "family": hit.get("family"),
            "subcategory": hit.get("subcategory") or "À classifier",
            "labor_task": hit.get("labor_task") or "Norme par défaut (à classifier)",
            "brand": hit.get("brand"),
            "material": hit.get("material"),
            "attributes": dict(hit.get("attributes", {})),
            "confidence": float(hit.get("confidence", 0.95)),
            "method": "claude_cache",
        }
        return out
    if backend in ("auto", "llm") and llm_available():
        try:
            return _classify_llm(line)
        except Exception:
            if backend == "llm":
                raise
    return classify_fallback(line)


def _classify_llm(line: RawLine) -> dict:  # pragma: no cover - needs SDK + key
    """Single-line Gemini classification. Reuses the project's GEMINI_API_KEY.

    Kept deliberately thin; the real run batches these and caches by line-hash.
    """
    import json
    import os

    import google.generativeai as genai

    genai.configure(api_key=os.environ["GEMINI_API_KEY"])
    model = genai.GenerativeModel("gemini-2.5-flash")
    prompt = (
        "Tu classes un produit de chantier d'un paysagiste parisien.\n"
        f"Désignation: {line.designation}\nUnité: {line.unit}\nCoût HT/u: {line.cost_ht}\n"
        f"Commentaire/CCTP: {line.comment}\nChemin de section: {' > '.join(line.section_path)}\n\n"
        "Renvoie un JSON STRICT: {\"family\":..., \"subcategory\":..., \"labor_task\":..., "
        "\"brand\":..., \"material\":..., \"attributes\":{...}, \"confidence\":0..1}. "
        "family = la grande famille de produit (Arbre, Arbuste, Vivace, Terre végétale, Drainage, "
        "Bac / jardinière, Étanchéité, ...). labor_task = la tâche de pose/plantation (ex 'Plantation arbre cépée'). "
        "Ne mets PAS de marque/matériau dans attributes s'ils ont déjà leur champ."
    )
    resp = model.generate_content(prompt)
    data = json.loads(resp.text.strip().lstrip("```json").rstrip("```").strip())
    data["method"] = "llm"
    return data

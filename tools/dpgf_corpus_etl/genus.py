"""Botanical genus → family map (encodes domain judgment for plant classification).

Plants are classified by GENUS, which overrides the section path — so a
"Persicaria bistorta" priced inside a "BACS JARDINIERES" section is still a Vivace,
not a Bac. Arbre-vs-Arbuste for genera that can be either is decided by forme
(tige/cépée → Arbre) and a few species rules. Covers the genera present in the
corpus plus common French-landscaping plants.
"""

from __future__ import annotations

import re
import unicodedata


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.lower().strip()


_STOP = {"plantation", "plantations", "fourniture", "fourn", "fourniteur", "forniture", "pose",
         "mise", "oeuvre", "de", "et", "du", "la", "le", "d", "l", "en", "des", "un", "une", "fft",
         "u", "fourni", "plantatoin", "plant", "plants", "haie", "massif", "fournituree"}


def genus_of(designation: str) -> str:
    """First meaningful word = the genus, skipping bullets/numbers and French verbs
    ('Plantation Ajuga reptans' -> 'ajuga', '- Achillea millefolium' -> 'achillea')."""
    s = _norm(designation)
    s = re.sub(r"^[\s\-*•·.\d,()]+", "", s)
    for w in re.findall(r"[a-z'’\-]+", s):
        if len(w) <= 2 or w in _STOP:
            continue
        return w
    return ""


def _g(*names: str) -> set[str]:
    return {_norm(n) for n in names}


# Clear trees (+ palms / large architectural).
_ARBRE = _g(
    "acer", "alnus", "betula", "carpinus", "castanea", "catalpa", "celtis", "cercidiphyllum",
    "cercis", "cladrastis", "davidia", "fagus", "fraxinus", "ginkgo", "gleditsia", "koelreuteria",
    "laburnum", "liquidambar", "liriodendron", "malus", "mespilus", "morus", "olea", "ostrya",
    "parrotia", "parrotiopsis", "paulownia", "picea", "pinus", "platanus", "populus", "pyrus",
    "quercus", "robinia", "sophora", "styphnolobium", "sorbus", "taxodium", "tilia", "ulmus",
    "zelkova", "erable", "amandier", "trachycarpus", "chamaerops", "phoenix", "licuala",
    "pritchardia", "washingtonia", "butia", "ensete", "musa", "tetrapanax", "abies", "cedrus",
    "cupressus", "metasequoia", "nyssa", "ostrya",
)
# Clear shrubs.
_ARBUSTE = _g(
    "abelia", "acca", "aronia", "aucuba", "berberis", "buddleja", "buxus", "callicarpa", "calluna",
    "camellia", "camelia", "ceanothus", "ceanothe", "cestrum", "choisya", "cistus", "clerodendrum",
    "cotoneaster", "daphne", "deutzia", "elaeagnus", "erica", "eriostemon", "escallonia", "euonymus",
    "fusain", "fatsia", "forsythia", "fuchsia", "garrya", "griselinia", "hebe", "hibiscus",
    "hydrangea", "hypericum", "kerria", "kolkwitzia", "lavandula", "lavande", "leucothoe", "ligustrum",
    "troene", "mahonia", "myrtus", "myrsine", "nandina", "nerium", "olearia", "osmanthus", "osmanthe",
    "philadelphus", "phillyrea", "photinia", "physocarpus", "pittosporum", "prostanthera", "pyracantha",
    "rhamnus", "rhaphiolepis", "rhododendron", "azalee", "ribes", "rosa", "rosier", "rosmarinus",
    "rubus", "ruscus", "sarcococca", "sarcoccocca", "skimmia", "spiraea", "spiree", "stachyurus",
    "stranvaesia", "symphoricarpos", "tamarix", "weigela", "yucca", "schefflera", "dracaena",
    "phormium", "cordyline", "leycesteria", "callistemon", "abeliophyllum", "lonicera",
)
# Climbers.
_GRIMPANTE = _g(
    "clematis", "hedera", "lierre", "humulus", "jasminum", "jasmin", "passiflora", "trachelospermum",
    "rhycospermum", "rhynchospermum", "rhyncospermum", "holboellia", "akebia", "wisteria", "glycine",
    "parthenocissus", "vitis", "vigne", "bignonia", "campsis", "hardenbergia", "billardiera",
    "ampelopsis", "lonicera_grimpante", "fallopia", "lathyrus", "lonicera",
)
# Grasses / sedges / rushes.
_GRAMINEE = _g(
    "agrostis", "alopecurus", "ammophila", "andropogon", "anemanthele", "arundo", "bolboschoenus",
    "bouteloua", "brachypodium", "briza", "calamagrostis", "carex", "chasmanthium", "cortaderia",
    "cymbopogon", "cynodon", "deschampsia", "elymus", "eragrostis", "festuca", "hakonechloa",
    "helictotrichon", "hordeum", "imperata", "juncus", "koeleria", "leymus", "luzula", "melica",
    "milium", "miscanthus", "molinia", "muhlenbergia", "mulhenbergia", "nassella", "panicum",
    "pennisetum", "phalaris", "poa", "schizachyrium", "scirpus", "sesleria", "sporobolus", "stipa",
    "typha", "uncinia", "anthoxanthum", "holcus",
)
# Ground-covers.
_COUVRE_SOL = _g(
    "ajuga", "asarum", "cotula", "dichondra", "glechoma", "herniaria", "leptinella", "lysimachia",
    "ophiopogon", "pachysandra", "phyla", "pratia", "sagina", "soleirolia", "waldsteinia", "dorycnium",
    "frankenia", "vinca", "muehlenbeckia",
)
# Bulbs / corms / tubers.
_BULBE = _g(
    "allium", "camassia", "calchicum", "colchicum", "crocosmia", "crocus", "cyclamen", "dahlia",
    "eranthis", "eremurus", "erythronium", "fritillaria", "galanthus", "gladiolus", "hyacinthoides",
    "hyacinthus", "ipheion", "leucojum", "lilium", "lis", "muscari", "narcissus", "nerine",
    "ornithogalum", "oxalis", "puschkinia", "scilla", "sternbergia", "tulipa", "triteleia",
    "zantedeschia", "sternbergia",
)
# Genera that may be Arbre or Arbuste — decided by forme/species.
_AMBIG = _g(
    "prunus", "cornus", "cornouiller", "salix", "magnolia", "sambucus", "viburnum", "viorne", "ilex",
    "taxus", "arbutus", "crataegus", "corylus", "coryllus", "noisetier", "hamamelis", "cotinus",
    "syringa", "amelanchier", "aralia", "ficus", "laurier",
)
_AMBIG_DEFAULT = {  # default when no forme signal
    "prunus": "Arbre", "salix": "Arbre", "magnolia": "Arbre", "arbutus": "Arbre",
    "crataegus": "Arbre", "amelanchier": "Arbre", "laurier": "Arbre",
    "cornus": "Arbuste", "cornouiller": "Arbuste", "sambucus": "Arbuste", "viburnum": "Arbuste",
    "viorne": "Arbuste", "ilex": "Arbuste", "taxus": "Arbuste", "corylus": "Arbuste",
    "coryllus": "Arbuste", "noisetier": "Arbuste", "hamamelis": "Arbuste", "cotinus": "Arbuste",
    "syringa": "Arbuste", "aralia": "Arbuste", "ficus": "Arbuste",
}

# Everything else herbaceous defaults to Vivace (perennials, ferns, herbs).
_VIVACE_HINT = _g(
    "achillea", "acchilea", "acthilea", "actaea", "agapanthus", "agastache", "alcea", "alchemilla",
    "anaphalis", "anemone", "angelica", "anthemis", "anthericum", "aquilegia", "armeria", "artemisia",
    "aruncus", "asclepias", "asphodelus", "aspidistra", "aster", "astilbe", "astrantia", "athyrium",
    "baptisia", "bergenia", "borago", "brunnera", "bupleurum", "bepleurum", "calamintha", "caltha",
    "campanula", "catananche", "centaurea", "centranthus", "cephalaria", "cerastium", "chelone",
    "cheiranthus", "chrysanthemum", "cimicifuga", "clarkia", "convallaria", "coreopsis", "coriandrum",
    "coriandre", "cosmos", "cosmis", "crambe", "cryptotaenia", "cynara", "cynoglossum", "darmera",
    "delphinium", "delosperma", "dorotheanthus", "dianthus", "dicentra", "dictamnus", "digitalis",
    "disporum", "doronicum", "dryopteris", "echinacea", "echinops", "epimedium", "erigeron", "eryngium",
    "eupatorium", "euphorbia", "ferula", "filipendula", "foeniculum", "fragaria", "gaillardia",
    "galium", "gaura", "gentiana", "geranium", "geum", "gillenia", "gunnera", "gypsophila", "helenium",
    "helianthus", "heliopsis", "helichrysum", "helleborus", "hemerocallis", "hesperis", "heuchera",
    "heucherella", "hosta", "houttuynia", "hyssopus", "iberis", "inula", "iris", "kalimeris",
    "kirengeshoma", "knautia", "kniphofia", "lamium", "lamprocapnos", "leucanthemum", "lewisia",
    "liatris", "ligularia", "limonium", "linaria", "linum", "liriope", "lobelia", "lobularia",
    "lobulaire", "lunaria", "lupinus", "lychnis", "lythrum", "macleaya", "malva", "matricaria",
    "mazus", "meconopsis", "melissa", "melisse", "mentha", "menthe", "mertensia", "mimulus", "monarda",
    "myosotis", "myrrhis", "nemophila", "nepeta", "nierembergia", "oenothera", "omphalodes", "origanum",
    "orpin", "paeonia", "papaver", "paradisea", "penstemon", "perovskia", "persicaria", "petasites",
    "peucedanum", "phlomis", "phlox", "phuopsis", "physostegia", "phytolacca", "plectranthus",
    "polemonium", "polygonatum", "polypodium", "polystichum", "potentilla", "primula", "prunela",
    "prunella", "pulmonaria", "pulsatilla", "ranunculus", "ratibida", "rheum", "rodgersia", "rudbeckia",
    "rumex", "salvia", "sauge", "sanguisorba", "santolina", "saponaria", "saruma", "satureja",
    "saxifraga", "scabiosa", "sedum", "sedums", "senecio", "sidalcea", "silene", "sisyrinchium",
    "solidago", "stachys", "stellaria", "stokesia", "succisa", "symphytum", "tanacetum", "tellima",
    "teucrium", "thalictrum", "thym", "thymus", "tiarella", "tradescantia", "tricyrtis", "trifolium",
    "trillium", "tripleurospermum", "trollius", "valeriana", "veratrum", "verbascum", "verbena",
    "vernonia", "veronica", "veronicastrum", "viola", "fougere", "asplenium", "pteridium", "blechnum",
    "matteuccia", "osmunda", "adiantum", "nepeta", "sternbergia",
)

# Supplementary: exotics / tropicals / bamboos / common-name genera seen in corpus.
_VIVACE_HINT |= _g("anthurium", "philodendron", "monstera", "alocasia", "colocasia", "calathea",
                   "spathiphyllum", "sansevieria", "zamioculcas", "strelitzia", "acanthus",
                   "duchesnea", "fragaria", "dieffenbachia", "aglaonema", "maranta", "peperomia",
                   "begonia", "clivia", "aspidistra")
_ARBUSTE |= _g("protea", "leucadendron", "leucospermum", "banksia", "grevillea", "plumeria",
               "frangipanier", "vaccinium", "vitex", "brugmansia", "datura", "lantana", "plumbago",
               "tibouchina", "pieris", "andromeda", "gaultheria", "leucothoe", "sarcococca")
_GRAMINEE |= _g("bambou", "bambous", "phyllostachys", "fargesia", "pleioblastus", "sasa",
                "indocalamus", "semiarundinaria")
_GRIMPANTE |= _g("chevrefeuille", "cobaea", "thunbergia", "ipomoea")
_COUVRE_SOL |= _g("duchesnea", "fragaria")

# Evergreen shrubs → subcategory 'Persistant' (else 'Caduc').
_PERSISTANT = _g(
    "buxus", "ilex", "camellia", "camelia", "choisya", "pittosporum", "photinia", "lavandula", "lavande",
    "rosmarinus", "euonymus", "osmanthus", "osmanthe", "prunus", "ligustrum", "troene", "ruscus",
    "sarcococca", "sarcoccocca", "mahonia", "nandina", "fatsia", "aucuba", "myrtus", "myrsine",
    "phillyrea", "taxus", "erica", "calluna", "hebe", "skimmia", "viburnum", "elaeagnus", "griselinia",
    "rhododendron", "azalee", "yucca", "phormium", "olea",
)

_TREE_FORME = ("tige", "cepee", "baliveau", "demi-tige", "demi tige", "haute tige", "1/2 tige",
               "multi-tige", "multi tige")
_SHRUB_SIG = ("godet", "conteneur", "container", "motte", "racine", "gdt")
_CORNUS_TREE = ("controversa", "kousa", "mas", "florida", "alternifolia", "nuttallii")
_PRUNUS_SHRUB = ("laurocerasus", "lusitanica", "laurier", "laurocer")


def _arbre_or_arbuste(genus: str, dnorm: str) -> str:
    if any(f in dnorm for f in _TREE_FORME):
        return "Arbre"
    if any(s in dnorm for s in _SHRUB_SIG) or re.search(r"\bc\d", dnorm):
        return "Arbuste"
    if genus in ("cornus", "cornouiller") and any(s in dnorm for s in _CORNUS_TREE):
        return "Arbre"
    if genus == "prunus" and any(s in dnorm for s in _PRUNUS_SHRUB):
        return "Arbuste"
    return _AMBIG_DEFAULT.get(genus, "Arbuste")


_TASK = {
    "Arbre": "Plantation arbre tige", "Arbuste": "Plantation arbuste",
    "Vivace": "Plantation vivace", "Graminée": "Plantation graminée",
    "Couvre-sol": "Plantation couvre-sol", "Grimpante": "Plantation grimpante",
    "Bulbe": "Plantation bulbe",
}
_SUB = {
    "Arbre": "Tige", "Arbuste": "Caduc", "Vivace": "Vivace", "Graminée": "Graminée",
    "Couvre-sol": "Couvre-sol", "Grimpante": "Grimpante", "Bulbe": "Bulbe",
}


def classify_plant(designation: str) -> tuple[str, str, str] | None:
    """Return (family, subcategory, labor_task) for a plant, or None if not a plant."""
    g = genus_of(designation)
    if not g:
        return None
    dnorm = _norm(designation)
    family = None
    if g in _GRAMINEE:
        family = "Graminée"
    elif g in _GRIMPANTE and g != "lonicera":
        family = "Grimpante"
    elif g in _BULBE:
        family = "Bulbe"
    elif g in _AMBIG:
        family = _arbre_or_arbuste(g, dnorm)
    elif g in _ARBRE:
        family = "Arbre"
    elif g in _ARBUSTE:
        family = "Arbuste"
    elif g in _COUVRE_SOL:
        family = "Couvre-sol"
    elif g in _VIVACE_HINT:
        family = "Vivace"
    if family is None:
        return None
    sub = _SUB[family]
    if family == "Arbre" and ("cepee" in dnorm or "cépée" in designation.lower()):
        sub = "Cépée"
    if family == "Arbuste" and g in _PERSISTANT:
        sub = "Persistant"
    task = _TASK[family]
    if family == "Arbre" and sub == "Cépée":
        task = "Plantation arbre cépée"
    return family, sub, task

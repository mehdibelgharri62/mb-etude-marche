"""
MB CONSULTING — Génération du contenu via Gemini (V5 quasi finale)
=======================================================
Objectif V5 : version quasi finale robuste : champs manquants, concurrence renforcée, avis prudents, marchés émergents, sources qualifiées, cohérence financière, nettoyage automatique des artefacts HTML/Markdown.

Principes intégrés :
  - 15 sections au lieu de 30.
  - Synthèse décisionnelle et verdict générés en dernier.
  - Dictionnaire unique d'hypothèses chiffrées, réutilisé partout.
  - Formulaire client court + 6 spécificités guidées, sans champ libre obligatoire.
  - Variantes limitées à 3 zones du rapport : hypothèses, accès au marché, opérations/finance.
  - Garde-fous anti-placeholder, anti-chiffres non qualifiés, anti-cellules trop longues.
  - Définitions immédiates des termes techniques à leur première apparition.
  - Reprise section par section en cas de quota/API 429.

INSTALLATION :
    pip install requests

LANCEMENT :
    Mac/Linux : export GEMINI_API_KEY="ta_cle_ici"
    Windows PowerShell : $env:GEMINI_API_KEY = "ta_cle_ici"
    python3 generate_content.py
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union

import requests

# ----------------------------------------------------------------------------
# 1. FORMULAIRE CLIENT — 8 champs simples + spécificités guidées
# ----------------------------------------------------------------------------

SPECIFICITY_OPTIONS: Dict[str, Dict[str, str]] = {
    "local_erp": {
        "label": "Local, accueil du public ou emplacement physique important",
        "impact": "Renforce l'analyse de zone, flux, visibilité, contraintes de local et charges fixes.",
    },
    "digital_acquisition": {
        "label": "Vente en ligne, acquisition digitale ou dépendance aux réseaux sociaux / SEO / publicité",
        "impact": "Renforce l'analyse de demande en ligne, concurrence digitale, CAC, conversion et tunnel de vente.",
    },
    "stock_matiere": {
        "label": "Stock, matières premières, achat-revente ou fabrication",
        "impact": "Renforce marge brute, fournisseurs, rotation de stock, trésorerie immobilisée et risque de surstock.",
    },
    "service_humain": {
        "label": "Prestation de service, expertise humaine ou temps de production important",
        "impact": "Renforce capacité mensuelle, pricing, temps vendu, preuve d'expertise et qualité de service.",
    },
    "reglementation": {
        "label": "Réglementation, hygiène, sécurité, autorisations ou normes spécifiques",
        "impact": "Renforce les points à vérifier avant lancement et les risques bloquants.",
    },
    "investissement_lourd": {
        "label": "Investissement de départ élevé, charges fixes fortes ou besoin de financement",
        "impact": "Renforce prudence financière, scénarios, point mort, trésorerie et conditions Go / No-Go.",
    },
}

PROJECT_TYPE_OPTIONS = [
    "commerce_local",
    "ecommerce",
    "service_b2b",
    "service_b2c_local",
    "restauration_alimentaire",
    "artisanat_production",
    "saas_application",
    "projet_hybride",
]


# Champs obligatoires et valeurs de remplacement.
# Principe : on ne met jamais 0 par défaut pour un champ absent ; on marque la donnée comme manquante,
# déduite ou hypothèse de travail, puis les prompts doivent rester prudents.
REQUIRED_INPUT_FIELDS = ["concept", "project_type", "zone", "target_customer", "main_offer"]
OPTIONAL_INPUT_FIELDS = [
    "budget_eur", "revenue_model", "specificities", "additional_context",
    "competitors_known", "price_positioning", "stage", "main_goal", "constraints",
]

DEFAULT_BUDGET_RANGES: Dict[str, str] = {
    "ecommerce": "5 000 à 15 000 €",
    "artisanat_production": "8 000 à 25 000 €",
    "service_b2b": "2 000 à 10 000 €",
    "service_b2c_local": "5 000 à 20 000 €",
    "commerce_local": "30 000 à 100 000 €",
    "restauration_alimentaire": "50 000 à 150 000 €",
    "saas_application": "20 000 à 100 000 €",
    "projet_hybride": "10 000 à 50 000 €",
}

DEFAULT_REVENUE_MODELS: Dict[str, str] = {
    "ecommerce": "vente unitaire en ligne, paniers moyens, ventes récurrentes ou coffrets selon l'offre",
    "artisanat_production": "vente unitaire de produits, coffrets, ventes ponctuelles physiques et revente possible en boutiques partenaires",
    "service_b2b": "prestations au forfait, journées d'intervention, accompagnements ou abonnements de service",
    "service_b2c_local": "prestations unitaires, forfaits, abonnements simples ou ventes complémentaires",
    "commerce_local": "vente unitaire en point de vente, panier moyen, ventes complémentaires et fidélisation",
    "restauration_alimentaire": "vente au ticket moyen, formules, récurrence locale et ventes additionnelles",
    "saas_application": "abonnements, freemium éventuel, options payantes ou frais de mise en service",
    "projet_hybride": "combinaison de ventes unitaires, prestations, revenus digitaux ou ventes physiques selon l'offre",
}

DEFAULT_SPECIFICITIES_BY_PROJECT_TYPE: Dict[str, List[str]] = {
    "ecommerce": ["digital_acquisition", "stock_matiere"],
    "artisanat_production": ["digital_acquisition", "stock_matiere", "reglementation"],
    "service_b2b": ["service_humain", "digital_acquisition"],
    "service_b2c_local": ["service_humain", "local_erp"],
    "commerce_local": ["local_erp", "stock_matiere"],
    "restauration_alimentaire": ["local_erp", "stock_matiere", "reglementation", "investissement_lourd"],
    "saas_application": ["digital_acquisition", "service_humain"],
    "projet_hybride": ["digital_acquisition", "local_erp"],
}

MISSING_FIELD_TEXTS: Dict[str, str] = {
    "budget_eur": "Budget non renseigné : l'analyse utilise une fourchette indicative adaptée au type de projet, à confirmer avant toute décision d'investissement.",
    "target_customer": "Client cible non précisé : l'analyse retient des segments probables à partir du concept, à valider par tests terrain.",
    "zone": "Zone non précisée : l'analyse locale est limitée ; préciser au minimum une ville, une région ou 'France / en ligne'.",
    "revenue_model": "Modèle de revenus non renseigné : l'analyse retient le modèle le plus probable pour ce type de projet, à confirmer.",
    "specificities": "Aucune spécificité cochée : le code applique des spécificités probables d'après le type de projet, à valider.",
    "competitors_known": "Aucun concurrent fourni : l'analyse repose sur les recherches disponibles et sur les acteurs comparables identifiés.",
}


CLIENT_PRIVATE_FIELDS = {"name", "email", "phone"}
CLIENT_REPORT_ALLOWED_FIELDS = {"project_name", "stage", "main_goal"}
STAGE_LABELS = {
    "idee": "idée à cadrer",
    "preparation": "préparation du lancement",
    "deja_lance": "activité déjà lancée",
    "repositionnement": "repositionnement d'une activité existante",
    "developpement": "développement / croissance d'une activité existante",
}
MAIN_GOAL_LABELS = {
    "valider_idee": "valider l'idée",
    "comprendre_marche": "comprendre le marché",
    "analyser_concurrence": "analyser la concurrence",
    "definir_offre_prix": "définir l'offre et les prix",
    "plan_lancement": "construire un plan de lancement",
    "preparer_financement": "préparer un financement",
    "developper_ventes": "développer les ventes",
    "se_rassurer": "se rassurer avant d'investir",
}


PROJECT_INPUT: Dict[str, Any] = {
    # Exemple volontairement vide : en production, main.py / FastAPI injecte les données réelles
    # AVANT d'appeler main(). Ne jamais laisser ici un vrai ancien projet de test.
    "concept": "",
    "project_type": "",
    "zone": "",
    "target_customer": "",
    "main_offer": "",
    "budget_eur": None,
    "revenue_model": "",
    "specificities": [],
    "additional_context": "",
}

CONTENT_FILE = os.environ.get("CONTENT_FILE", "contenu_genere.json")  # nom final sans suffixe V5

API_KEY = os.environ.get("GEMINI_API_KEY")

MODEL_REDACTION = os.environ.get("MODEL_REDACTION", "gemini-2.5-flash")
MODELES_REDACTION_CANDIDATS = [
    m.strip() for m in os.environ.get("MODELES_REDACTION_CANDIDATS", f"{MODEL_REDACTION},gemini-2.0-flash").split(",")
    if m.strip()
]
MODELES_RECHERCHE_CANDIDATS = [
    m.strip() for m in os.environ.get("MODELES_RECHERCHE_CANDIDATS", "gemini-3-flash,gemini-2.5-flash").split(",")
    if m.strip()
]
_modele_recherche_valide: Optional[str] = None
PAUSE_ENTRE_APPELS = 0.8
MAX_RETRIES = 4

# V4.1 : budget de recherche.
# light = très économique ; standard = rapport de base vendable ; premium = plus de preuves.
RESEARCH_MODE = "standard"
FORCE_REFRESH_RESEARCH = False
STALE_RESEARCH_MARKERS = [
    "les noms des concurrents spécifiques n'ont pas été fournis",
    "noms des concurrents spécifiques n’ont pas été fournis",
    "archétypes d'acteurs",
    "archétypes d’acteurs",
]


# ----------------------------------------------------------------------------
# 2. PROFIL PROJET — peu de champs client, logique côté code
# ----------------------------------------------------------------------------

def normalize_specificities(values: Sequence[str]) -> List[str]:
    valid = []
    for value in values:
        if value in SPECIFICITY_OPTIONS and value not in valid:
            valid.append(value)
    return valid


def derive_project_profile(project: Dict[str, Any]) -> Dict[str, Any]:
    """Déduit les variantes utiles sans multiplier les sections.

    Les variantes visibles restent limitées à 3 zones :
      - section 3 : hypothèses chiffrées,
      - section 4 : demande / accès au marché,
      - sections 10 à 12 : opérations + finance.
    """
    project_type = project.get("project_type", "projet_hybride")
    specificities = normalize_specificities(project.get("specificities", []))

    is_local = project_type in {"commerce_local", "service_b2c_local", "restauration_alimentaire", "artisanat_production", "projet_hybride"} or "local_erp" in specificities
    is_digital = project_type in {"ecommerce", "saas_application", "projet_hybride"} or "digital_acquisition" in specificities
    has_stock = project_type in {"ecommerce", "artisanat_production", "restauration_alimentaire", "commerce_local", "projet_hybride"} or "stock_matiere" in specificities
    is_service = project_type in {"service_b2b", "service_b2c_local", "saas_application", "projet_hybride"} or "service_humain" in specificities
    regulated = project_type in {"restauration_alimentaire"} or "reglementation" in specificities
    budget_value = project.get("budget_eur") or 0
    heavy_investment = budget_value >= 50000 or "investissement_lourd" in specificities

    if is_local and is_digital:
        implantation = "hybride"
    elif is_digital:
        implantation = "digitale"
    else:
        implantation = "physique"

    if project_type == "ecommerce" or (is_digital and has_stock):
        financial_model = "vente_en_ligne_avec_stock"
    elif project_type in {"service_b2b", "service_b2c_local"} or (is_service and not has_stock):
        financial_model = "service_capacite_temps"
    elif project_type == "saas_application":
        financial_model = "abonnement_digital"
    elif has_stock:
        financial_model = "commerce_stock_local"
    else:
        financial_model = "modele_mixte_simple"

    risk_profile: List[str] = []
    if is_local:
        risk_profile += ["mauvais emplacement", "charges fixes", "flux insuffisant"]
    if is_digital:
        risk_profile += ["coût d'acquisition client", "taux de conversion", "dépendance plateforme"]
    if has_stock:
        risk_profile += ["surstock", "marge brute", "dépendance fournisseurs", "trésorerie immobilisée"]
    if is_service:
        risk_profile += ["capacité de production", "qualité de service", "temps fondateur"]
    if regulated:
        risk_profile += ["autorisations", "normes", "hygiène ou sécurité"]
    if heavy_investment:
        risk_profile += ["point mort élevé", "besoin de financement", "délai de retour sur investissement"]

    # Déduplique en conservant l'ordre.
    risk_profile = list(dict.fromkeys(risk_profile))

    variant_focus = {
        "section_3_hypotheses": [],
        "section_4_marche": [],
        "section_10_12_operations_finance": [],
    }
    if is_local:
        variant_focus["section_4_marche"].append("zone de chalandise, flux, emplacement")
        variant_focus["section_10_12_operations_finance"].append("local, charges fixes, capacité physique")
    if is_digital:
        variant_focus["section_4_marche"].append("demande en ligne, requêtes, canaux digitaux")
        variant_focus["section_10_12_operations_finance"].append("CAC, conversion, tunnel digital")
    if has_stock:
        variant_focus["section_3_hypotheses"].append("stock, coût matière, marge brute, rotation")
        variant_focus["section_10_12_operations_finance"].append("fournisseurs, approvisionnement, stockage")
    if is_service:
        variant_focus["section_3_hypotheses"].append("temps vendu, capacité mensuelle, prix prestation")
        variant_focus["section_10_12_operations_finance"].append("capacité humaine, planning, qualité de service")
    if regulated:
        variant_focus["section_10_12_operations_finance"].append("normes, autorisations, points bloquants")
    if heavy_investment:
        variant_focus["section_3_hypotheses"].append("CAPEX, trésorerie initiale, charges fixes")
        variant_focus["section_10_12_operations_finance"].append("point mort, trésorerie, conditions Go / No-Go")

    return {
        "implantation": implantation,
        "financial_model": financial_model,
        "is_local": is_local,
        "is_digital": is_digital,
        "has_stock": has_stock,
        "is_service": is_service,
        "regulated": regulated,
        "heavy_investment": heavy_investment,
        "risk_profile": risk_profile,
        "variant_focus": variant_focus,
        "specificity_labels": [SPECIFICITY_OPTIONS[s]["label"] for s in specificities],
    }


def _is_blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) == 0
    return False


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def infer_specificities(project_type: str, supplied: Sequence[str]) -> Tuple[List[str], bool]:
    supplied_valid = normalize_specificities(supplied or [])
    if supplied_valid:
        return supplied_valid, False
    return normalize_specificities(DEFAULT_SPECIFICITIES_BY_PROJECT_TYPE.get(project_type, [])), True


def normalize_project_input(raw_project: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Normalise le formulaire avant toute recherche/génération.

    Sorties :
      - project : valeurs propres utilisables par les prompts.
      - meta : statut champ par champ, textes de remplacement et limites.

    Garde-fous :
      - pas de budget à 0 par défaut ; si absent, budget_eur=None + fourchette indicative.
      - champs bloquants détectés tôt.
      - spécificités déduites seulement si non cochées.
    """
    project = dict(raw_project)
    meta: Dict[str, Any] = {
        "fields": {},
        "blocking_errors": [],
        "fallbacks": {},
        "global_notes": [],
        "private_contact": {},
    }

    # Champs commerciaux : conservés pour le suivi, jamais injectés dans les prompts ni dans le rapport.
    for private_field in CLIENT_PRIVATE_FIELDS:
        if not _is_blank(raw_project.get(private_field)):
            meta["private_contact"][private_field] = _clean_text(raw_project.get(private_field))
        project.pop(private_field, None)

    project_type = _clean_text(project.get("project_type")) or "projet_hybride"
    if project_type not in PROJECT_TYPE_OPTIONS:
        meta["blocking_errors"].append(f"project_type invalide : {project_type}. Valeurs acceptées : {', '.join(PROJECT_TYPE_OPTIONS)}")
        project_type = "projet_hybride"
    project["project_type"] = project_type
    meta["fields"]["project_type"] = {"status": "provided" if not _is_blank(raw_project.get("project_type")) else "inferred", "value": project_type}

    for field in ["concept", "zone", "target_customer", "main_offer"]:
        if _is_blank(project.get(field)):
            meta["fields"][field] = {"status": "missing", "value": None, "fallback_text": MISSING_FIELD_TEXTS.get(field, "Champ non renseigné.")}
            meta["blocking_errors"].append(f"Champ obligatoire manquant : {field}")
        else:
            project[field] = _clean_text(project[field])
            meta["fields"][field] = {"status": "provided", "value": project[field]}

    # Budget : facultatif mais jamais remplacé par 0.
    raw_budget = project.get("budget_eur")
    budget_range = DEFAULT_BUDGET_RANGES.get(project_type, "5 000 à 25 000 €")
    if _is_blank(raw_budget):
        project["budget_eur"] = None
        project["budget_display"] = f"Non renseigné — fourchette indicative : {budget_range}"
        meta["fields"]["budget_eur"] = {
            "status": "missing",
            "value": None,
            "fallback_range": budget_range,
            "fallback_text": MISSING_FIELD_TEXTS["budget_eur"],
        }
    else:
        try:
            budget_float = float(str(raw_budget).replace("€", "").replace(" ", "").replace(",", "."))
            if budget_float <= 0:
                raise ValueError("budget <= 0")
            project["budget_eur"] = int(budget_float) if budget_float.is_integer() else budget_float
            project["budget_display"] = f"{project['budget_eur']} €"
            meta["fields"]["budget_eur"] = {"status": "provided", "value": project["budget_eur"]}
        except Exception:
            project["budget_eur"] = None
            project["budget_display"] = f"Budget à clarifier — fourchette indicative : {budget_range}"
            meta["fields"]["budget_eur"] = {
                "status": "invalid_or_missing",
                "value": None,
                "fallback_range": budget_range,
                "fallback_text": "Budget fourni non exploitable : l'analyse utilise une fourchette indicative à confirmer.",
            }

    # Modèle de revenus : facultatif, déduit si absent.
    if _is_blank(project.get("revenue_model")):
        fallback = DEFAULT_REVENUE_MODELS.get(project_type, DEFAULT_REVENUE_MODELS["projet_hybride"])
        project["revenue_model"] = fallback
        meta["fields"]["revenue_model"] = {"status": "inferred", "value": fallback, "fallback_text": MISSING_FIELD_TEXTS["revenue_model"]}
    else:
        project["revenue_model"] = _clean_text(project["revenue_model"])
        meta["fields"]["revenue_model"] = {"status": "provided", "value": project["revenue_model"]}

    specs, inferred_specs = infer_specificities(project_type, project.get("specificities", []))
    project["specificities"] = specs
    meta["fields"]["specificities"] = {
        "status": "inferred" if inferred_specs else "provided",
        "value": specs,
        "fallback_text": MISSING_FIELD_TEXTS["specificities"] if inferred_specs else "",
    }

    for field in ["additional_context", "competitors_known", "price_positioning", "stage", "main_goal", "constraints", "project_name"]:
        if _is_blank(project.get(field)):
            project[field] = ""
            meta["fields"][field] = {"status": "missing", "value": "", "fallback_text": MISSING_FIELD_TEXTS.get(field, "Champ facultatif non renseigné.")}
        else:
            if isinstance(project[field], str):
                project[field] = _clean_text(project[field])
            meta["fields"][field] = {"status": "provided", "value": project[field]}

    # Libellés utiles pour les prompts, sans complexifier la logique.
    if project.get("stage"):
        project["stage_label"] = STAGE_LABELS.get(str(project["stage"]).strip(), str(project["stage"]).strip())
    if project.get("main_goal"):
        project["main_goal_label"] = MAIN_GOAL_LABELS.get(str(project["main_goal"]).strip(), str(project["main_goal"]).strip())

    if meta["blocking_errors"]:
        # On bloque avant d'appeler l'API, pour éviter des rapports absurdes et des coûts inutiles.
        # En production, main.py doit capter cette exception et envoyer un email technique.
        message = "\n".join(meta["blocking_errors"])
        raise SystemExit("Erreur formulaire :\n" + message)

    meta["global_notes"].append("Statuts utilisés : provided = fourni par le porteur de projet ; inferred = déduit automatiquement ; missing = non renseigné ; working_hypothesis = hypothèse de travail à vérifier.")
    return project, meta


PROJECT_INPUT_RAW: Dict[str, Any] = {}
PROJECT_INPUT_META: Dict[str, Any] = {}
PROJECT_PROFILE: Dict[str, Any] = {}


# ----------------------------------------------------------------------------
# 3. PROMPT SYSTÈME + GARDE-FOUS UNIVERSELS
# ----------------------------------------------------------------------------

UNIVERSAL_WRITING_RULES = """
RÈGLES ABSOLUES DE SORTIE :
- Ne produis jamais de page blanche, de section vide, de texte du type "section non générée", ni de placeholder.
- Interdiction stricte d'utiliser : [Nom], [Date], [X], [Y], [Z], "à compléter", "X milliers", "X milliards", "TODO", "<br>". Ne produis jamais de séparateur Markdown horizontal "---".
- Aucun chiffre précis ne doit apparaître sans statut clair : "source web trouvée", "hypothèse de travail", "calcul interne" ou "à vérifier terrain".
- Pour les concurrents, avis clients et prix observés : n'invente jamais de noms, notes, nombres d'avis, verbatims, prix ou volumes. Si la preuve manque, indique "signal faible" ou "à vérifier".
- Si un chiffre n'est pas réellement sourcé dans le contexte fourni, écris "hypothèse de travail à vérifier".
- Définis immédiatement tout terme technique business, commerce, finance, comptabilité, marketing ou digital à sa première apparition. Définition courte, entre parenthèses, en langage simple.
- Ne commence jamais par "Bonjour", "Cher porteur de projet", "En tant que consultant", ni ne termine par "Cordialement".
- Paragraphes courts : 4 à 7 lignes maximum.
- Pas plus de deux paragraphes consécutifs sans respiration : sous-titre, liste, encadré ou tableau.
- Les tableaux doivent avoir 3 à 4 colonnes maximum, 8 lignes maximum, et des cellules courtes.
- Aucune cellule de tableau ne doit dépasser 35 mots. Si l'analyse est plus longue, sors-la du tableau et mets-la en paragraphe après le tableau.
- Utilise du Markdown simple : ## sous-titre, ### sous-sous-titre, tableaux Markdown, puces avec "-". N'utilise jamais de <br>, <br/>, <br />, ni de séparateurs "---".
- N'utilise pas d'astérisques bruts pour simuler des puces.
- N'utilise pas de tableaux pour tout : texte court + tableaux seulement quand ils clarifient une comparaison, une décision, une hypothèse, un risque ou une action.
- Les signaux issus d'avis publics concurrents doivent être formulés prudemment : "certains avis consultables mentionnent", "signal faible", "signal récurrent apparent". Ne cite pas de longs verbatims.
- Qualifie mieux les sources : utilise si possible des labels comme source institutionnelle, étude sectorielle, source professionnelle, analyse concurrentielle, sites concurrents, avis publics, Google/Maps/annuaires, données macro à vérifier, hypothèse de travail. Si le nom de l’organisme est disponible, cite-le brièvement (ex : INRS, DARES, Bpifrance, France Num, CCI, Xerfi, Wavestone, Google Maps).
""".strip()

SYSTEM_PROMPT = (
    "Tu es un consultant senior MB Consulting spécialisé en stratégie commerciale, marketing, "
    "études de marché et lancement d'activité pour entrepreneurs. Ton style est clair, concret, "
    "pédagogique, actionnable, sans jargon inutile. Tu écris pour un porteur de projet non spécialiste.\n\n"
    + UNIVERSAL_WRITING_RULES
)

SECTION_COMMON_TEMPLATE = """
CONTEXTE PROJET :
{project_json}

PROFIL DÉDUIT PAR LE CODE :
{profile_json}

STATUT DES CHAMPS ET VALEURS DE REMPLACEMENT :
{input_meta_json}

SPÉCIFICITÉS GUIDÉES COCHÉES PAR LE CLIENT :
{specificity_labels}

RÈGLES DE STRUCTURE :
- Respecte exactement les blocs demandés dans cette section.
- Le but n'est pas de remplir : chaque bloc doit apporter un angle différent.
- Ne répète pas longuement les constats déjà traités dans les autres sections.
- Si tu rappelles une idée déjà établie, fais-le en une phrase maximum.
""".strip()

# ----------------------------------------------------------------------------
# 4. API GEMINI + SÉCURITÉ
# ----------------------------------------------------------------------------

def url_pour_modele(nom_modele: str) -> str:
    return f"https://generativelanguage.googleapis.com/v1beta/models/{nom_modele}:generateContent"


def sanitize(texte: Any) -> str:
    if texte is None:
        return ""
    return str(texte).replace(API_KEY or "", "[CLE_API_MASQUEE]") if API_KEY else str(texte)


def appel_gemini_brut(payload: Dict[str, Any], nom_modele: str) -> Dict[str, Any]:
    global API_KEY
    API_KEY = API_KEY or os.environ.get("GEMINI_API_KEY")
    if not API_KEY:
        raise RuntimeError("GEMINI_API_KEY absent : génération impossible.")
    headers = {"Content-Type": "application/json", "x-goog-api-key": API_KEY}
    response = requests.post(url_pour_modele(nom_modele), json=payload, headers=headers, timeout=160)
    response.raise_for_status()
    return response.json()


def choisir_modele_recherche() -> str:
    global _modele_recherche_valide
    if _modele_recherche_valide:
        return _modele_recherche_valide
    for candidat in MODELES_RECHERCHE_CANDIDATS:
        try:
            appel_gemini_brut({"contents": [{"parts": [{"text": "Dis juste ok."}]}]}, candidat)
            _modele_recherche_valide = candidat
            print(f"    (modèle de recherche retenu : {candidat})")
            return candidat
        except requests.exceptions.RequestException:
            print(f"    ('{candidat}' indisponible, essai suivant...)")
    _modele_recherche_valide = MODEL_REDACTION
    return MODEL_REDACTION


COMPTEUR = {"tokens_in": 0, "tokens_out": 0, "requetes_recherche": 0}


def _model_candidates(use_search: bool) -> List[str]:
    if use_search:
        return [choisir_modele_recherche()]
    # Déduplique en conservant l'ordre.
    return list(dict.fromkeys(MODELES_REDACTION_CANDIDATS or [MODEL_REDACTION]))


def call_gemini(prompt_text: str, use_search: bool = False, max_retries: int = MAX_RETRIES) -> Tuple[str, int, Optional[str]]:
    payload: Dict[str, Any] = {
        "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": [{"parts": [{"text": prompt_text}]}],
    }
    if use_search:
        payload["tools"] = [{"google_search": {}}]

    erreurs_modeles: List[str] = []
    for nom_modele in _model_candidates(use_search):
        derniere_erreur: Optional[str] = None
        for tentative in range(1, max_retries + 1):
            try:
                data = appel_gemini_brut(payload, nom_modele)
                candidate = data["candidates"][0]
                texte = candidate["content"]["parts"][0]["text"]
                grounding = candidate.get("groundingMetadata", {})
                nb_sources = len(grounding.get("groundingChunks", []))
                nb_requetes = len(grounding.get("webSearchQueries", [])) or (1 if use_search else 0)
                usage = data.get("usageMetadata", {})
                COMPTEUR["tokens_in"] += usage.get("promptTokenCount", 0)
                COMPTEUR["tokens_out"] += usage.get("candidatesTokenCount", 0)
                COMPTEUR["requetes_recherche"] += nb_requetes
                return texte.strip(), nb_sources, None
            except requests.exceptions.HTTPError as e:
                code = e.response.status_code if e.response is not None else None
                derniere_erreur = sanitize(f"{nom_modele} : Erreur HTTP {code}")
                if code == 429:
                    attente = min(180, 20 * tentative * tentative)
                    print(f"    ({nom_modele} quota atteint, pause de {attente}s avant retry {tentative}/{max_retries})")
                    time.sleep(attente)
                    continue
                if code and 500 <= code < 600:
                    time.sleep(8 * tentative)
                    continue
                break
            except (KeyError, IndexError):
                derniere_erreur = f"{nom_modele} : Réponse inattendue de l'API."
                break
            except requests.exceptions.RequestException as e:
                derniere_erreur = sanitize(f"{nom_modele} : Erreur réseau : {type(e).__name__}")
                time.sleep(8 * tentative)
                continue
            except Exception as e:
                derniere_erreur = sanitize(f"{nom_modele} : Erreur inattendue : {type(e).__name__}")
                break
        erreurs_modeles.append(derniere_erreur or f"{nom_modele} : échec inconnu")
        if not use_search and nom_modele != _model_candidates(use_search)[-1]:
            print(f"    ({nom_modele} indisponible ou en échec, essai modèle suivant...)")

    return "", 0, " | ".join(erreurs_modeles) or "Échec inconnu"

# ----------------------------------------------------------------------------
# 5. RECHERCHES GROUPÉES — adaptées par profil, pas par section
# ----------------------------------------------------------------------------

def join_or_none(items: Iterable[str]) -> str:
    text = ", ".join([str(x) for x in items if str(x).strip()])
    return text or "aucune spécificité cochée"


def build_search_prompts(project: Dict[str, Any], profile: Dict[str, Any]) -> Dict[str, str]:
    """Recherches V4.1 : moins de paquets, plus ciblés.

    Objectif : éviter les 20+ requêtes de la V4.
    - light : 2 recherches groupées
    - standard : 3 recherches groupées
    - premium : 4 recherches groupées

    Attention : Gemini peut déclencher plusieurs requêtes web internes par appel.
    On réduit donc surtout le nombre d'appels de recherche et on demande des recherches
    plus concentrées.
    """
    base = (
        f"Projet : {project['concept']}\n"
        f"Type : {project['project_type']}\n"
        f"Zone : {project['zone']}\n"
        f"Cible : {project['target_customer']}\n"
        f"Offre : {project['main_offer']}\n"
        f"Budget : {project.get('budget_display', project.get('budget_eur'))}\n"
        f"Spécificités : {join_or_none(profile['specificity_labels'])}\n"
    )
    demande_focus = []
    if profile["is_local"]:
        demande_focus.append("demande locale, zone de chalandise, flux, population, projets urbains, sécurité perçue")
    if profile["is_digital"]:
        demande_focus.append("demande en ligne, requêtes qualitatives, Google Business Profile, réseaux sociaux")
    if profile["has_stock"]:
        demande_focus.append("prix observables, logistique, fournisseurs, coûts matières si disponibles")
    if profile["regulated"]:
        demande_focus.append("réglementation, normes, autorisations, hygiène ou sécurité")

    prompts: Dict[str, str] = {
        "marche_demande": (
            base
            + "\nFais une recherche web concentrée, avec 2 à 4 requêtes maximum, pour évaluer la demande et les signaux de marché. "
            + "Axes prioritaires : " + join_or_none(demande_focus) + ". "
            + "Retour attendu : 6 à 10 signaux utiles maximum, chacun avec un label de source qualifié (source institutionnelle, étude sectorielle, source professionnelle, avis publics, Google/Maps/annuaires, analyse concurrentielle) ou statut 'à vérifier terrain'. Nomme l'organisme si disponible. "
            + "Ne donne aucun chiffre précis sans source claire. Ne généralise pas une donnée nationale comme preuve locale."
        ),
        "concurrence": (
            base
            + "\nFais une recherche web concentrée, avec 3 à 5 requêtes maximum, pour identifier des concurrents nommés et exploitables. "
            + "Pour un projet local, privilégie Google/Maps/pages locales autour de la zone ; pour un projet digital, Google, marketplaces et acteurs spécialisés. "
            + "Retour attendu : 5 à 8 concurrents nommés si possible, avec nom, localisation/canal, offre, positionnement, prix si trouvé, signaux d'avis clients et source qualifiée (site concurrent, avis publics, Google/Maps/annuaires, source professionnelle). "
            + "Si les avis clients sont peu nombreux, indique 'signal faible'. N'invente pas de notes, nombres d'avis, verbatims ou prix. "
            + "N'écris jamais que les concurrents n'ont pas été fournis par le client : ton rôle est de les rechercher. Si aucun concurrent fiable n'est trouvé, dis-le clairement."
        ),
    }

    if RESEARCH_MODE in {"standard", "premium"}:
        prompts["prix_contraintes"] = (
            base
            + "\nFais une recherche web courte, avec 1 à 3 requêtes maximum, sur les repères utiles de prix, coûts, réglementation ou contraintes pratiques. "
            + "Retour attendu : uniquement les éléments qui changent les hypothèses, les risques ou le plan d'action, avec source-label qualifié ou statut de preuve. Évite la formule vague 'source web trouvée' seule. "
            + "Tout élément incertain doit être marqué 'à vérifier'."
        )
    if RESEARCH_MODE == "premium":
        prompts["marketing_digital"] = (
            base
            + "\nRecherche rapidement les signaux marketing utiles : présence digitale des concurrents, contenus, canaux, intentions qualitatives. "
            + "Ne donne pas de volumes SEO non sourcés."
        )
    return prompts


def is_stale_research(key: str, stored: Dict[str, Any]) -> bool:
    if FORCE_REFRESH_RESEARCH:
        return True
    text = (stored or {}).get("texte") or ""
    if key == "concurrence":
        low = text.lower()
        return any(marker.lower() in low for marker in STALE_RESEARCH_MARKERS)
    return False

# ----------------------------------------------------------------------------
# 6. SECTIONS — 15 sections, blocs imposés, pas de nombre de mots
# ----------------------------------------------------------------------------

ResearchKey = Optional[Union[str, Tuple[str, ...]]]

@dataclass(frozen=True)
class Section:
    id: str
    title: str
    research: ResearchKey
    generate_order: int
    instruction: str


SECTIONS: List[Section] = [
    Section(
        "s01_synthese_decisionnelle",
        "Synthèse décisionnelle",
        ("marche_demande", "concurrence", "prix_contraintes"),
        15,
        """
        Génère une synthèse courte à afficher au début, mais écrite en dernier.
        Blocs obligatoires :
        1) ## Verdict en bref : 5 à 7 lignes maximum, avec Go / Go sous conditions / Pivot / No-Go.
        2) Tableau obligatoire : Critère | Lecture | Impact. 5 lignes maximum : marché, concurrence, modèle économique, risques, priorité.
        3) ## 3 raisons d'y croire : 3 puces courtes.
        4) ## 3 risques à traiter : 3 puces courtes.
        5) ## Décision recommandée : encadré textuel court, pas de tableau.
        Interdiction : aucun nouveau chiffre non déjà présent dans les sections précédentes.
        """,
    ),
    Section(
        "s02_concept_perimetre",
        "Concept, périmètre et hypothèses de départ",
        None,
        1,
        """
        Blocs obligatoires :
        1) ## Concept analysé : paragraphe de 8 lignes maximum.
        2) Tableau obligatoire STRICTEMENT À 3 COLONNES : Élément | Hypothèse retenue | Impact sur l'analyse. Lignes : concept, zone, cible, offre, budget, modèle de revenus, spécificités cochées. Ne crée jamais de 4e ou 5e colonne.
        3) ## Ce que l'étude permet : 4 puces.
        4) ## Ce que l'étude ne prouve pas encore : 4 puces.
        5) ## Conclusion de cadrage : 5 lignes maximum.
        """,
    ),
    Section(
        "s03_hypotheses_chiffrees",
        "Dictionnaire unique des hypothèses chiffrées",
        None,
        2,
        """
        Cette section verrouille les chiffres. Les autres sections financières devront les reprendre sans les régénérer.
        Adapte les variables selon les spécificités cochées, mais reste compact.
        Blocs obligatoires :
        1) ## Rôle du dictionnaire : 5 lignes maximum.
        2) Tableau obligatoire : Variable | Valeur retenue | Statut | Utilisation. 8 lignes maximum. Inclure budget, investissement initial, trésorerie, charges fixes, panier moyen ou prix moyen, volume d'activité.
        3) Si stock/matières cochés : ajouter 3 lignes maximum sur coût matière, marge brute et rotation de stock. Sinon ne pas inventer ces lignes.
        4) Si service humain coché : ajouter 3 lignes maximum sur temps vendu, capacité mensuelle et prix prestation. Sinon ne pas inventer ces lignes.
        5) ## Points à vérifier avant de figer les chiffres : 5 puces maximum.
        Chaque valeur non sourcée doit être explicitement marquée "hypothèse de travail à vérifier".
        """,
    ),
    Section(
        "s04_demande_acces_marche",
        "Demande, accès au marché et zone utile",
        "marche_demande",
        3,
        """
        Adapte cette section au profil : local, digital ou hybride. C'est une des seules sections à variante forte.
        Blocs obligatoires :
        1) ## Lecture du marché accessible : paragraphe de 8 lignes maximum.
        2) Tableau obligatoire : Signal observé | Intérêt pour le projet | Niveau de fiabilité / source. 6 lignes maximum.
        3) ## Densité concurrentielle apparente : 4 à 6 lignes maximum. Formule-la comme un signal indicatif, jamais comme un recensement exhaustif. Si peu de concurrents directs sont trouvés, explique que cela peut signaler un marché émergent ou mal structuré, mais ne prouve pas la demande.
        4) Si profil local : sous-bloc ## Lecture locale avec flux, emplacement, zone de chalandise, sécurité/accessibilité.
        5) Si profil digital : sous-bloc ## Lecture digitale avec canaux, demande en ligne, intentions de recherche qualitatives, dépendance plateformes.
        6) ## Implications concrètes : 5 puces maximum.
        7) ## Données à vérifier terrain : 4 puces maximum.
        Contexte recherche : {contexte}
        """,
    ),
    Section(
        "s05_tendances_utiles",
        "Tendances marché utiles au projet",
        "marche_demande",
        4,
        """
        Ne fais pas une dissertation macro. Ne garde que les tendances qui changent une décision.
        Blocs obligatoires :
        1) ## Pourquoi ces tendances comptent : 5 lignes maximum.
        2) 3 sous-blocs texte courts : tendance principale 1, tendance principale 2, tendance principale 3.
        3) ## Repères chiffrés à rechercher / à valider : 2 à 4 lignes maximum, uniquement avec des chiffres présents dans le contexte de recherche. Si aucun chiffre fiable n'est disponible, écris clairement qu'aucun repère chiffré suffisamment fiable n'a été retenu automatiquement.
        4) Tableau obligatoire : Tendance | Ce que ça permet | Ce que ça ne prouve pas. 5 lignes maximum.
        5) ## Prudence sur les chiffres : 4 lignes maximum, avec cette idée : ces chiffres sont des repères macro, pas une preuve directe du potentiel commercial du projet, et doivent être vérifiés sur le segment ciblé.
        Toute statistique non sourcée dans le contexte doit être labellisée hypothèse à vérifier. Évite les formulations vagues sans donnée quand le contexte fournit un chiffre exploitable.
        Contexte recherche : {contexte}
        """,
    ),
    Section(
        "s06_concurrence_synthese",
        "Concurrence, benchmark et synthèse stratégique intégrée",
        "concurrence",
        5,
        """
        Fusionne concurrence, benchmark, avis clients prudents, mini-SWOT. Pas de PESTEL complet.
        Blocs obligatoires :
        1) ## Paysage concurrentiel : 6 à 8 lignes, avec lecture directe/indirecte et densité apparente. Ne présente jamais un comptage comme exhaustif. Si peu de concurrents directs fiables existent, ne force pas un faux benchmark : analyse les alternatives, substituts, solutions bricolées et arbitrages budgétaires.
        2) Tableau obligatoire : Acteur ou alternative | Type / canal | Positionnement ou usage actuel | Ce que le projet peut apprendre. 8 lignes max. Priorité aux concurrents nommés. Si aucun acteur nommé fiable n'est disponible, utilise des alternatives clairement marquées "solution de substitution à vérifier".
        3) ## Signaux issus d'avis clients publics : court paragraphe de prudence + tableau obligatoire : Signal observé | Niveau de preuve | Opportunité de différenciation. 4 lignes max. Formulations prudentes uniquement : "certains avis consultables mentionnent", "signal faible", "signal apparent". N'invente aucun avis.
        4) ## Lecture par pilier : 3 paragraphes courts maximum : offre principale, alternative client, angle hybride/différenciant.
        5) Tableau obligatoire mini-SWOT : Forces | Faiblesses | Opportunités | Menaces. 3 bullets max par cellule.
        6) ## Opportunités de différenciation prioritaires : 3 à 5 puces, reliées aux signaux concurrents/avis/prix.
        Contexte recherche : {contexte}
        """,
    ),
    Section(
        "s07_personas",
        "Personas clients et situations d'achat",
        None,
        6,
        """
        Les personas doivent être actionnables, pas décoratifs.
        Blocs obligatoires :
        1) ## À quoi servent ces personas : 5 lignes maximum.
        2) Tableau obligatoire : Persona | Besoin | Frein | Offre adaptée. 5 personas maximum.
        3) ## Situations d'achat typiques : 5 à 7 puces concrètes.
        4) Tableau optionnel si utile : Persona | Message à tester | Canal prioritaire. 5 lignes max.
        5) ## Implication commerciale : 6 lignes maximum.
        """,
    ),
    Section(
        "s08_positionnement",
        "Positionnement recommandé et différenciation",
        "concurrence",
        7,
        """
        Cette section doit transformer la concurrence et les irritants clients en positionnement. Ne te contente pas de dire "premium" ou "différent".
        Blocs obligatoires :
        1) ## Positionnement en une phrase : une phrase forte et claire.
        2) ## Justification : 8 lignes maximum, en mentionnant les signaux concurrents/avis seulement s'ils sont disponibles.
        3) Tableau obligatoire : Axe de différenciation | Preuve attendue | Action concrète. 6 lignes maximum.
        4) ## Points de preuve à rendre visibles : 4 puces maximum (ex : composition, délai, conseil, packaging, garantie, expertise).
        5) ## Ce que le projet ne doit pas devenir : 5 puces maximum.
        6) ## Promesse commerciale à tester : 4 lignes maximum.
        Contexte recherche : {contexte}
        """,
    ),
    Section(
        "s09_offre_prix_revenus",
        "Offre, prix, packs et sources de revenus",
        "concurrence",
        8,
        """
        L'offre doit exploiter les opportunités de différenciation observées dans la concurrence et les avis.
        Blocs obligatoires :
        1) ## Logique d'offre : 6 lignes maximum.
        2) Tableau obligatoire : Offre | Client cible | Prix indicatif | Rôle business. 8 lignes maximum. Inclure si pertinent une offre découverte, une offre standard, une offre premium, un pack/coffret ou une option récurrente.
        3) ## Logique de prix : 8 lignes maximum. Tous les prix non sourcés doivent être hypothèses à vérifier. Distingue prix unitaire, panier moyen et offre pack si nécessaire.
        4) Tableau obligatoire : Source de revenus | Potentiel | Point de vigilance. 6 lignes maximum.
        5) ## Ajustements issus de la concurrence : 4 puces maximum, reliées aux irritants/prix/attentes repérés.
        6) ## À vérifier terrain : 5 puces.
        Contexte recherche : {contexte}
        """,
    ),
    Section(
        "s10_operations_contraintes",
        "Canaux, emplacement et contraintes opérationnelles",
        "marche_demande",
        9,
        """
        Adapte uniquement cette section selon les spécificités : local, digital, stock, service, réglementation.
        Blocs obligatoires :
        1) ## Pourquoi l'opérationnel est critique : 6 à 8 lignes.
        2) Tableau obligatoire : Critère | Niveau d'importance | Risque si absent. 8 lignes max.
        3) Si local_erp : inclure visibilité, flux, surface, accessibilité, normes d'accueil du public.
        4) Si digital_acquisition : inclure site, landing page, paiement, tracking, conversion, dépendance plateformes.
        5) Si stock_matiere : inclure approvisionnement, stockage, pertes, rotation, fournisseurs.
        6) Si reglementation : inclure autorisations/normes à vérifier sans donner de conseil juridique définitif.
        7) Si reglementation : ajoute un court avertissement indiquant que les éléments réglementaires ne remplacent pas une validation juridique, administrative ou technique spécialisée.
        8) ## Checklist opérationnelle : 6 puces maximum.
        9) ## Erreur fatale à éviter : 5 lignes maximum.
        Contexte recherche : {contexte}
        """,
    ),
    Section(
        "s11_modele_economique",
        "Modèle économique et structure de coûts",
        None,
        10,
        """
        Reprends les hypothèses de la section 3 sans inventer de nouveaux montants.
        Blocs obligatoires :
        1) ## Comment le projet gagne de l'argent : 8 lignes maximum.
        2) Tableau obligatoire : Activité | Revenu | Coûts principaux | Levier de marge. 6 lignes maximum.
        3) Tableau obligatoire : Type de coût | Exemples | Impact. 6 lignes maximum.
        4) ## Synergies économiques : 8 lignes maximum.
        5) ## Fragilités du modèle : 4 à 6 puces.
        """,
    ),
    Section(
        "s12_scenarios_financiers",
        "Scénarios financiers, point mort et KPIs",
        None,
        11,
        """
        Section à forte variante selon le modèle financier, mais le format reste unique.
        Reprends les hypothèses de la section 3. Ne crée pas de nouveaux chiffres contradictoires. Si tu dois introduire un repère absent, marque-le clairement "nouvelle hypothèse à vérifier".
        Blocs obligatoires :
        1) ## Avertissement sur les scénarios : 5 lignes maximum.
        2) Tableau obligatoire STRICTEMENT À 4 COLONNES : Scénario | Hypothèses clés | Résultat estimé | Lecture / décision. 3 lignes : prudent, réaliste, ambitieux. Ne crée jamais de tableau à 5 colonnes. N'utilise jamais <br> dans une cellule : fais des phrases courtes.
        3) Tableau obligatoire : Variable | Valeur | Commentaire. 6 lignes max pour le point mort. Les valeurs doivent correspondre au dictionnaire section 3 ou être explicitement qualifiées.
        4) Tableau obligatoire : KPI | Seuil cible | Alerte si. 8 lignes maximum.
        5) Si digital_acquisition : inclure CAC, taux de conversion et panier moyen en cohérence avec section 3.
        6) Si stock_matiere : inclure marge brute, rotation de stock et stock dormant.
        7) Si service_humain : inclure taux d'occupation, temps vendu et capacité mensuelle.
        8) ## Lecture finale : 6 lignes maximum. Si le point mort comptable est très bas (ex : moins d'une vente/prestation par mois), précise que cela ne sécurise pas l'activité : le vrai risque reste l'acquisition régulière de clients, la conversion et la capacité de livraison. Si le scénario prudent reste positif malgré un faible volume, explique que cela vient du prix moyen ou des coûts variables contenus, mais que la marge de sécurité reste fragile face aux retards de paiement, dépassements de production, coûts commerciaux et délais de conversion.
        """,
    ),
    Section(
        "s13_validation_mvp",
        "Validation terrain / MVP avant investissement complet",
        None,
        12,
        """
        MVP doit être défini immédiatement à sa première apparition.
        Les tests doivent intégrer les irritants concurrents/avis quand ils existent : prix, délai, choix, packaging, intensité, qualité, rassurance, preuve, expérience d'achat. Pour un projet innovant ou peu comparable, teste surtout : compréhension de l'offre, intensité du problème, disposition à payer, alternatives actuelles et preuve attendue.
        Blocs obligatoires :
        1) ## Pourquoi tester avant d'investir : 8 lignes maximum.
        2) Tableau obligatoire : Test | Durée | Signal attendu | Décision associée. 7 lignes maximum.
        3) ## Tests de différenciation à intégrer : 4 puces maximum, reliées à la concurrence ou aux avis clients publics si disponibles.
        4) ## Méthode de collecte : 6 lignes maximum.
        5) ## Seuils de validation : 5 puces maximum. Les seuils non sourcés sont hypothèses de travail.
        6) ## Avant de signer ou d'investir : 5 lignes maximum.
        """,
    ),
    Section(
        "s14_marketing_90j",
        "Plan marketing de lancement et calendrier 90 jours",
        ("concurrence", "prix_contraintes"),
        13,
        """
        Le calendrier ne doit pas devenir une section géante.
        Blocs obligatoires :
        1) ## Stratégie de lancement : 8 lignes maximum.
        2) Tableau obligatoire : Cible | Message | Canal. 5 lignes maximum.
        3) Tableau obligatoire : Période | Objectif | Actions clés | Indicateur. 5 lignes : J-60 à J-30, J-30 à J-7, ouverture, J+7 à J+30, J+30 à J+90.
        4) ## Contenus prioritaires : 8 puces maximum.
        5) ## Priorité marketing : 4 lignes maximum.
        Contexte recherche : {contexte}
        """,
    ),
    Section(
        "s15_verdict_plan_action",
        "Verdict d'opportunité, risques et plan d'action final",
        ("marche_demande", "concurrence", "prix_contraintes"),
        14,
        """
        Génère cette section après les sections 2 à 14. Elle doit conclure, pas réouvrir l'analyse.
        Le verdict doit faire remonter les conditions critiques : différenciation réelle, concurrence ou alternatives, CAC/prix/marge, conformité/réglementation si applicable, validation terrain/MVP. Si peu de concurrents directs ont été identifiés, rappelle que cela augmente l'incertitude et renforce la nécessité de valider la demande.
        Blocs obligatoires :
        1) ## Verdict final : 6 à 8 lignes avec Go / Go sous conditions / Pivot / No-Go.
        2) Tableau obligatoire : Risque | Gravité | Prévention. 5 risques maximum.
        3) Tableau obligatoire : Condition | Seuil minimal | Décision si non atteint. 6 lignes maximum.
        4) ## Conditions de différenciation à prouver : 3 puces maximum, issues du benchmark/avis/MVP.
        5) ## Plan d'action 30 jours : 5 puces.
        6) ## Plan d'action 90 jours : 5 puces.
        7) ## Ce qui reste à vérifier : 5 puces maximum.
        Aucun nouveau chiffre non présent dans le dictionnaire ou les sections précédentes.
        Contexte recherche : {contexte}
        """,
    ),
]

DISPLAY_ORDER = [
    "s01_synthese_decisionnelle",
    "s02_concept_perimetre",
    "s03_hypotheses_chiffrees",
    "s04_demande_acces_marche",
    "s05_tendances_utiles",
    "s06_concurrence_synthese",
    "s07_personas",
    "s08_positionnement",
    "s09_offre_prix_revenus",
    "s10_operations_contraintes",
    "s11_modele_economique",
    "s12_scenarios_financiers",
    "s13_validation_mvp",
    "s14_marketing_90j",
    "s15_verdict_plan_action",
]

# ----------------------------------------------------------------------------
# 7. CONTRÔLE QUALITÉ JSON — avant PDF
# ----------------------------------------------------------------------------

def clean_ai_text(text: str) -> str:
    """Nettoie les artefacts IA/HTML avant QA et avant sauvegarde.

    Un <br> ne doit jamais bloquer un PDF : c'est un artefact de formatage
    réparable. On le convertit en retour ligne avant tout contrôle qualité.
    """
    if not text:
        return ""
    text = str(text)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"&lt;br\s*/?&gt;", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"\u003cbr\s*/?\u003e", "\n", text, flags=re.IGNORECASE)
    # Supprime les séparateurs Markdown horizontaux qui polluent parfois le PDF.
    text = re.sub(r"(?m)^\s*-{3,}\s*$", "", text)
    # Nettoie les triples backticks isolés sans supprimer le contenu entre les lignes.
    text = text.replace("```", "")
    # Compacte les excès de lignes vides.
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()

FORBIDDEN_PATTERNS = [
    # Placeholders client-visibles très probables.
    r"\[[A-Za-zÀ-ÿ ]{1,30}\]",
    r"\bX\s*(prises de contact|RDV|rendez-vous|contrats|nouveaux leads|leads|clients|ventes|euros)\b",
    r"\bX\s*(milliers|millions|milliards)\b",
    r"\bY\s*(milliers|millions|milliards)\b",
    r"\bZ\s*(milliers|millions|milliards)\b",
    r"à compléter",
    r"TODO",
    r"Cette section n['’]a pas pu être générée",
    # Fuites de prompt / consignes internes.
    r"Color know the following code snippet",
    r"Code Snippet provides additional context",
    r"complete the analysis on existing input",
    r"user is asking to complete",
    r"existing input",
    r"continue from where",
    r"Do not regenerate",
]

AMBIGUOUS_PLACEHOLDER_PATTERNS = [
    # On ne bloque pas automatiquement X% / X€ : cela peut être une variable théorique.
    # On le signale seulement en warning pour relecture.
    r"\bX\s*(%|€)\b",
    r"\bobjectif\s*:?\s*X\b",
    r"\bbudget\s*:?\s*X\b",
    r"\batteindre\s+X\b",
    r"\bobtenir\s+X\b",
]


def validate_markdown_tables(text: str) -> List[str]:
    """Détecte les tableaux trop denses. En V4.1, ces erreurs sont réparables côté PDF."""
    issues: List[str] = []
    blocks = re.split(r"\n\s*\n", text or "")
    for block in blocks:
        if "|" not in block or "\n" not in block:
            continue
        lines = [l.strip() for l in block.splitlines() if l.strip()]
        data_lines = [l for l in lines if "|" in l and not re.match(r"^\|?[\s:|-]+\|?$", l)]
        if not data_lines:
            continue
        headers = [c.strip() for c in data_lines[0].strip("|").split("|")]
        if len(headers) > 4:
            issues.append(f"Tableau à {len(headers)} colonnes (>4) - réparable PDF.")
        rows = data_lines[1:]
        if len(rows) > 10:
            issues.append(f"Tableau à {len(rows)} lignes (>10) - réparable PDF.")
        for row in rows:
            cells = [c.strip() for c in row.strip("|").split("|")]
            for cell in cells:
                if len(cell.split()) > 35:
                    issues.append("Cellule de tableau >35 mots - réparable PDF.")
                    return issues
    return issues


def classify_quality_issues(section_id: str, text: str) -> Tuple[List[str], List[str]]:
    """Retourne (blocking_issues, format_warnings).

    V4 rejetait toute erreur de tableau. V4.1 ne bloque que les vrais problèmes
    de contenu ; les problèmes de tableau sont laissés au moteur PDF qui sait les
    convertir en fiches.
    """
    blocking: List[str] = []
    warnings: List[str] = []
    raw_text = text or ""
    if re.search(r"<br\s*/?>|&lt;br\s*/?&gt;|\\u003cbr", raw_text, flags=re.IGNORECASE):
        warnings.append("Balise HTML <br> détectée - réparée automatiquement avant PDF.")
    if re.search(r"(?m)^\s*-{3,}\s*$", raw_text):
        warnings.append("Séparateur Markdown --- détecté - réparé automatiquement avant PDF.")
    text = clean_ai_text(raw_text)
    if not text or len(text.strip()) < 900:
        if section_id not in {"s01_synthese_decisionnelle", "s15_verdict_plan_action"}:
            blocking.append("Section trop courte ou vide.")
    for pattern in FORBIDDEN_PATTERNS:
        if re.search(pattern, text or "", flags=re.IGNORECASE):
            blocking.append(f"Placeholder/interdit détecté : {pattern}")
    for pattern in AMBIGUOUS_PLACEHOLDER_PATTERNS:
        if re.search(pattern, text or "", flags=re.IGNORECASE):
            warnings.append(f"Placeholder ambigu à relire : {pattern}")
            break
    # Détection prudente des troncatures : on évite les faux positifs comme "à vérifier" ou "à valider".
    # On signale surtout les fins visiblement coupées par une ellipse après un connecteur.
    if re.search(r"(hypothèse de|source|notamment|avec|pour|afin de|en cas de)\s*…\s*$", text.strip(), flags=re.IGNORECASE):
        blocking.append("Texte probablement tronqué en fin de section.")
    if "```" in text:
        warnings.append("Bloc code Markdown détecté - réparable PDF.")
    warnings.extend(validate_markdown_tables(text))
    return blocking, warnings


def validate_section_text(section_id: str, text: str) -> List[str]:
    blocking, warnings = classify_quality_issues(section_id, text)
    return blocking + warnings


def quality_report(resultats: Dict[str, Any]) -> Dict[str, Any]:
    report: Dict[str, Any] = {"sections": {}, "summary": {}}
    blocking_total = 0
    warning_total = 0
    missing = []
    for section_id in DISPLAY_ORDER:
        item = resultats.get(section_id)
        if not item or not item.get("texte"):
            missing.append(section_id)
            report["sections"][section_id] = {"blocking": ["Section manquante."], "warnings": []}
            blocking_total += 1
            continue
        # Une erreur API réelle bloque. Une ancienne erreur 'Contrôle qualité' ne bloque pas si le texte existe.
        err = item.get("erreur")
        if err and not str(err).startswith("Contrôle qualité"):
            missing.append(section_id)
            report["sections"][section_id] = {"blocking": [f"Erreur API/génération : {err}"], "warnings": []}
            blocking_total += 1
            continue
        blocking, warnings = classify_quality_issues(section_id, item.get("texte", ""))
        blocking_total += len(blocking)
        warning_total += len(warnings)
        report["sections"][section_id] = {"blocking": blocking, "warnings": warnings}
    report["summary"] = {
        "sections_attendues": len(DISPLAY_ORDER),
        "sections_manquantes_ou_bloquantes": len(missing),
        "blocking_issues_total": blocking_total,
        "format_warnings_total": warning_total,
        "ready_for_pdf": len(missing) == 0 and blocking_total == 0,
    }
    return report

# ----------------------------------------------------------------------------
# 8. EXÉCUTION
# ----------------------------------------------------------------------------

def charger_resultats_existants() -> Dict[str, Any]:
    if os.path.exists(CONTENT_FILE):
        with open(CONTENT_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def sauvegarder(resultats: Dict[str, Any]) -> None:
    with open(CONTENT_FILE, "w", encoding="utf-8") as f:
        json.dump(resultats, f, ensure_ascii=False, indent=2)


def initialize_project_context(raw_project: Optional[Dict[str, Any]] = None) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    """Normalise et dérive le profil au moment de l'appel, jamais au chargement du module.

    Cela évite qu'un ancien PROJECT_INPUT codé en dur reste en mémoire si FastAPI
    injecte les données après import dynamique.
    """
    global PROJECT_INPUT_RAW, PROJECT_INPUT, PROJECT_INPUT_META, PROJECT_PROFILE
    PROJECT_INPUT_RAW = dict(raw_project if raw_project is not None else PROJECT_INPUT)
    PROJECT_INPUT, PROJECT_INPUT_META = normalize_project_input(PROJECT_INPUT_RAW)
    PROJECT_PROFILE = derive_project_profile(PROJECT_INPUT)
    PROJECT_PROFILE["input_meta"] = PROJECT_INPUT_META
    return PROJECT_INPUT, PROJECT_INPUT_META, PROJECT_PROFILE


def prompt_base() -> str:
    return SECTION_COMMON_TEMPLATE.format(
        project_json=json.dumps(PROJECT_INPUT, ensure_ascii=False, indent=2),
        profile_json=json.dumps(PROJECT_PROFILE, ensure_ascii=False, indent=2),
        input_meta_json=json.dumps(PROJECT_INPUT_META, ensure_ascii=False, indent=2),
        specificity_labels="; ".join(PROJECT_PROFILE.get("specificity_labels", [])) or "aucune",
    )


def summarize_previous_sections(resultats: Dict[str, Any]) -> str:
    """Résumé léger injecté dans verdict/synthèse pour éviter contradictions sans exploser le coût."""
    pieces: List[str] = []
    for sid in DISPLAY_ORDER:
        if sid in {"s01_synthese_decisionnelle", "s15_verdict_plan_action"}:
            continue
        item = resultats.get(sid)
        if not item or not item.get("texte"):
            continue
        text = re.sub(r"\s+", " ", item["texte"]).strip()
        pieces.append(f"[{item.get('titre', sid)}] {text[:1200]}")
    return "\n\n".join(pieces)


def build_section_prompt(section: Section, contextes: Dict[str, str], resultats: Dict[str, Any]) -> str:
    contexte = ""
    if section.research:
        keys = section.research if isinstance(section.research, tuple) else (section.research,)
        contexte = "\n\n---\n\n".join(contextes.get(k, "[aucune donnée trouvée]") for k in keys)

    previous_summary = ""
    if section.id in {"s01_synthese_decisionnelle", "s15_verdict_plan_action"}:
        previous_summary = "\n\nRÉSUMÉ DES SECTIONS DÉJÀ GÉNÉRÉES À RESPECTER :\n" + summarize_previous_sections(resultats)

    priority_context = ""
    # Cohérence financière : section 12 doit reprendre le dictionnaire, pas inventer.
    if section.id in {"s11_modele_economique", "s12_scenarios_financiers", "s15_verdict_plan_action"} and resultats.get("s03_hypotheses_chiffrees", {}).get("texte"):
        priority_context += "\n\nDICTIONNAIRE FINANCIER À RESPECTER STRICTEMENT :\n" + resultats["s03_hypotheses_chiffrees"]["texte"][:3500]
    # Positionnement/offre/MVP/verdict doivent réutiliser la concurrence et les avis.
    if section.id in {"s08_positionnement", "s09_offre_prix_revenus", "s13_validation_mvp", "s15_verdict_plan_action"} and resultats.get("s06_concurrence_synthese", {}).get("texte"):
        priority_context += "\n\nENSEIGNEMENTS CONCURRENCE / AVIS À RÉUTILISER SANS INVENTER :\n" + resultats["s06_concurrence_synthese"]["texte"][:3500]

    instruction = section.instruction.replace("{contexte}", contexte)
    return (
        prompt_base()
        + "\n\nSECTION À GÉNÉRER :\n"
        + f"Titre : {section.title}\n"
        + instruction.strip()
        + priority_context
        + previous_summary
    )

def _print_quality_snapshot(resultats: Dict[str, Any], exception: Optional[BaseException] = None) -> None:
    """Toujours écrire un résumé qualité exploitable dans les logs."""
    try:
        qr = quality_report(resultats or {})
        print("\n--- Contrôle qualité interne ---")
        if exception is not None:
            print(f"Exception pendant génération : {type(exception).__name__}: {sanitize(exception)}")
        print(json.dumps(qr["summary"], ensure_ascii=False, indent=2))
        for sid, payload in qr.get("sections", {}).items():
            blocking = payload.get("blocking", []) if isinstance(payload, dict) else []
            if blocking:
                print(f"  - {sid} : {blocking[:3]}")
    except Exception as log_error:
        print("\n--- Contrôle qualité interne ---")
        print(f"Impossible de produire le résumé qualité : {type(log_error).__name__}: {sanitize(log_error)}")


def main(raw_project: Optional[Dict[str, Any]] = None, output_file: Optional[str] = None) -> Dict[str, Any]:
    """Génère le contenu JSON du rapport.

    Contrat production :
      - raw_project vient du formulaire FastAPI ;
      - output_file permet à main.py d'utiliser un fichier unique par commande ;
      - le contexte projet est recalculé ici, jamais au chargement du module.
    """
    global CONTENT_FILE
    if output_file:
        CONTENT_FILE = output_file
    resultats: Dict[str, Any] = {}
    exception: Optional[BaseException] = None
    try:
        initialize_project_context(raw_project)
        resultats = charger_resultats_existants()
        if not resultats:
            resultats = {}
        resultats["_project_input"] = PROJECT_INPUT
        resultats["_project_input_meta"] = PROJECT_INPUT_META
        resultats["_project_profile"] = PROJECT_PROFILE
        resultats["_sections_display_order"] = DISPLAY_ORDER
        resultats["_section_titles"] = {s.id: s.title for s in SECTIONS}
        sauvegarder(resultats)

        print("Spécificités guidées retenues :")
        for spec in PROJECT_INPUT.get("specificities", []):
            if spec in SPECIFICITY_OPTIONS:
                print(f"  - {SPECIFICITY_OPTIONS[spec]['label']}")

        # 1) Recherches groupées.
        search_prompts = build_search_prompts(PROJECT_INPUT, PROJECT_PROFILE)
        contextes: Dict[str, str] = {}
        for key, template in search_prompts.items():
            stored = resultats.get(f"_recherche_{key}")
            if stored and stored.get("texte") and not stored.get("erreur") and not is_stale_research(key, stored):
                contextes[key] = stored["texte"]
                print(f"--> Recherche '{key}' déjà faite, réutilisée.")
                continue
            if stored and is_stale_research(key, stored):
                print(f"--> Recherche '{key}' jugée trop générique, relancée une seule fois.")
            if key == "avis_clients":
                template = template.replace("{contexte_concurrence}", contextes.get("concurrence", "[aucune concurrence identifiée]"))
            print(f"--> Recherche groupée : {key}")
            texte, nb_sources, erreur = call_gemini(template, use_search=True)
            texte = clean_ai_text(texte)
            contextes[key] = texte
            resultats[f"_recherche_{key}"] = {
                "titre": f"[Recherche interne : {key}]",
                "texte": texte,
                "nb_sources_web": nb_sources,
                "erreur": erreur,
            }
            sauvegarder(resultats)
            time.sleep(PAUSE_ENTRE_APPELS)

        # 2) Sections 2 à 14, puis 15, puis 1.
        generation_order = sorted(SECTIONS, key=lambda s: s.generate_order)
        for section in generation_order:
            stored = resultats.get(section.id)
            if stored and stored.get("texte") and not stored.get("erreur"):
                print(f"--> {section.title} déjà générée, on saute.")
                continue
            print(f"--> Génération : {section.title}")
            prompt = build_section_prompt(section, contextes, resultats)
            texte, nb_sources, erreur = call_gemini(prompt, use_search=False)
            texte = clean_ai_text(texte)
            blocking, warnings = classify_quality_issues(section.id, texte) if texte else (["Texte vide"], [])
            if blocking and not erreur:
                erreur = "Contrôle qualité bloquant : " + "; ".join(blocking[:4])
            resultats[section.id] = {
                "titre": section.title,
                "texte": texte,
                "erreur": erreur,
                "quality_issues": blocking + warnings,
                "blocking_issues": blocking,
                "format_warnings": warnings,
            }
            sauvegarder(resultats)
            time.sleep(PAUSE_ENTRE_APPELS)

        qr = quality_report(resultats)
        resultats["_quality_report"] = qr
        sauvegarder(resultats)

        if qr["summary"]["ready_for_pdf"]:
            print("✅ Contenu prêt pour génération PDF.")
        else:
            print("⚠️ Contenu généré avec points à vérifier : PDF brouillon autorisé.")
        warnings = qr["summary"].get("format_warnings_total", 0)
        if warnings:
            print(f"ℹ️ {warnings} avertissements de mise en forme seront réparés côté PDF (tableaux/fiches).")

        cout_tokens = (COMPTEUR["tokens_in"] / 1_000_000 * 0.30) + (COMPTEUR["tokens_out"] / 1_000_000 * 2.50)
        cout_recherche = COMPTEUR["requetes_recherche"] * 0.035
        cout_total_usd = cout_tokens + cout_recherche
        print("\n--- Estimation coût interne, à ne jamais mettre dans le PDF ---")
        print(f"Tokens entrée/sortie : {COMPTEUR['tokens_in']} / {COMPTEUR['tokens_out']}")
        print(f"Requêtes recherche web : {COMPTEUR['requetes_recherche']}")
        print(f"Coût estimé prudent : ~{cout_total_usd:.3f} $ US")
        return resultats
    except BaseException as exc:
        exception = exc
        # On sauvegarde au maximum ce qui est disponible pour que main.py puisse diagnostiquer.
        try:
            if resultats is None:
                resultats = {}
            resultats["_fatal_error"] = f"{type(exc).__name__}: {sanitize(exc)}"
            if PROJECT_INPUT:
                resultats["_project_input"] = PROJECT_INPUT
            if PROJECT_INPUT_META:
                resultats["_project_input_meta"] = PROJECT_INPUT_META
            if PROJECT_PROFILE:
                resultats["_project_profile"] = PROJECT_PROFILE
            sauvegarder(resultats)
        except Exception:
            pass
        raise
    finally:
        _print_quality_snapshot(resultats, exception=exception)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\nErreur inattendue : {sanitize(str(e))}")
        print("Relance le script : il reprendra section par section.")

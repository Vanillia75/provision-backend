"""
francetravail_offres.py — Client de l'API France Travail « Offres d'emploi v2 ».

═══════════════════════════════════════════════════════════════════════════════
 SÉCURITÉ : les identifiants (FT_CLIENT_ID / FT_CLIENT_SECRET) sont lus UNIQUEMENT
 depuis les variables d'environnement Railway. Ils ne sont JAMAIS écrits en dur,
 JAMAIS loggés, JAMAIS renvoyés au front. Le front ne parle qu'à NOTRE backend.

 ISOLATION : ce module ne touche pas au moteur 507h. Une offre ne « compte » jamais
 pour le renouvellement — c'est juste une piste de mission.

 FALLBACK : en cas d'échec FT (timeout, quota, 5xx), on LÈVE une exception. L'appelant
 renvoie une erreur propre. On ne fabrique JAMAIS de fausses offres.

 ⚠️ À VALIDER au 1er appel réel (portail partenaire France Travail) : le host/scope
 OAuth exact et la liste des codes ROME. Les valeurs ci-dessous suivent la doc
 publique mais doivent être confirmées contre l'app enregistrée.
═══════════════════════════════════════════════════════════════════════════════
"""
import os
import re
import time
import logging
import unicodedata

import requests

logger = logging.getLogger("francetravail")

# ─── Endpoints (à confirmer contre le portail partenaire) ───────────────────────
OAUTH_URL = "https://entreprise.francetravail.fr/connexion/oauth2/access_token"
OAUTH_REALM = "/partenaire"
OAUTH_SCOPE = "api_offresdemploiv2 o2dsoffre"
SEARCH_URL = "https://api.francetravail.io/partenaire/offresdemploi/v2/offres/search"
GEO_COMMUNES_URL = "https://geo.api.gouv.fr/communes"

HTTP_TIMEOUT = 8  # secondes — on préfère échouer proprement que faire poireauter l'UI

# ─── Codes ROME du spectacle (série L) → famille de métier ──────────────────────
# NB : mapping best-effort, À VALIDER contre le référentiel ROME. Sert (a) à cibler
# la recherche, (b) à déduire roleType. On EXCLUT L1401 (sportif professionnel).
ROME_ROLE = {
    "L1101": "artiste",     # Animation musicale et scénique
    "L1201": "artiste",     # Danse
    "L1202": "artiste",     # Musique et chant
    "L1203": "artiste",     # Art dramatique
    "L1204": "artiste",     # Arts du cirque et arts visuels
    "L1301": "technicien",  # Mise en scène de spectacles vivants
    "L1302": "admin",       # Production et administration spectacle/cinéma/audiovisuel
    "L1303": "admin",       # Promotion d'artistes et de spectacles
    "L1304": "technicien",  # Réalisation cinématographique et audiovisuelle
    "L1501": "technicien",  # Décor et accessoires spectacle
    "L1502": "technicien",  # Costume et habillage spectacle
    "L1503": "technicien",  # Coiffure et maquillage spectacle
    "L1504": "technicien",  # Éclairage, sonorisation et machinerie de spectacle
    "L1505": "technicien",  # Image cinématographique et télévisuelle
    "L1506": "technicien",  # Films d'animation et effets spéciaux
    "L1507": "technicien",  # Montage audiovisuel et post-production
    "L1508": "technicien",  # Prise de son et sonorisation
    "L1509": "technicien",  # Régie générale
}
ROME_SPECTACLE = list(ROME_ROLE.keys())
MOTS_CLES_SPECTACLE = "spectacle intermittent CDDU cachet"

# Types de contrat courts à privilégier dans le tri (CDDU, intérim, saisonnier, CDD).
CONTRATS_COURTS = {"CDD", "MIS", "SAI", "DDI"}

# ─── Filtre spectacle : MOTEUR DE SCORE (identité, pas liste de mots) ────────────
# Question du filtre : « un intermittent ouvrirait-il cette annonce en pensant
# SINCÈREMENT qu'elle le concerne ? ». On score chaque offre sur des SIGNAUX :
#   métier compatible +3 · contexte spectacle +1 · contrat CDDU +2 ·
#   employeur du spectacle +2 · fonction INCOMPATIBLE −10 (veto). Garde si score ≥ seuil.
# Extensible : ajouter un signal (descriptif, expérience…) ne change pas la philosophie.
# Le code ROME de FT est POLLUÉ (maçon tagué « L1202 Musicien ») → on score sur le
# TITRE (texte libre employeur), jamais sur romeLibelle/appellation (pollués aussi).
# Mots normalisés (minuscules, sans accents), « mot entier » (\b). À AFFINER au fil des
# faux positifs : un nouveau cas = UN mot ajouté à la bonne famille.

# MÉTIERS COMPATIBLES — artistes (sert aussi à badger roleType=artiste)
_MOTS_ARTISTE = [
    "comedien", "comedienne", "comedie musicale", "acteur", "actrice", "musicien",
    "musicienne", "instrumentiste", "chanteur", "chanteuse", "choriste", "danseur",
    "danseuse", "artiste", "circassien", "cirque", "figurant", "figurante",
    "figuration", "doublure", "cascadeur", "cascadeuse", "marionnettiste",
    "humoriste", "magicien", "clown", "mime", "orchestre",
]
# MÉTIERS COMPATIBLES — techniciens (badge roleType=technicien). Élargi (audit 2026-07-05).
_MOTS_TECHNICIEN = [
    "regie", "regisseur", "regisseuse", "machiniste", "backline", "eclairagiste",
    "eclairage scenique", "sonorisation", "ingenieur du son", "prise de son",
    "preneur de son", "cadreur", "cadreuse", "chef operateur", "operateur audiovisuel",
    "operateur de prise de vue", "projectionniste", "costumier", "costumiere",
    "habilleuse", "habilleur", "maquilleur", "maquilleuse", "decorateur",
    "accessoiriste", "technicien du spectacle", "technicien plateau",
    "technicien lumiere", "technicien son", "technicien audiovisuel", "technicien video",
    "rigger", "poursuiteur", "perchman", "scripte", "etalonneur", "chef electricien",
    "electricien de spectacle", "electricien scenique", "truquiste", "chef monteur",
    "monteur audiovisuel", "monteur video", "monteur truquiste", "producteur audiovisuel",
    "charge de production", "chargee de production", "assistant de production",
    "regisseur de production", "mediateur culturel", "mediation culturelle",
    "animateur culturel", "action culturelle", "atelier theatre", "atelier artistique",
]
# CONTEXTE SPECTACLE (signal faible)
_MOTS_CONTEXTE = [
    "spectacle", "theatre", "tournage", "audiovisuel", "concert", "festival",
    "cachet", "intermittent", "cddu", "opera", "ballet", "tournee", "captation",
    "long metrage", "court metrage", "salle de spectacle", "scene", "scenique",
]
# FONCTIONS INCOMPATIBLES (veto) — corporate/commercial/admin, homonymes, apprentissage.
_MOTS_INCOMPATIBLE = [
    "business developer", "commercial", "commerciale", "account manager",
    "technico-commercial", "ingenieur d'affaires", "ingenieur commercial",
    "charge d'affaires", "charge de clientele", "developpeur", "consultant",
    "consultante", "recruteur", "recruteuse", "charge de recrutement", "comptable",
    "assistant de direction", "assistant commercial", "assistante commerciale",
    "gestionnaire", "chef de projet", "responsable commercial", "directeur commercial",
    "vendeur", "vendeuse", "conseiller clientele", "community manager", "product manager",
    "promotion", "marketing",
    # homonymes du spectacle
    "d'immeuble", "de copropriete", "gestion des espaces", "gestion locative",
    # apprentissage / stage (décision produit : hors intermittence)
    "alternance", "apprentissage", "apprenti", "apprentie", "stage", "stagiaire",
]
# EMPLOYEUR DU SPECTACLE (signal +2, sur le nom de l'entreprise)
_MOTS_EMPLOYEUR = [
    "theatre", "compagnie", "production", "productions", "festival", "opera",
    "orchestre", "ballet", "cirque", "films", "audiovisuel", "spectacles",
    "scenique", "conservatoire", "philharmonique", "ensemble", "prod",
]


def _norm(s: str) -> str:
    """minuscule + sans accents, pour comparer proprement."""
    s = unicodedata.normalize("NFD", s or "")
    return "".join(c for c in s if unicodedata.category(c) != "Mn").lower()


def _compile(mots):
    return re.compile(r"\b(" + "|".join(re.escape(m) for m in mots) + r")\b")


_RE_ARTISTE = _compile(_MOTS_ARTISTE)
_RE_TECHNICIEN = _compile(_MOTS_TECHNICIEN)
_RE_METIER = _compile(_MOTS_ARTISTE + _MOTS_TECHNICIEN)   # « métier compatible » (fort)
_RE_CONTEXTE = _compile(_MOTS_CONTEXTE)
_RE_INCOMPATIBLE = _compile(_MOTS_INCOMPATIBLE)
_RE_EMPLOYEUR = _compile(_MOTS_EMPLOYEUR)

_SEUIL_SPECTACLE = 1  # on garde une offre dont le score est ≥ à ce seuil


def _texte_offre(raw) -> str:
    # UNIQUEMENT l'intitulé (texte libre employeur). La description échoit souvent
    # l'appellation ROME polluée (« Musicien »…) → l'inclure ferait repasser les maçons.
    return _norm(raw.get("intitule") or "")


def _score_offre(raw) -> int:
    """
    Score « est-ce qu'un intermittent se sentirait sincèrement concerné ? ».
    Extensible : ajouter un signal ici ne change pas la philosophie du filtre.
    """
    titre = _texte_offre(raw)
    score = 0
    if _RE_METIER.search(titre):
        score += 3                                    # métier compatible = signal FORT
    if _RE_CONTEXTE.search(titre):
        score += 1                                    # contexte spectacle = signal faible
    if _infer_contract(raw) == "CDDU":
        score += 2                                    # contrat d'usage = typique intermittent
    emp = _norm((raw.get("entreprise") or {}).get("nom") or "")
    if emp and _RE_EMPLOYEUR.search(emp):
        score += 2                                    # employeur clairement du spectacle
    if _RE_INCOMPATIBLE.search(titre):
        score -= 10                                   # fonction incompatible = VETO
    return score


def _est_spectacle(raw) -> bool:
    # Garde-t-on l'offre ? (le filtrage passe désormais par le score.)
    return _score_offre(raw) >= _SEUIL_SPECTACLE


# ─── OAuth : jeton en cache mémoire ─────────────────────────────────────────────
_token_cache = {"value": None, "expire_at": 0.0}


def _credentials():
    """Lit les identifiants depuis l'env Railway. Ne renvoie/loggue jamais les valeurs."""
    cid = os.environ.get("FT_CLIENT_ID", "").strip()
    secret = os.environ.get("FT_CLIENT_SECRET", "").strip()
    if not cid or not secret:
        raise RuntimeError("FT_CLIENT_ID / FT_CLIENT_SECRET absents des variables d'environnement.")
    return cid, secret


def _get_token() -> str:
    """Jeton OAuth2 client_credentials, mis en cache jusqu'à ~1 min avant expiration."""
    now = time.time()
    if _token_cache["value"] and now < _token_cache["expire_at"]:
        return _token_cache["value"]

    cid, secret = _credentials()
    resp = requests.post(
        OAUTH_URL,
        params={"realm": OAUTH_REALM},
        data={
            "grant_type": "client_credentials",
            "client_id": cid,
            "client_secret": secret,
            # France Travail EXIGE que le scope inclue application_<CLIENT_ID>.
            "scope": f"application_{cid} {OAUTH_SCOPE}",
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=HTTP_TIMEOUT,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"OAuth France Travail: statut {resp.status_code}")
    data = resp.json()
    token = data.get("access_token")
    if not token:
        raise RuntimeError("OAUTH: réponse sans access_token.")
    expires_in = int(data.get("expires_in", 1400))
    _token_cache["value"] = token
    _token_cache["expire_at"] = now + max(60, expires_in - 60)
    return token


# ─── Résolution de la localisation ──────────────────────────────────────────────
def _resolve_lieu(lieu: str):
    """
    Convertit ce que tape l'utilisateur en paramètres FT.
      - '75' ou '2A'         → (departement, None)
      - '75056' (code INSEE) → (None, commune_insee)
      - 'Paris'              → geo.api.gouv.fr → (None, commune_insee)
    Renvoie (departement, commune). L'un des deux au plus est non-None.
    """
    if not lieu:
        return (None, None)
    lieu = lieu.strip()
    # Département : 2 chiffres, ou 2A/2B (Corse), ou 3 chiffres (DOM)
    if lieu.isdigit() and len(lieu) in (2, 3):
        return (lieu, None)
    if lieu.upper() in ("2A", "2B"):
        return (lieu.upper(), None)
    # Code INSEE commune : 5 caractères
    if len(lieu) == 5 and lieu[0].isdigit():
        return (None, lieu)
    # Sinon : nom de ville → INSEE via geo.api.gouv.fr (public, sans credential)
    try:
        r = requests.get(
            GEO_COMMUNES_URL,
            params={"nom": lieu, "fields": "code", "boost": "population", "limit": 1},
            timeout=HTTP_TIMEOUT,
        )
        r.raise_for_status()
        arr = r.json()
        if arr:
            code = arr[0]["code"]
            # Paris/Lyon/Marseille : FT refuse le code « commune » global → on passe par le département.
            plm = {"75056": "75", "69123": "69", "13055": "13"}
            if code in plm:
                return (plm[code], None)
            return (None, code)
    except Exception:
        pass  # localisation non résolue → recherche nationale (pas d'échec dur)
    return (None, None)


# ─── Mapping offre FT → notre modèle JobOffer ───────────────────────────────────
def _infer_role(raw) -> str:
    # Basé sur le CONTENU (le romeCode de FT est pollué). Artiste prioritaire, puis technicien.
    t = _texte_offre(raw)
    if _RE_ARTISTE.search(t):
        return "artiste"
    if _RE_TECHNICIEN.search(t):
        return "technicien"
    return "autre"


def _infer_contract(raw) -> str:
    tc = (raw.get("typeContrat") or "").upper()
    nature = (raw.get("natureContrat") or "").lower()
    if "cddu" in nature or "usage" in nature:
        return "CDDU"
    if tc in ("MIS", "SAI", "DDI", "CDD"):
        return "CDDU" if "spectacle" in nature else "mission"
    return "mission"


def _map(raw) -> dict:
    lieu = (raw.get("lieuTravail") or {})
    origine = (raw.get("origineOffre") or {})
    return {
        "id": raw.get("id") or "",
        "title": raw.get("intitule") or "Offre",
        "roleType": _infer_role(raw),
        "contractType": _infer_contract(raw),
        "location": lieu.get("libelle") or "",
        "region": "",  # FT ne donne pas la région directement ; libellé lieu suffit en V1
        "source": "France Travail",
        "sourceUrl": origine.get("urlOrigine") or "",
        "publishedAt": (raw.get("dateCreation") or "")[:10],
        "description": (raw.get("description") or "")[:300] or None,
    }


# ─── Recherche ──────────────────────────────────────────────────────────────────
def search_offres(role_type: str = "", contract_type: str = "", lieu: str = "", rayon: int = 20) -> list:
    """
    Interroge FT et renvoie une liste de JobOffer (dict). LÈVE une exception si FT échoue
    (l'appelant renverra une erreur propre ; jamais de mocks).
    """
    token = _get_token()

    # On envoie TOUS les codes ROME spectacle. Le tag ROME de FT étant peu fiable, le
    # ciblage fin par métier se fait ensuite sur le CONTENU (roleType), pas sur le ROME.
    romes = ROME_SPECTACLE

    params = {
        "codeROME": ",".join(romes),
        "sort": 1,  # tri par date décroissante
        # Pas de motsCles : les codes ROME ciblent déjà le spectacle ; ajouter des
        # mots-clés en ET sur-filtrait (souvent 0 résultat).
    }
    departement, commune = _resolve_lieu(lieu)
    if commune:
        params["commune"] = commune
        params["distance"] = max(0, int(rayon or 20))
    elif departement:
        params["departement"] = departement

    resp = requests.get(
        SEARCH_URL,
        params=params,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        timeout=HTTP_TIMEOUT,
    )
    # 204 = requête valide mais AUCUNE offre ; 200 = OK ; 206 = résultats partiels (pagination).
    if resp.status_code == 204:
        return []
    if resp.status_code not in (200, 206):
        raise RuntimeError(f"Recherche France Travail: statut {resp.status_code}")

    resultats = (resp.json() or {}).get("resultats") or []
    # FT tague mal certaines offres (maçon posté sous « Musicien »…) : on ne peut pas
    # se fier au code ROME → filtre sur le contenu réel (intitulé + description).
    resultats = [o for o in resultats if _est_spectacle(o)]
    offres = [_map(o) for o in resultats]

    # Filtre métier fin (au cas où l'API élargit) + filtre contrat demandé.
    if role_type in ("artiste", "technicien", "admin"):
        offres = [o for o in offres if o["roleType"] == role_type]
    if contract_type in ("cachet", "CDDU", "mission", "heures"):
        offres = [o for o in offres if o["contractType"] == contract_type]

    # Priorise les contrats courts / CDDU en tête.
    offres.sort(key=lambda o: 0 if o["contractType"] == "CDDU" else 1)
    return offres

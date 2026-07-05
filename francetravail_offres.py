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
import time
import logging

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
        # DIAG TEMPORAIRE : le corps d'erreur OAuth explique le refus (invalid_scope,
        # invalid_client…) et ne contient jamais le secret.
        raise RuntimeError(f"OAUTH {resp.status_code}: {resp.text[:160]}")
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
    code = (raw.get("romeCode") or "")[:5]
    return ROME_ROLE.get(code, "autre")


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

    # Ciblage ROME selon le métier demandé (sinon tout le spectacle).
    if role_type in ("artiste", "technicien", "admin"):
        romes = [c for c, r in ROME_ROLE.items() if r == role_type] or ROME_SPECTACLE
    else:
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
        raise RuntimeError(f"SEARCH {resp.status_code}: {resp.text[:160]}")

    resultats = (resp.json() or {}).get("resultats") or []
    offres = [_map(o) for o in resultats]

    # Filtre métier fin (au cas où l'API élargit) + filtre contrat demandé.
    if role_type in ("artiste", "technicien", "admin"):
        offres = [o for o in offres if o["roleType"] == role_type]
    if contract_type in ("cachet", "CDDU", "mission", "heures"):
        offres = [o for o in offres if o["contractType"] == contract_type]

    # Priorise les contrats courts / CDDU en tête.
    offres.sort(key=lambda o: 0 if o["contractType"] == "CDDU" else 1)
    return offres

"""
Module de connexion bancaire via Enable Banking (AISP agréé dans l'UE, lecture seule).

Remplace l'ancien rail Powens (squelette conservé dans powens.py) en gardant les MÊMES
routes côté frontend : /bank/connect, /bank/callback, /bank/balance, /bank/disconnect
(+ /bank/banques, nouveau : chez Enable Banking l'utilisateur choisit sa banque AVANT
la redirection, il n'y a pas de « webview magasin » comme chez Powens).

Flux :
  1. GET  /bank/banques    -> liste des banques françaises couvertes (mise en cache).
  2. POST /bank/connect    -> {banque, usage} : crée l'autorisation chez Enable Banking,
                              renvoie l'URL de la banque à ouvrir (webview_url).
  3. L'utilisateur s'authentifie CHEZ SA BANQUE (identifiants jamais vus par TOTOR).
  4. Retour sur /bank-callback?code=... -> le front appelle POST /bank/callback {code}.
  5. On échange le code contre une session, on choisit le compte principal, on lit le
     solde et on met à jour profile.solde_bancaire.
  6. GET  /bank/balance    -> relit le solde (appelé à chaque ouverture de l'app).
  7. POST /bank/disconnect -> oublie la session (retour à la saisie manuelle).

Sécurité : la clé privée signe des JWT côté serveur uniquement. Lecture seule DSP2 :
aucune capacité de paiement. Les identifiants bancaires ne transitent JAMAIS par TOTOR.

Variables d'environnement (Railway) :
  ENABLE_APP_ID           id de l'application Enable Banking
  ENABLE_PRIVATE_KEY      clé privée PEM (contenu complet, multiligne)
  BANK_SYNC_BETA_EMAILS   emails autorisés, séparés par des virgules (mode restreint
                          Enable Banking : seuls les comptes liés dans leur console
                          fonctionnent -> on ne montre la carte qu'aux testeurs).
                          Vide ou absent = personne ; "*" = ouvert à tous (après
                          la levée de restriction chez Enable Banking).
  FRONTEND_URL            ex: https://www.montotor.fr (pour la redirection)
"""

import os
import time

import jwt as pyjwt
import requests as http_requests
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db
from models import User, Profile
from auth import get_current_user
from bank_regles import choisir_solde, choisir_compte

router = APIRouter(prefix="/bank", tags=["bank"])

EB_API = "https://api.enablebanking.com"
ENABLE_APP_ID = os.environ.get("ENABLE_APP_ID", "")
ENABLE_PRIVATE_KEY = os.environ.get("ENABLE_PRIVATE_KEY", "")
FRONTEND_URL = os.environ.get("FRONTEND_URL", "https://www.montotor.fr")
BANK_CALLBACK_URL = f"{FRONTEND_URL}/bank-callback"
EB_TIMEOUT = 20
# Durée demandée pour le consentement (les banques plafonnent elles-mêmes, souvent 90 j).
CONSENT_DAYS = 90

# Cache mémoire de la liste des banques (elle bouge rarement).
_banques_cache = {"quand": 0.0, "liste": []}
_BANQUES_TTL = 24 * 3600


def _configure() -> bool:
    return bool(ENABLE_APP_ID and ENABLE_PRIVATE_KEY)


def _beta_autorise(email: str) -> bool:
    """Mode restreint Enable Banking : la connexion ne marche que pour les comptes liés
    dans leur console. On ne propose donc la carte qu'aux emails listés ("*" = tous)."""
    brut = os.environ.get("BANK_SYNC_BETA_EMAILS", "")
    if brut.strip() == "*":
        return True
    autorises = {e.strip().lower() for e in brut.split(",") if e.strip()}
    return (email or "").lower() in autorises


def _jeton() -> str:
    """JWT RS256 signé avec notre clé privée (auth applicative Enable Banking)."""
    now = int(time.time())
    return pyjwt.encode(
        {"iss": "enablebanking.com", "aud": "api.enablebanking.com", "iat": now, "exp": now + 3600},
        ENABLE_PRIVATE_KEY,
        algorithm="RS256",
        headers={"kid": ENABLE_APP_ID},
    )


def _eb(methode: str, chemin: str, **kwargs):
    """Appel Enable Banking avec gestion d'erreurs uniforme."""
    try:
        resp = http_requests.request(
            methode, f"{EB_API}{chemin}",
            headers={"Authorization": f"Bearer {_jeton()}"},
            timeout=EB_TIMEOUT, **kwargs,
        )
    except http_requests.RequestException:
        raise HTTPException(status_code=502, detail="Banque indisponible (réseau).")
    return resp


# ----------------------------------------------------------------
# Routes
# ----------------------------------------------------------------

@router.get("/banques")
def bank_banques(user: User = Depends(get_current_user)):
    """Liste des banques françaises couvertes (nom + pays), en cache 24 h."""
    if not _configure():
        raise HTTPException(status_code=503, detail="Connexion bancaire pas encore activée.")
    if time.time() - _banques_cache["quand"] > _BANQUES_TTL or not _banques_cache["liste"]:
        resp = _eb("GET", "/aspsps", params={"country": "FR"})
        if resp.status_code != 200:
            raise HTTPException(status_code=502, detail="Liste des banques indisponible.")
        brutes = resp.json().get("aspsps", [])
        _banques_cache["liste"] = sorted(
            ({"nom": b.get("name"), "logo": b.get("logo")} for b in brutes if b.get("name")),
            key=lambda b: (b["nom"] or "").lower(),
        )
        _banques_cache["quand"] = time.time()
    return {"banques": _banques_cache["liste"]}


class BankConnectRequest(BaseModel):
    banque: str
    usage: str = "personal"  # "personal" | "business"


class BankConnectResponse(BaseModel):
    webview_url: str


@router.post("/connect", response_model=BankConnectResponse)
def bank_connect(
    req: BankConnectRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Crée l'autorisation chez Enable Banking et renvoie l'URL de la banque."""
    if not _configure():
        raise HTTPException(status_code=503, detail="Connexion bancaire pas encore activée.")
    if not _beta_autorise(user.email):
        raise HTTPException(status_code=503, detail="Connexion bancaire en cours d'ouverture : bientôt disponible.")

    valid_until = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(time.time() + CONSENT_DAYS * 86400))
    corps = {
        "access": {"valid_until": valid_until},
        "aspsp": {"name": req.banque, "country": "FR"},
        "state": "totor-bank",
        "redirect_url": BANK_CALLBACK_URL,
        "psu_type": "business" if req.usage == "business" else "personal",
    }
    resp = _eb("POST", "/auth", json=corps)
    if resp.status_code != 200 or not resp.json().get("url"):
        raise HTTPException(status_code=502, detail="Connexion bancaire indisponible pour cette banque.")

    profile = db.query(Profile).filter(Profile.user_id == user.id).first()
    if profile:
        profile.eb_banque = req.banque
        db.commit()
    return {"webview_url": resp.json()["url"]}


class BankCallbackRequest(BaseModel):
    code: str


@router.post("/callback")
def bank_callback(
    req: BankCallbackRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Échange le code de retour contre une session, choisit le compte principal,
    lit le solde et met à jour le profil."""
    profile = db.query(Profile).filter(Profile.user_id == user.id).first()
    if not profile:
        raise HTTPException(status_code=404, detail="Profil introuvable.")

    resp = _eb("POST", "/sessions", json={"code": req.code})
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail="La banque n'a pas confirmé la connexion. Réessaie.")
    session = resp.json()
    compte = choisir_compte(session.get("accounts", []))
    if not compte or not compte.get("uid"):
        raise HTTPException(status_code=502, detail="Aucun compte lisible sur cette connexion.")

    profile.eb_session_id = session.get("session_id")
    profile.eb_account_uid = compte["uid"]
    iban = ((compte.get("account_id") or {}).get("iban")) or ""
    profile.eb_iban_fin = iban[-4:] if iban else None
    db.commit()

    solde = _lire_solde(profile, db)
    return {"ok": True, "solde": solde}


def _lire_solde(profile: Profile, db: Session):
    """Lit le solde du compte relié et met à jour profile.solde_bancaire."""
    if not profile.eb_account_uid:
        return None
    resp = _eb("GET", f"/accounts/{profile.eb_account_uid}/balances")
    if resp.status_code != 200:
        print(f"[enable-banking] balances -> {resp.status_code} : {resp.text[:200]}", flush=True)
        return None
    solde = choisir_solde(resp.json().get("balances", []))
    if solde is None:
        return None
    profile.solde_bancaire = solde
    db.commit()
    return solde


@router.get("/balance")
def bank_balance(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """État de la connexion + solde synchronisé + disponibilité de la feature.
    Le front s'en sert pour afficher la carte (disponible), le badge (connected)
    et le solde."""
    disponible = _configure() and _beta_autorise(user.email)
    profile = db.query(Profile).filter(Profile.user_id == user.id).first()
    if not profile:
        return {"disponible": disponible, "connected": False, "solde": None}

    connected = bool(profile.eb_account_uid)
    solde = _lire_solde(profile, db) if connected else None
    # Session expirée ou révoquée : on l'annonce pour que le front propose de relier.
    expiree = connected and solde is None
    return {
        "disponible": disponible,
        "connected": connected,
        "solde": solde,
        "banque": profile.eb_banque,
        "iban_fin": profile.eb_iban_fin,
        "expiree": expiree,
    }


def _uids_de_session(profile: Profile) -> list:
    """Les uid des comptes accessibles sur la session en cours (liste vide si expirée)."""
    if not profile.eb_session_id:
        return []
    resp = _eb("GET", f"/sessions/{profile.eb_session_id}")
    if resp.status_code != 200:
        return []
    bruts = resp.json().get("accounts", [])
    return [b.get("uid") if isinstance(b, dict) else b for b in bruts if b]


@router.get("/comptes")
def bank_comptes(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Liste les comptes accessibles sur la connexion en cours, pour laisser
    l'utilisateur choisir LE compte suivi (retour testeur n°1 : la banque en
    renvoie plusieurs, le choix automatique ne suffit pas)."""
    profile = db.query(Profile).filter(Profile.user_id == user.id).first()
    if not profile or not profile.eb_session_id:
        raise HTTPException(status_code=404, detail="Aucune banque reliée.")
    uids = _uids_de_session(profile)
    if not uids:
        raise HTTPException(status_code=502, detail="Session bancaire expirée : relie ta banque.")
    comptes = []
    for uid in uids[:10]:
        det = _eb("GET", f"/accounts/{uid}/details")
        iban, nom = "", None
        if det.status_code == 200:
            dd = det.json()
            iban = ((dd.get("account_id") or {}).get("iban")) or ""
            nom = dd.get("name") or dd.get("product") or dd.get("details")
        comptes.append({
            "uid": uid,
            "iban_fin": iban[-4:] if iban else None,
            "nom": nom,
            "suivi": uid == profile.eb_account_uid,
        })
    return {"comptes": comptes}


class BankCompteRequest(BaseModel):
    uid: str


@router.post("/compte")
def bank_choisir_compte(
    req: BankCompteRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Change le compte suivi (sécurité : il doit appartenir à la session de l'utilisateur)."""
    profile = db.query(Profile).filter(Profile.user_id == user.id).first()
    if not profile or not profile.eb_session_id:
        raise HTTPException(status_code=404, detail="Aucune banque reliée.")
    if req.uid not in _uids_de_session(profile):
        raise HTTPException(status_code=403, detail="Ce compte n'appartient pas à ta connexion.")
    profile.eb_account_uid = req.uid
    det = _eb("GET", f"/accounts/{req.uid}/details")
    if det.status_code == 200:
        iban = ((det.json().get("account_id") or {}).get("iban")) or ""
        profile.eb_iban_fin = iban[-4:] if iban else None
    db.commit()
    solde = _lire_solde(profile, db)
    return {"ok": True, "solde": solde}


@router.post("/disconnect")
def bank_disconnect(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Débranche la banque (retour à la saisie manuelle, solde saisi conservé)."""
    profile = db.query(Profile).filter(Profile.user_id == user.id).first()
    if profile:
        if profile.eb_session_id:
            try:
                _eb("DELETE", f"/sessions/{profile.eb_session_id}")
            except HTTPException:
                pass  # best effort : la session expirera d'elle-même
        profile.eb_session_id = None
        profile.eb_account_uid = None
        profile.eb_banque = None
        profile.eb_iban_fin = None
        db.commit()
    return {"ok": True}

"""
Module de connexion bancaire via Powens (agrégateur DSP2, lecture seule).

Rôle : permettre à un utilisateur de relier son compte bancaire pour que son
solde se mette à jour automatiquement, SANS que H€CTOR ne voie jamais ses
identifiants bancaires (tout passe par la webview Powens) et SANS jamais
pouvoir toucher à l'argent (lecture seule, contrainte réglementaire DSP2).

Flux :
  1. POST /bank/connect   -> crée (ou réutilise) l'utilisateur Powens, génère un
                             code temporaire, renvoie l'URL de la webview à ouvrir.
  2. L'utilisateur connecte sa banque dans la webview Powens.
  3. Powens le renvoie sur le front (/bank-callback?connection_id=...), qui appelle
     POST /bank/callback pour enregistrer la connexion.
  4. GET  /bank/balance   -> lit le solde du compte connecté chez Powens.
  5. POST /bank/disconnect-> débranche la banque (oublie le lien Powens).

Sécurité : le client_secret n'est utilisé QUE côté serveur (ici), jamais exposé
au front. Les tokens Powens sont stockés sur le Profile de l'utilisateur.

Variables d'environnement attendues (à définir sur Railway) :
  POWENS_CLIENT_ID      ex: 61272990
  POWENS_CLIENT_SECRET  (secret, à garder privé)
  POWENS_DOMAIN         ex: hector-sandbox   (sans .biapi.pro)
  FRONTEND_URL          ex: https://www.hector-app.fr   (pour la redirection)
"""

import os
import requests as http_requests
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db
from models import User, Profile
from auth import get_current_user

router = APIRouter(prefix="/bank", tags=["bank"])

# ── Configuration (depuis l'environnement Railway) ──
POWENS_CLIENT_ID = os.environ.get("POWENS_CLIENT_ID", "")
POWENS_CLIENT_SECRET = os.environ.get("POWENS_CLIENT_SECRET", "")
POWENS_DOMAIN = os.environ.get("POWENS_DOMAIN", "hector-sandbox")
FRONTEND_URL = os.environ.get("FRONTEND_URL", "https://www.hector-app.fr")

# URL de base de l'API Powens pour ton domaine (REST).
POWENS_API_BASE = f"https://{POWENS_DOMAIN}.biapi.pro/2.0"
# URL de la webview hébergée par Powens (parcours de connexion bancaire).
POWENS_WEBVIEW_BASE = "https://webview.powens.com/fr/connect"
# URL de callback déclarée dans la console Powens (doit correspondre exactement).
BANK_CALLBACK_URL = f"{FRONTEND_URL}/bank-callback"

# Délai réseau (s) pour les appels à Powens. Au-delà, on échoue proprement.
POWENS_TIMEOUT = 20


def _powens_configured() -> bool:
    """True si les identifiants Powens sont présents dans l'environnement."""
    return bool(POWENS_CLIENT_ID and POWENS_CLIENT_SECRET)


def _ensure_powens_user(profile: Profile, db: Session) -> str:
    """
    Garantit que l'utilisateur possède un token Powens permanent.
    Le crée via /auth/init au premier appel, le réutilise ensuite.
    Renvoie le token permanent.
    """
    if profile.powens_token:
        return profile.powens_token

    # Création d'un nouvel utilisateur côté Powens. C'est l'unique endroit où
    # le client_secret est transmis : depuis le serveur, jamais le front.
    try:
        resp = http_requests.post(
            f"{POWENS_API_BASE}/auth/init",
            json={
                "client_id": POWENS_CLIENT_ID,
                "client_secret": POWENS_CLIENT_SECRET,
            },
            timeout=POWENS_TIMEOUT,
        )
    except http_requests.RequestException:
        raise HTTPException(status_code=502, detail="Banque indisponible (réseau).")

    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail="Connexion bancaire indisponible.")

    data = resp.json()
    token = data.get("auth_token")
    if not token:
        raise HTTPException(status_code=502, detail="Réponse bancaire invalide.")

    profile.powens_token = token
    # L'id utilisateur Powens peut servir plus tard (webhooks, debug).
    if data.get("id_user") is not None:
        profile.powens_user_id = str(data.get("id_user"))
    db.commit()
    return token


def _temporary_code(token: str) -> str:
    """
    Échange le token permanent contre un code temporaire (valide ~30 min),
    seul élément passé à la webview (on n'expose jamais le token permanent).
    """
    try:
        resp = http_requests.get(
            f"{POWENS_API_BASE}/auth/token/code",
            headers={"Authorization": f"Bearer {token}"},
            timeout=POWENS_TIMEOUT,
        )
    except http_requests.RequestException:
        raise HTTPException(status_code=502, detail="Banque indisponible (réseau).")

    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail="Connexion bancaire indisponible.")

    code = resp.json().get("code")
    if not code:
        raise HTTPException(status_code=502, detail="Réponse bancaire invalide.")
    return code


# ----------------------------------------------------------------
# Routes
# ----------------------------------------------------------------

class BankConnectResponse(BaseModel):
    webview_url: str


@router.post("/connect", response_model=BankConnectResponse)
def bank_connect(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Démarre la connexion bancaire : renvoie l'URL de la webview Powens que le
    front doit ouvrir. L'utilisateur y choisit sa banque et s'authentifie.
    """
    if not _powens_configured():
        raise HTTPException(status_code=503, detail="Connexion bancaire pas encore activée.")

    profile = db.query(Profile).filter(Profile.user_id == user.id).first()
    if not profile:
        profile = Profile(user_id=user.id)
        db.add(profile)
        db.commit()

    token = _ensure_powens_user(profile, db)
    code = _temporary_code(token)

    webview_url = (
        f"{POWENS_WEBVIEW_BASE}"
        f"?domain={POWENS_DOMAIN}.biapi.pro"
        f"&client_id={POWENS_CLIENT_ID}"
        f"&redirect_uri={BANK_CALLBACK_URL}"
        f"&code={code}"
    )
    return {"webview_url": webview_url}


class BankCallbackRequest(BaseModel):
    connection_id: int


@router.post("/callback")
def bank_callback(
    req: BankCallbackRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Appelé par le front après le retour de la webview. Enregistre l'id de la
    connexion bancaire créée, qui sert ensuite à retrouver les comptes.
    """
    profile = db.query(Profile).filter(Profile.user_id == user.id).first()
    if not profile:
        raise HTTPException(status_code=404, detail="Profil introuvable.")

    profile.powens_connection_id = req.connection_id
    db.commit()
    # On tente de récupérer le solde tout de suite (best effort).
    solde = _fetch_balance(profile, db)
    return {"ok": True, "solde": solde}


def _fetch_balance(profile: Profile, db: Session):
    """
    Lit les comptes de l'utilisateur chez Powens et renvoie le solde du compte
    que l'utilisateur a coché dans la webview Powens. Les comptes décochés sont
    marqués "disabled" par Powens : on ne garde donc que les comptes actifs.
    Met à jour profile.solde_bancaire pour que le reste de l'app le voie.
    Renvoie None si rien n'est disponible.
    """
    if not profile.powens_token:
        print("[powens] _fetch_balance: pas de powens_token sur le profil")
        return None

    try:
        resp = http_requests.get(
            f"{POWENS_API_BASE}/users/me/accounts",
            headers={"Authorization": f"Bearer {profile.powens_token}"},
            params={"all": "1"},
            timeout=POWENS_TIMEOUT,
        )
    except http_requests.RequestException as e:
        print(f"[powens] _fetch_balance: erreur réseau /accounts : {e}")
        return None

    if resp.status_code != 200:
        print(f"[powens] _fetch_balance: /accounts a renvoyé {resp.status_code} — {resp.text[:300]}")
        return None

    accounts = resp.json().get("accounts", [])
    print(f"[powens] _fetch_balance: {len(accounts)} compte(s) reçu(s) de Powens")
    for a in accounts:
        print(f"[powens]   compte id={a.get('id')} name={a.get('name')!r} "
              f"type={a.get('type')!r} disabled={a.get('disabled')!r} "
              f"balance={a.get('balance')} currency={(a.get('currency') or {}).get('id')}")

    # IMPORTANT (RGPD Powens) : le champ "disabled" est une DATE (ou null), pas un
    # booléen. Un compte est ACTIF si disabled est null/None. La webview Powens
    # active les comptes que l'utilisateur a cochés ; les autres restent "disabled".
    # On garde donc les comptes avec disabled == null ET un solde présent.
    actifs = [
        a for a in accounts
        if a.get("disabled") in (None, False)
        and a.get("balance") is not None
    ]
    print(f"[powens] _fetch_balance: {len(actifs)} compte(s) actif(s) (coché(s))")

    # Repli : si aucun "actif" mais des comptes existent avec un solde, on prend
    # ceux qui ont un solde (certaines configs ne marquent pas disabled).
    if not actifs:
        actifs = [a for a in accounts if a.get("balance") is not None]
        print(f"[powens] _fetch_balance: repli sur {len(actifs)} compte(s) avec solde")

    if not actifs:
        print("[powens] _fetch_balance: aucun compte exploitable → solde non mis à jour")
        return None

    # Si l'utilisateur n'a coché qu'un compte (cas attendu), il est seul → on le prend.
    # S'il en a coché plusieurs, on privilégie un compte courant ; sinon le plus gros.
    def est_courant(a):
        t = (a.get("type") or "").lower()
        return t in ("checking", "current", "compte courant")

    courants = [a for a in actifs if est_courant(a)]
    pool = courants if courants else actifs
    principal = max(pool, key=lambda a: a.get("balance") or 0)

    solde = principal.get("balance")
    if solde is None:
        return None

    print(f"[powens] _fetch_balance: compte retenu = {principal.get('name')!r} "
          f"solde={solde}")
    profile.solde_bancaire = float(solde)
    db.commit()
    return float(solde)


@router.get("/balance")
def bank_balance(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Renvoie l'état de la connexion bancaire et le solde synchronisé.
    Le front appelle ça pour afficher le solde et savoir si une banque est reliée.
    """
    profile = db.query(Profile).filter(Profile.user_id == user.id).first()
    if not profile:
        return {"connected": False, "solde": None}

    connected = bool(profile.powens_connection_id)
    solde = _fetch_balance(profile, db) if connected else None
    return {"connected": connected, "solde": solde}


@router.post("/disconnect")
def bank_disconnect(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Débranche la banque : oublie le lien Powens côté H€CTOR. L'utilisateur
    revient à la saisie manuelle. (Le solde déjà saisi est conservé.)
    """
    profile = db.query(Profile).filter(Profile.user_id == user.id).first()
    if profile:
        profile.powens_connection_id = None
        # On garde powens_token (le user Powens existe toujours), mais plus de
        # connexion active. On pourrait aussi supprimer la connexion côté Powens
        # via l'API, mais ce n'est pas nécessaire pour le débranchement côté app.
        db.commit()
    return {"ok": True}

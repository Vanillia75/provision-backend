"""
API principale de l'application de provisionnement des cotisations.
Lancer avec : uvicorn api:app --reload
"""

import os
import html
import hashlib
import secrets
import json
import asyncio
import shutil
import tempfile
import requests as http_requests
from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Request, Body, Form
from fastapi.responses import Response, HTMLResponse, RedirectResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session
from google.oauth2 import id_token as google_id_token
from google.auth.transport import requests as google_requests

from apple_auth import verifier_identity_token as verifier_apple_identity_token, AppleTokenInvalide
from database import Base, engine, get_db, SessionLocal
from models import User, Profile, IncomeEntry, ClientInvoice, Expense, Contact, Quote, IntermittentActivity, AIUsage, LoginAttempt, FiscalSettings, Subscription, ChatMessage as ChatMessageDB
from auth import (
    hash_password, verify_password, create_token, get_current_user,
    create_purpose_token, verify_purpose_token,
)
from emailing import send_reset_password_email, send_verification_email, send_invoice_email, send_email, send_founder_signup_alert, send_founder_trial_ending_alert, PIONNIER_PLACES
from invoice_pdf import generate_invoice_pdf
from legal_mentions import (
    get_franchise_vat_mention, append_ei_mention, resolve_fiscal_settings,
    compute_invoice_totals, format_vat_rate, get_b2b_late_fee_mention,
    ASSUJETTI, ASSUJETTI_UE, ASSUJETTI_EXPORT,
)
from numerotation import compute_next_numero, normalize_numero_depart
from tax_engine import estimate, STATUTS_DISPONIBLES, STATUTS_A_VENIR, AUTO_ENTREPRENEUR_RATES, periode_urssaf_a_declarer
from projection import projeter_tresorerie
from paie_engine import calculer_paie
from aide_app import prompt_aide
from invoice_extractor import extract_invoice_data
from aem_extractor import extract_aem_data, extract_are_data
import r2_storage
import sauvegarde
import intermittent_engine as ie
import allocation_engine as ae
from regles_intermittent import valeur_de as regle_valeur
import conges_spectacles as cs
import voice_agent
import voice_access
import encaissement
from insee_lookup import lookup_siret, SiretLookupError
import billing
import revenuecat_webhook
import quotas_freemium
import sentry_sdk

# ── Observabilité backend (Sentry) — INERTE tant qu'aucun SENTRY_DSN n'est fourni ──
# (même logique que les sourcemaps front : sans la variable d'env, rien ne s'active,
#  aucun risque). send_default_pii=False : on n'envoie JAMAIS de données perso
# (NIR, noms, emails, corps de requête) à Sentry — cf. règle vie privée du projet.
_SENTRY_DSN = os.environ.get("SENTRY_DSN", "").strip()
if _SENTRY_DSN:
    sentry_sdk.init(
        dsn=_SENTRY_DSN,
        environment="production",
        traces_sample_rate=0.1,
        send_default_pii=False,
    )

Base.metadata.create_all(bind=engine)

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()  # .strip() : Railway garde parfois un \n en fin de valeur -> en-tete x-api-key invalide

# ── Plafonds anti-abus des appels IA (par utilisateur, par jour) ──
# Bornent le coût Anthropic. Largement au-dessus d'un usage normal : ils ne
# servent qu'à couper une boucle anormale ou un abus, pas à gêner un vrai user.
# Surchageables par variable d'environnement sans toucher au code.
AI_CHAT_DAILY_LIMIT = int(os.environ.get("AI_CHAT_DAILY_LIMIT", "40"))
AI_AEM_DAILY_LIMIT = int(os.environ.get("AI_AEM_DAILY_LIMIT", "15"))
# Garde-fou anti-abus journalier des scans de justificatifs AE (OCR local, peu coûteux).
AI_DOC_SCAN_DAILY_LIMIT = int(os.environ.get("AI_DOC_SCAN_DAILY_LIMIT", "30"))

# Taille maximale d'un fichier AEM uploadé (anti-DoS disque/mémoire/coût Vision).
AEM_MAX_BYTES = int(os.environ.get("AEM_MAX_BYTES", str(10 * 1024 * 1024)))  # 10 Mo


def _enregistrer_upload(file, max_bytes: int = None):
    """Écrit un upload dans un dossier temporaire, de façon sûre (audit 09/07/2026) :
    - nom de fichier NEUTRALISÉ (anti-traversée de chemin : basename + caractères sûrs) ;
    - taille PLAFONNÉE pendant la copie (413 au-delà, disque protégé).
    Renvoie (tmp_dir, file_path). L'appelant nettoie tmp_dir dans un finally."""
    import re as _re
    if max_bytes is None:
        max_bytes = AEM_MAX_BYTES
    nom = _re.sub(r"[^A-Za-z0-9._-]", "_", os.path.basename(file.filename or "document"))[-100:] or "document"
    tmp_dir = tempfile.mkdtemp()
    file_path = os.path.join(tmp_dir, nom)
    taille = 0
    with open(file_path, "wb") as f:
        while True:
            chunk = file.file.read(1024 * 1024)
            if not chunk:
                break
            taille += len(chunk)
            if taille > max_bytes:
                f.close()
                shutil.rmtree(tmp_dir, ignore_errors=True)
                raise HTTPException(status_code=413,
                                    detail=f"Fichier trop volumineux (max {max_bytes // (1024 * 1024)} Mo).")
            f.write(chunk)
    return tmp_dir, file_path

# Protection anti brute-force du login : au-delà de N échecs consécutifs, on
# bloque temporairement les tentatives pour cet email pendant un délai.
LOGIN_MAX_ECHECS = int(os.environ.get("LOGIN_MAX_ECHECS", "8"))
LOGIN_BLOCAGE_MINUTES = int(os.environ.get("LOGIN_BLOCAGE_MINUTES", "15"))

STATUTS_FACTURE = ("brouillon", "envoyee", "payee", "impayee")
STATUTS_DEVIS = ("brouillon", "envoye", "accepte", "refuse", "expire")

CATEGORIES_FRAIS = (
    "logiciels", "abonnements", "taxi", "repas", "materiel",
    "coworking", "telephone_internet", "autre",
)

app = FastAPI(title="API Provision Cotisations")

ALLOWED_ORIGINS = [
    "https://provision-frontend-nu.vercel.app",
    "https://hector-app.fr",       # ancien domaine : garde pour la transition (redirigera vers montotor.fr)
    "https://www.hector-app.fr",
    "https://montotor.fr",         # nouveau domaine TOTOR (rebranding 07/2026)
    "https://www.montotor.fr",
    "http://localhost:5173",  # developpement local (Vite)
    "capacitor://localhost",  # app iOS native (Capacitor, App Store)
    "https://localhost",      # app Android native (Capacitor, valeur par defaut)
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Module de connexion bancaire (Powens, lecture seule DSP2).
# Rail bancaire : Enable Banking depuis le 09/07/2026 (powens.py conservé comme squelette).
from enable_banking import router as bank_router
app.include_router(bank_router)


# ----------------------------------------------------------------
# Schemas
# ----------------------------------------------------------------

class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    gclid: Optional[str] = None  # clic Google Ads, capturé pour la mesure serveur


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class GoogleAuthRequest(BaseModel):
    credential: str
    gclid: Optional[str] = None  # clic Google Ads, capturé si nouveau compte


class AppleAuthRequest(BaseModel):
    identity_token: str
    # Nonce BRUT tire par l'app (elle a envoye sa SHA-256 a Apple). Obligatoire :
    # c'est lui qui empeche qu'un jeton intercepte soit rejoue.
    nonce: str
    gclid: Optional[str] = None  # clic Google Ads (surtout web), capturé si nouveau compte


class AuthResponse(BaseModel):
    token: str
    email: str


class ProfileRequest(BaseModel):
    statut: str
    activite: Optional[str] = None
    periodicite: str
    acre: bool = False
    versement_liberatoire: bool = False
    date_creation_activite: Optional[date] = None


class IncomeRequest(BaseModel):
    date: date
    amount: float
    description: Optional[str] = None


# ----------------------------------------------------------------
# Auth
# ----------------------------------------------------------------

@app.post("/auth/register", response_model=AuthResponse)
def register(req: RegisterRequest, db: Session = Depends(get_db)):
    existing = db.query(User).filter(User.email == req.email).first()
    if existing:
        raise HTTPException(status_code=409, detail="Un compte existe deja avec cet email")

    user = User(email=req.email, password_hash=hash_password(req.password), gclid=(req.gclid or None))
    db.add(user)
    db.commit()
    db.refresh(user)

    verify_token = create_purpose_token(user.id, "verify_email", expire_minutes=60 * 24)
    send_verification_email(user.email, verify_token)

    # Alerte fondateur : nouvel inscrit (best-effort, ne doit JAMAIS bloquer l'inscription).
    try:
        total_inscrits = db.query(User).count()
        send_founder_signup_alert(total_inscrits, user.email)
    except Exception:
        pass

    return AuthResponse(token=create_token(user.id), email=user.email)


def _compter_stats(db: Session):
    """Compte inscrits + abonnés payants en EXCLUANT les comptes marqués `is_test`.
    Marque EXPLICITE (User.is_test), plus de devinette fragile sur l'email : un vrai
    client ne peut plus être compté comme un test par hasard. AUCUNE donnée n'est
    supprimée, les comptes de test existent toujours, ils ne sont juste pas comptés.
    Pour marquer un compte plus tard : POST /admin/mark-test."""
    inscrits = db.query(User).filter(User.is_test.is_(False)).count()
    # Abonnés payants RÉELS toutes caisses (Stripe, Apple, Google), hors comptes
    # de test et hors achats sandbox : même compteur que les alertes fondateur.
    abonnes = billing.compter_abonnes_payants(db)
    return inscrits, abonnes


_ADMIN_COOKIE = "totor_admin"


def _cookie_token() -> str:
    """Jeton dérivé de la clé admin (SHA-256 salé). C'est LUI qui est stocké dans le
    cookie, JAMAIS la clé en clair : le secret ne traîne pas dans le navigateur.
    Change automatiquement si la clé change → invalide les anciens cookies."""
    expected = os.environ.get("ADMIN_STATS_KEY", "")
    return hashlib.sha256(("totor-admin-cookie-v1|" + expected).encode()).hexdigest()


def _admin_authed(request: Request, key: str = "") -> bool:
    """Vrai si la requête est authentifiée : via le COOKIE (jeton dérivé, préféré) ou
    via le paramètre ?key= (clé en clair, rétro-compat pour mes scripts). Le cookie
    évite d'avoir la clé dans l'URL (donc dans les logs). Sans clé configurée : jamais."""
    expected = os.environ.get("ADMIN_STATS_KEY", "")
    if not expected:
        return False
    return request.cookies.get(_ADMIN_COOKIE, "") == _cookie_token() or key == expected


def _poser_cookie_admin(resp):
    # HttpOnly (pas lisible par JS) + Secure (HTTPS only) + SameSite=Strict (jamais
    # envoyé depuis un autre site). Valeur = jeton dérivé, pas la clé en clair.
    resp.set_cookie(_ADMIN_COOKIE, _cookie_token(), httponly=True, secure=True,
                    samesite="strict", max_age=60 * 60 * 24 * 30)


# Anti-brute-force sur la connexion admin (en mémoire ; se réinitialise au redéploiement,
# acceptable pour cet usage). Derrière le proxy Railway, l'IP est souvent celle du proxy
# → la limite devient quasi globale, ce qui va bien pour un outil mono-admin.
_ADMIN_LOGIN_FAILS = {}
_ADMIN_LOGIN_MAX = 5
_ADMIN_LOGIN_BLOCK_MIN = 10


@app.get("/admin", response_class=HTMLResponse)
def admin_login_page():
    """Page de connexion admin : un champ mot de passe qui POST la clé (jamais dans l'URL)."""
    return HTMLResponse(
        "<!doctype html><html lang=\"fr\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        "<title>TOTOR admin</title><style>"
        "body{background:#07192E;color:#F8FAFC;font-family:system-ui,sans-serif;min-height:100vh;display:flex;align-items:center;justify-content:center;margin:0}"
        "form{background:#0A2540;border:1px solid #16324f;border-radius:16px;padding:34px 30px;width:300px;text-align:center}"
        ".b{font-family:Georgia,serif;font-weight:800;font-size:30px;letter-spacing:1px;margin-bottom:22px}.o{color:#5DCAA5}"
        "input{width:100%;padding:12px;border-radius:8px;border:1px solid #16324f;background:#07192E;color:#fff;margin-bottom:14px;box-sizing:border-box;font-size:15px}"
        "button{width:100%;padding:12px;border:0;border-radius:8px;background:#5DCAA5;color:#04342C;font-weight:700;font-size:15px;cursor:pointer}"
        "</style></head><body><form method=\"post\" action=\"/admin/login\">"
        "<div class=\"b\">T<span class=\"o\">O</span>T<span class=\"o\">O</span>R</div>"
        "<input type=\"password\" name=\"key\" placeholder=\"Clé admin\" autofocus autocomplete=\"current-password\">"
        "<button type=\"submit\">Entrer</button></form></body></html>"
    )


@app.post("/admin/login")
def admin_login(request: Request, key: str = Form("")):
    """Reçoit la clé dans le CORPS (jamais l'URL), pose le cookie (jeton dérivé, pas la
    clé en clair), redirige vers le dashboard. Limité en tentatives (anti-brute-force) :
    au-delà de 5 échecs, blocage temporaire. Clé fausse -> retour à la page de connexion."""
    ip = request.client.host if request.client else "?"
    now = datetime.utcnow()
    nb, bloque = _ADMIN_LOGIN_FAILS.get(ip, (0, None))
    if bloque and bloque > now:
        raise HTTPException(status_code=429, detail="Trop de tentatives. Réessaie plus tard.")
    expected = os.environ.get("ADMIN_STATS_KEY", "")
    if not expected or key != expected:
        nb += 1
        bloque = now + timedelta(minutes=_ADMIN_LOGIN_BLOCK_MIN) if nb >= _ADMIN_LOGIN_MAX else None
        _ADMIN_LOGIN_FAILS[ip] = (nb, bloque)
        return RedirectResponse(url="/admin", status_code=303)
    _ADMIN_LOGIN_FAILS.pop(ip, None)  # succès : on efface le compteur d'échecs
    resp = RedirectResponse(url="/admin/dashboard", status_code=303)
    _poser_cookie_admin(resp)
    return resp


@app.get("/admin/stats")
def admin_stats(request: Request, key: str = "", db: Session = Depends(get_db)):
    """Compteur privé (fondateur) : inscrits, abonnés payants, places Pionnier restantes.
    Auth par cookie (préféré) ou ?key= (rétro-compat). Sans auth -> 404."""
    if not _admin_authed(request, key):
        raise HTTPException(status_code=404, detail="Not found")
    inscrits, abonnes = _compter_stats(db)
    return {
        "inscrits": inscrits,
        "abonnes_payants": abonnes,
        "places_pionnier_restantes": billing.offre_pionnier(db)["pionnier_restantes"],  # compteur REEL (prix Pionnier, hors is_test)
    }


@app.get("/admin/trials", response_class=HTMLResponse)
def admin_trials(request: Request, key: str = "", db: Session = Depends(get_db)):
    """Suivi des essais gratuits (fondateur) : qui est en essai, date de fin, qui
    a annulé. Auth par cookie ou ?key=. Sans auth -> 404."""
    if not _admin_authed(request, key):
        raise HTTPException(status_code=404, detail="Not found")
    essais = billing.lister_essais(db)
    maintenant = datetime.utcnow()
    if essais:
        rows = ""
        for e in essais:
            fin = e["fin"]
            if fin:
                jours = (fin - maintenant).days
                fin_txt = f"{fin:%d/%m/%Y}"
                reste = "dernier jour" if jours <= 0 else (f"dans {jours} j" if jours > 1 else "demain")
            else:
                fin_txt, reste = "(inconnue)", ""
            if e["annulera"]:
                etat = "<span style='color:#F0A24B;font-weight:600;'>a annulé — à relancer</span>"
            else:
                etat = "<span style='color:#5DCAA5;'>se convertira en payant</span>"
            proche = " <span style='color:#6B7A8D;font-size:11px;'>(proche)</span>" if e["est_proche"] else ""
            rows += (f"<tr><td>{e['email']}{proche}</td><td>{e['source']}</td>"
                     f"<td>{fin_txt}<br><span style='color:#6B7A8D;font-size:11px;'>{reste}</span></td>"
                     f"<td>{etat}</td></tr>")
        corps = (f"<p class='sub'>{len(essais)} essai(s) en cours</p>"
                 "<table><thead><tr><th>Personne</th><th>Store</th><th>Fin de l'essai</th>"
                 "<th>État</th></tr></thead><tbody>" + rows + "</tbody></table>")
    else:
        corps = "<p class='sub'>Aucun essai gratuit en cours pour l'instant.</p>"
    html = f"""<!doctype html><html lang="fr"><head><meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Essais en cours — TOTOR</title>
    <style>
      body{{font-family:sans-serif;background:#07192E;color:#F8FAFC;margin:0;padding:28px 18px;}}
      .wrap{{max-width:720px;margin:0 auto;}}
      h1{{color:#5DCAA5;font-size:20px;margin:0 0 4px;}}
      .sub{{color:#9BB0C4;font-size:13px;margin:0 0 18px;}}
      table{{width:100%;border-collapse:collapse;font-size:13px;}}
      th{{text-align:left;color:#6B7A8D;font-weight:600;padding:8px 10px;border-bottom:1px solid rgba(255,255,255,.12);}}
      td{{padding:10px;border-bottom:1px solid rgba(255,255,255,.06);vertical-align:top;}}
      a{{color:#378ADD;}}
    </style></head><body><div class="wrap">
      <h1>Essais gratuits en cours</h1>
      {corps}
      <p style="margin-top:22px;"><a href="/admin/dashboard">← Retour au tableau de bord</a></p>
    </div></body></html>"""
    return HTMLResponse(html)


@app.get("/admin/ads-conversions.csv")
def admin_ads_conversions(request: Request, key: str = "", days: int = 90, db: Session = Depends(get_db)):
    """Export des conversions Google Ads — import HORS LIGNE (Chemin B, sans cookie).

    Deux conversions, UNIQUEMENT pour les comptes NON test qui portent un gclid :
      - « Inscription gratuite » : à la date de création du compte.
      - « Abonnement web »       : à la date d'un abonnement Stripe (web) actif.
    Les abonnements in-app (Apple/Android) n'ont pas de gclid → absents (limite assumée).

    À déposer dans Google Ads (Outils → Conversions → Importer). Ré-uploader la même
    période est sans risque : Google dédoublonne les conversions identiques
    (même gclid + même action + même heure). Sans auth → 404.
    """
    import csv, io
    # Accès : soit la clé admin (fondateur), soit la clé DÉDIÉE LECTURE SEULE
    # ADS_EXPORT_KEY (stockée chez Google pour l'import programmé). Cette clé
    # n'ouvre QUE ce CSV — jamais les stats, le dashboard ou le mark-test.
    _ads_key = os.environ.get("ADS_EXPORT_KEY", "")
    if not (_admin_authed(request, key) or (_ads_key and key == _ads_key)):
        raise HTTPException(status_code=404, detail="Not found")

    depuis = datetime.utcnow() - timedelta(days=max(1, min(days, 90)))  # fenêtre Google ≤ 90 j
    lignes = []  # (gclid, nom_action, horodatage_utc)

    for u in (db.query(User)
              .filter(User.gclid.isnot(None), User.is_test.is_(False), User.created_at >= depuis)
              .all()):
        lignes.append((u.gclid, "Inscription gratuite", u.created_at))

    for s, u in (db.query(Subscription, User)
                 .join(User, User.id == Subscription.user_id)
                 .filter(User.gclid.isnot(None), User.is_test.is_(False),
                         Subscription.source == "stripe", Subscription.status == "active",
                         Subscription.is_sandbox.isnot(True), Subscription.created_at >= depuis)
                 .all()):
        lignes.append((u.gclid, "Abonnement web", s.created_at))

    buf = io.StringIO()
    buf.write("Parameters:TimeZone=+0000\n")  # created_at est en UTC
    w = csv.writer(buf)
    w.writerow(["Google Click ID", "Conversion Name", "Conversion Time", "Conversion Value", "Conversion Currency"])
    for gclid, nom, quand in lignes:
        w.writerow([gclid, nom, quand.strftime("%Y-%m-%d %H:%M:%S"), "", "EUR"])

    return Response(content=buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": "attachment; filename=totor-ads-conversions.csv"})


class MarkTestRequest(BaseModel):
    key: str
    email: str
    test: bool = True


@app.post("/admin/mark-test")
def admin_mark_test(req: MarkTestRequest, db: Session = Depends(get_db)):
    """Marque (test=true) ou dé-marque (test=false) UN compte comme compte de test.
    Cible le compte par email EXACT (email unique) → jamais d'ambiguïté.
    POST : la clé et l'email sont dans le CORPS de la requête, JAMAIS dans l'URL,
    pour ne pas fuiter dans les logs (les logs d'accès ne contiennent que méthode
    + chemin, pas le corps). Protégé par ADMIN_STATS_KEY (sinon 404).
    Renvoie l'état du compte + les nouveaux chiffres du dashboard."""
    expected = os.environ.get("ADMIN_STATS_KEY", "")
    if not expected or req.key != expected:
        raise HTTPException(status_code=404, detail="Not found")
    u = db.query(User).filter(User.email == req.email).first()
    if not u:
        raise HTTPException(status_code=404, detail="Compte introuvable pour cet email")
    u.is_test = bool(req.test)
    db.commit()
    inscrits, abonnes = _compter_stats(db)
    return {
        "email": u.email,
        "is_test": u.is_test,
        "inscrits": inscrits,
        "abonnes_payants": abonnes,
        "places_pionnier_restantes": billing.offre_pionnier(db)["pionnier_restantes"],  # compteur REEL (prix Pionnier, hors is_test)
    }


_APPSTORE_CACHE = {"t": 0.0, "note": None, "nb": None}
_APPSTORE_TTL = 600  # 10 min : la note bouge lentement, inutile d'appeler Apple souvent


def _note_appstore():
    """Note App Store (moyenne, nb d'avis) via l'API iTunes publique. Cache 10 min,
    tolérant à la panne (renvoie la dernière valeur connue, jamais d'erreur)."""
    import time as _t
    now = _t.time()
    if now - _APPSTORE_CACHE["t"] < _APPSTORE_TTL and _APPSTORE_CACHE["note"] is not None:
        return _APPSTORE_CACHE["note"], _APPSTORE_CACHE["nb"]
    try:
        import requests as _rq
        d = _rq.get("https://itunes.apple.com/lookup?id=6789915732&country=fr", timeout=4).json()
        res = (d.get("results") or [{}])[0]
        _APPSTORE_CACHE.update({"t": now, "note": res.get("averageUserRating"), "nb": res.get("userRatingCount")})
    except Exception:
        pass  # Apple injoignable : on garde la dernière valeur connue
    return _APPSTORE_CACHE["note"], _APPSTORE_CACHE["nb"]


@app.get("/admin/dashboard", response_class=HTMLResponse)
def admin_dashboard(request: Request, key: str = "", db: Session = Depends(get_db)):
    """Tableau de bord fondateur (page HTML), charte TOTOR, rafraîchissement auto 60 s.
    Auth par COOKIE : l'URL et les rafraîchissements n'ont plus la clé. Un ancien
    marque-page avec ?key= pose le cookie puis redirige vers l'URL propre (la clé
    n'apparaît alors qu'UNE fois dans les logs). Sans auth -> page de connexion /admin."""
    expected = os.environ.get("ADMIN_STATS_KEY", "")
    # Ancien marque-page ?key= : on pose le cookie et on renvoie vers l'URL propre.
    if expected and key == expected and request.cookies.get(_ADMIN_COOKIE, "") != expected:
        resp = RedirectResponse(url="/admin/dashboard", status_code=303)
        _poser_cookie_admin(resp)
        return resp
    if not _admin_authed(request, key):
        return RedirectResponse(url="/admin", status_code=303)
    inscrits, abonnes = _compter_stats(db)
    # Transparence : combien de ces vrais payeurs sont des proches/VIP (comptes
    # marqués is_test qui ont néanmoins réellement payé). Le total les inclut.
    _, proches = billing.compter_abonnes_detail(db)
    proches_txt = (f" · dont {proches} proche" + ("s" if proches > 1 else "")) if proches else ""
    # Places Pionnier : le VRAI compteur (Pionniers vendus chez Stripe), cohérent
    # avec /admin/stats. Avant : `PIONNIER_PLACES - abonnes` comptait à tort CHAQUE
    # abonné (même non-Pionnier, ex. l'annuel Apple) comme une place prise → fausse
    # rareté, contre la Loi X pricing.
    places = billing.offre_pionnier(db)["pionnier_restantes"]
    pionniers_pris = max(0, PIONNIER_PLACES - places)
    pct = min(100, int(pionniers_pris * 100 / PIONNIER_PLACES)) if PIONNIER_PLACES else 0
    # Note App Store (moyenne + nb d'avis), tolérante à la panne.
    _note_as, _nb_as = _note_appstore()
    if _note_as:
        note_txt = (f"{float(_note_as):.1f}".replace(".", ",")) + " ★"
        nb_txt = (f"sur {_nb_as} avis" if _nb_as else "sur l'App Store")
    else:
        note_txt, nb_txt = "—", "pas encore de note"
    maj = datetime.utcnow().strftime("%d/%m/%Y a %H:%M UTC")
    html_page = f"""<!doctype html>
<html lang="fr"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="60">
<title>TOTOR - Tableau de bord</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ background:#07192E; color:#F8FAFC; font-family:-apple-system,Segoe UI,Roboto,sans-serif;
          min-height:100vh; padding:40px 20px; }}
  .wrap {{ max-width:900px; margin:0 auto; }}
  .brand {{ text-align:center; font-family:Georgia,'Times New Roman',serif; font-weight:800;
            font-size:38px; letter-spacing:1px; margin-bottom:4px; }}
  .brand .o {{ color:#5DCAA5; }}
  .sub {{ text-align:center; color:#9BB0C4; font-size:14px; margin-bottom:36px; }}
  .grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); gap:20px; }}
  .card {{ background:#0A2540; border:1px solid #16324f; border-radius:18px; padding:28px 24px; text-align:center; }}
  .label {{ color:#9BB0C4; font-size:13px; text-transform:uppercase; letter-spacing:1.5px; margin-bottom:12px; }}
  .num {{ font-family:Georgia,serif; font-weight:800; font-size:64px; line-height:1; color:#F8FAFC; }}
  .num.green {{ color:#5DCAA5; }}
  .hint {{ color:#6B8199; font-size:12px; margin-top:10px; }}
  .barwrap {{ background:#07192E; border-radius:99px; height:10px; margin-top:16px; overflow:hidden; }}
  .bar {{ background:#5DCAA5; height:100%; width:{pct}%; border-radius:99px; }}
  .foot {{ text-align:center; color:#6B8199; font-size:12px; margin-top:32px; }}
</style></head>
<body><div class="wrap">
  <div class="brand">T<span class="o">O</span>T<span class="o">O</span>R</div>
  <div class="sub">Tableau de bord fondateur</div>
  <div class="grid">
    <div class="card">
      <div class="label">Inscrits</div>
      <div class="num">{inscrits}</div>
      <div class="hint">comptes créés (gratuit + payant)</div>
    </div>
    <div class="card">
      <div class="label">Abonnés payants</div>
      <div class="num green">{abonnes}</div>
      <div class="hint">argent réel (Stripe, Apple, Google), hors sandbox{proches_txt}</div>
    </div>
    <div class="card">
      <div class="label">Places Pionnier</div>
      <div class="num">{places}</div>
      <div class="hint">restantes sur {PIONNIER_PLACES}</div>
      <div class="barwrap"><div class="bar"></div></div>
    </div>
    <div class="card">
      <div class="label">Note App Store</div>
      <div class="num green">{note_txt}</div>
      <div class="hint">{nb_txt}</div>
    </div>
  </div>
  <div class="foot">Mis à jour le {maj} · rafraîchissement automatique toutes les 60 s</div>
</div></body></html>"""
    return HTMLResponse(html_page)


def _login_verifier_blocage(db: Session, email: str):
    """Si trop d'échecs récents pour cet email, refuse temporairement (429)."""
    att = db.query(LoginAttempt).filter(LoginAttempt.email == email).first()
    if att and att.bloque_jusqua and att.bloque_jusqua > datetime.utcnow():
        reste = int((att.bloque_jusqua - datetime.utcnow()).total_seconds() // 60) + 1
        raise HTTPException(
            status_code=429,
            detail=f"Trop de tentatives. Réessaie dans {reste} minute(s), ou réinitialise ton mot de passe.",
        )
    return att


def _login_enregistrer_echec(db: Session, email: str, att):
    """Incrémente le compteur d'échecs ; au-delà du seuil, pose un blocage temporaire."""
    if not att:
        att = LoginAttempt(email=email, echecs=0)
        db.add(att)
    att.echecs = int(att.echecs or 0) + 1
    att.dernier_echec = datetime.utcnow()
    if att.echecs >= LOGIN_MAX_ECHECS:
        att.bloque_jusqua = datetime.utcnow() + timedelta(minutes=LOGIN_BLOCAGE_MINUTES)
        att.echecs = 0  # on repart à zéro après avoir posé le blocage
    db.commit()


def _login_reset(db: Session, email: str):
    """Réinitialise le compteur après une connexion réussie."""
    att = db.query(LoginAttempt).filter(LoginAttempt.email == email).first()
    if att:
        att.echecs = 0
        att.bloque_jusqua = None
        db.commit()


@app.post("/auth/login", response_model=AuthResponse)
def login(req: LoginRequest, db: Session = Depends(get_db)):
    email = (req.email or "").strip().lower()
    att = _login_verifier_blocage(db, email)
    user = db.query(User).filter(User.email == req.email).first()
    if not user or not user.password_hash or not verify_password(req.password, user.password_hash):
        _login_enregistrer_echec(db, email, att)
        raise HTTPException(status_code=401, detail="Email ou mot de passe incorrect")

    _login_reset(db, email)
    return AuthResponse(token=create_token(user.id), email=user.email)


@app.post("/auth/google", response_model=AuthResponse)
def auth_google(req: GoogleAuthRequest, db: Session = Depends(get_db)):
    if not GOOGLE_CLIENT_ID:
        raise HTTPException(status_code=500, detail="Connexion Google non configuree")

    try:
        payload = google_id_token.verify_oauth2_token(
            req.credential, google_requests.Request(), GOOGLE_CLIENT_ID
        )
    except ValueError:
        raise HTTPException(status_code=401, detail="Jeton Google invalide")

    email = payload.get("email")
    google_id = payload.get("sub")
    if not email or not google_id:
        raise HTTPException(status_code=401, detail="Reponse Google incomplete")

    user = db.query(User).filter(User.email == email).first()
    if not user:
        user = User(email=email, google_id=google_id, password_hash=None, gclid=(req.gclid or None))
        db.add(user)
        db.commit()
        db.refresh(user)
        # Alerte fondateur : nouvel inscrit (seulement à la CRÉATION, best-effort,
        # ne doit JAMAIS bloquer l'inscription).
        try:
            total_inscrits = db.query(User).count()
            send_founder_signup_alert(total_inscrits, user.email)
        except Exception:
            pass
    elif not user.google_id:
        user.google_id = google_id
        db.commit()

    return AuthResponse(token=create_token(user.id), email=user.email)


@app.post("/auth/apple", response_model=AuthResponse)
def auth_apple(req: AppleAuthRequest, db: Session = Depends(get_db)):
    """« Se connecter avec Apple » — l'app iPhone (Google refuse l'auth en WebView).

    Rattachement, dans cet ordre :
      1. par `apple_id` : la personne s'est deja connectee avec Apple ;
      2. par email : elle a deja un compte (web, Google, mot de passe) et
         a choisi « Partager mon email » -> on lie Apple a CE compte, pas de doublon ;
      3. sinon, nouveau compte.

    Si elle masque son email, l'etape 2 ne peut pas la reconnaitre (l'adresse de
    relais ne correspond a rien) : elle repart sur un compte vierge. L'ecran de
    connexion previent avant, c'est le seul garde-fou possible.

    Le `nonce` brut envoye par l'app est obligatoire (anti-rejeu) : voir
    apple_auth.verifier_identity_token.
    """
    try:
        infos = verifier_apple_identity_token(req.identity_token, req.nonce)
    except AppleTokenInvalide:
        raise HTTPException(status_code=401, detail="Jeton Apple invalide")

    apple_id = infos["apple_id"]
    email = infos["email"]

    user = db.query(User).filter(User.apple_id == apple_id).first()
    if user:
        return AuthResponse(token=create_token(user.id), email=user.email)

    if not email:
        # Premiere connexion sans email : Apple ne le transmet qu'a la toute
        # premiere autorisation. La personne a deja autorise TOTOR puis supprime
        # son compte -> elle doit retirer l'app dans Reglages > Compte Apple.
        raise HTTPException(
            status_code=401,
            detail="Apple ne nous a pas transmis ton email. Dans Reglages iPhone > "
                   "ton nom > Connexion avec Apple > TOTOR, choisis « Ne plus utiliser », "
                   "puis reessaie.",
        )

    user = db.query(User).filter(User.email == email).first()
    if user:
        user.apple_id = apple_id
        db.commit()
        return AuthResponse(token=create_token(user.id), email=user.email)

    user = User(
        email=email,
        apple_id=apple_id,
        password_hash=None,
        # Apple a deja verifie l'adresse : pas de bannière « verifie ton email ».
        email_verified=bool(infos["email_verified"]),
        gclid=(req.gclid or None),
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    # Alerte fondateur : nouvel inscrit (best-effort, ne bloque jamais l'inscription).
    try:
        send_founder_signup_alert(db.query(User).count(), user.email)
    except Exception:
        pass

    return AuthResponse(token=create_token(user.id), email=user.email)


# ----------------------------------------------------------------
# Mot de passe oublie / verification email
# ----------------------------------------------------------------

class ForgotPasswordRequest(BaseModel):
    email: EmailStr


@app.post("/auth/forgot-password")
def forgot_password(req: ForgotPasswordRequest, db: Session = Depends(get_db)):
    # On repond toujours {"ok": True}, meme si l'email n'existe pas en base,
    # pour ne jamais reveler quels emails ont un compte (securite).
    # Les comptes inscrits via Google (password_hash=None) recoivent AUSSI le lien :
    # il leur sert a CREER un mot de passe (indispensable pour l'app iPhone, qui n'a
    # pas de bouton Google). La connexion Google du web continue de marcher a cote.
    user = db.query(User).filter(User.email == req.email).first()
    if user:
        token = create_purpose_token(user.id, "reset_password", expire_minutes=60)
        send_reset_password_email(user.email, token)
    return {"ok": True}


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str


@app.post("/auth/reset-password")
def reset_password(req: ResetPasswordRequest, db: Session = Depends(get_db)):
    user_id = verify_purpose_token(req.token, "reset_password")
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Utilisateur introuvable")

    # Meme exigence que le changement de mot de passe connecte (8 caracteres mini).
    if len(req.new_password or "") < 8:
        raise HTTPException(status_code=400, detail="Le mot de passe doit contenir au moins 8 caractères.")
    user.password_hash = hash_password(req.new_password)
    db.commit()
    return {"ok": True}


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


@app.post("/auth/change-password")
def change_password(
    req: ChangePasswordRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # Compte Google : pas de mot de passe local à changer.
    if not user.password_hash:
        raise HTTPException(
            status_code=400,
            detail="Ton compte utilise la connexion Google — il n'y a pas de mot de passe à changer.",
        )
    # Le mot de passe ACTUEL est exigé (empêche un changement via une session volée).
    if not verify_password(req.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="Mot de passe actuel incorrect.")
    if len(req.new_password or "") < 8:
        raise HTTPException(status_code=400, detail="Le nouveau mot de passe doit contenir au moins 8 caractères.")
    user.password_hash = hash_password(req.new_password)
    db.commit()
    return {"ok": True}


@app.post("/auth/send-verification")
def send_verification(user: User = Depends(get_current_user)):
    if user.email_verified:
        return {"ok": True, "already_verified": True}
    token = create_purpose_token(user.id, "verify_email", expire_minutes=60 * 24)
    send_verification_email(user.email, token)
    return {"ok": True}


@app.get("/auth/verify-email")
def verify_email(token: str, db: Session = Depends(get_db)):
    user_id = verify_purpose_token(token, "verify_email")
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Utilisateur introuvable")

    user.email_verified = True
    db.commit()
    return {"ok": True}

@app.get("/profile")
def get_profile(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    profile = db.query(Profile).filter(Profile.user_id == user.id).first()
    if not profile:
        return {"onboarding_complete": False}

    prem = billing.is_premium(db, user)
    # Quotas freemium : exposés UNIQUEMENT pour les comptes gratuits (un abonné n'a pas de limite).
    # used = consommation du mois, limit = vraie valeur d'env (le front masque la jauge si limit > 100).
    # ⭐ Le chat se compte par CONVERSATION (fil 24h), jamais par message : la jauge lit etat_chat.
    if prem:
        quotas = None
    else:
        quotas = {
            t: {"used": billing.usage_this_month(db, user.id, t), "limit": billing.free_quota_for(t)}
            for t in ("doc_scan", "aem_scan")
        }
        etat_fils = quotas_freemium.etat_chat(db, user)
        quotas["chat"] = {"used": etat_fils["utilises"], "limit": etat_fils["limite"]}

    return {
        "statut": profile.statut,
        "activite": profile.activite,
        "periodicite": profile.periodicite,
        "acre": profile.acre,
        "versement_liberatoire": profile.versement_liberatoire,
        "date_creation_activite": profile.date_creation_activite,
        "onboarding_complete": profile.onboarding_complete,
        "walkthrough_vu": profile.walkthrough_vu,
        "siret": profile.siret,
        "raison_sociale": profile.raison_sociale,
        "adresse": profile.adresse,
        "prenom": profile.prenom,
        "nom": profile.nom,
        "telephone": profile.telephone,
        "entreprise": profile.entreprise,
        "depenses_mensuelles": profile.depenses_mensuelles,
        "solde_bancaire": profile.solde_bancaire,
        "reserve_securite": profile.reserve_securite,
        "tmi": profile.tmi,
        "relance_auto_jours": profile.relance_auto_jours,
        "salaire_reference": profile.salaire_reference,
        "heures_reference": profile.heures_reference,
        "annexe_allocation": profile.annexe_allocation,
        "email": user.email,
        "email_verified": user.email_verified,
        # true si le compte a un mot de passe local (false = connexion Google uniquement).
        # Sert à afficher ou masquer la section « changer mon mot de passe » des réglages.
        "has_password": bool(user.password_hash),
        # Rappel d'actualisation (email du 28) : actif par défaut, opt-out dans les Réglages.
        "rappel_actu_active": not bool(profile.rappel_actu_desactive),
        # Rappel de déclaration URSSAF (auto-entrepreneurs) : même philosophie.
        "rappel_urssaf_active": not bool(profile.rappel_urssaf_desactive),
        # Quotas de jours par employeur (intermittent technicien). Liste [{nom, quota}].
        "quotas_employeurs": _lire_quotas_employeurs(profile),
        "is_premium": prem,
        "premium_source": billing.premium_source(db, user),   # "stripe" | "comp" | None
        "trial_days_left": billing.trial_days_left(db, user),  # jours restants si essai Stripe (trialing), sinon None
        "quotas": quotas,
        # Paramètres TVA de facturation (table isolée fiscal_settings, fallback franchise).
        "fiscal_settings": _read_fiscal_settings(db, user.id),
    }


@app.post("/profile")
def set_profile(
    req: ProfileRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if req.statut not in STATUTS_DISPONIBLES and req.statut not in STATUTS_A_VENIR:
        raise HTTPException(status_code=400, detail="Statut inconnu")

    if req.statut == "auto_entrepreneur" and req.activite not in AUTO_ENTREPRENEUR_RATES:
        raise HTTPException(status_code=400, detail="Activite invalide pour ce statut")

    profile = db.query(Profile).filter(Profile.user_id == user.id).first()
    if not profile:
        profile = Profile(user_id=user.id)
        db.add(profile)

    profile.statut = req.statut
    profile.activite = req.activite
    profile.periodicite = req.periodicite
    profile.acre = req.acre
    profile.versement_liberatoire = req.versement_liberatoire
    profile.date_creation_activite = req.date_creation_activite
    profile.onboarding_complete = True

    db.commit()
    return {"ok": True}


@app.post("/profile/walkthrough-vu")
def marquer_walkthrough_vu(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """La visite guidee a ete vue : on le retient EN BASE, pas dans le navigateur.

    Sinon elle repartait a zero a chaque reinstallation de l'app ou changement
    de telephone, et la personne se la retapait comme une debutante.
    """
    profile = db.query(Profile).filter(Profile.user_id == user.id).first()
    if not profile:
        # Pas encore de profil : rien a retenir, la visite ne s'affiche qu'apres
        # l'onboarding. On ne cree pas de profil vide pour autant.
        return {"ok": True}
    profile.walkthrough_vu = True
    db.commit()
    return {"ok": True}


# ----------------------------------------------------------------
# Recherche SIRET (INSEE)
# ----------------------------------------------------------------

@app.get("/siret/lookup")
def siret_lookup(siret: str, user: User = Depends(get_current_user)):
    try:
        return lookup_siret(siret)
    except SiretLookupError as e:
        raise HTTPException(status_code=422, detail=str(e))


class SiretSaveRequest(BaseModel):
    siret: str
    raison_sociale: Optional[str] = None
    adresse: Optional[str] = None


@app.post("/profile/siret")
def save_siret(
    req: SiretSaveRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    profile = db.query(Profile).filter(Profile.user_id == user.id).first()
    if not profile:
        profile = Profile(user_id=user.id)
        db.add(profile)

    profile.siret = req.siret
    profile.raison_sociale = req.raison_sociale
    if req.adresse:
        profile.adresse = req.adresse

    db.commit()
    return {"ok": True}


class SoldeRequest(BaseModel):
    solde: Optional[float] = None


@app.post("/profile/solde")
def save_solde(
    req: SoldeRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    profile = db.query(Profile).filter(Profile.user_id == user.id).first()
    if not profile:
        profile = Profile(user_id=user.id)
        db.add(profile)

    profile.solde_bancaire = req.solde

    db.commit()
    return {"ok": True}


class RelanceAutoRequest(BaseModel):
    jours: Optional[int] = None  # None = relances automatiques désactivées


@app.post("/profile/relance-auto")
def save_relance_auto(
    req: RelanceAutoRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Active/désactive les relances automatiques d'impayés (opt-in explicite de l'utilisateur).
    Activer les relances est une fonction Premium ; les DÉSACTIVER (jours=None) reste toujours possible."""
    if req.jours is not None and not (1 <= req.jours <= 90):
        raise HTTPException(status_code=400, detail="Délai invalide (entre 1 et 90 jours)")
    if req.jours is not None and not billing.is_premium(db, user):
        raise HTTPException(status_code=402, detail={
            "code": "premium_requis",
            "fonction": "relance_auto",
            "message": "Les relances automatiques sont une fonction Premium. Laisse-moi réclamer tes impayés à ta place. 🔓",
        })
    profile = db.query(Profile).filter(Profile.user_id == user.id).first()
    if not profile:
        raise HTTPException(status_code=404, detail="Profil introuvable")
    profile.relance_auto_jours = req.jours
    db.commit()
    return {"relance_auto_jours": profile.relance_auto_jours}


class AvisRequest(BaseModel):
    message: str
    prenom: Optional[str] = None
    metier: Optional[str] = None
    consentement_publication: bool = False


@app.post("/avis")
def envoyer_avis(
    req: AvisRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Dépôt d'un avis/retour par l'utilisateur. Part par email à l'équipe (aucune table :
    Camille lit, trie, et publie sur le site UNIQUEMENT les avis avec consentement coché —
    le consentement est horodaté par l'email lui-même). Reply-To = l'utilisateur."""
    texte = (req.message or "").strip()
    if len(texte) < 10:
        raise HTTPException(status_code=400, detail="Dis-m'en un peu plus (10 caractères minimum).")
    if len(texte) > 2000:
        raise HTTPException(status_code=400, detail="Ton avis est trop long (2000 caractères max).")
    # Anti-abus : quelques envois par jour suffisent.
    _verifier_et_incrementer_quota_ia(db, user.id, "avis", 3)
    consent = "OUI — publiable avec prénom + métier" if req.consentement_publication else "NON — retour privé, ne pas publier"
    corps = f"""
    <div style="font-family: sans-serif; max-width: 520px; margin: 0 auto; color:#0A2540;">
      <h2>⭐ Nouvel avis d'un utilisateur</h2>
      <ul style="font-size:14px; line-height:1.7;">
        <li>De : {html.escape(user.email or "")}</li>
        <li>Prénom : {html.escape(req.prenom or "(non renseigné)")}</li>
        <li>Métier : {html.escape(req.metier or "(non renseigné)")}</li>
        <li><strong>Consentement publication : {consent}</strong></li>
      </ul>
      <blockquote style="border-left:3px solid #5DCAA5; margin:16px 0; padding:8px 16px; font-size:15px; line-height:1.6;">
        {html.escape(texte)}
      </blockquote>
      <p style="color:#6B7A8D; font-size:12px;">Réponds directement à ce mail pour remercier — le Reply-To est l'utilisateur.</p>
    </div>
    """
    if not send_email(SUPPORT_EMAIL, "⭐ TOTOR — nouvel avis utilisateur", corps, reply_to=user.email):
        raise HTTPException(status_code=502, detail="L'envoi a échoué. Réessaie dans un moment.")
    return {"ok": True}


class RappelActuRequest(BaseModel):
    active: bool


@app.post("/profile/rappel-actu")
def save_rappel_actu(
    req: RappelActuRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Active/désactive le rappel mensuel d'actualisation (email du 28).
    Jamais premium : couper un email doit toujours être gratuit et immédiat."""
    profile = db.query(Profile).filter(Profile.user_id == user.id).first()
    if not profile:
        raise HTTPException(status_code=404, detail="Profil introuvable")
    profile.rappel_actu_desactive = not req.active
    db.commit()
    return {"rappel_actu_active": req.active}


class RappelUrssafRequest(BaseModel):
    active: bool


@app.post("/profile/rappel-urssaf")
def save_rappel_urssaf(
    req: RappelUrssafRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Active/désactive le rappel d'échéance URSSAF (auto-entrepreneurs).
    Jamais premium : couper un email doit toujours être gratuit et immédiat."""
    profile = db.query(Profile).filter(Profile.user_id == user.id).first()
    if not profile:
        raise HTTPException(status_code=404, detail="Profil introuvable")
    profile.rappel_urssaf_desactive = not req.active
    db.commit()
    return {"rappel_urssaf_active": req.active}


# ─── Quotas de jours par employeur (intermittent technicien) ────────────────
def _lire_quotas_employeurs(profile: Profile) -> list:
    """Renvoie la liste [{nom, quota}] stockée en JSON, ou [] si absente/illisible."""
    if not profile or not profile.quotas_employeurs:
        return []
    try:
        data = json.loads(profile.quotas_employeurs)
        if isinstance(data, list):
            return [
                {"nom": str(e.get("nom", "")).strip(), "quota": int(e.get("quota"))}
                for e in data
                if isinstance(e, dict) and str(e.get("nom", "")).strip() and e.get("quota") is not None
            ]
    except (ValueError, TypeError):
        pass
    return []


class QuotaEmployeurRequest(BaseModel):
    nom: str
    quota: Optional[int] = None  # None ou 0 = on retire le quota de cet employeur


@app.post("/profile/quota-employeur")
def save_quota_employeur(
    req: QuotaEmployeurRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Ajoute, met à jour ou retire (quota None/0) le quota de jours d'un employeur.
    Les valeurs sont saisies par l'utilisateur (internes aux boîtes), jamais codées.
    La SURVEILLANCE des quotas est une fonction Premium (les jours comptés restent
    visibles pour tous : c'est la donnée de l'utilisateur, jamais verrouillée)."""
    profile = db.query(Profile).filter(Profile.user_id == user.id).first()
    if not profile:
        raise HTTPException(status_code=404, detail="Profil introuvable")
    quotas_freemium.verifier_surveillance_quotas_employeur(db, user)
    nom = (req.nom or "").strip()
    if not nom:
        raise HTTPException(status_code=400, detail="Nom d'employeur requis.")
    quotas = _lire_quotas_employeurs(profile)
    # On retire l'entrée existante pour ce nom (comparaison insensible à la casse), puis on la remet.
    quotas = [e for e in quotas if e["nom"].lower() != nom.lower()]
    if req.quota and req.quota > 0:
        quotas.append({"nom": nom, "quota": int(req.quota)})
    profile.quotas_employeurs = json.dumps(quotas, ensure_ascii=False)
    db.commit()
    return {"quotas_employeurs": quotas}


class SettingsRequest(BaseModel):
    reserve_securite: Optional[float] = None
    tmi: Optional[str] = None
    versement_liberatoire: Optional[bool] = None
    activite: Optional[str] = None


@app.post("/profile/settings")
def save_settings(
    req: SettingsRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    profile = db.query(Profile).filter(Profile.user_id == user.id).first()
    if not profile:
        profile = Profile(user_id=user.id)
        db.add(profile)

    if req.reserve_securite is not None:
        profile.reserve_securite = req.reserve_securite
    if req.tmi is not None:
        profile.tmi = req.tmi
    if req.versement_liberatoire is not None:
        profile.versement_liberatoire = req.versement_liberatoire
    if req.activite is not None:
        if req.activite not in ("vente", "services", "bnc"):
            raise HTTPException(status_code=400, detail="Activité inconnue")
        profile.activite = req.activite

    db.commit()
    return {"ok": True}


class ProfileDetailsRequest(BaseModel):
    prenom: Optional[str] = None
    nom: Optional[str] = None
    telephone: Optional[str] = None
    entreprise: Optional[str] = None
    adresse: Optional[str] = None
    depenses_mensuelles: Optional[float] = None


@app.post("/profile/details")
def save_profile_details(
    req: ProfileDetailsRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    profile = db.query(Profile).filter(Profile.user_id == user.id).first()
    if not profile:
        profile = Profile(user_id=user.id)
        db.add(profile)

    profile.prenom = req.prenom
    profile.nom = req.nom
    profile.telephone = req.telephone
    profile.entreprise = req.entreprise
    if req.adresse is not None:
        profile.adresse = req.adresse
    profile.depenses_mensuelles = req.depenses_mensuelles

    db.commit()
    return {"ok": True}


# ----------------------------------------------------------------
# Revenus (saisie libre, sans facture formelle)
# ----------------------------------------------------------------

@app.get("/income")
def list_income(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    entries = (
        db.query(IncomeEntry)
        .filter(IncomeEntry.user_id == user.id)
        .order_by(IncomeEntry.date.desc())
        .all()
    )
    return [
        {
            "id": e.id,
            "date": e.date,
            "amount": e.amount,
            "description": e.description,
            "source": e.source,
            "filename": e.filename,
        }
        for e in entries
    ]


@app.post("/income")
def add_income(
    req: IncomeRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    entry = IncomeEntry(
        user_id=user.id,
        date=req.date,
        amount=req.amount,
        description=req.description,
        source="manuel",
    )
    db.add(entry)
    db.commit()
    return {"ok": True, "id": entry.id}


@app.delete("/income/{income_id}")
def delete_income(
    income_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    entry = (
        db.query(IncomeEntry)
        .filter(IncomeEntry.id == income_id, IncomeEntry.user_id == user.id)
        .first()
    )
    if not entry:
        raise HTTPException(status_code=404, detail="Entree introuvable")
    db.delete(entry)
    db.commit()
    return {"ok": True}


@app.post("/income/extract")
async def extract_invoice(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    allowed_ext = (".pdf", ".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".webp")
    if not file.filename.lower().endswith(allowed_ext):
        raise HTTPException(status_code=400, detail="Format de fichier non supporte")

    # Quota freemium : scans factures + frais partagent le compteur mensuel "doc_scan".
    _consommer_quota(db, user, "doc_scan", AI_DOC_SCAN_DAILY_LIMIT)

    tmp_dir, file_path = _enregistrer_upload(file)
    try:
        data = extract_invoice_data(file_path)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Impossible de lire la facture : {e}")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    if data["amount"] is None:
        raise HTTPException(
            status_code=422,
            detail="Montant introuvable sur cette facture, merci de l'ajouter manuellement",
        )

    return {
        "amount": data["amount"],
        "date": data["date"].date().isoformat() if data["date"] else date.today().isoformat(),
        "filename": data["filename"],
        "client": data.get("client"),
        "description": data.get("description"),
        "numero_facture": data.get("numero_facture"),
        "tva_pct": data.get("tva_pct"),
    }


class IncomeConfirm(BaseModel):
    date: date
    amount: float
    client: Optional[str] = None
    description: Optional[str] = None
    numero_facture: Optional[str] = None
    filename: Optional[str] = None
    force: bool = False


@app.post("/income/confirm")
def confirm_invoice_income(
    payload: IncomeConfirm,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not payload.force:
        from datetime import timedelta
        candidats = (
            db.query(IncomeEntry)
            .filter(
                IncomeEntry.user_id == user.id,
                IncomeEntry.amount == payload.amount,
                IncomeEntry.date >= payload.date - timedelta(days=3),
                IncomeEntry.date <= payload.date + timedelta(days=3),
            )
            .all()
        )
        doublon = None
        for c in candidats:
            desc = (c.description or "").lower()
            numero_match = payload.numero_facture and payload.numero_facture.lower() in desc
            client_match = payload.client and payload.client.lower() in desc
            if numero_match or client_match or not payload.numero_facture:
                doublon = c
                if numero_match:
                    break
        if doublon:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "DOUBLON_POTENTIEL",
                    "message": "Facture potentiellement deja importee",
                    "existing_id": doublon.id,
                    "existing_date": doublon.date.isoformat(),
                    "existing_amount": doublon.amount,
                    "existing_description": doublon.description,
                },
            )

    desc_parts = []
    if payload.numero_facture:
        desc_parts.append(f"N. {payload.numero_facture}")
    if payload.client:
        desc_parts.append(f"Client : {payload.client}")
    if payload.description:
        desc_parts.append(payload.description)
    description = " - ".join(desc_parts) if desc_parts else (f"Facture importee : {payload.filename}" if payload.filename else None)

    entry = IncomeEntry(
        user_id=user.id,
        date=payload.date,
        amount=payload.amount,
        description=description,
        source="facture" if payload.filename else "manuel",
        filename=payload.filename,
    )
    db.add(entry)
    db.commit()
    return {"ok": True, "id": entry.id}


# ----------------------------------------------------------------
# Factures Clients
# Regle centrale : une facture n'alimente le CA que lorsqu'elle est "payee"
# ----------------------------------------------------------------

class FactureLigne(BaseModel):
    description: str = ""
    quantite: float = 1
    prix_unitaire: float = 0


class InvoiceCreateRequest(BaseModel):
    client_nom: str
    client_email: Optional[str] = None
    client_adresse: Optional[str] = None
    client_type: Optional[str] = None      # "particulier" | "professionnel"
    client_siret: Optional[str] = None
    client_tva: Optional[str] = None
    # "france" (défaut) | "ue" | "hors_ue" — client PRO à l'étranger (émetteur assujetti
    # uniquement) : TVA 0 % + mention art. 259-1 (+ Autoliquidation pour l'UE).
    client_localisation: Optional[str] = None
    date_emission: date
    date_echeance: Optional[date] = None
    lignes: list[FactureLigne] = []
    notes: Optional[str] = None
    statut: str = "brouillon"


class InvoiceUpdateRequest(BaseModel):
    client_nom: Optional[str] = None
    client_email: Optional[str] = None
    client_adresse: Optional[str] = None
    client_type: Optional[str] = None
    client_siret: Optional[str] = None
    client_tva: Optional[str] = None
    client_localisation: Optional[str] = None  # None = conserver la localisation figée
    date_emission: Optional[date] = None
    date_echeance: Optional[date] = None
    lignes: Optional[list[FactureLigne]] = None
    notes: Optional[str] = None


class InvoiceStatusRequest(BaseModel):
    statut: str


def _verifier_et_incrementer_quota_ia(db: Session, user_id: str, type_appel: str, limite: int):
    """
    Vérifie le quota IA du jour pour cet utilisateur et ce type d'appel.
    - Si la limite est atteinte : lève une HTTPException 429 (message Totor chaleureux).
    - Sinon : incrémente le compteur du jour et laisse passer.
    Borne le coût Anthropic. La ligne (user, jour, type) est créée à la volée.
    """
    aujourdhui = date.today()
    usage = (
        db.query(AIUsage)
        .filter(AIUsage.user_id == user_id, AIUsage.jour == aujourdhui, AIUsage.type_appel == type_appel)
        .first()
    )
    deja = int(usage.count) if usage else 0
    if deja >= limite:
        if type_appel == "aem_scan":
            msg = ("Tu as scanné beaucoup d'AEM aujourd'hui — je fais une petite pause pour rester raisonnable. "
                   "Réessaie demain, ou saisis cette activité à la main en attendant.")
        else:
            msg = ("On a beaucoup échangé aujourd'hui ! Je me repose un peu et je reviens en pleine forme demain. "
                   "En attendant, le reste de l'app fonctionne normalement.")
        raise HTTPException(status_code=429, detail=msg)

    if usage:
        usage.count = deja + 1
        usage.updated_at = datetime.utcnow()
    else:
        db.add(AIUsage(user_id=user_id, jour=aujourdhui, type_appel=type_appel, count=1))
    db.commit()


def _consommer_quota(db: Session, user: User, type_appel: str, limite_jour: int):
    """Applique le quota freemium d'une fonction IA/scan.

    - Premium (is_premium == True) : seul le garde-fou anti-abus JOURNALIER s'applique.
    - Gratuit : limite MENSUELLE d'abord (402 si atteinte → le front propose le Premium),
      puis le même garde-fou journalier.
    ⭐ CHAT : le quota gratuit se compte par CONVERSATION (fil ≈ 24h), jamais par
    message — les questions de précision de Totor ne consomment rien (PRICING.md).
    Le compteur de MESSAGES du jour reste incrémenté (suivi de coût + anti-abus).
    is_premium() reste la SEULE source de vérité.
    """
    if not billing.is_premium(db, user):
        if type_appel == "chat":
            quotas_freemium.consommer_fil_chat(db, user)
        else:
            deja_ce_mois = billing.usage_this_month(db, user.id, type_appel)
            if deja_ce_mois >= billing.free_quota_for(type_appel):
                raise HTTPException(
                    status_code=402,
                    detail={
                        "code": "quota_gratuit_atteint",
                        "fonction": type_appel,
                        "message": "Tu as atteint ton quota gratuit du mois pour cette fonction. "
                                   "Passe en Premium pour la débloquer.",
                    },
                )
    _verifier_et_incrementer_quota_ia(db, user.id, type_appel, limite_jour)


def _montant_lignes(lignes: list) -> float:
    total = 0.0
    for l in lignes or []:
        q = l.get("quantite", 0) if isinstance(l, dict) else l.quantite
        p = l.get("prix_unitaire", 0) if isinstance(l, dict) else l.prix_unitaire
        total += (q or 0) * (p or 0)
    return round(total, 2)


def _client_fields(client_type, client_siret, client_tva) -> dict:
    """
    Normalise les champs client d'une facture/devis. Un PROFESSIONNEL garde son
    SIRET / n° TVA (facultatifs) ; un PARTICULIER les ignore (forcés à None), même
    s'ils sont envoyés. Type inconnu / absent → « particulier ».
    """
    ctype = client_type if client_type in ("particulier", "professionnel") else "particulier"
    if ctype != "professionnel":
        return {"client_type": "particulier", "client_siret": None, "client_tva": None}
    return {
        "client_type": "professionnel",
        "client_siret": (client_siret or "").strip() or None,
        "client_tva": (client_tva or "").strip() or None,
    }


def _next_numero(db: Session, user_id: str) -> str:
    year = date.today().year
    numeros = [r[0] for r in db.query(ClientInvoice.numero).filter(ClientInvoice.user_id == user_id).all()]
    fs = db.query(FiscalSettings).filter(FiscalSettings.user_id == user_id).first()
    floor = fs.facture_numero_depart if fs else None
    return compute_next_numero("F", year, numeros, floor)


def _invoice_to_dict(inv: ClientInvoice) -> dict:
    return {
        "id": inv.id,
        "numero": inv.numero,
        "client_nom": inv.client_nom,
        "client_email": inv.client_email,
        "client_adresse": inv.client_adresse,
        "client_type": inv.client_type,
        "client_siret": inv.client_siret,
        "client_tva": inv.client_tva,
        "date_emission": inv.date_emission,
        "date_echeance": inv.date_echeance,
        "date_paiement": inv.date_paiement,
        "montant": inv.montant,
        "statut": inv.statut,
        "relance_envoyee_le": inv.relance_envoyee_le,
        "lignes": inv.lignes,
        "notes": inv.notes,
        # Régime TVA figé (snapshot). NULL → le front applique le fallback franchise.
        "vat_mode": inv.vat_mode,
        "vat_rate": inv.vat_rate,
        "vat_number": inv.vat_number,
        "client_localisation": _localisation_de(inv),
        # Paiement en ligne : jeton du lien public + prélèvement SEPA en attente.
        "payment_token": inv.payment_token,
        "paiement_en_cours": bool(inv.paiement_en_cours),
        "solde_integre": bool(inv.solde_integre),
    }


@app.get("/invoices")
def list_invoices(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    invoices = (
        db.query(ClientInvoice)
        .filter(ClientInvoice.user_id == user.id)
        .order_by(ClientInvoice.date_emission.desc())
        .all()
    )
    return [_invoice_to_dict(inv) for inv in invoices]


@app.get("/invoices/summary")
def invoices_summary(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    invoices = db.query(ClientInvoice).filter(ClientInvoice.user_id == user.id).all()
    facture_total = sum(i.montant for i in invoices)
    paye_total = sum(i.montant for i in invoices if i.statut == "payee")
    en_attente_total = sum(i.montant for i in invoices if i.statut in ("envoyee", "brouillon"))
    impayees_montant = sum(i.montant for i in invoices if i.statut == "impayee")
    impayees_count = sum(1 for i in invoices if i.statut == "impayee")
    return {
        "facture_total": round(facture_total, 2),
        "paye_total": round(paye_total, 2),
        "en_attente_total": round(en_attente_total + impayees_montant, 2),
        "impayees_count": impayees_count,
    }


@app.post("/invoices")
def create_invoice(
    req: InvoiceCreateRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # Freemium 1.0.1 : 5 créations par mois en gratuit. Jamais rétroactif :
    # les factures existantes restent consultables, modifiables et envoyables.
    quotas_freemium.verifier_creation_document(db, user, "facture")
    if req.statut not in STATUTS_FACTURE:
        raise HTTPException(status_code=400, detail="Statut de facture inconnu")

    lignes_dicts = [l.dict() for l in req.lignes]
    montant = _montant_lignes(lignes_dicts)
    cf = _client_fields(req.client_type, req.client_siret, req.client_tva)

    inv = ClientInvoice(
        user_id=user.id,
        numero=_next_numero(db, user.id),
        client_nom=req.client_nom,
        client_email=req.client_email,
        client_adresse=req.client_adresse,
        client_type=cf["client_type"],
        client_siret=cf["client_siret"],
        client_tva=cf["client_tva"],
        date_emission=req.date_emission,
        date_echeance=req.date_echeance,
        montant=montant,
        statut=req.statut,
        date_paiement=date.today() if req.statut == "payee" else None,
        lignes=lignes_dicts,
        notes=req.notes,
    )
    _verifier_localisation(req.client_localisation, cf["client_tva"])
    _snapshot_fiscal(inv, db, user.id, req.client_localisation)   # fige le régime TVA courant sur la facture
    db.add(inv)
    db.commit()
    db.refresh(inv)
    return _invoice_to_dict(inv)


@app.put("/invoices/{invoice_id}")
def update_invoice(
    invoice_id: str,
    req: InvoiceUpdateRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    inv = db.query(ClientInvoice).filter(ClientInvoice.id == invoice_id, ClientInvoice.user_id == user.id).first()
    if not inv:
        raise HTTPException(status_code=404, detail="Facture introuvable")

    if req.client_nom is not None:
        inv.client_nom = req.client_nom
    if req.client_email is not None:
        inv.client_email = req.client_email
    if req.client_adresse is not None:
        inv.client_adresse = req.client_adresse
    # Type client fourni → on (re)normalise : passer en particulier efface SIRET/TVA.
    if req.client_type is not None:
        cf = _client_fields(req.client_type, req.client_siret, req.client_tva)
        inv.client_type = cf["client_type"]
        inv.client_siret = cf["client_siret"]
        inv.client_tva = cf["client_tva"]
    if req.date_emission is not None:
        inv.date_emission = req.date_emission
    if req.date_echeance is not None:
        inv.date_echeance = req.date_echeance
    if req.notes is not None:
        inv.notes = req.notes
    if req.lignes is not None:
        lignes_dicts = [l.dict() for l in req.lignes]
        inv.lignes = lignes_dicts
        inv.montant = _montant_lignes(lignes_dicts)

    # Tant que la facture est un BROUILLON, son régime suit le réglage courant.
    # Une fois émise (Envoyée/Payée), elle n'est plus modifiable ici → snapshot intact.
    if inv.statut == "brouillon":
        loc = req.client_localisation if req.client_localisation is not None else _localisation_de(inv)
        _verifier_localisation(loc, inv.client_tva)
        _snapshot_fiscal(inv, db, user.id, loc)

    db.commit()
    db.refresh(inv)
    return _invoice_to_dict(inv)


@app.patch("/invoices/{invoice_id}/status")
def update_invoice_status(
    invoice_id: str,
    req: InvoiceStatusRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if req.statut not in STATUTS_FACTURE:
        raise HTTPException(status_code=400, detail="Statut de facture inconnu")

    inv = db.query(ClientInvoice).filter(ClientInvoice.id == invoice_id, ClientInvoice.user_id == user.id).first()
    if not inv:
        raise HTTPException(status_code=404, detail="Facture introuvable")

    was_brouillon = inv.statut == "brouillon"
    inv.statut = req.statut
    if req.statut == "payee" and not inv.date_paiement:
        inv.date_paiement = date.today()
    elif req.statut != "payee":
        inv.date_paiement = None

    # Émission (sortie de brouillon) : on fige définitivement le régime TVA du moment.
    # La localisation client déjà figée est conservée (garde-fou UE sans n° TVA inclus).
    if was_brouillon and req.statut != "brouillon":
        _verifier_localisation(_localisation_de(inv), inv.client_tva)
        _snapshot_fiscal(inv, db, user.id)

    db.commit()
    db.refresh(inv)
    return _invoice_to_dict(inv)


def _invoice_ttc(inv: ClientInvoice) -> float:
    """TTC encaissé d'une facture, calculé sur son régime TVA FIGÉ (snapshot).
    En franchise : TTC = HT. En assujetti : TTC = HT × (1 + taux). montant reste le HT."""
    return compute_invoice_totals(inv.montant, resolve_fiscal_settings(inv))["ttc"]


@app.post("/invoices/{invoice_id}/integrate-solde")
def integrate_invoice_solde(
    invoice_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Ajoute le TTC encaissé d'une facture PAYÉE au solde bancaire (incrément), sur action
    EXPLICITE de l'utilisateur. Idempotent : refuse si déjà intégrée. N'affecte JAMAIS le
    CA URSSAF (/estimate lit `montant` = HT, jamais le solde).
    """
    inv = db.query(ClientInvoice).filter(ClientInvoice.id == invoice_id, ClientInvoice.user_id == user.id).first()
    if not inv:
        raise HTTPException(status_code=404, detail="Facture introuvable")
    if inv.statut != "payee":
        raise HTTPException(status_code=400, detail="La facture doit être payée pour être ajoutée au solde")
    if inv.solde_integre:
        raise HTTPException(status_code=409, detail="Cette facture a déjà été ajoutée au solde")

    profile = db.query(Profile).filter(Profile.user_id == user.id).first()
    if not profile:
        raise HTTPException(status_code=400, detail="Profil introuvable")

    ttc = _invoice_ttc(inv)
    profile.solde_bancaire = round((profile.solde_bancaire or 0) + ttc, 2)
    inv.solde_integre = True
    db.commit()
    return {"solde_bancaire": profile.solde_bancaire, "montant_ajoute": ttc}


@app.post("/invoices/{invoice_id}/retire-solde")
def retire_invoice_solde(
    invoice_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Retire du solde le TTC d'une facture précédemment intégrée (ex. on la dé-paie), sur
    action EXPLICITE. Idempotent : refuse si pas intégrée. Le TTC vient du snapshot figé,
    donc le retrait égale exactement l'ajout.
    """
    inv = db.query(ClientInvoice).filter(ClientInvoice.id == invoice_id, ClientInvoice.user_id == user.id).first()
    if not inv:
        raise HTTPException(status_code=404, detail="Facture introuvable")
    if not inv.solde_integre:
        raise HTTPException(status_code=409, detail="Cette facture n'a pas été ajoutée au solde")

    profile = db.query(Profile).filter(Profile.user_id == user.id).first()
    if not profile:
        raise HTTPException(status_code=400, detail="Profil introuvable")

    ttc = _invoice_ttc(inv)
    profile.solde_bancaire = round((profile.solde_bancaire or 0) - ttc, 2)
    inv.solde_integre = False
    db.commit()
    return {"solde_bancaire": profile.solde_bancaire, "montant_retire": ttc}


@app.delete("/invoices/{invoice_id}")
def delete_invoice(
    invoice_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    inv = db.query(ClientInvoice).filter(ClientInvoice.id == invoice_id, ClientInvoice.user_id == user.id).first()
    if not inv:
        raise HTTPException(status_code=404, detail="Facture introuvable")
    db.delete(inv)
    db.commit()
    return {"ok": True}


def _build_emitter_info(profile: Optional[Profile]) -> dict:
    if not profile:
        return {"nom": None, "adresse": None, "siret": None, "mention": None}
    # Nom émetteur : nom commercial saisi, sinon raison sociale (lookup SIRET/INSEE écrit
    # `raison_sociale`, pas `entreprise`), sinon « Prénom Nom ».
    nom = profile.entreprise or profile.raison_sociale or f"{profile.prenom or ''} {profile.nom or ''}".strip() or None
    # Mention légale « EI » obligatoire pour les entrepreneurs individuels (auto-entrepreneurs).
    nom = append_ei_mention(nom, profile.statut)
    mention = (
        "Auto-entrepreneur, dispensé d'immatriculation au RCS et au RM"
        if profile.statut == "auto_entrepreneur" else None
    )
    return {"nom": nom, "adresse": profile.adresse, "siret": profile.siret, "mention": mention}


def _read_fiscal_settings(db: Session, user_id: str) -> dict:
    """
    Lecture des paramètres fiscaux de facturation d'un utilisateur, avec fallback
    franchise si aucune ligne n'existe. Réservé à la FACTURATION (les moteurs
    fiscaux n'y touchent jamais).
    """
    row = db.query(FiscalSettings).filter(FiscalSettings.user_id == user_id).first()
    return resolve_fiscal_settings(row)


def _localisation_de(obj) -> str:
    """Localisation client déduite du snapshot d'un document : "france" | "ue" | "hors_ue"."""
    if obj.vat_mode == ASSUJETTI_UE:
        return "ue"
    if obj.vat_mode == ASSUJETTI_EXPORT:
        return "hors_ue"
    return "france"


def _snapshot_fiscal(obj, db: Session, user_id: str, client_localisation: Optional[str] = None) -> None:
    """
    Fige sur la facture/devis `obj` le régime TVA COURANT de l'utilisateur.
    Appelé à la création et tant que le document est en brouillon ; au passage à
    « émise » (Envoyée/Payée), c'est ce snapshot qui devient définitif et immuable.

    `client_localisation` ("france" | "ue" | "hors_ue") : cas du client PRO à
    l'étranger, décidé DOCUMENT PAR DOCUMENT (jamais dans les réglages). Si None
    (appels de re-snapshot : changement de statut, envoi), on CONSERVE la
    localisation déjà figée sur le document — sinon l'émission l'écraserait.
    Ne s'applique qu'à un émetteur assujetti : en franchise, la 293 B couvre tout.
    """
    if client_localisation is None:
        client_localisation = _localisation_de(obj)
    f = _read_fiscal_settings(db, user_id)
    obj.vat_mode = f["vat_mode"]
    obj.vat_rate = f["vat_rate"]
    obj.vat_number = f["vat_number"]
    if f["vat_mode"] == ASSUJETTI and client_localisation in ("ue", "hors_ue"):
        obj.vat_mode = ASSUJETTI_UE if client_localisation == "ue" else ASSUJETTI_EXPORT
        obj.vat_rate = 0.0


def _verifier_localisation(client_localisation: Optional[str], client_tva: Optional[str]) -> None:
    """Garde-fous du cas étranger : valeur connue, et n° TVA client obligatoire pour l'UE
    (sans lui, la mention « Autoliquidation » serait irrégulière)."""
    if client_localisation not in (None, "france", "ue", "hors_ue"):
        raise HTTPException(status_code=400, detail="Localisation client inconnue.")
    if client_localisation == "ue" and not (client_tva or "").strip():
        raise HTTPException(
            status_code=400,
            detail="Pour un client professionnel dans l'UE, son n° de TVA intracommunautaire est obligatoire (autoliquidation).",
        )


class FiscalRequest(BaseModel):
    vat_mode: str = "franchise"
    vat_rate: Optional[float] = None
    vat_number: Optional[str] = None


@app.post("/profile/fiscal")
def save_fiscal_settings(
    req: FiscalRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Écrit les paramètres TVA dans la table ISOLÉE fiscal_settings (upsert sur user_id).
    N'écrit JAMAIS dans Profile. Sans effet sur le moteur fiscal / le CA URSSAF.
    """
    if req.vat_mode not in ("franchise", "assujetti"):
        raise HTTPException(status_code=400, detail="Mode TVA inconnu")

    row = db.query(FiscalSettings).filter(FiscalSettings.user_id == user.id).first()
    if not row:
        row = FiscalSettings(user_id=user.id)
        db.add(row)

    row.vat_mode = req.vat_mode
    if req.vat_rate is not None:
        row.vat_rate = req.vat_rate
    row.vat_number = (req.vat_number or "").strip() or None

    db.commit()
    db.refresh(row)
    return resolve_fiscal_settings(row)


class FactureNumeroRequest(BaseModel):
    facture_numero_depart: Optional[str] = None  # "42" ou "F-2026-042" ; vide/None = pas de reprise


@app.post("/profile/facture-numero")
def save_facture_numero_depart(
    req: FactureNumeroRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Définit le PLANCHER de numérotation des factures (reprise d'une séquence existante).
    N'édite PAS les factures existantes ; sert seulement de point de départ au générateur.
    Endpoint dédié (séparé de la TVA) pour ne jamais l'effacer par mégarde.
    """
    row = db.query(FiscalSettings).filter(FiscalSettings.user_id == user.id).first()
    if not row:
        row = FiscalSettings(user_id=user.id)
        db.add(row)

    try:
        row.facture_numero_depart = normalize_numero_depart(req.facture_numero_depart, date.today().year)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    db.commit()
    db.refresh(row)
    return resolve_fiscal_settings(row)


@app.get("/invoices/{invoice_id}/pdf")
def get_invoice_pdf(
    invoice_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    inv = db.query(ClientInvoice).filter(ClientInvoice.id == invoice_id, ClientInvoice.user_id == user.id).first()
    if not inv:
        raise HTTPException(status_code=404, detail="Facture introuvable")

    profile = db.query(Profile).filter(Profile.user_id == user.id).first()
    emitter = _build_emitter_info(profile)
    # Régime TVA FIGÉ sur la facture (snapshot) — pas le réglage courant. NULL → franchise.
    fiscal = resolve_fiscal_settings(inv)

    try:
        pdf_bytes = generate_invoice_pdf(_invoice_to_dict(inv), emitter, fiscal)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur lors de la generation du PDF : {e}")

    filename = f"facture-{inv.numero}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


@app.get("/quotes/{quote_id}/pdf")
def get_quote_pdf(
    quote_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    q = db.query(Quote).filter(Quote.id == quote_id, Quote.user_id == user.id).first()
    if not q:
        raise HTTPException(status_code=404, detail="Devis introuvable")

    profile = db.query(Profile).filter(Profile.user_id == user.id).first()
    emitter = _build_emitter_info(profile)
    # Régime TVA FIGÉ sur le devis (snapshot) — même immuabilité que la facture.
    fiscal = resolve_fiscal_settings(q)

    try:
        pdf_bytes = generate_invoice_pdf(_quote_to_dict(q), emitter, fiscal, kind="devis")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur lors de la generation du PDF : {e}")

    filename = f"devis-{q.numero}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


class SendInvoiceRequest(BaseModel):
    emitter_nom: Optional[str] = None
    emitter_adresse: Optional[str] = None
    emitter_siret: Optional[str] = None
    message: Optional[str] = None


def _build_invoice_email_html(inv: ClientInvoice, req: "SendInvoiceRequest", fiscal: dict = None,
                              afficher_bloc_emetteur: bool = True) -> str:
    """
    afficher_bloc_emetteur : le bloc grisé d'identification (nom, adresse, SIRET).
    Utile sur l'envoi de facture ; sur une RELANCE, le message signe déjà en toutes
    lettres (« Bien à vous, Prénom Nom, ENTREPRISE ») → le bloc doublonnerait.
    """
    # Tout ce qui vient de l'utilisateur est échappé avant d'entrer dans le HTML
    # de l'email envoyé au client (anti-injection / anti-phishing).
    e = lambda v: html.escape(str(v)) if v is not None else ""

    # Totaux d'affichage (inv.montant reste le HT). En assujetti : ligne TVA + TTC, pas de 293 B.
    totals = compute_invoice_totals(inv.montant, fiscal, inv.date_emission)
    if totals["mode"] == "assujetti":
        totaux_html = (
            '<div style="text-align:right; margin-top:16px; font-size:13px; color:#0A2540;">'
            f'Total HT : {totals["ht"]:.2f} €<br/>'
            f'TVA ({format_vat_rate(totals["rate"])} %) : {totals["tva"]:.2f} €<br/>'
            f'<span style="font-size:16px; font-weight:700;">Total TTC : {totals["ttc"]:.2f} €</span></div>'
        )
        mention_html = ""
    else:
        totaux_html = (
            '<div style="text-align:right; margin-top:16px; font-size:16px; font-weight:700; color:#0A2540;">'
            f'Total TTC : {totals["ttc"]:.2f} €</div>'
        )
        mention_html = f'<p style="color:#8BA5C0; font-size:11px; margin-top:24px;">{e(totals["mention"])}</p>'
    vat_html = f'<br/>N° TVA : {e(totals["vat_number"])}' if totals.get("vat_number") else ""

    lignes_html = ""
    for l in (inv.lignes or []):
        desc = l.get("description", "") if isinstance(l, dict) else l.description
        qte = l.get("quantite", 0) if isinstance(l, dict) else l.quantite
        pu = l.get("prix_unitaire", 0) if isinstance(l, dict) else l.prix_unitaire
        total_ligne = (qte or 0) * (pu or 0)
        lignes_html += f"""
        <tr>
          <td style="padding:8px 0; border-bottom:1px solid #EEF2F7;">{e(desc)}</td>
          <td style="padding:8px 0; border-bottom:1px solid #EEF2F7; text-align:center;">{e(qte)}</td>
          <td style="padding:8px 0; border-bottom:1px solid #EEF2F7; text-align:right;">{pu:.2f} €</td>
          <td style="padding:8px 0; border-bottom:1px solid #EEF2F7; text-align:right; font-weight:600;">{total_ligne:.2f} €</td>
        </tr>"""

    # Les sauts de ligne du message deviennent de vrais retours dans l'email
    # (sinon « Bonjour X, » et la suite se retrouvent collés sur une ligne).
    message_html = (
        f'<p style="color:#3D4452; line-height:1.6;">{e(req.message).replace(chr(10), "<br/>")}</p>'
        if req.message else ""
    )
    echeance_html = (
        f'<p style="color:#6B7A8D; font-size:13px;">Échéance : {inv.date_echeance.strftime("%d/%m/%Y")}</p>'
        if inv.date_echeance else ""
    )

    bloc_emetteur_html = (
        f'''<div style="background:#F7F9F5; border-radius:10px; padding:16px; margin:16px 0; font-size:13px; color:#5B6573;">
        <strong>{e(req.emitter_nom)}</strong><br/>
        {e(req.emitter_adresse)}<br/>
        {f"SIRET : {e(req.emitter_siret)}" if req.emitter_siret else ""}{vat_html}
      </div>'''
        if afficher_bloc_emetteur else ""
    )

    return f"""
    <div style="font-family: sans-serif; max-width: 560px; margin: 0 auto;">
      <h2 style="color:#0A2540;">Facture {e(inv.numero)}</h2>
      {message_html}
      {bloc_emetteur_html}
      <p style="color:#6B7A8D; font-size:13px;">
        Émise le {inv.date_emission.strftime("%d/%m/%Y")} — destinée à {e(inv.client_nom)}
      </p>
      {echeance_html}
      <table style="width:100%; border-collapse:collapse; margin-top:16px; font-size:14px;">
        <thead>
          <tr style="color:#6B7A8D; font-size:12px; text-align:left;">
            <th style="padding-bottom:8px;">Description</th>
            <th style="padding-bottom:8px; text-align:center;">Qté</th>
            <th style="padding-bottom:8px; text-align:right;">PU</th>
            <th style="padding-bottom:8px; text-align:right;">Total</th>
          </tr>
        </thead>
        <tbody>{lignes_html}</tbody>
      </table>
      {totaux_html}
      {mention_html}
      {f'<p style="color:#8BA5C0; font-size:11px; margin-top:8px;">{e(get_b2b_late_fee_mention(inv.client_type))}</p>' if get_b2b_late_fee_mention(inv.client_type) else ""}
      {f'<p style="color:#6B7A8D; font-size:12px;">{e(inv.notes)}</p>' if inv.notes else ""}
      {(
        f'<div style="text-align:center; margin:26px 0 8px;">'
        f'<a href="{SIGNATURE_BASE_URL}/paiement/{inv.payment_token}" '
        f'style="display:inline-block; background:#378ADD; color:#ffffff; text-decoration:none; '
        f'font-weight:700; font-size:15px; padding:13px 26px; border-radius:10px;">'
        f'Payer en ligne</a>'
        f'<p style="color:#8BA5C0; font-size:11px; margin-top:10px;">Carte ou prélèvement SEPA. '
        f'Paiement sécurisé par Stripe. TOTOR ne détient jamais les fonds.</p></div>'
      ) if (inv.payment_token and inv.statut != "payee") else ""}
    </div>
    """


@app.post("/invoices/{invoice_id}/send")
def send_invoice(
    invoice_id: str,
    req: SendInvoiceRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    inv = db.query(ClientInvoice).filter(ClientInvoice.id == invoice_id, ClientInvoice.user_id == user.id).first()
    if not inv:
        raise HTTPException(status_code=404, detail="Facture introuvable")
    if not inv.client_email:
        raise HTTPException(status_code=400, detail="Aucun email client renseigne sur cette facture")

    # Émetteur de l'email : on PRÉSERVE un nom éventuellement fourni par le front (en lui
    # ajoutant la mention « EI ») ; s'il est vide, on le dérive du profil serveur (repli sur
    # raison_sociale + EI), comme le PDF. Aucune saisie n'est jamais écrasée.
    profile = db.query(Profile).filter(Profile.user_id == user.id).first()
    req.emitter_nom = (
        append_ei_mention(req.emitter_nom, profile.statut if profile else None)
        if req.emitter_nom else _build_emitter_info(profile)["nom"]
    )

    # Envoi = émission : si c'était un brouillon, on fige le régime TVA du moment AVANT
    # de construire l'email. Une facture déjà émise garde son snapshot (immuable).
    if inv.statut == "brouillon":
        _snapshot_fiscal(inv, db, user.id)
    # Paiement en ligne : si l'utilisateur a un compte d'encaissement Stripe, la
    # facture reçoit son jeton public (bouton « Payer en ligne » dans l'email).
    # La page publique re-vérifie l'état réel du compte au moment du clic.
    if not inv.payment_token and inv.statut != "payee" and _compte_encaissement_de(db, user.id):
        inv.payment_token = secrets.token_urlsafe(24)
    fiscal = resolve_fiscal_settings(inv)
    html = _build_invoice_email_html(inv, req, fiscal)
    # Expéditeur = le nom de l'utilisateur (signature du profil, repli sur le nom émetteur) ;
    # Reply-To = son email. Même logique que les relances automatiques : le client final
    # reçoit un mail de la personne qu'il connaît, et peut lui répondre directement.
    ok = send_invoice_email(
        inv.client_email, f"Facture {inv.numero}", html,
        from_name=_signature_relance(profile) or req.emitter_nom,
        reply_to=user.email,
    )
    if not ok:
        raise HTTPException(status_code=502, detail="Erreur lors de l'envoi de l'email")

    if inv.statut == "brouillon":
        inv.statut = "envoyee"
    db.commit()
    db.refresh(inv)
    return _invoice_to_dict(inv)


# ----------------------------------------------------------------
# Relances automatiques d'impayés — OPT-IN par utilisateur (OFF par défaut).
# L'utilisateur décide la RÈGLE une fois (profile.relance_auto_jours) ; Totor l'applique
# et le montre (relance_envoyee_le visible sur la facture). Garde-fous :
#   - une facture n'est JAMAIS relancée deux fois automatiquement ;
#   - uniquement les factures envoyées/impayées avec un email client ;
#   - mode "dry" par défaut : on journalise ce qui SERAIT envoyé, sans rien envoyer.
#     Passer RELANCES_AUTO_MODE=live sur Railway pour armer réellement.
# ----------------------------------------------------------------
RELANCES_AUTO_MODE = os.environ.get("RELANCES_AUTO_MODE", "dry")


def _signature_relance(profile: Profile) -> Optional[str]:
    """
    Signature de l'utilisateur pour les mails envoyés à SES clients :
    « Prénom Nom, ENTREPRISE » (l'entreprise seule en repli). None si le profil
    ne permet aucune signature → la relance de ce profil ne doit PAS partir.
    """
    if not profile:
        return None
    nom_personne = f"{profile.prenom or ''} {profile.nom or ''}".strip()
    entreprise = (profile.entreprise or "").strip()
    if nom_personne and entreprise:
        return f"{nom_personne}, {entreprise}"
    return nom_personne or entreprise or None


def _message_relance_auto(inv: ClientInvoice, signature: str) -> str:
    """Relance destinée au CLIENT de l'utilisateur : registre professionnel, vouvoiement."""
    salut = f"Bonjour {inv.client_nom}," if inv.client_nom else "Bonjour,"
    retard = (date.today() - inv.date_echeance).days if inv.date_echeance else 0
    montant = f"{inv.montant:,.2f}".replace(",", " ").replace(".", ",")
    echeance = inv.date_echeance.strftime("%d/%m/%Y") if inv.date_echeance else ""
    mention_retard = f" ({retard} jour{'s' if retard > 1 else ''} de retard à ce jour)" if retard > 0 else ""
    return (
        f"{salut}\n\n"
        f"Je me permets de revenir vers vous concernant la facture {inv.numero or ''} "
        f"d'un montant de {montant} €, arrivée à échéance le {echeance}{mention_retard}.\n\n"
        f"Pourriez-vous me confirmer la date de règlement prévue ? "
        f"Si le paiement a déjà été effectué, merci d'ignorer ce message.\n\n"
        f"Bien à vous,\n{signature}"
    )


def _executer_relances_auto():
    db = SessionLocal()
    try:
        profils = db.query(Profile).filter(Profile.relance_auto_jours.isnot(None)).all()
        print(f"[relances] passe demarree (mode {RELANCES_AUTO_MODE}) — {len(profils)} profil(s) opt-in", flush=True)
        for profile in profils:
            # Garde-fou signature : sans nom ni entreprise au profil, aucune relance ne
            # part pour ce profil (un mail anonyme ferait plus de mal que l'impayé).
            signature = _signature_relance(profile)
            if not signature:
                print(f"[relances] profil {profile.user_id} sans signature — relance suspendue", flush=True)
                continue
            utilisateur = db.query(User).filter(User.id == profile.user_id).first()
            # Relances = fonction Premium : on ne relance PAS les comptes gratuits
            # (défense en profondeur, même si un profil avait activé l'option avant/hors premium).
            if not utilisateur or not billing.is_premium(db, utilisateur):
                continue
            email_reponse = utilisateur.email
            seuil = date.today() - timedelta(days=profile.relance_auto_jours)
            factures = (
                db.query(ClientInvoice)
                .filter(
                    ClientInvoice.user_id == profile.user_id,
                    ClientInvoice.statut.in_(["envoyee", "impayee"]),
                    ClientInvoice.client_email.isnot(None),
                    ClientInvoice.relance_envoyee_le.is_(None),
                    ClientInvoice.date_echeance.isnot(None),
                    ClientInvoice.date_echeance <= seuil,
                )
                .all()
            )
            for inv in factures:
                retard = (date.today() - inv.date_echeance).days
                if RELANCES_AUTO_MODE != "live":
                    print(f"[relances][repetition] AURAIT relance la facture {inv.numero} "
                          f"({inv.client_email}, {retard}j de retard) — mode dry, rien d'envoye", flush=True)
                    continue
                try:
                    emetteur = _build_emitter_info(profile)["nom"]
                    req = SendInvoiceRequest(emitter_nom=emetteur, message=_message_relance_auto(inv, signature))
                    fiscal = resolve_fiscal_settings(inv)
                    # Pas de bloc émetteur grisé : la relance signe déjà en toutes lettres.
                    html_mail = _build_invoice_email_html(inv, req, fiscal, afficher_bloc_emetteur=False)
                    # Expéditeur = le nom de l'utilisateur (adresse technique inchangée,
                    # DMARC en place) ; Reply-To = son email, pour que le client réponde
                    # directement à la bonne personne.
                    if send_invoice_email(inv.client_email, f"Relance — Facture {inv.numero}", html_mail,
                                          from_name=signature, reply_to=email_reponse):
                        inv.relance_envoyee_le = datetime.utcnow()
                        db.commit()
                        print(f"[relances] relance envoyee : facture {inv.numero} -> {inv.client_email}")
                    else:
                        print(f"[relances] echec d'envoi pour la facture {inv.numero} (on retentera au prochain passage)")
                except Exception as e:
                    db.rollback()
                    print(f"[relances] erreur sur la facture {inv.numero}: {e}")
    except Exception as e:
        print(f"[relances] erreur globale: {e}")
    finally:
        db.close()


# ─── Rappel d'actualisation France Travail (intermittents) ──────────────────
# Le 28 du mois, la fenêtre d'actualisation ouvre. TOTOR envoie UN email de rappel
# par mois et par intermittent (dédup : profiles.dernier_rappel_actu = "AAAA-MM").
# L'email ne contient AUCUN chiffre calculé (pas d'heures, pas de brut) : uniquement
# des faits (nombre de contrats, d'employeurs) et l'invitation à ouvrir TOTOR, qui
# reste la seule source des chiffres. Mode "dry" par défaut (logs sans envoi),
# passer RAPPELS_ACTU_MODE=live sur Railway pour armer.
RAPPELS_ACTU_MODE = os.environ.get("RAPPELS_ACTU_MODE", "dry")
FT_ESPACE_URL = "https://candidat.francetravail.fr/espacepersonnel/"

# En-tête commun des emails de rappel : la tête d'Hector + le logotype TOTOR
# (image hébergée sur le frontend — URL absolue obligatoire dans un email).
EMAIL_ENTETE_TOTOR = """
      <div style="text-align:center; margin:8px 0 20px;">
        <img src="https://www.montotor.fr/hector-tete.png" alt="TOTOR" width="64" height="64"
             style="border-radius:50%; display:inline-block;" />
        <div style="font-size:20px; font-weight:800; letter-spacing:3px; color:#0A2540; margin-top:6px;">
          T<span style="color:#5DCAA5">O</span>T<span style="color:#5DCAA5">O</span>R
        </div>
      </div>
"""
MOIS_FR_NOMS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet",
                "août", "septembre", "octobre", "novembre", "décembre"]


def _html_rappel_actu(mois_nom: str, nb_contrats: int, nb_employeurs: int) -> str:
    frontend = os.environ.get("FRONTEND_URL", "https://www.montotor.fr")
    if nb_contrats > 0:
        resume = (f"J'ai <strong>{nb_contrats} contrat{'s' if nb_contrats > 1 else ''}</strong> "
                  f"chez <strong>{nb_employeurs} employeur{'s' if nb_employeurs > 1 else ''}</strong> "
                  f"enregistré{'s' if nb_contrats > 1 else ''} pour {mois_nom}. "
                  "Ton récap complet (heures, brut, employeurs) t'attend dans TOTOR, prêt à recopier.")
    else:
        resume = (f"Je n'ai aucun contrat enregistré pour {mois_nom}. "
                  "Rappel important : même un mois sans cachet, l'actualisation reste "
                  "obligatoire pour garder tes droits.")
    return f"""
    <div style="font-family: sans-serif; max-width: 480px; margin: 0 auto; color:#0A2540;">
      {EMAIL_ENTETE_TOTOR}
      <h2 style="color:#0A2540;">🐾 C'est le moment de t'actualiser</h2>
      <p>Salut, c'est Totor. La fenêtre d'actualisation France Travail de <strong>{mois_nom}</strong>
      vient d'ouvrir (elle ferme vers le 15 du mois prochain).</p>
      <p>{resume}</p>
      <p style="margin:24px 0;">
        <a href="{frontend}" style="background:#5DCAA5; color:#04342C; padding:12px 20px;
           border-radius:8px; text-decoration:none; display:inline-block; font-weight:bold;">
          Ouvrir TOTOR
        </a>
        &nbsp;&nbsp;
        <a href="{FT_ESPACE_URL}" style="color:#378ADD; text-decoration:underline;">
          Aller sur France Travail
        </a>
      </p>
      <p style="color:#6B7A8D; font-size:13px;">
        C'est toujours toi qui valides sur France Travail : je prépare, tu recopies, tu restes aux commandes.
      </p>
      <p style="color:#6B7A8D; font-size:12px; border-top:1px solid #e5e9f0; padding-top:12px;">
        Tu reçois ce rappel une fois par mois parce que tu utilises TOTOR.
        Tu peux le couper à tout moment dans TOTOR, Réglages → Rappel d'actualisation
        (ou en répondant à ce mail).
      </p>
    </div>
    """


def _executer_rappels_actualisation():
    aujourdhui = date.today()
    # La fenêtre ouvre le 28 (26 en février côté FT ; on garde le 28, simple et sûr).
    # RAPPELS_ACTU_FORCE=1 : ignore la date (tests avant le 28, en mode dry).
    if aujourdhui.day < 28 and os.environ.get("RAPPELS_ACTU_FORCE") != "1":
        return
    cle_mois = aujourdhui.strftime("%Y-%m")
    mois_nom = MOIS_FR_NOMS[aujourdhui.month - 1] + " " + str(aujourdhui.year)
    db = SessionLocal()
    try:
        profils = db.query(Profile).filter(Profile.statut == "intermittent").all()
        print(f"[rappels-actu] passe (mode {RAPPELS_ACTU_MODE}) — {len(profils)} intermittent(s), fenêtre {cle_mois}", flush=True)
        for profile in profils:
            if profile.dernier_rappel_actu == cle_mois:
                continue  # déjà rappelé ce mois-ci
            if profile.rappel_actu_desactive:
                continue  # opt-out explicite dans les Réglages
            utilisateur = db.query(User).filter(User.id == profile.user_id).first()
            if not utilisateur or not utilisateur.email:
                continue
            # Éligibilité : email vérifié OU au moins une activité enregistrée (preuve d'un
            # compte réellement utilisé — décision Camille 08/07 : les testeurs n'ont presque
            # jamais cliqué le lien de vérification, on ne les prive pas du rappel pour ça).
            a_deja_une_activite = (
                db.query(IntermittentActivity.id)
                .filter(IntermittentActivity.user_id == profile.user_id)
                .first()
                is not None
            )
            if not utilisateur.email_verified and not a_deja_une_activite:
                continue
            debut_mois = aujourdhui.replace(day=1)
            activites = (
                db.query(IntermittentActivity)
                .filter(
                    IntermittentActivity.user_id == profile.user_id,
                    IntermittentActivity.date >= debut_mois,
                    IntermittentActivity.date <= aujourdhui,
                )
                .all()
            )
            nb_contrats = len(activites)
            nb_employeurs = len({(a.employeur or "").strip().lower() for a in activites if (a.employeur or "").strip()})
            if RAPPELS_ACTU_MODE != "live":
                print(f"[rappels-actu][repetition] AURAIT rappelé {utilisateur.email} "
                      f"({nb_contrats} contrat(s), {nb_employeurs} employeur(s)) — mode dry, rien d'envoyé", flush=True)
                continue
            try:
                html = _html_rappel_actu(mois_nom, nb_contrats, nb_employeurs)
                if send_email(utilisateur.email, f"🐾 Ton actualisation de {mois_nom} est ouverte", html,
                              reply_to=SUPPORT_EMAIL):
                    profile.dernier_rappel_actu = cle_mois
                    db.commit()
                    print(f"[rappels-actu] rappel envoyé à {utilisateur.email}", flush=True)
                else:
                    print(f"[rappels-actu] échec d'envoi pour {utilisateur.email} (on retentera au prochain passage)", flush=True)
            except Exception as e:
                db.rollback()
                print(f"[rappels-actu] erreur pour {profile.user_id}: {e}", flush=True)
    except Exception as e:
        print(f"[rappels-actu] erreur globale: {e}", flush=True)
    finally:
        db.close()


# ─── Rappel d'échéance URSSAF (auto-entrepreneurs) ──────────────────────────
# Miroir du rappel d'actualisation : présence au bon moment, jamais de chiffre
# calculé dans l'email (le chiffre exact vit dans TOTOR). Un email par échéance
# (dédup : profiles.dernier_rappel_urssaf = "AAAA-MM" du mois d'échéance), envoyé
# à partir du 20 du mois d'échéance. Mode "dry" par défaut, RAPPELS_URSSAF_MODE=live
# sur Railway pour armer. Opt-out : Réglages → Rappel URSSAF.
RAPPELS_URSSAF_MODE = os.environ.get("RAPPELS_URSSAF_MODE", "dry")
URSSAF_AE_URL = "https://www.autoentrepreneur.urssaf.fr"


def _html_rappel_urssaf(periode_label: str, date_limite: date) -> str:
    frontend = os.environ.get("FRONTEND_URL", "https://www.montotor.fr")
    limite_txt = f"{date_limite.day} {MOIS_FR_NOMS[date_limite.month - 1]}"
    return f"""
    <div style="font-family: sans-serif; max-width: 480px; margin: 0 auto; color:#0A2540;">
      {EMAIL_ENTETE_TOTOR}
      <h2 style="color:#0A2540;">🐾 Ta déclaration URSSAF approche</h2>
      <p>Salut, c'est Totor. Ta déclaration de <strong>{periode_label}</strong> est à faire
      au plus tard le <strong>{limite_txt}</strong>.</p>
      <p>Ton chiffre exact à recopier t'attend dans TOTOR, préparé à partir des encaissements
      que tu m'as confiés. Deux minutes, pas plus.</p>
      <p style="margin:24px 0;">
        <a href="{frontend}" style="background:#5DCAA5; color:#04342C; padding:12px 20px;
           border-radius:8px; text-decoration:none; display:inline-block; font-weight:bold;">
          Ouvrir TOTOR
        </a>
        &nbsp;&nbsp;
        <a href="{URSSAF_AE_URL}" style="color:#378ADD; text-decoration:underline;">
          Aller sur l'URSSAF
        </a>
      </p>
      <p style="color:#6B7A8D; font-size:13px;">
        C'est toujours toi qui déclares sur le site officiel : je prépare, tu recopies, tu restes aux commandes.
      </p>
      <p style="color:#6B7A8D; font-size:12px; border-top:1px solid #e5e9f0; padding-top:12px;">
        Tu reçois ce rappel à chaque échéance parce que tu utilises TOTOR.
        Tu peux le couper à tout moment dans TOTOR, Réglages → Rappel URSSAF
        (ou en répondant à ce mail).
      </p>
    </div>
    """


def _executer_rappels_urssaf():
    aujourdhui = date.today()
    # On rappelle en fin de fenêtre (à partir du 20), quand la déclaration devient urgente.
    # RAPPELS_URSSAF_FORCE=1 : ignore la date (tests en mode dry).
    if aujourdhui.day < 20 and os.environ.get("RAPPELS_URSSAF_FORCE") != "1":
        return
    cle_echeance = aujourdhui.strftime("%Y-%m")
    db = SessionLocal()
    try:
        profils = db.query(Profile).filter(Profile.statut == "auto_entrepreneur").all()
        print(f"[rappels-urssaf] passe (mode {RAPPELS_URSSAF_MODE}) — {len(profils)} AE, échéance {cle_echeance}", flush=True)
        for profile in profils:
            if profile.dernier_rappel_urssaf == cle_echeance:
                continue  # déjà rappelé pour cette échéance
            if profile.rappel_urssaf_desactive:
                continue  # opt-out explicite dans les Réglages
            periode = periode_urssaf_a_declarer(aujourdhui, profile.periodicite or "mensuelle")
            if not periode:
                continue  # trimestriel hors mois d'échéance
            utilisateur = db.query(User).filter(User.id == profile.user_id).first()
            if not utilisateur or not utilisateur.email:
                continue
            # Éligibilité (même décision que le rappel d'actualisation, 08/07) :
            # email vérifié OU un compte réellement utilisé (au moins un encaissement
            # ou une facture).
            compte_utilise = (
                db.query(IncomeEntry.id).filter(IncomeEntry.user_id == profile.user_id).first() is not None
                or db.query(ClientInvoice.id).filter(ClientInvoice.user_id == profile.user_id).first() is not None
            )
            if not utilisateur.email_verified and not compte_utilise:
                continue
            label, date_limite = periode
            if RAPPELS_URSSAF_MODE != "live":
                print(f"[rappels-urssaf][repetition] AURAIT rappelé {utilisateur.email} "
                      f"({label}, limite {date_limite}) — mode dry, rien d'envoyé", flush=True)
                continue
            try:
                html_mail = _html_rappel_urssaf(label, date_limite)
                if send_email(utilisateur.email, f"🐾 Ta déclaration URSSAF de {label} approche", html_mail,
                              reply_to=SUPPORT_EMAIL):
                    profile.dernier_rappel_urssaf = cle_echeance
                    db.commit()
                    print(f"[rappels-urssaf] rappel envoyé à {utilisateur.email}", flush=True)
                else:
                    print(f"[rappels-urssaf] échec d'envoi pour {utilisateur.email} (on retentera au prochain passage)", flush=True)
            except Exception as e:
                db.rollback()
                print(f"[rappels-urssaf] erreur pour {profile.user_id}: {e}", flush=True)
    except Exception as e:
        print(f"[rappels-urssaf] erreur globale: {e}", flush=True)
    finally:
        db.close()


_ESSAIS_ALERTES = set()  # dédup en mémoire : "email|AAAA-MM-JJ" déjà signalés au fondateur


def _executer_alerte_essais_fin():
    """Prévient le fondateur quand un essai gratuit arrive à ~2 jours de sa fin,
    pour qu'il puisse relancer avant la bascule payante. Dédup en mémoire (une
    seule alerte par essai ; au pire un doublon après un redéploiement)."""
    from datetime import datetime as _dt, timedelta as _td
    db = SessionLocal()
    try:
        maintenant = _dt.utcnow()
        limite = maintenant + _td(days=2)
        bientot = []
        for e in billing.lister_essais(db):
            fin = e.get("fin")
            if fin is None or not (maintenant < fin <= limite):
                continue
            cle = f"{e['email']}|{fin.date().isoformat()}"
            if cle in _ESSAIS_ALERTES:
                continue
            bientot.append((cle, e))
        if bientot:
            if send_founder_trial_ending_alert([e for _, e in bientot]):
                for cle, _ in bientot:
                    _ESSAIS_ALERTES.add(cle)
                print(f"[essais-fin] alerte envoyée pour {len(bientot)} essai(s)", flush=True)
            else:
                print("[essais-fin] échec d'envoi (on retentera au prochain passage)", flush=True)
    except Exception as e:
        print(f"[essais-fin] erreur globale: {e}", flush=True)
    finally:
        db.close()


@app.on_event("startup")
async def _demarrer_relances_auto():
    async def boucle():
        await asyncio.sleep(120)  # laisser l'app finir de démarrer
        while True:
            await asyncio.to_thread(_executer_relances_auto)
            await asyncio.to_thread(_executer_rappels_actualisation)
            await asyncio.to_thread(_executer_rappels_urssaf)
            # Alerte fondateur : essais gratuits (stores) bientôt à échéance.
            await asyncio.to_thread(_executer_alerte_essais_fin)
            # Sauvegarde quotidienne de la base vers R2 (dédupliquée par jour).
            await asyncio.to_thread(sauvegarde.executer_sauvegarde_quotidienne)
            await asyncio.sleep(6 * 3600)  # 4 passages par jour, dédupliqués en base
    asyncio.create_task(boucle())


# ----------------------------------------------------------------
# Devis
# ----------------------------------------------------------------

class QuoteCreateRequest(BaseModel):
    client_nom: str
    client_email: Optional[str] = None
    client_adresse: Optional[str] = None
    client_type: Optional[str] = None
    client_siret: Optional[str] = None
    client_tva: Optional[str] = None
    client_localisation: Optional[str] = None  # "france" | "ue" | "hors_ue" (cf. factures)
    date_emission: date
    date_validite: Optional[date] = None
    lignes: list[FactureLigne] = []
    notes: Optional[str] = None
    statut: str = "brouillon"


class QuoteUpdateRequest(BaseModel):
    client_nom: Optional[str] = None
    client_email: Optional[str] = None
    client_adresse: Optional[str] = None
    client_type: Optional[str] = None
    client_siret: Optional[str] = None
    client_tva: Optional[str] = None
    client_localisation: Optional[str] = None  # None = conserver la localisation figée
    date_emission: Optional[date] = None
    date_validite: Optional[date] = None
    lignes: Optional[list[FactureLigne]] = None
    notes: Optional[str] = None


class QuoteStatusRequest(BaseModel):
    statut: str


def _next_numero_devis(db: Session, user_id: str) -> str:
    # Devis : générateur robuste (max+1 + anti-doublon), SANS plancher de départ
    # (la reprise de séquence ne concerne que les factures, document fiscal).
    year = date.today().year
    numeros = [r[0] for r in db.query(Quote.numero).filter(Quote.user_id == user_id).all()]
    return compute_next_numero("D", year, numeros)


def _quote_to_dict(q: Quote) -> dict:
    return {
        "id": q.id,
        "numero": q.numero,
        "client_nom": q.client_nom,
        "client_email": q.client_email,
        "client_adresse": q.client_adresse,
        "client_type": q.client_type,
        "client_siret": q.client_siret,
        "client_tva": q.client_tva,
        "date_emission": q.date_emission,
        "date_validite": q.date_validite,
        "montant": q.montant,
        "statut": q.statut,
        "lignes": q.lignes,
        "notes": q.notes,
        "converted_invoice_id": q.converted_invoice_id,
        "vat_mode": q.vat_mode,
        "vat_rate": q.vat_rate,
        "vat_number": q.vat_number,
        "client_localisation": _localisation_de(q),
        # Signature en ligne : le jeton sert au lien d'acceptation (app + email),
        # signe_le/signe_email prouvent l'acceptation dans la vue détail.
        "signature_token": q.signature_token,
        "signe_le": q.signe_le,
        "signe_email": q.signe_email,
    }


@app.get("/quotes")
def list_quotes(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    quotes = (
        db.query(Quote)
        .filter(Quote.user_id == user.id)
        .order_by(Quote.date_emission.desc())
        .all()
    )
    return [_quote_to_dict(q) for q in quotes]


@app.get("/quotes/summary")
def quotes_summary(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    quotes = db.query(Quote).filter(Quote.user_id == user.id).all()
    total = sum(q.montant for q in quotes)
    accepte_total = sum(q.montant for q in quotes if q.statut == "accepte")
    en_attente_total = sum(q.montant for q in quotes if q.statut in ("brouillon", "envoye"))
    envoyes = sum(1 for q in quotes if q.statut in ("envoye", "accepte", "refuse", "expire"))
    acceptes = sum(1 for q in quotes if q.statut == "accepte")
    taux_conversion = round((acceptes / envoyes) * 100) if envoyes > 0 else None
    return {
        "total": round(total, 2),
        "accepte_total": round(accepte_total, 2),
        "en_attente_total": round(en_attente_total, 2),
        "taux_conversion": taux_conversion,
    }


@app.post("/quotes")
def create_quote(
    req: QuoteCreateRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # Freemium 1.0.1 : 5 créations par mois en gratuit (jamais rétroactif).
    quotas_freemium.verifier_creation_document(db, user, "devis")
    if req.statut not in STATUTS_DEVIS:
        raise HTTPException(status_code=400, detail="Statut de devis inconnu")

    lignes_dicts = [l.dict() for l in req.lignes]
    montant = _montant_lignes(lignes_dicts)
    cf = _client_fields(req.client_type, req.client_siret, req.client_tva)

    q = Quote(
        user_id=user.id,
        numero=_next_numero_devis(db, user.id),
        client_nom=req.client_nom,
        client_email=req.client_email,
        client_adresse=req.client_adresse,
        client_type=cf["client_type"],
        client_siret=cf["client_siret"],
        client_tva=cf["client_tva"],
        date_emission=req.date_emission,
        date_validite=req.date_validite,
        montant=montant,
        statut=req.statut,
        lignes=lignes_dicts,
        notes=req.notes,
    )
    _verifier_localisation(req.client_localisation, cf["client_tva"])
    _snapshot_fiscal(q, db, user.id, req.client_localisation)   # fige le régime TVA courant sur le devis
    db.add(q)
    db.commit()
    db.refresh(q)
    return _quote_to_dict(q)


@app.put("/quotes/{quote_id}")
def update_quote(
    quote_id: str,
    req: QuoteUpdateRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    q = db.query(Quote).filter(Quote.id == quote_id, Quote.user_id == user.id).first()
    if not q:
        raise HTTPException(status_code=404, detail="Devis introuvable")

    if req.client_nom is not None:
        q.client_nom = req.client_nom
    if req.client_email is not None:
        q.client_email = req.client_email
    if req.client_adresse is not None:
        q.client_adresse = req.client_adresse
    if req.client_type is not None:
        cf = _client_fields(req.client_type, req.client_siret, req.client_tva)
        q.client_type = cf["client_type"]
        q.client_siret = cf["client_siret"]
        q.client_tva = cf["client_tva"]
    if req.date_emission is not None:
        q.date_emission = req.date_emission
    if req.date_validite is not None:
        q.date_validite = req.date_validite
    if req.notes is not None:
        q.notes = req.notes
    if req.lignes is not None:
        lignes_dicts = [l.dict() for l in req.lignes]
        q.lignes = lignes_dicts
        q.montant = _montant_lignes(lignes_dicts)

    # Tant que le devis est un brouillon, son régime suit le réglage courant.
    if q.statut == "brouillon":
        loc = req.client_localisation if req.client_localisation is not None else _localisation_de(q)
        _verifier_localisation(loc, q.client_tva)
        _snapshot_fiscal(q, db, user.id, loc)

    db.commit()
    db.refresh(q)
    return _quote_to_dict(q)


@app.patch("/quotes/{quote_id}/status")
def update_quote_status(
    quote_id: str,
    req: QuoteStatusRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if req.statut not in STATUTS_DEVIS:
        raise HTTPException(status_code=400, detail="Statut de devis inconnu")

    q = db.query(Quote).filter(Quote.id == quote_id, Quote.user_id == user.id).first()
    if not q:
        raise HTTPException(status_code=404, detail="Devis introuvable")

    was_brouillon = q.statut == "brouillon"
    q.statut = req.statut
    # Émission (sortie de brouillon) : on fige définitivement le régime TVA du moment.
    if was_brouillon and req.statut != "brouillon":
        _verifier_localisation(_localisation_de(q), q.client_tva)
        _snapshot_fiscal(q, db, user.id)
    db.commit()
    db.refresh(q)
    return _quote_to_dict(q)


@app.delete("/quotes/{quote_id}")
def delete_quote(
    quote_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    q = db.query(Quote).filter(Quote.id == quote_id, Quote.user_id == user.id).first()
    if not q:
        raise HTTPException(status_code=404, detail="Devis introuvable")
    db.delete(q)
    db.commit()
    return {"ok": True}


def _build_quote_email_html(q: Quote, req: "SendInvoiceRequest", fiscal: dict = None) -> str:
    e = lambda v: html.escape(str(v)) if v is not None else ""

    # Totaux d'affichage (q.montant reste le HT). En assujetti : ligne TVA + TTC, pas de 293 B.
    totals = compute_invoice_totals(q.montant, fiscal, q.date_emission)
    if totals["mode"] == "assujetti":
        totaux_html = (
            '<div style="text-align:right; margin-top:16px; font-size:13px; color:#0A2540;">'
            f'Total HT : {totals["ht"]:.2f} €<br/>'
            f'TVA ({format_vat_rate(totals["rate"])} %) : {totals["tva"]:.2f} €<br/>'
            f'<span style="font-size:16px; font-weight:700;">Total TTC : {totals["ttc"]:.2f} €</span></div>'
        )
        mention_html = ""
    else:
        totaux_html = (
            '<div style="text-align:right; margin-top:16px; font-size:16px; font-weight:700; color:#0A2540;">'
            f'Total TTC : {totals["ttc"]:.2f} €</div>'
        )
        mention_html = f'<p style="color:#8BA5C0; font-size:11px; margin-top:24px;">{e(totals["mention"])}</p>'
    vat_html = f'<br/>N° TVA : {e(totals["vat_number"])}' if totals.get("vat_number") else ""

    lignes_html = ""
    for l in (q.lignes or []):
        desc = l.get("description", "") if isinstance(l, dict) else l.description
        qte = l.get("quantite", 0) if isinstance(l, dict) else l.quantite
        pu = l.get("prix_unitaire", 0) if isinstance(l, dict) else l.prix_unitaire
        total_ligne = (qte or 0) * (pu or 0)
        lignes_html += f"""
        <tr>
          <td style="padding:8px 0; border-bottom:1px solid #EEF2F7;">{e(desc)}</td>
          <td style="padding:8px 0; border-bottom:1px solid #EEF2F7; text-align:center;">{e(qte)}</td>
          <td style="padding:8px 0; border-bottom:1px solid #EEF2F7; text-align:right;">{pu:.2f} €</td>
          <td style="padding:8px 0; border-bottom:1px solid #EEF2F7; text-align:right; font-weight:600;">{total_ligne:.2f} €</td>
        </tr>"""

    # Les sauts de ligne du message deviennent de vrais retours dans l'email
    # (sinon « Bonjour X, » et la suite se retrouvent collés sur une ligne).
    message_html = (
        f'<p style="color:#3D4452; line-height:1.6;">{e(req.message).replace(chr(10), "<br/>")}</p>'
        if req.message else ""
    )
    validite_html = (
        f'<p style="color:#6B7A8D; font-size:13px;">Devis valable jusqu\'au {q.date_validite.strftime("%d/%m/%Y")}</p>'
        if q.date_validite else ""
    )

    return f"""
    <div style="font-family: sans-serif; max-width: 560px; margin: 0 auto;">
      <h2 style="color:#0A2540;">Devis {e(q.numero)}</h2>
      {message_html}
      <div style="background:#F7F9F5; border-radius:10px; padding:16px; margin:16px 0; font-size:13px; color:#5B6573;">
        <strong>{e(req.emitter_nom)}</strong><br/>
        {e(req.emitter_adresse)}<br/>
        {f"SIRET : {e(req.emitter_siret)}" if req.emitter_siret else ""}{vat_html}
      </div>
      <p style="color:#6B7A8D; font-size:13px;">
        Émis le {q.date_emission.strftime("%d/%m/%Y")} — destiné à {e(q.client_nom)}
      </p>
      {validite_html}
      <table style="width:100%; border-collapse:collapse; margin-top:16px; font-size:14px;">
        <thead>
          <tr style="color:#6B7A8D; font-size:12px; text-align:left;">
            <th style="padding-bottom:8px;">Description</th>
            <th style="padding-bottom:8px; text-align:center;">Qté</th>
            <th style="padding-bottom:8px; text-align:right;">PU</th>
            <th style="padding-bottom:8px; text-align:right;">Total</th>
          </tr>
        </thead>
        <tbody>{lignes_html}</tbody>
      </table>
      {totaux_html}
      {mention_html}
      {f'<p style="color:#6B7A8D; font-size:12px;">{e(q.notes)}</p>' if q.notes else ""}
      {(
        f'<div style="text-align:center; margin:26px 0 8px;">'
        f'<a href="{SIGNATURE_BASE_URL}/devis/{q.signature_token}" '
        f'style="display:inline-block; background:#5DCAA5; color:#04342C; text-decoration:none; '
        f'font-weight:700; font-size:15px; padding:13px 26px; border-radius:10px;">'
        f'Lire et accepter le devis en ligne</a>'
        f'<p style="color:#8BA5C0; font-size:11px; margin-top:10px;">Acceptation en 1 clic, '
        f'horodatée et sécurisée. Aucune création de compte.</p></div>'
      ) if q.signature_token else ""}
    </div>
    """


# ════════════════════════════════════════════════════════════════════════
#  SIGNATURE ÉLECTRONIQUE DES DEVIS — pages PUBLIQUES (jeton, sans compte).
#  Le client reçoit un lien /devis/{token} : il lit le devis (PDF) et clique
#  « Bon pour accord ». Preuve « signature simple » (eIDAS art. 25, C. civ.
#  1367) : email destinataire + horodatage + IP + user-agent + SHA-256 du PDF
#  au moment du clic + copie scellée sur R2. Servi via montotor.fr/devis/*
#  (rewrite Vercel) ou directement sur le backend.
# ════════════════════════════════════════════════════════════════════════
SIGNATURE_BASE_URL = os.environ.get("SIGNATURE_BASE_URL", "https://www.montotor.fr")

_PAGE_DEVIS_CSS = """
  body{background:#07192E;color:#F8FAFC;font-family:-apple-system,'Segoe UI',Roboto,sans-serif;
       margin:0;padding:28px 16px;display:flex;justify-content:center;}
  .carte{max-width:520px;width:100%;}
  .logo{font-family:Georgia,serif;font-weight:700;font-size:22px;letter-spacing:1px;}
  .logo .o{color:#5DCAA5;}
  .encart{background:rgba(255,255,255,0.04);border:1px solid rgba(255,255,255,0.1);
          border-radius:14px;padding:20px 22px;margin-top:18px;}
  .ligne{display:flex;justify-content:space-between;font-size:14px;padding:3px 0;color:#B5D4F4;}
  .total{font-size:18px;font-weight:700;color:#F8FAFC;border-top:1px solid rgba(255,255,255,0.14);
         margin-top:10px;padding-top:12px;}
  .btn{display:block;width:100%;background:#5DCAA5;color:#04342C;border:none;border-radius:12px;
       padding:14px;font-size:15px;font-weight:700;cursor:pointer;font-family:inherit;margin-top:14px;}
  .btn:disabled{opacity:.45;cursor:default;}
  .pdf{color:#378ADD;font-size:13.5px;}
  .petit{color:#8BA5C0;font-size:11.5px;line-height:1.5;}
  .ok{color:#5DCAA5;font-weight:700;}
  a{color:#378ADD;}
"""


def _devis_par_token(db: Session, token: str):
    """Devis correspondant à un jeton public (None si jeton absent/inconnu)."""
    if not token or len(token) < 16:
        return None
    return db.query(Quote).filter(Quote.signature_token == token).first()


def _accepter_devis(db: Session, q: Quote, ip: str, user_agent: str) -> bool:
    """Enregistre l'acceptation en ligne d'un devis : fichier de preuve complet
    (horodatage, IP, user-agent, email destinataire, SHA-256 du PDF au moment
    exact du clic, copie scellée sur R2) + statut « accepté ».
    Renvoie False si le devis était déjà signé (idempotent, on ne réécrit RIEN :
    la première preuve fait foi)."""
    # Verrou de ligne anti-course : deux clics « Accepter » simultanés ne
    # doivent produire QU'UNE preuve (la première fait foi, jamais réécrite).
    # Sous SQLite (tests) le FOR UPDATE est ignoré, sans effet.
    q = db.query(Quote).filter(Quote.id == q.id).with_for_update().first()
    if q is None or q.signe_le is not None:
        return False
    profile = db.query(Profile).filter(Profile.user_id == q.user_id).first()
    emitter = _build_emitter_info(profile)
    fiscal = resolve_fiscal_settings(q)
    pdf = generate_invoice_pdf(_quote_to_dict(q), emitter, fiscal, kind="devis")
    q.signe_hash = hashlib.sha256(pdf).hexdigest()
    q.signe_le = datetime.utcnow()
    q.signe_ip = (ip or "")[:100]
    q.signe_user_agent = (user_agent or "")[:300]
    q.signe_email = q.client_email
    try:
        if r2_storage.R2_ENABLED:
            q.signe_pdf_key = r2_storage.upload_devis_signe(pdf, q.user_id, q.id)
    except Exception:
        pass  # la preuve principale (hash + horodatage + IP) est en base
    q.statut = "accepte"
    db.commit()
    return True


def _page_devis_html(titre: str, corps: str, sous_titre: str = "devis en ligne") -> str:
    return f"""<!doctype html><html lang="fr"><head><meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{html.escape(titre)}</title><style>{_PAGE_DEVIS_CSS}</style></head>
    <body><div class="carte">
      <div class="logo">T<span class="o">O</span>T<span class="o">O</span>R
        <span style="font-size:12px;color:#8BA5C0;font-family:sans-serif;font-weight:400;"> · {sous_titre}</span></div>
      {corps}
      <p class="petit" style="margin-top:22px;">Document présenté par TOTOR (montotor.fr) pour le compte de l'émetteur.</p>
    </div></body></html>"""


@app.get("/devis/{token}", response_class=HTMLResponse)
def page_devis_public(token: str, db: Session = Depends(get_db)):
    """Page publique de lecture + acceptation d'un devis (lien envoyé au client)."""
    q = _devis_par_token(db, token)
    if not q:
        raise HTTPException(status_code=404, detail="Devis introuvable")
    e = lambda v: html.escape(str(v)) if v is not None else ""
    profile = db.query(Profile).filter(Profile.user_id == q.user_id).first()
    emitter = _build_emitter_info(profile)
    fiscal = resolve_fiscal_settings(q)
    totals = compute_invoice_totals(q.montant, fiscal, q.date_emission)

    lignes = "".join(
        f"<div class='ligne'><span>{e((l.get('description') if isinstance(l, dict) else l.description) or 'Prestation')}</span>"
        f"<span>{((l.get('quantite', 0) if isinstance(l, dict) else l.quantite) or 0) * ((l.get('prix_unitaire', 0) if isinstance(l, dict) else l.prix_unitaire) or 0):.2f} €</span></div>"
        for l in (q.lignes or [])
    )
    mention = f"<p class='petit'>{e(totals['mention'])}</p>" if totals.get("mention") else ""
    validite = f" · valable jusqu'au {q.date_validite.strftime('%d/%m/%Y')}" if q.date_validite else ""

    if q.signe_le is not None:
        action = (f"<p class='ok'>✓ Devis accepté le {q.signe_le.strftime('%d/%m/%Y à %H:%M')} (UTC).</p>"
                  "<p class='petit'>L'acceptation a été enregistrée et transmise à l'émetteur.</p>")
    elif q.statut in ("refuse", "expire"):
        action = "<p class='petit'>Ce devis n'est plus proposé à l'acceptation. Contactez directement l'émetteur.</p>"
    else:
        action = f"""
        <form method="post" action="{SIGNATURE_BASE_URL}/devis/{e(token)}/accepter" style="margin-top:16px;">
          <label style="display:flex;gap:10px;align-items:flex-start;font-size:13.5px;color:#E6EDF5;cursor:pointer;">
            <input type="checkbox" required style="margin-top:3px;width:16px;height:16px;">
            <span>Bon pour accord : j'ai lu le devis {e(q.numero)} et j'accepte la prestation
            pour un total de {totals['ttc']:.2f} €.</span>
          </label>
          <button class="btn" type="submit">Accepter le devis</button>
          <p class="petit" style="margin-top:10px;">En cliquant, votre acceptation est enregistrée avec
          la date, l'heure et l'empreinte du document (signature électronique simple). Une copie est
          conservée par l'émetteur.</p>
        </form>"""

    corps = f"""
      <div class="encart">
        <p style="margin:0 0 2px;font-size:12px;color:#8BA5C0;">Devis {e(q.numero)}{validite}</p>
        <p style="margin:0 0 2px;font-size:16px;font-weight:600;">{e(emitter.get('nom') or 'Votre prestataire')}</p>
        <p style="margin:0 0 14px;font-size:12.5px;color:#8BA5C0;">pour {e(q.client_nom)}</p>
        {lignes}
        <div class="ligne total"><span>Total</span><span>{totals['ttc']:.2f} €</span></div>
        {mention}
        <p style="margin-top:14px;"><a class="pdf" href="{SIGNATURE_BASE_URL}/devis/{e(token)}/pdf" target="_blank">Voir le devis complet (PDF)</a></p>
      </div>
      {action}"""
    return HTMLResponse(_page_devis_html(f"Devis {q.numero}", corps))


@app.get("/devis/{token}/pdf")
def pdf_devis_public(token: str, db: Session = Depends(get_db)):
    """PDF du devis, accessible par le jeton (le client n'a pas de compte)."""
    q = _devis_par_token(db, token)
    if not q:
        raise HTTPException(status_code=404, detail="Devis introuvable")
    profile = db.query(Profile).filter(Profile.user_id == q.user_id).first()
    pdf = generate_invoice_pdf(_quote_to_dict(q), _build_emitter_info(profile),
                               resolve_fiscal_settings(q), kind="devis")
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f"inline; filename=devis-{q.numero}.pdf"})


@app.post("/devis/{token}/accepter", response_class=HTMLResponse)
def accepter_devis_public(token: str, request: Request, db: Session = Depends(get_db)):
    """Acceptation en ligne (clic « Bon pour accord ») : enregistre la preuve,
    passe le devis en « accepté », prévient l'émetteur. Idempotent."""
    q = _devis_par_token(db, token)
    if not q:
        raise HTTPException(status_code=404, detail="Devis introuvable")
    if q.signe_le is None and q.statut in ("refuse", "expire"):
        return HTMLResponse(_page_devis_html(
            "Devis indisponible",
            "<div class='encart'><p class='petit'>Ce devis n'est plus proposé à l'acceptation.</p></div>"))

    # IP réelle : derrière le proxy Vercel/Railway, elle est dans X-Forwarded-For.
    ip = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    if not ip and request.client:
        ip = request.client.host or ""
    nouveau = _accepter_devis(db, q, ip, request.headers.get("user-agent", ""))

    if nouveau:
        # Prévenir l'émetteur : c'est la bonne nouvelle du jour (best-effort).
        try:
            u = db.query(User).filter(User.id == q.user_id).first()
            if u and u.email:
                corps_mail = f"""
                <div style="font-family:sans-serif;max-width:480px;margin:0 auto;text-align:center;
                            background:#07192E;color:#F8FAFC;padding:32px 24px;border-radius:16px;">
                  <p style="color:#5DCAA5;font-weight:bold;letter-spacing:1px;margin:0 0 8px;">DEVIS ACCEPTÉ 🎉</p>
                  <div style="font-size:26px;font-weight:800;margin:8px 0;">{html.escape(q.numero)}</div>
                  <p style="color:#5DCAA5;font-size:15px;margin:4px 0 16px;">{html.escape(q.client_nom or '')} vient d'accepter ton devis en ligne.</p>
                  <p style="color:#9BB0C4;font-size:13px;margin:0;">Acceptation horodatée et enregistrée.
                  Dans TOTOR, tu peux le convertir en facture en un clic.</p>
                </div>"""
                send_email(u.email, f"🎉 Devis {q.numero} accepté par {q.client_nom or 'ton client'}", corps_mail)
        except Exception:
            pass

    quand = q.signe_le.strftime('%d/%m/%Y à %H:%M') if q.signe_le else ""
    corps = f"""
      <div class="encart" style="text-align:center;">
        <div style="font-size:34px;margin-bottom:10px;">✓</div>
        <p class="ok" style="font-size:16px;margin:0 0 8px;">{'Devis accepté, merci !' if nouveau else 'Ce devis était déjà accepté.'}</p>
        <p class="petit">Acceptation enregistrée le {quand} (UTC). L'émetteur a été prévenu
        et conserve la preuve horodatée. Vous pouvez fermer cette page.</p>
        <p style="margin-top:12px;"><a class="pdf" href="{SIGNATURE_BASE_URL}/devis/{html.escape(token)}/pdf" target="_blank">Télécharger le devis (PDF)</a></p>
      </div>"""
    return HTMLResponse(_page_devis_html("Devis accepté", corps))


# ════════════════════════════════════════════════════════════════════════
#  PAIEMENT EN LIGNE DES FACTURES (Stripe Connect, charges directes).
#  L'utilisateur active son compte d'encaissement (KYC hébergé par Stripe) ;
#  ses clients paient sur une page publique à jeton (carte ou SEPA) ; la
#  facture ne passe « payée » que sur confirmation réelle par webhook.
#  L'argent ne transite JAMAIS par TOTOR. Aucune commission TOTOR.
# ════════════════════════════════════════════════════════════════════════
@app.post("/billing/connect/onboarding")
def connect_onboarding(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Active l'encaissement en ligne : crée (au premier appel) le compte Stripe
    connecté de l'utilisateur, puis renvoie le lien du formulaire hébergé Stripe
    (KYC fait par Stripe, jamais par nous)."""
    fs = db.query(FiscalSettings).filter(FiscalSettings.user_id == user.id).first()
    if fs is None:
        fs = FiscalSettings(user_id=user.id)
        db.add(fs)
        db.commit()
        db.refresh(fs)
    try:
        if not fs.stripe_account_id:
            fs.stripe_account_id = encaissement.creer_compte_connecte(user)
            db.commit()
        return {"url": encaissement.lien_onboarding(fs.stripe_account_id)}
    except Exception as e:
        print(f"[connect-onboarding] {type(e).__name__}: {e}", flush=True)
        raise HTTPException(status_code=502, detail="Stripe est injoignable pour le moment, réessaie dans un instant")


@app.get("/billing/connect/status")
def connect_status(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """État de l'encaissement en ligne pour les Réglages : non configuré /
    dossier en cours chez Stripe / actif."""
    fs = db.query(FiscalSettings).filter(FiscalSettings.user_id == user.id).first()
    if not fs or not fs.stripe_account_id:
        return {"configure": False, "actif": False, "dossier_complet": False}
    try:
        st = encaissement.statut_compte(fs.stripe_account_id)
        return {"configure": True, **st}
    except Exception as e:
        # Jamais d'erreur silencieuse ici : c'est ce qui a masqué le bug .get()
        # du 22/07 (statut « pas complet » renvoyé à tort à tous les configurés).
        print(f"[encaissement] statut Stripe illisible : {billing.redact_secrets(f'{type(e).__name__}: {e}')}", flush=True)
        return {"configure": True, "actif": False, "dossier_complet": False, "erreur": True}


def _facture_par_token(db: Session, token: str):
    if not token or len(token) < 16:
        return None
    return db.query(ClientInvoice).filter(ClientInvoice.payment_token == token).first()


def _compte_encaissement_de(db: Session, user_id: str):
    fs = db.query(FiscalSettings).filter(FiscalSettings.user_id == user_id).first()
    return fs.stripe_account_id if fs and fs.stripe_account_id else None


@app.get("/paiement/{token}", response_class=HTMLResponse)
def page_paiement_public(token: str, db: Session = Depends(get_db)):
    """Page publique « Payer en ligne » d'une facture (lien envoyé au client)."""
    inv = _facture_par_token(db, token)
    if not inv:
        raise HTTPException(status_code=404, detail="Facture introuvable")
    e = lambda v: html.escape(str(v)) if v is not None else ""
    profile = db.query(Profile).filter(Profile.user_id == inv.user_id).first()
    emitter = _build_emitter_info(profile)
    totals = compute_invoice_totals(inv.montant, resolve_fiscal_settings(inv), inv.date_emission)

    lignes = "".join(
        f"<div class='ligne'><span>{e((l.get('description') if isinstance(l, dict) else l.description) or 'Prestation')}</span>"
        f"<span>{((l.get('quantite', 0) if isinstance(l, dict) else l.quantite) or 0) * ((l.get('prix_unitaire', 0) if isinstance(l, dict) else l.prix_unitaire) or 0):.2f} €</span></div>"
        for l in (inv.lignes or [])
    )
    mention = f"<p class='petit'>{e(totals['mention'])}</p>" if totals.get("mention") else ""
    echeance = f" · échéance le {inv.date_echeance.strftime('%d/%m/%Y')}" if inv.date_echeance else ""

    if inv.statut == "payee":
        action = "<p class='ok'>✓ Cette facture est déjà réglée. Merci !</p>"
    elif inv.paiement_en_cours:
        action = ("<p class='ok'>⏳ Un prélèvement SEPA est en cours pour cette facture.</p>"
                  "<p class='petit'>La confirmation bancaire prend quelques jours ouvrés. Rien d'autre à faire.</p>")
    else:
        compte = _compte_encaissement_de(db, inv.user_id)
        if not compte:
            action = "<p class='petit'>Le paiement en ligne n'est pas disponible pour cette facture. Réglez-la directement auprès de l'émetteur.</p>"
        else:
            action = f"""
        <form method="post" action="{SIGNATURE_BASE_URL}/paiement/{e(token)}/session" style="margin-top:16px;">
          <button class="btn" type="submit" name="mode" value="card">💳 Payer par carte</button>
          <button class="btn" type="submit" name="mode" value="sepa"
                  style="background:transparent;color:#F8FAFC;border:1px solid rgba(255,255,255,0.25);">
            🏦 Prélèvement SEPA</button>
          <p class="petit" style="margin-top:10px;">Paiement traité par Stripe. TOTOR ne détient jamais les fonds.
          Le règlement va directement à l'émetteur de la facture.</p>
        </form>"""

    corps = f"""
      <div class="encart">
        <p style="margin:0 0 2px;font-size:12px;color:#8BA5C0;">Facture {e(inv.numero)}{echeance}</p>
        <p style="margin:0 0 2px;font-size:16px;font-weight:600;">{e(emitter.get('nom') or 'Votre prestataire')}</p>
        <p style="margin:0 0 14px;font-size:12.5px;color:#8BA5C0;">destinée à {e(inv.client_nom)}</p>
        {lignes}
        <div class="ligne total"><span>Total à payer</span><span>{totals['ttc']:.2f} €</span></div>
        {mention}
        <p style="margin-top:14px;"><a class="pdf" href="{SIGNATURE_BASE_URL}/paiement/{e(token)}/pdf" target="_blank">Voir la facture complète (PDF)</a></p>
      </div>
      {action}"""
    return HTMLResponse(_page_devis_html(f"Facture {inv.numero}", corps, sous_titre="paiement en ligne"))


@app.get("/paiement/{token}/pdf")
def pdf_facture_public(token: str, db: Session = Depends(get_db)):
    """PDF de la facture, accessible par le jeton de paiement."""
    inv = _facture_par_token(db, token)
    if not inv:
        raise HTTPException(status_code=404, detail="Facture introuvable")
    profile = db.query(Profile).filter(Profile.user_id == inv.user_id).first()
    pdf = generate_invoice_pdf(_invoice_to_dict(inv), _build_emitter_info(profile),
                               resolve_fiscal_settings(inv))
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f"inline; filename=facture-{inv.numero}.pdf"})


@app.post("/paiement/{token}/session")
def creer_session_paiement_public(token: str, mode: str = Form("card"), db: Session = Depends(get_db)):
    """Le clic « Payer » : crée la session Stripe Checkout sur le compte connecté
    de l'émetteur (charge directe) et redirige le client vers la page Stripe."""
    inv = _facture_par_token(db, token)
    if not inv:
        raise HTTPException(status_code=404, detail="Facture introuvable")
    if inv.statut == "payee":
        return RedirectResponse(url=f"{SIGNATURE_BASE_URL}/paiement/{token}", status_code=303)
    compte = _compte_encaissement_de(db, inv.user_id)
    if not compte:
        raise HTTPException(status_code=400, detail="Paiement en ligne indisponible")
    totals = compute_invoice_totals(inv.montant, resolve_fiscal_settings(inv), inv.date_emission)
    try:
        url = encaissement.creer_session_paiement(inv, totals["ttc"], compte, "sepa" if mode == "sepa" else "card")
    except Exception as e:
        print(f"[paiement-session] {type(e).__name__}: {e}", flush=True)
        raise HTTPException(status_code=502, detail="Le paiement est momentanément indisponible, réessayez dans un instant")
    return RedirectResponse(url=url, status_code=303)


@app.get("/paiement/{token}/merci", response_class=HTMLResponse)
def page_paiement_merci(token: str, db: Session = Depends(get_db)):
    """Retour de Stripe après le paiement. La confirmation RÉELLE arrive par
    webhook : on remercie sans jamais affirmer plus que ce qu'on sait."""
    inv = _facture_par_token(db, token)
    if not inv:
        raise HTTPException(status_code=404, detail="Facture introuvable")
    if inv.statut == "payee":
        message = "<p class='ok'>✓ Paiement reçu, merci !</p><p class='petit'>La facture est réglée. Vous pouvez fermer cette page.</p>"
    else:
        message = ("<p class='ok'>Merci, votre paiement a bien été transmis.</p>"
                   "<p class='petit'>Par carte, la confirmation est immédiate ; par prélèvement SEPA, la banque "
                   "met quelques jours ouvrés à confirmer. L'émetteur est prévenu automatiquement.</p>")
    corps = f"<div class='encart' style='text-align:center;'><div style='font-size:34px;margin-bottom:10px;'>💶</div>{message}</div>"
    return HTMLResponse(_page_devis_html("Merci", corps, sous_titre="paiement en ligne"))


@app.post("/stripe/webhook-connect")
async def stripe_webhook_connect(request: Request, db: Session = Depends(get_db)):
    """Webhook des ÉVÉNEMENTS DES COMPTES CONNECTÉS (checkout des factures).
    C'est LA source de vérité du « payé » : signature vérifiée, idempotent."""
    payload = await request.body()
    signature = request.headers.get("stripe-signature", "")
    try:
        event = encaissement.construire_evenement(payload, signature)
    except Exception:
        raise HTTPException(status_code=400, detail="Signature invalide")
    res = encaissement.traiter_evenement_connect(db, event)

    # La bonne nouvelle au propriétaire (best-effort, jamais bloquant).
    if res.get("resultat") == "payee":
        try:
            inv = db.query(ClientInvoice).filter(ClientInvoice.id == res["invoice_id"]).first()
            u = db.query(User).filter(User.id == inv.user_id).first() if inv else None
            if inv and u and u.email:
                corps_mail = f"""
                <div style="font-family:sans-serif;max-width:480px;margin:0 auto;text-align:center;
                            background:#07192E;color:#F8FAFC;padding:32px 24px;border-radius:16px;">
                  <p style="color:#5DCAA5;font-weight:bold;letter-spacing:1px;margin:0 0 8px;">FACTURE PAYÉE 💶</p>
                  <div style="font-size:26px;font-weight:800;margin:8px 0;">{html.escape(inv.numero)}</div>
                  <p style="color:#5DCAA5;font-size:15px;margin:4px 0 16px;">{html.escape(inv.client_nom or 'Ton client')} vient de payer en ligne.</p>
                  <p style="color:#9BB0C4;font-size:13px;margin:0;">L'argent arrive directement sur ton compte Stripe,
                  puis sur ton compte bancaire. La facture est passée « payée » dans TOTOR.</p>
                </div>"""
                send_email(u.email, f"💶 Facture {inv.numero} payée par {inv.client_nom or 'ton client'}", corps_mail)
        except Exception:
            pass
    return {"ok": True}


@app.post("/quotes/{quote_id}/send")
def send_quote(
    quote_id: str,
    req: SendInvoiceRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    q = db.query(Quote).filter(Quote.id == quote_id, Quote.user_id == user.id).first()
    if not q:
        raise HTTPException(status_code=404, detail="Devis introuvable")
    if not q.client_email:
        raise HTTPException(status_code=400, detail="Aucun email client renseigne sur ce devis")

    # Émetteur de l'email : on PRÉSERVE un nom éventuellement fourni par le front (en lui
    # ajoutant la mention « EI ») ; s'il est vide, on le dérive du profil serveur (repli sur
    # raison_sociale + EI), comme le PDF. Aucune saisie n'est jamais écrasée.
    profile = db.query(Profile).filter(Profile.user_id == user.id).first()
    req.emitter_nom = (
        append_ei_mention(req.emitter_nom, profile.statut if profile else None)
        if req.emitter_nom else _build_emitter_info(profile)["nom"]
    )

    # Envoi = émission : si brouillon, on fige le régime TVA AVANT de construire l'email.
    if q.statut == "brouillon":
        _snapshot_fiscal(q, db, user.id)
    # Lien d'acceptation en ligne : jeton créé au premier envoi, stable ensuite
    # (le client peut recliquer le même lien depuis n'importe quel email).
    if not q.signature_token:
        q.signature_token = secrets.token_urlsafe(24)
    fiscal = resolve_fiscal_settings(q)
    html = _build_quote_email_html(q, req, fiscal)
    # Expéditeur = le nom de l'utilisateur (signature du profil, repli sur le nom
    # émetteur) ; Reply-To = son email. Même circuit que la facture : le client reçoit
    # un mail de la personne qu'il connaît et peut lui répondre directement.
    ok = send_invoice_email(
        q.client_email, f"Devis {q.numero}", html,
        from_name=_signature_relance(profile) or req.emitter_nom,
        reply_to=user.email,
    )
    if not ok:
        raise HTTPException(status_code=502, detail="Erreur lors de l'envoi de l'email")

    if q.statut == "brouillon":
        q.statut = "envoye"
    db.commit()
    db.refresh(q)
    return _quote_to_dict(q)


@app.post("/quotes/{quote_id}/convert")
def convert_quote_to_invoice(
    quote_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    q = db.query(Quote).filter(Quote.id == quote_id, Quote.user_id == user.id).first()
    if not q:
        raise HTTPException(status_code=404, detail="Devis introuvable")
    if q.converted_invoice_id:
        raise HTTPException(status_code=409, detail="Ce devis a deja ete converti en facture")

    inv = ClientInvoice(
        user_id=user.id,
        numero=_next_numero(db, user.id),
        client_nom=q.client_nom,
        client_email=q.client_email,
        client_adresse=q.client_adresse,
        client_type=q.client_type,
        client_siret=q.client_siret,
        client_tva=q.client_tva,
        date_emission=date.today(),
        date_echeance=None,
        montant=q.montant,
        statut="brouillon",
        lignes=q.lignes,
        notes=q.notes,
    )
    # La facture hérite de la localisation client figée sur le devis converti.
    _snapshot_fiscal(inv, db, user.id, _localisation_de(q))
    db.add(inv)
    q.statut = "accepte"
    db.commit()
    db.refresh(inv)
    q.converted_invoice_id = inv.id
    db.commit()
    db.refresh(q)

    return {"quote": _quote_to_dict(q), "invoice": _invoice_to_dict(inv)}


# ----------------------------------------------------------------
# Frais d'Entreprise
# ----------------------------------------------------------------

class ExpenseCreateRequest(BaseModel):
    date: date
    montant: float
    categorie: str = "autre"
    description: Optional[str] = None
    client_nom: Optional[str] = None


class ExpenseUpdateRequest(BaseModel):
    date: Optional[date] = None
    montant: Optional[float] = None
    categorie: Optional[str] = None
    description: Optional[str] = None
    client_nom: Optional[str] = None


def _expense_to_dict(e: Expense) -> dict:
    return {
        "id": e.id,
        "date": e.date,
        "montant": e.montant,
        "categorie": e.categorie,
        "description": e.description,
        "source": e.source,
        "filename": e.filename,
        "client_nom": e.client_nom,
    }


@app.get("/expenses")
def list_expenses(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    expenses = (
        db.query(Expense)
        .filter(Expense.user_id == user.id)
        .order_by(Expense.date.desc())
        .all()
    )
    return [_expense_to_dict(e) for e in expenses]


@app.get("/expenses/summary")
def expenses_summary(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    expenses = db.query(Expense).filter(Expense.user_id == user.id).all()
    today = date.today()

    frais_mois = sum(e.montant for e in expenses if e.date.year == today.year and e.date.month == today.month)
    frais_annee = sum(e.montant for e in expenses if e.date.year == today.year)

    par_categorie = {}
    for e in expenses:
        if e.date.year == today.year:
            par_categorie[e.categorie] = par_categorie.get(e.categorie, 0) + e.montant

    repartition = sorted(
        [{"categorie": k, "montant": round(v, 2)} for k, v in par_categorie.items()],
        key=lambda x: x["montant"],
        reverse=True,
    )

    return {
        "frais_mois": round(frais_mois, 2),
        "frais_annee": round(frais_annee, 2),
        "par_categorie": repartition,
    }


@app.post("/expenses")
def create_expense(
    req: ExpenseCreateRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if req.categorie not in CATEGORIES_FRAIS:
        raise HTTPException(status_code=400, detail="Categorie de frais inconnue")

    expense = Expense(
        user_id=user.id,
        date=req.date,
        montant=req.montant,
        categorie=req.categorie,
        description=req.description,
        source="manuel",
        client_nom=(req.client_nom or None),
    )
    db.add(expense)
    db.commit()
    db.refresh(expense)
    return _expense_to_dict(expense)


@app.put("/expenses/{expense_id}")
def update_expense(
    expense_id: str,
    req: ExpenseUpdateRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    expense = db.query(Expense).filter(Expense.id == expense_id, Expense.user_id == user.id).first()
    if not expense:
        raise HTTPException(status_code=404, detail="Frais introuvable")

    if req.categorie is not None and req.categorie not in CATEGORIES_FRAIS:
        raise HTTPException(status_code=400, detail="Categorie de frais inconnue")

    if req.date is not None:
        expense.date = req.date
    if req.montant is not None:
        expense.montant = req.montant
    if req.categorie is not None:
        expense.categorie = req.categorie
    if req.description is not None:
        expense.description = req.description
    if req.client_nom is not None:
        # chaîne vide = détacher le client ; sinon rattacher
        expense.client_nom = req.client_nom.strip() or None

    db.commit()
    db.refresh(expense)
    return _expense_to_dict(expense)


@app.delete("/expenses/{expense_id}")
def delete_expense(
    expense_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    expense = db.query(Expense).filter(Expense.id == expense_id, Expense.user_id == user.id).first()
    if not expense:
        raise HTTPException(status_code=404, detail="Frais introuvable")
    db.delete(expense)
    db.commit()
    return {"ok": True}


@app.post("/expenses/extract")
async def extract_expense(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    allowed_ext = (".pdf", ".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".webp")
    if not file.filename.lower().endswith(allowed_ext):
        raise HTTPException(status_code=400, detail="Format de fichier non supporte")

    # Quota freemium : scans factures + frais partagent le compteur mensuel "doc_scan".
    _consommer_quota(db, user, "doc_scan", AI_DOC_SCAN_DAILY_LIMIT)

    tmp_dir, file_path = _enregistrer_upload(file)
    try:
        data = extract_invoice_data(file_path)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Impossible de lire la facture : {e}")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    if data["amount"] is None:
        raise HTTPException(
            status_code=422,
            detail="Montant introuvable sur cette facture, merci de l'ajouter manuellement",
        )

    return {
        "amount": data["amount"],
        "date": data["date"].date().isoformat() if data["date"] else date.today().isoformat(),
        "filename": data["filename"],
        "description": data.get("description"),
    }


# ----------------------------------------------------------------
# Contacts
# ----------------------------------------------------------------

class ContactCreateRequest(BaseModel):
    nom: str
    email: Optional[str] = None
    siret: Optional[str] = None
    adresse: Optional[str] = None


def _contact_to_dict(c: Contact) -> dict:
    return {
        "id": c.id,
        "nom": c.nom,
        "email": c.email,
        "siret": c.siret,
        "adresse": c.adresse,
    }


@app.get("/contacts")
def list_contacts(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    contacts = (
        db.query(Contact)
        .filter(Contact.user_id == user.id)
        .order_by(Contact.created_at.desc())
        .all()
    )
    return [_contact_to_dict(c) for c in contacts]


@app.post("/contacts")
def create_contact(
    req: ContactCreateRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    contact = Contact(
        user_id=user.id,
        nom=req.nom,
        email=req.email,
        siret=req.siret,
        adresse=req.adresse,
    )
    db.add(contact)
    db.commit()
    db.refresh(contact)
    return _contact_to_dict(contact)


@app.delete("/contacts/{contact_id}")
def delete_contact(
    contact_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    contact = db.query(Contact).filter(Contact.id == contact_id, Contact.user_id == user.id).first()
    if not contact:
        raise HTTPException(status_code=404, detail="Contact introuvable")
    db.delete(contact)
    db.commit()
    return {"ok": True}


# ----------------------------------------------------------------
# RGPD : export et suppression du compte
# ----------------------------------------------------------------

@app.get("/account/export")
def export_account_data(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    profile = db.query(Profile).filter(Profile.user_id == user.id).first()
    incomes = db.query(IncomeEntry).filter(IncomeEntry.user_id == user.id).all()
    invoices = db.query(ClientInvoice).filter(ClientInvoice.user_id == user.id).all()
    expenses = db.query(Expense).filter(Expense.user_id == user.id).all()

    return {
        "export_genere_le": datetime.now().isoformat(),
        "compte": {
            "id": user.id,
            "email": user.email,
            "cree_le": user.created_at.isoformat() if user.created_at else None,
        },
        "profil": {
            "statut": profile.statut,
            "activite": profile.activite,
            "periodicite": profile.periodicite,
            "acre": profile.acre,
            "versement_liberatoire": profile.versement_liberatoire,
            "siret": profile.siret,
            "raison_sociale": profile.raison_sociale,
            "adresse": profile.adresse,
            "prenom": profile.prenom,
            "nom": profile.nom,
            "telephone": profile.telephone,
            "entreprise": profile.entreprise,
            "solde_bancaire": profile.solde_bancaire,
            "reserve_securite": profile.reserve_securite,
            "tmi": profile.tmi,
        } if profile else None,
        "revenus": [
            {
                "date": e.date.isoformat(),
                "montant": e.amount,
                "description": e.description,
                "source": e.source,
            } for e in incomes
        ],
        "factures_clients": [
            {
                "numero": i.numero,
                "client_nom": i.client_nom,
                "client_email": i.client_email,
                "date_emission": i.date_emission.isoformat() if i.date_emission else None,
                "date_paiement": i.date_paiement.isoformat() if i.date_paiement else None,
                "montant": i.montant,
                "statut": i.statut,
            } for i in invoices
        ],
        "frais": [
            {
                "date": ex.date.isoformat(),
                "montant": ex.montant,
                "categorie": ex.categorie,
                "description": ex.description,
            } for ex in expenses
        ],
        # Historique « Parle à Totor » (les deux espaces) : données personnelles,
        # donc incluses dans la portabilité RGPD.
        "conversations_totor": [
            {
                "espace": m.espace,
                "role": m.role,
                "contenu": m.content,
                "date": m.created_at.isoformat() if m.created_at else None,
            } for m in db.query(ChatMessageDB)
                        .filter(ChatMessageDB.user_id == user.id)
                        .order_by(ChatMessageDB.created_at)
                        .all()
        ],
    }


@app.delete("/account")
def delete_account(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    # RGPD : on purge d'abord tous les documents AEM de l'utilisateur sur R2.
    if r2_storage.R2_ENABLED:
        r2_storage.delete_all_for_user(str(user.id))
    # Purge des tentatives de login (table sans FK : on cible par email).
    db.query(LoginAttempt).filter(LoginAttempt.email == (user.email or "").strip().lower()).delete()
    # AIUsage est supprimé en cascade via la relationship, mais on l'efface aussi
    # explicitement par sécurité (au cas où la cascade ne serait pas appliquée).
    db.query(AIUsage).filter(AIUsage.user_id == user.id).delete()
    # Même précaution pour l'historique du chat (données personnelles, RGPD).
    db.query(ChatMessageDB).filter(ChatMessageDB.user_id == user.id).delete()
    db.delete(user)
    db.commit()
    return {"ok": True}


# ----------------------------------------------------------------
# Estimation des cotisations
# ----------------------------------------------------------------

@app.get("/estimate")
def get_estimate(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    profile = db.query(Profile).filter(Profile.user_id == user.id).first()
    if not profile or not profile.onboarding_complete:
        raise HTTPException(status_code=400, detail="Profil non configure")

    if profile.statut in STATUTS_A_VENIR:
        return {
            "statut": profile.statut,
            "disponible": False,
            "reason": "statut_a_venir",
            "message": f"Le statut {profile.statut.upper()} arrive bientot. "
            f"On vous previent des qu'il est pret.",
        }

    # L'intermittent a son propre cockpit (compteur 507h), pas l'estimation fiscale AE.
    # On renvoie proprement plutot que de laisser tax_engine planter sur un statut inconnu.
    if profile.statut != "auto_entrepreneur":
        return {
            "statut": profile.statut,
            "disponible": False,
            "reason": "statut_autre",
            "message": "Ce statut utilise un autre tableau de bord.",
        }

    # Garde-fou : sans activite renseignee, tax_engine ne peut pas calculer.
    # reason="activite_manquante" → le front affiche la modale de choix d'activite (jamais "pas de revenu").
    if not profile.activite:
        return {
            "statut": profile.statut,
            "disponible": False,
            "reason": "activite_manquante",
            "message": "Renseigne ton type d'activite dans ton profil pour activer ton estimation.",
        }

    entries = db.query(IncomeEntry).filter(IncomeEntry.user_id == user.id).all()
    incomes = [(e.date, e.amount) for e in entries]

    paid_invoices = (
        db.query(ClientInvoice)
        .filter(ClientInvoice.user_id == user.id, ClientInvoice.statut == "payee")
        .all()
    )
    # Une facture est rattachée à sa date d'ENCAISSEMENT (date_paiement). Celle-ci est toujours
    # posée au passage « payee » (create_invoice + update_invoice_status) → le repli sur
    # date_emission est un simple filet défensif qui ne se déclenche pas en pratique.
    incomes += [(inv.date_paiement or inv.date_emission, inv.montant) for inv in paid_invoices]

    try:
        result = estimate(
            statut=profile.statut,
            activite=profile.activite,
            periodicite=profile.periodicite,
            acre=profile.acre,
            versement_liberatoire=profile.versement_liberatoire,
            incomes=incomes,
            today=date.today(),
        )
    except Exception as e:
        # Filet : on ne laisse jamais une erreur de calcul casser le chargement du dashboard.
        raise HTTPException(status_code=422, detail=f"Estimation indisponible : {e}")

    return {
        "statut": result.statut,
        "disponible": True,
        "activite": result.activite,
        "ca_periode_courante": result.ca_periode_courante,
        "ca_periode_precedente": result.ca_periode_precedente,
        "taux_global_pct": result.taux_global_pct,
        "montant_a_provisionner": result.montant_a_provisionner,
        "detail": result.detail,
        "ca_annuel": result.ca_annuel,
        "plafond": result.plafond,
        "pourcentage_plafond": result.pourcentage_plafond,
        "periode_courante": {
            "label": result.periode_courante.label,
            "start": result.periode_courante.start,
            "end": result.periode_courante.end,
            "date_limite_declaration": result.periode_courante.date_limite_declaration,
            "jours_restants": result.periode_courante.jours_restants,
        },
        "periode_precedente": {
            "label": result.periode_precedente.label,
            "start": result.periode_precedente.start,
            "end": result.periode_precedente.end,
            "date_limite_declaration": result.periode_precedente.date_limite_declaration,
            "jours_restants": result.periode_precedente.jours_restants,
        },
        # ── Champs ADDITIFs (bug 1.5) — le dashboard les consommera au Temps 2. ──
        # Les clés ci-dessus sont INCHANGÉES → pas de régression d'affichage entre T1 et T2.
        "provision_periode_courante": result.provision_periode_courante,
        "regularisations_periodes_passees": result.regularisations_periodes_passees,
        "total_a_prevoir": result.total_a_prevoir,
    }


@app.get("/paie")
def get_paie(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """La Paie d'Hector : le salaire lissé recommandé de l'auto-entrepreneur.
    Trois montants (prudent / recommandé / maximum) calculés par paie_engine sur
    les 6 derniers mois civils complets de net réel (encaissé − provision URSSAF).
    RECOMMANDATION seulement : c'est l'utilisateur qui décide et qui vire."""
    profile = db.query(Profile).filter(Profile.user_id == user.id).first()
    if not profile or not profile.onboarding_complete:
        return {"disponible": False}
    if profile.statut != "auto_entrepreneur" or not profile.activite:
        return {"disponible": False}

    entries = db.query(IncomeEntry).filter(IncomeEntry.user_id == user.id).all()
    incomes = [(e.date, e.amount) for e in entries]
    paid_invoices = (
        db.query(ClientInvoice)
        .filter(ClientInvoice.user_id == user.id, ClientInvoice.statut == "payee")
        .all()
    )
    incomes += [(inv.date_paiement or inv.date_emission, inv.montant) for inv in paid_invoices]

    # Le taux global vient du moteur fiscal existant (activité + versement libératoire).
    try:
        res = estimate(
            statut=profile.statut, activite=profile.activite,
            periodicite=profile.periodicite or "mensuelle",
            acre=profile.acre, versement_liberatoire=profile.versement_liberatoire,
            incomes=incomes, today=date.today(),
        )
    except Exception:
        return {"disponible": False}
    taux = (res.taux_global_pct or 0) / 100.0

    # Les 6 derniers mois civils COMPLETS (le mois en cours n'est pas fini, on ne le juge pas).
    aujourdhui = date.today()
    mois_fr = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet",
               "août", "septembre", "octobre", "novembre", "décembre"]
    fenetres = []
    annee, mois = aujourdhui.year, aujourdhui.month
    for _ in range(6):
        mois -= 1
        if mois == 0:
            mois, annee = 12, annee - 1
        fenetres.append((annee, mois))
    fenetres.reverse()

    nets, historique = [], []
    for (a, m) in fenetres:
        ca = sum(float(mnt or 0) for (d, mnt) in incomes if d and d.year == a and d.month == m)
        net = round(ca * (1 - taux), 2)
        nets.append(net)
        historique.append({"mois": f"{mois_fr[m - 1]} {a}", "encaisse": round(ca, 2), "net": net})

    paie = calculer_paie(nets)
    # Freemium 1.0.1 (carte officielle) : le salaire reste GRATUIT (l'habitude, le
    # rituel du 1er), mais le gratuit reçoit UN montant conseillé (le recommandé).
    # Les 3 montants (prudent / recommandé / maximum) font partie de TOTOR Veille.
    # On ne fait jamais payer la donnée : l'historique reste visible pour tous.
    if not billing.is_premium(db, user):
        paie["prudent"] = None
        paie["maximum"] = None
        paie["paie_verrouillee"] = True
    dernier = historique[-1]
    return {
        "disponible": True,
        "mois_label": f"{mois_fr[aujourdhui.month - 1]} {aujourdhui.year}",
        "cle_mois": aujourdhui.strftime("%Y-%m"),
        "dernier_mois": {
            "label": dernier["mois"],
            "encaisse": dernier["encaisse"],
            "provision": round(dernier["encaisse"] * taux, 2),
            "net": dernier["net"],
        },
        "historique": historique,
        "taux_global_pct": res.taux_global_pct,
        "versement_liberatoire": bool(profile.versement_liberatoire),
        "reserve_visee": profile.reserve_securite,
        **paie,
    }


@app.get("/projection")
def get_projection(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Projection de trésorerie auto-entrepreneur : « vais-je m'en sortir le mois prochain ? »
    Calcule deux scénarios (plancher / optimiste) à partir des factures, devis, solde et
    train de vie déjà en base — aucune donnée stockée, tout recalculé à la demande."""
    profile = db.query(Profile).filter(Profile.user_id == user.id).first()
    if not profile or not profile.onboarding_complete:
        raise HTTPException(status_code=400, detail="Profil non configure")

    # La projection est propre à l'auto-entrepreneur (l'intermittent a son cockpit 507h).
    if profile.statut != "auto_entrepreneur":
        return {"disponible": False, "message": "Ce statut utilise un autre tableau de bord."}

    # « Je regarde ton mois prochain » = fonction TOTOR Veille (intelligence, pas donnée) :
    # en gratuit on renvoie un drapeau doux, jamais une erreur (même logique que la paie).
    if quotas_freemium.projection_verrouillee(db, user):
        return {"disponible": False, "verrouille": True}

    # Sans solde renseigné, rien à projeter : l'appli affichera « renseigne ton solde ».
    if profile.solde_bancaire is None:
        return {"disponible": False}

    # Entrées certaines : factures émises mais pas encore payées.
    factures = [
        {
            "montant": inv.montant,
            "statut": inv.statut,
            "date_echeance": inv.date_echeance,
            "date_paiement": inv.date_paiement,
            "numero": inv.numero,
        }
        for inv in db.query(ClientInvoice)
        .filter(
            ClientInvoice.user_id == user.id,
            ClientInvoice.statut.in_(("envoyee", "impayee")),
        )
        .all()
    ]

    # Entrées probables : devis acceptés (le pipeline).
    devis = [
        {"montant": q.montant, "statut": q.statut, "date_validite": q.date_validite}
        for q in db.query(Quote)
        .filter(Quote.user_id == user.id, Quote.statut == "accepte")
        .all()
    ]

    try:
        p = projeter_tresorerie(
            solde=profile.solde_bancaire,
            depenses_mensuelles=profile.depenses_mensuelles,
            activite=profile.activite,
            acre=profile.acre,
            versement_liberatoire=profile.versement_liberatoire,
            factures=factures,
            devis=devis,
            today=date.today(),
        )
    except Exception as e:
        # Filet : une erreur de calcul ne doit jamais casser le dashboard.
        raise HTTPException(status_code=422, detail=f"Projection indisponible : {e}")

    return {
        "disponible": p.disponible,
        "horizon": p.horizon,
        "horizon_label": p.horizon_label,
        "solde_actuel": p.solde_actuel,
        "plancher": p.plancher,
        "optimiste": p.optimiste,
        "nb_mois": p.nb_mois,
        "detail": {
            "factures_a_encaisser": {"montant": p.factures_montant, "count": p.factures_count},
            "devis_probables": {"montant": p.devis_montant, "count": p.devis_count},
            "train_de_vie": p.train_de_vie,
            "charges": p.charges,
        },
        "ton": p.ton,
        "message": p.message,
        "leviers": p.leviers,
    }


class BugReportRequest(BaseModel):
    """Signalement de bug depuis l'Aide vivante. Totor ne répare pas : il collecte
    et transmet. Infos techniques utiles au debug autorisées (écran, URL, navigateur,
    dernières erreurs console) — JAMAIS de données financières ou personnelles du compte."""
    description: str
    email: Optional[str] = None      # optionnel : « si tu veux que Camille te réponde »
    ecran: Optional[str] = None
    url: Optional[str] = None
    navigateur: Optional[str] = None
    erreurs_console: Optional[list] = None


@app.post("/aide/bug")
def signaler_bug(
    req: BugReportRequest,
    user: User = Depends(get_current_user),
):
    """Transmet un signalement de bug à Camille (email [BUG], distinct de [Aide])."""
    description = (req.description or "").strip()
    if not description:
        raise HTTPException(status_code=400, detail="Décris ce qui s'est passé, même en deux mots.")
    erreurs = ""
    for e in (req.erreurs_console or [])[:5]:
        erreurs += f"<div style='font-family:monospace;font-size:11px;color:#A32D2D'>{html.escape(str(e)[:300])}</div>"
    corps = (
        "<div style='font-family:sans-serif;max-width:520px'>"
        f"<p><strong>Écran :</strong> {html.escape(req.ecran or 'inconnu')}"
        f" · <strong>URL :</strong> {html.escape((req.url or '')[:200])}</p>"
        f"<p><strong>Navigateur :</strong> {html.escape((req.navigateur or '')[:200])}</p>"
        f"<p><strong>Description :</strong><br/>{html.escape(description[:2000])}</p>"
        f"<p><strong>Email pour réponse :</strong> {html.escape(req.email or 'non fourni')}</p>"
        + (f"<p><strong>Dernières erreurs console :</strong>{erreurs}</p>" if erreurs else "")
        + "<p style='color:#6B7A8D;font-size:12px'>Signalement via l'Aide vivante — aucune donnée de compte.</p></div>"
    )
    ok = send_email(SUPPORT_EMAIL, f"[BUG] {req.ecran or 'écran inconnu'}", corps,
                    reply_to=(req.email or None))
    if not ok:
        raise HTTPException(status_code=502, detail="L'envoi n'a pas marché — réessaie, ou écris directement à bonjour@montotor.fr.")
    return {"ok": True}


class ChatMessage(BaseModel):
    role: str
    content: str


class AssistantRequest(BaseModel):
    messages: list[ChatMessage]
    # Mode "aide" (pastille L'Aide vivante) : questions sur le FONCTIONNEMENT de l'app.
    # Le canal décide du régime de quota, pas une classification IA.
    mode: Optional[str] = None
    ecran: Optional[str] = None  # écran courant (pour les stats UX, jamais de données du compte)
    # canal = "chat" (écran « Parle à Totor ») : l'échange est ENREGISTRÉ pour être
    # retrouvé d'un jour à l'autre. Les autres appels (aide, « Que se passe-t-il si »,
    # anciennes versions de l'app qui n'envoient pas le champ) restent éphémères.
    canal: Optional[str] = None


@app.post("/vapi/tools")
async def vapi_tools(request: Request, db: Session = Depends(get_db)):
    """Webhook des OUTILS de l'assistant vocal (Vapi). Auth par en-tête secret
    partagé (X-Vapi-Secret). Reçoit des tool-calls, exécute chercher_guide /
    escalader_humain / programmer_rappel (Phase 1) + verifier_abonnement /
    verifier_code (contrôle d'accès abonnés), renvoie les résultats au format Vapi."""
    import json as _json
    secret = os.environ.get("VAPI_SECRET", "")
    if secret and request.headers.get("x-vapi-secret", "") != secret:
        raise HTTPException(status_code=401, detail="Unauthorized")
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="JSON invalide")

    msg = payload.get("message") or {}
    # Contexte de l'appel (lu côté serveur, plus fiable que via le modèle) :
    # numéro de l'appelant (caller ID) + id d'appel (pour le verrou anti-force-brute).
    call = msg.get("call") or {}
    caller = (call.get("customer") or {}).get("number") or ""
    call_id = call.get("id")

    calls = msg.get("toolCallList") or msg.get("toolCalls") or []
    results = []
    for tc in calls:
        tcid = tc.get("id") or tc.get("toolCallId")
        fn = tc.get("function") or {}
        name = fn.get("name")
        args = fn.get("arguments")
        if args is None:
            args = tc.get("arguments") or {}
        if isinstance(args, str):
            try:
                args = _json.loads(args)
            except Exception:
                args = {}
        try:
            if name == "chercher_guide":
                res = voice_agent.chercher_guide(args.get("question", ""))
            elif name == "escalader_humain":
                res = voice_agent.escalader_humain(args.get("prenom"), args.get("telephone"), args.get("question"))
            elif name == "programmer_rappel":
                res = voice_agent.programmer_rappel(args.get("prenom"), args.get("telephone"), args.get("creneau"))
            elif name == "verifier_abonnement":
                # priorité au caller ID du payload ; repli sur l'argument du modèle.
                res = voice_access.verifier_abonnement(db, caller or args.get("telephone", ""))
            elif name == "verifier_code":
                res = voice_access.verifier_code(db, args.get("code", ""), call_id)
            else:
                res = "Outil inconnu."
        except Exception as e:
            print(f"[VAPI TOOL ERROR] {name}: {type(e).__name__}: {e}", flush=True)
            res = "Désolée, un souci technique. Je te propose de te faire rappeler par un humain."
        results.append({"toolCallId": tcid, "result": res})
    return {"results": results}


@app.get("/voice/code")
def voice_code(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Code du jour à afficher dans l'app (Plan B de l'accès à la secrétaire vocale).
    Réservé aux abonnés actifs : un non-abonné ne reçoit aucun code."""
    if not billing.is_premium(db, user):
        return {"abonne": False, "code": None, "chiffres": voice_access.CODE_DIGITS}
    return {"abonne": True, "code": voice_access.code_du_jour(user.id),
            "chiffres": voice_access.CODE_DIGITS}


# Jingle « TOTOR veille » joué au début de chaque appel vocal (Vapi lit cette URL
# comme firstMessage audio). MP3 léger (~150 Ko) pour un chargement rapide.
# Public, sans auth : Vapi doit pouvoir le récupérer.
_JINGLE_PATH = os.path.join(os.path.dirname(__file__), "static", "totor-veille.mp3")


@app.get("/voice/jingle.mp3")
def voice_jingle():
    if not os.path.exists(_JINGLE_PATH):
        raise HTTPException(status_code=404, detail="Jingle indisponible")
    return FileResponse(_JINGLE_PATH, media_type="audio/mpeg",
                        headers={"Cache-Control": "public, max-age=86400"})


def _espace_chat(profile) -> str:
    """L'espace dont on parle : chaque métier garde son fil de conversation."""
    return "intermittent" if (profile and profile.statut == "intermittent") else "auto_entrepreneur"


def enregistrer_echange_chat(db: Session, user_id: str, espace: str, question: str, reponse: str):
    """Historique « Parle à Totor » : garde la question et la réponse pour que la
    conversation se retrouve d'un jour à l'autre (demande testeuse du 24/07).
    JAMAIS bloquant : si l'enregistrement échoue, la réponse part quand même."""
    try:
        if question:
            db.add(ChatMessageDB(user_id=user_id, espace=espace, role="user", content=question[:8000]))
        db.add(ChatMessageDB(user_id=user_id, espace=espace, role="assistant", content=(reponse or "")[:8000]))
        db.commit()
    except Exception:
        db.rollback()


@app.get("/assistant/chat/historique")
def chat_historique(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Les derniers échanges « Parle à Totor » de l'espace courant, en ordre
    chronologique. La lecture ne touche à AUCUN quota (relire n'est pas discuter)."""
    profile = db.query(Profile).filter(Profile.user_id == user.id).first()
    espace = _espace_chat(profile)
    rows = (
        db.query(ChatMessageDB)
        .filter(ChatMessageDB.user_id == user.id, ChatMessageDB.espace == espace)
        .order_by(ChatMessageDB.created_at.desc())
        .limit(200)
        .all()
    )
    # Ordre chronologique ; à horodatage égal (même commit), la question précède la réponse.
    rows.sort(key=lambda r: (r.created_at or datetime.min, 0 if r.role == "user" else 1))
    return {
        "espace": espace,
        "messages": [
            {"role": r.role, "content": r.content, "date": r.created_at.isoformat() if r.created_at else None}
            for r in rows
        ],
    }


@app.delete("/assistant/chat/historique")
def effacer_chat_historique(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """« Repartir de zéro » : efface l'historique de l'espace courant seulement
    (l'autre espace garde le sien). Ne touche pas aux quotas."""
    profile = db.query(Profile).filter(Profile.user_id == user.id).first()
    espace = _espace_chat(profile)
    n = (
        db.query(ChatMessageDB)
        .filter(ChatMessageDB.user_id == user.id, ChatMessageDB.espace == espace)
        .delete()
    )
    db.commit()
    return {"ok": True, "supprimes": n}


@app.post("/assistant/chat")
def assistant_chat(
    req: AssistantRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not ANTHROPIC_API_KEY:
        raise HTTPException(status_code=500, detail="Assistant IA non configure")

    # Quota freemium (mensuel pour les gratuits) + garde-fou anti-abus journalier.
    # Le mode "aide" (mode d'emploi de l'app) ne consomme PAS le quota chat : le quota
    # protège l'expertise métier, pas le droit de comprendre l'app. Garde-fou séparé.
    mode_aide = req.mode == "aide"
    if mode_aide:
        _verifier_et_incrementer_quota_ia(db, user.id, "aide", 30)
    else:
        _consommer_quota(db, user, "chat", AI_CHAT_DAILY_LIMIT)

    profile = db.query(Profile).filter(Profile.user_id == user.id).first()
    context = ""
    if profile and profile.onboarding_complete and profile.statut == "auto_entrepreneur":
        entries = db.query(IncomeEntry).filter(IncomeEntry.user_id == user.id).all()
        incomes = [(e.date, e.amount) for e in entries]
        paid_invoices = (
            db.query(ClientInvoice)
            .filter(ClientInvoice.user_id == user.id, ClientInvoice.statut == "payee")
            .all()
        )
        incomes += [(inv.date_paiement or inv.date_emission, inv.montant) for inv in paid_invoices]
        try:
            result = estimate(
                statut=profile.statut, activite=profile.activite, periodicite=profile.periodicite,
                acre=profile.acre, versement_liberatoire=profile.versement_liberatoire,
                incomes=incomes, today=date.today(),
            )
            context = (
                f"Donnees reelles de l'utilisateur : statut auto-entrepreneur, activite {profile.activite}, "
                f"periodicite {profile.periodicite}. CA annuel {result.ca_annuel}EUR sur un plafond de {result.plafond}EUR "
                f"({result.pourcentage_plafond}%). Taux de cotisations global {result.taux_global_pct}%. "
                f"A provisionner pour la periode en cours ({result.periode_courante.label}) : {result.montant_a_provisionner}EUR, "
                f"echeance dans {result.periode_courante.jours_restants} jours ({result.periode_courante.date_limite_declaration}). "
            )
            if profile.solde_bancaire is not None:
                context += f"Solde bancaire actuel declare : {profile.solde_bancaire}EUR. "
            if profile.reserve_securite is not None:
                context += f"Objectif de reserve de securite qu'il s'est fixe : {profile.reserve_securite}EUR. "
            # Etat du profil emetteur (pour les factures/devis : mentions legales obligatoires)
            siret_ok = bool(profile.siret)
            adresse_ok = bool(profile.adresse)
            if siret_ok and adresse_ok:
                context += "Son profil emetteur est complet (SIRET et adresse renseignes). "
            else:
                manques = []
                if not siret_ok:
                    manques.append("le SIRET")
                if not adresse_ok:
                    manques.append("l'adresse de l'entreprise")
                context += (
                    f"ATTENTION : il manque {' et '.join(manques)} dans son profil. Ces informations "
                    "sont OBLIGATOIRES sur une facture ou un devis legalement valable. Si la personne "
                    "demande de creer une facture ou un devis, previens-la gentiment une fois que "
                    f"{' et '.join(manques)} doit etre renseigne dans son profil pour que le document "
                    "soit conforme — mais propose quand meme de preparer le document. "
                )
            context += (
                "Utilise ces vrais chiffres pour repondre precisement (ex: combien il peut se verser "
                "ou depenser sans risque, combien mettre de cote maintenant, quand il risque de depasser "
                "le plafond). Ne reclame jamais une information qui est deja ci-dessus."
            )
        except Exception:
            context = f"L'utilisateur est {profile.statut} en activite '{profile.activite}'."

    # ─── Contexte INTERMITTENT : on injecte les vraies donnees du compteur 507h ───
    is_intermittent = bool(profile and profile.statut == "intermittent")
    if is_intermittent:
        try:
            rows = (
                db.query(IntermittentActivity)
                .filter(IntermittentActivity.user_id == user.id)
                .all()
            )
            activites = [
                ie.Activite(date=r.date, type_activite=r.type_activite, nombre=r.nombre)
                for r in rows
            ]
            res = ie.calculer(activites, date_anniversaire=profile.date_anniversaire, aujourdhui=date.today())
            context = (
                f"Donnees reelles de l'utilisateur (intermittent du spectacle) : "
                f"il a cumule {res.total_heures} heures sur les 12 derniers mois glissants, "
                f"sur un seuil de {res.seuil} heures pour ouvrir ses droits. "
                f"Il lui manque {res.manquant} heures. "
                f"Seuil d'heures du filet (338h, premiere des deux conditions de la clause de rattrapage) "
                f"{'FRANCHI' if res.filet_atteint else 'pas encore franchi'}. "
                f"ATTENTION : franchir 338h ne garantit PAS le filet — il faut AUSSI avoir ouvert des "
                f"droits 5 fois sur 10 ans, ce que tu ne peux pas verifier ici. Ne dis donc jamais a "
                f"l'utilisateur que son filet est acquis sur la seule base des 338h : dis que c'est une "
                f"premiere condition remplie, et que la seconde depend de son historique (a verifier "
                f"aupres de France Travail). "
                f"Droits {'SECURISES' if res.droits_securises else 'pas encore securises'}. "
            )
            if res.date_anniversaire:
                if res.jours_avant_anniversaire is not None and res.jours_avant_anniversaire >= 0:
                    context += f"Sa date anniversaire est le {res.date_anniversaire}, dans {res.jours_avant_anniversaire} jours. "
                    if getattr(res, "projection_disponible", False):
                        context += (
                            f"PROJECTION A SON ECHEANCE (essentiel, distingue bien ces deux chiffres) : "
                            f"son compteur AUJOURD'HUI est {res.total_heures}h, mais ce qui compte pour "
                            f"renouveler, c'est ce qu'il aura A SA DATE ANNIVERSAIRE (les vieux contrats de "
                            f"plus de 12 mois sortent de la fenetre d'ici la). "
                            f"S'il ne retravaille plus d'ici l'echeance (plancher), il serait a "
                            f"{res.projection_plancher_heures}h (il manquerait {res.projection_plancher_manquant}h). "
                        )
                        if getattr(res, "projection_a_des_contrats_futurs", False):
                            context += (
                                f"En comptant ses contrats futurs DEJA SAISIS (qui sont, par convention du dossier, "
                                f"des contrats DEJA SIGNES donc certains), il serait a "
                                f"{res.projection_avec_prevus_heures}h (il manquerait {res.projection_avec_prevus_manquant}h). "
                                f"Explique-lui la difference : ce qu'il a deja realise/securise vs ce qu'il a deja signe. "
                                f"Les contrats seulement POSSIBLES ne sont jamais dans le dossier : pour ceux-la, oriente-le "
                                f"vers le simulateur 'Que se passe-t-il si'. Ne melange jamais le certain et l'hypothetique. "
                            )
                        context += (
                            "IMPORTANT : ne confonds jamais le compteur d'aujourd'hui avec la projection a "
                            "l'echeance. Un intermittent peut avoir 'assez' aujourd'hui mais pas assez a sa "
                            "date anniversaire si de vieux contrats vont sortir. C'est ce risque que tu dois "
                            "l'aider a voir, sans l'affoler : tu montres le chemin, pas juste l'alerte. "
                        )
                else:
                    context += f"Sa date anniversaire ({res.date_anniversaire}) est passee — invite-le a faire le point avec France Travail. "
            else:
                context += (
                    "Il n'a PAS encore renseigne sa date anniversaire. C'est une info CLE : sans elle, tu ne "
                    "peux pas lui dire s'il va renouveler (tu connais son compteur du jour, mais pas ce qu'il "
                    "aura a son echeance). Invite-le gentiment a la renseigner dans son cockpit pour que tu "
                    "puisses l'aider sur le renouvellement. Ne devine jamais cette date. "
                )
            context += (
                "Utilise ces vrais chiffres pour repondre precisement a ses questions sur ses 507h "
                "(ex: combien d'heures il lui reste a faire, ou il en est, si tel contrat l'aiderait). "
                "Ne reclame jamais une information deja presente ci-dessus."
            )
        except Exception:
            context = "L'utilisateur est intermittent du spectacle. Ses donnees de compteur ne sont pas disponibles pour le moment."

    if mode_aide:
        # L'Aide vivante : Totor guide dans l'app (carte des écrans + lexique dans aide_app.py).
        system_prompt = prompt_aide(profile.statut if profile else "auto_entrepreneur")
    elif is_intermittent:
        system_prompt = (
            "Tu es Totor, le compagnon de confiance d'un intermittent du spectacle francais. "
            "Tu es un EXPERT du regime intermittent, et tu en es fier. Tu n'es pas une IA generaliste : "
            "tu es specialise, precis, et profondement honnete. La communaute des intermittents a "
            "l'habitude qu'on lui explique mal ses droits — toi, tu rends les choses CLAIRES. "
            "LA chose la plus importante de ta personnalite : tu n'es pas un conseiller qui assene une "
            "reponse, tu es un compagnon qui regarde la situation AVEC la personne. Tu es de son cote, "
            "tu veilles sur ses heures avec elle, tu l'accompagnes dans la duree. Glisse naturellement "
            "une touche de presence par reponse ('je garde un oeil sur ton compteur', 'on refait le "
            "point apres ton prochain contrat'), sans en abuser. "
            "Tu es fidele, calme, rassurant, jamais dans le jugement. Tu ne te re-presentes JAMAIS "
            "(la personne est dans l'app Totor) : reponds directement, sans preambule. "
            "Tu as une ame de chien fidele mais tu ne la joues JAMAIS de facon caricaturale : aucun "
            "aboiement, aucun jeu de mots canin, pas d'emojis pattes. "
            "Tu reponds en francais, clair et direct, en tutoyant, et tu vas a l'essentiel. "
            "\n"
            "MEMOIRE DU FIL : cette conversation est CONSERVEE dans l'app. La personne la retrouve "
            "d'un jour a l'autre et peut l'effacer quand elle veut (« Repartir de zero », sous le chat). "
            "Toi, tu recois les messages recents du fil : ne pretends jamais te souvenir d'un echange "
            "qui n'est pas dans les messages fournis ; si on t'evoque un vieil echange absent, dis "
            "simplement que tu n'as plus ce detail sous les yeux et repars de la question du jour. "
            "\n"
            "SEPARATION DES METIERS (absolue) : cette personne est intermittente du spectacle. Tu ne "
            "mentionnes JAMAIS de notions d'auto-entreprise (cotisations URSSAF micro, versement "
            "liberatoire, la Paie lissee, chiffre d'affaires, TVA micro) : ce n'est pas son monde, "
            "les melanger serait une faute qui trahit l'app. "
            "\n\n"
            "TON EXPERTISE — tu maitrises le regime intermittent en profondeur, et tu reponds avec "
            "precision a presque toutes les questions. Voici ta base de connaissances fiable : "
            "\n"
            "OUVERTURE DES DROITS : il faut 507 heures sur les 12 mois (365 jours) glissants precedant "
            "la derniere fin de contrat retenue. C'est la derniere fin de contrat (FCT) qui fixe le point "
            "de depart de la fenetre de 12 mois et la date anniversaire. Pour s'inscrire : francetravail.fr/spectacle. "
            "\n"
            "ANNEXES : annexe 8 = techniciens et ouvriers (payes a l'heure reelle) ; annexe 10 = artistes "
            "(payes au cachet). Chaque cachet d'artiste compte pour 12h dans le decompte des droits, "
            "qu'il soit isole ou consecutif : Totor applique 12h a TOUS les cachets. (Une ancienne regle "
            "comptait certains cachets groupes 8h ; elle n'est PAS appliquee, faute de source fiable.) "
            "On peut cumuler des heures des deux annexes ; c'est l'annexe ou on a le "
            "plus d'heures qui s'applique. A profil egal, l'annexe 10 est souvent plus favorable. "
            "Il existe des plafonds mensuels de declaration (un nombre maximal d'heures pour les techniciens, "
            "de cachets pour les artistes), mais ne cite pas de chiffre exact si on ne te le demande pas "
            "precisement : ces plafonds evoluent et la valeur a jour se verifie aupres de France Travail. "
            "\n"
            "DATE ANNIVERSAIRE : reexamen 12 mois apres la fin de contrat ayant ouvert les droits (flottante, "
            "differente chaque annee). A cette date il faut 507 nouvelles heures pour etre readmis, sinon "
            "bascule vers le regime general ou la clause de rattrapage. "
            "\n"
            "CLAUSE DE RATTRAPAGE : si entre 338 et 506h a la date anniversaire, prolongation de l'indemnisation "
            "jusqu'a 6 mois au meme taux. Les heures faites pendant comptent pour rouvrir les droits. "
            "On peut la refuser pour continuer a chercher ses 507h jusqu'a l'echeance. "
            "\n"
            "HEURES ASSIMILEES (comptent dans les 507h sans contrat) : certaines periodes sans contrat sont "
            "assimilees a du temps de travail et comptent dans les 507h — notamment conge maternite/paternite, "
            "accident du travail, arret maladie longue duree (ALD), et certaines heures de formation ou "
            "d'enseignement artistique/technique sous conditions. Le nombre d'heures retenu par jour assimile "
            "obeit a un bareme precis : explique le principe, mais renvoie a France Travail pour le compte exact. "
            "\n"
            "CONGES SPECTACLES (Audiens) : tes conges payes sont verses par la Caisse des Conges Spectacles, "
            "pas par l'employeur. Les employeurs cotisent sur ton brut, et tu demandes ton indemnite chaque "
            "annee sur une periode de reference (1er avril au 31 mars) — si tu ne la demandes pas dans les "
            "delais, les droits de la periode peuvent etre perdus. Pour les taux et montants exacts, oriente "
            "vers Audiens : ce sont leurs baremes, ils evoluent. "
            "\n"
            "AUTRES DISPOSITIFS que tu connais : droit d'option (renoncer au regime general pour ouvrir en "
            "annexe 8/10 des qu'on a 507h) ; Afdas (formation, sous conditions d'anciennete et de cachets) ; "
            "retraite complementaire Agirc-Arrco ; Garantie Sante Intermittents Audiens (sous condition d'un "
            "nombre de cachets ou d'heures) ; il existe aussi un dispositif de maintien des droits jusqu'a la "
            "retraite sous conditions d'anciennete. Pour ces dispositifs, explique le principe et les conditions "
            "generales, mais renvoie a l'organisme concerne (Audiens, Afdas, France Travail) pour les seuils "
            "chiffres exacts : tu ne les affirmes pas de memoire. "
            "\n\n"
            "LE CALCUL DE L'ALLOCATION (ARE) — tu EXPLIQUES la mecanique clairement pour que la personne "
            "COMPRENNE comment ca marche, mais tu ne donnes JAMAIS de chiffre final ni de coefficient precis : "
            "le moteur de calcul ARE n'est pas encore valide cote Totor, et on refuse d'inventer un nombre. "
            "Voici la logique que tu peux expliquer : "
            "L'allocation journaliere (AJ) combine trois composantes — une partie liee a ton salaire de reference, "
            "une partie liee a ton nombre d'heures travaillees, et une partie fixe. Il existe un plancher (l'AJ "
            "ne descend pas sous un certain montant par jour) et un plafond. Tu peux dire si une situation tend "
            "plutot vers le haut ou le bas de la fourchette, mais SANS calculer toi-meme le montant en euros. "
            "\n"
            "REPERES CHIFFRES SOURCES (referentiel TOTOR, version 2026, memes valeurs que les moteurs de "
            "calcul de l'app) : tu PEUX donner ces constantes telles quelles — ce sont des parametres PUBLIES, "
            "pas des calculs personnels — en precisant toujours 'valeur 2026' et que France Travail fait foi : "
            f"plancher de l'AJ artiste (annexe 10) = {regle_valeur('allocationParametresAnnexe10')['plancherAJ']:.0f} euros/jour ; "
            f"plancher technicien (annexe 8) = {regle_valeur('allocationParametresAnnexe8')['plancherAJ']:.0f} euros/jour ; "
            f"plafond de l'AJ = {regle_valeur('allocationPlafondAJ'):.2f} euros/jour ; "
            f"retenue retraite complementaire = 0,93 % du salaire journalier moyen quand l'AJ depasse 31,96 euros. "
            "Quand quelqu'un demande une ECHELLE ('c'est quoi le plancher ? le plafond ?'), tu donnes ces reperes "
            "franchement au lieu de te derober : refuser une constante publiee, c'est ne pas repondre. "
            "\n"
            "SA PROJECTION PERSONNELLE existe DANS l'app : la carte 'Ton prochain renouvellement' sur le Cockpit "
            "(abonnes TOTOR Veille) projette son allocation estimee depuis ses AEM scannees, avec la courbe de "
            "l'effet de chaque cachet en plus. Quand on te demande une projection personnelle chiffree, "
            "oriente vers cette carte (qui utilise le moteur valide) plutot que de refuser sechement. "
            "POINT CLE souvent incompris, que tu DOIS expliquer clairement : France Travail ne paie pas tous les "
            "jours du mois. Le nombre de jours NON indemnises depend des heures travaillees dans le mois — plus "
            "tu travailles un mois, moins tu as de jours indemnises ce mois-la (mais tu gardes tes salaires). "
            "C'est un cumul salaire + allocation, lui aussi plafonne. Tu expliques ce mecanisme avec des mots, "
            "sans sortir la formule chiffree exacte (elle se verifie sur le simulateur). "
            "PLUSIEURS mecanismes retardent ou reduisent les premiers paiements : un differe d'indemnisation, un "
            "delai d'attente, une franchise conges payes, une franchise salaires. Explique a quoi ils servent. "
            "Quand quelqu'un demande son montant exact : explique-lui d'abord ces mecanismes pour qu'il comprenne, "
            "DIS-LUI clairement que tu ne donnes pas le chiffre toi-meme parce qu'il depend de parametres fins "
            "(salaire de reference precis, franchises, dates) et que tu refuses de lui donner un nombre approximatif "
            "qui pourrait l'induire en erreur, PUIS oriente-le vers le simulateur OFFICIEL de France Travail "
            "('estimez vos allocations' sur leur site) qui calcule a partir de ses vrais salaires et heures. "
            "Ne sors jamais toi-meme un montant en euros, meme approximatif, meme si on insiste. "
            "\n\n"
            "TA POSTURE — tu es la pour aider la personne a COMPRENDRE et a DECIDER, pas pour botter en touche, "
            "mais pas non plus pour balancer des chiffres que tu ne peux pas garantir. Distingue trois registres : "
            "(1) LE COEUR QUE TU MAITRISES DE FACON SURE — les heures, les 507h, la fenetre de 12 mois glissants, "
            "la conversion des cachets en heures (12h), la clause de rattrapage (338-506h, 6 mois) : la-dessus tu "
            "es PRECIS, AFFIRMATIF, tu vas au fond, tu ne renvoies pas vers France Travail par reflexe. "
            "(2) LES CONSTANTES PUBLIEES (planchers, plafond, cachet 12h, seuils du referentiel ci-dessus) : "
            "tu les DONNES, datees 'valeur 2026', France Travail fait foi. "
            "(3) LES CALCULS PERSONNELS — le montant exact de SON allocation, ses jours indemnises, ses "
            "franchises : la-dessus tu expliques la LOGIQUE clairement, tu peux situer (plutot haut, plutot bas), "
            "mais tu ne CALCULES pas toi-meme le chiffre : tu orientes vers la carte 'Ton prochain "
            "renouvellement' du Cockpit (moteur valide) et le simulateur officiel de France Travail. "
            "Cette prudence n'est pas de la faiblesse : c'est ce qui rend Totor fiable. Mieux vaut un 'voici la "
            "logique, le chiffre exact se verifie ici' qu'un nombre approximatif faux. "
            "Si une information precise te manque pour conclure (par ex. tu ne sais pas son salaire de reference), "
            "tu POSES la bonne question pour avancer. "
            "Tu ne devines JAMAIS un chiffre reglementaire dont tu n'es pas sur : dans le doute sur une valeur "
            "exacte (un coefficient, un plafond, un seuil), tu dis l'ordre de grandeur si tu le connais ET que la "
            "valeur exacte est a confirmer sur le simulateur ou le site officiel — ou tu ne donnes pas le chiffre. "
            "\n\n"
            "Tu vis A L'INTERIEUR de l'app TOTOR. Quand c'est utile, renvoie vers les sections : le Cockpit "
            "(le compteur 507h, la date anniversaire, la carte 'Ton prochain renouvellement' avec l'allocation "
            "estimee et sa courbe), 'Mes AEM' (scanner ses attestations), 'Mes activites' (saisir ses cachets "
            "et heures, y compris formation et autre salaire hors spectacle), 'Mes documents' (recapitulatif de "
            "revenus detaille), 'Comprendre' (les fiches sur le regime). Ne recommande JAMAIS un outil "
            "concurrent payant. "
            "\n\n"
            f"{context} "
            "\n\n"
            "IMPORTANT : ecris en texte simple, SANS aucun formatage Markdown (pas d'asterisques, pas de "
            "dieses, pas de tirets en debut de ligne). Si tu enumeres, fais-le en phrases. "
            "Reponses claires et completes mais sans blabla (vise 5-10 lignes, plus si la question est technique "
            "et le merite). Tu peux confirmer aupres de France Travail Spectacle pour les cas vraiment importants, "
            "mais seulement en complement d'une vraie reponse de ta part, jamais a la place."
        )
    else:
        system_prompt = (
        "LA chose la plus importante de ta personnalite : tu n'es pas un conseiller qui donne "
        "une reponse, tu es un compagnon qui regarde la situation AVEC la personne. La nuance "
        "est cruciale. Un conseiller dit 'voici la reponse'. Toi tu dis 'on regarde ca ensemble'. "
        "Tu es de son cote, tu veilles sur son argent avec elle, tu l'accompagnes dans la duree. "
        "Emploie naturellement des tournures qui montrent que tu es present a ses cotes : "
        "'on reverra ca ensemble', 'je garde un oeil dessus', 'je veille sur ca avec toi', "
        "'attends tes prochains encaissements et on refait le point'. Sans en abuser : une de ces "
        "touches par reponse suffit, glissee naturellement, jamais plaquee. "
        "Tu es fidele, calme, rassurant, jamais dans le jugement. Tu ne te re-presentes JAMAIS "
        "(la personne sait qui tu es, elle est dans l'app Totor) : reponds directement, sans "
        "'Salut je suis Totor' ni preambule. "
        "Quand une reponse est difficile (ex: 'non, n'achete pas ca maintenant'), reste honnete "
        "mais humain : pas de 'la reponse est non' sec. Plutot : 'avec X EUR aujourd'hui, je ne te "
        "le conseillerais pas pour le moment — attends tes prochaines rentrees et on regardera ca "
        "ensemble.' La personne doit sentir quelqu'un AVEC elle, pas un verdict. "
        "Tu as une ame de chien fidele, mais tu ne la joues JAMAIS de facon caricaturale : aucun "
        "aboiement, aucun jeu de mots canin, pas d'emojis pattes. Le meilleur Totor, ce n'est pas "
        "un chien qui parle — c'est ce que ton meilleur compagnon te repondrait s'il comprenait la "
        "fiscalite et tes comptes. "
        "Tu reponds en francais, clair et direct, en tutoyant, et tu vas a l'essentiel sans blabla. "
        "MEMOIRE DU FIL : cette conversation est CONSERVEE dans l'app. La personne la retrouve "
        "d'un jour a l'autre et peut l'effacer quand elle veut (« Repartir de zero », sous le chat). "
        "Toi, tu recois les messages recents du fil : ne pretends jamais te souvenir d'un echange "
        "qui n'est pas dans les messages fournis ; si on t'evoque un vieil echange absent, dis "
        "simplement que tu n'as plus ce detail sous les yeux et repars de la question du jour. "
        "SEPARATION DES METIERS (absolue) : cette personne est auto-entrepreneur. Tu ne mentionnes "
        "JAMAIS de notions d'intermittent du spectacle (AEM ou attestation employeur, 507 heures, "
        "cachets, actualisation, allocation, ARE, France Travail, date anniversaire) : ce n'est pas "
        "son monde, les melanger serait une faute qui trahit l'app. "
        "CAPACITE SPECIALE — preparer un devis OU une facture : si la personne te demande de "
        "preparer/faire un devis ou une facture (ex: 'fais-moi un devis pour Dupont, 800 EUR de design', "
        "ou 'une facture pour Martin de 354 EUR de consulting'), tu reponds en une phrase chaleureuse, "
        "PUIS tu ajoutes a la toute fin de ton message un bloc technique sur une ligne separee, au "
        "format EXACT suivant (rien apres) : "
        "[[DOC:{\"type\":\"devis\",\"client_nom\":\"...\",\"client_adresse\":\"\",\"client_email\":\"\",\"lignes\":[{\"description\":\"...\",\"quantite\":1,\"prix_unitaire\":800}],\"notes\":\"\"}]] "
        "Regles du bloc : 'type' vaut 'devis' ou 'facture' selon la demande ; client_nom obligatoire ; "
        "lignes est une liste avec description, quantite (defaut 1) et prix_unitaire en euros ; "
        "client_adresse et client_email sont optionnels (laisse vide si non fournis). "
        "N'invente JAMAIS un montant, un client ou une prestation absents de la demande. S'il manque "
        "le client, le montant OU la description de la prestation, ne mets PAS de bloc et demande "
        "gentiment l'info manquante (une facture sans description de prestation n'est pas valable). "
        "Si on te demande si un prix est coherent, donne ton avis honnete AVANT de proposer le document. "
        "Ne mets ce bloc QUE pour une vraie demande de creation de devis ou facture. "
        "Ne mentionne jamais le bloc ni son format a l'utilisateur (il devient un bouton dans l'interface). "
        f"{context} "
        "TRES IMPORTANT — tu vis A L'INTERIEUR de l'application TOTOR, et tu connais ce "
        "qu'elle sait faire. Quand l'utilisateur demande une action que TOTOR propose, tu "
        "le renvoies vers la bonne section de l'app, tu ne dis JAMAIS que tu ne peux pas et "
        "tu ne recommandes JAMAIS un outil concurrent (Indy, Freebe, Shine, Tiime, Abby, etc.). "
        "Voici ce que TOTOR fait, vers quoi orienter l'utilisateur : "
        "- creer, envoyer par email et telecharger en PDF des factures (section 'Facturer') ; "
        "- creer des devis et les convertir en factures (section Outils > Devis) ; "
        "- enregistrer et scanner ses frais par photo (section 'Encaisser / Frais') ; "
        "- suivre ce qu'il peut depenser, sa reserve et ses echeances (le Cockpit) ; "
        "- preparer sa declaration URSSAF (section 'Preparer'). "
        "Exemple : si on te demande 'tu peux faire une facture ?', reponds que OUI, l'app le "
        "fait, et indique d'aller dans 'Facturer'. "
        "Tu donnes des conseils pratiques et ACTIONNABLES sur : combien il peut depenser ou se "
        "verser sans risque, comment mettre de l'argent de cote pour l'URSSAF et les impots avant "
        "de les depenser par erreur, comment se constituer une reserve de securite adaptee a la "
        "regularite de ses revenus, et comment lisser des rentrees d'argent irregulieres. "
        "Tu maitrises aussi l'URSSAF, les cotisations, la TVA, l'ACRE et la declaration de revenus. "
        "Si l'utilisateur precise son metier (graphiste, coach, artisan, createur de contenu, "
        "consultant...), adapte tes conseils a la realite de ce metier : saisonnalite, gros "
        "montants espaces ou petits revenus reguliers, frais typiques du secteur. "
        "Quand c'est utile, propose-lui une regle simple a suivre plutot qu'une longue explication "
        "(ex: 'mets X% de cote a chaque encaissement'). "
        "Tu rappelles de consulter un comptable pour les cas complexes, sans en faire trop. "
        "IMPORTANT : ecris en texte simple, SANS aucun formatage Markdown. N'utilise jamais "
        "d'asterisques (*, **), de dièses (#), ni de tirets en debut de ligne pour des listes. "
        "Si tu dois enumerer, ecris en phrases ou separe par des sauts de ligne simples. "
        "Reponses courtes (5-8 lignes maximum sauf si l'utilisateur demande plus de detail)."
    )

    try:
        resp = http_requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-sonnet-4-6",
                "max_tokens": 800,
                "system": system_prompt,
                "messages": [{"role": m.role, "content": m.content} for m in req.messages],
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        reply = "".join(block.get("text", "") for block in data.get("content", []))
        # Filet de securite : meme si le modele glisse du Markdown, on nettoie l'affichage.
        if reply:
            import re as _re
            # On protege d'abord un eventuel bloc [[DOC:...]] pour que le nettoyage ne l'abime pas.
            doc_blocks = _re.findall(r"\[\[DOC:.*?\]\]", reply, _re.DOTALL)
            reply = _re.sub(r"\[\[DOC:.*?\]\]", "\x00DOC\x00", reply, flags=_re.DOTALL)
            reply = _re.sub(r"\*{1,3}", "", reply)          # retire * ** ***
            reply = _re.sub(r"(?m)^\s{0,3}#{1,6}\s*", "", reply)  # retire les titres #
            reply = _re.sub(r"(?m)^\s*[-•]\s+", "", reply)   # retire les puces - ou •
            # On remet le bloc intact.
            for blk in doc_blocks:
                reply = reply.replace("\x00DOC\x00", blk, 1)
            reply = reply.strip()
    except Exception as e:
        # On logge l'erreur reelle (masquee) cote serveur, jamais l'exception brute au client
        # (elle peut contenir la cle Anthropic si l'en-tete x-api-key est invalide).
        print(f"[ASSISTANT ERROR] {type(e).__name__}: {billing.redact_secrets(e)}", flush=True)
        raise HTTPException(status_code=502, detail="L'assistant n'est pas disponible pour le moment. Réessaie dans un instant.")

    # Radar UX (mode aide) : la PREMIÈRE question de chaque conversation part en copie
    # à Camille — la question et l'écran SEULEMENT (jamais de données du compte, aucun
    # identifiant en clair). Les relances de la même conversation ne partent pas
    # (retour Camille 09/07 : un email par message = trop bavard). Jamais bloquant.
    if mode_aide and sum(1 for m in req.messages if m.role == "user") == 1:
        try:
            question = next((m.content for m in reversed(req.messages) if m.role == "user"), "")
            send_email(
                SUPPORT_EMAIL,
                f"[Aide] {req.ecran or 'écran inconnu'}",
                f"<div style='font-family:sans-serif;max-width:480px'>"
                f"<p><strong>Écran :</strong> {html.escape(req.ecran or 'inconnu')}</p>"
                f"<p><strong>Question :</strong> {html.escape(question[:500])}</p>"
                f"<p style='color:#6B7A8D;font-size:12px'>Radar UX de l'Aide vivante — aucune donnée de compte.</p></div>",
            )
        except Exception:
            pass

    # Historique (canal "chat" du « Parle à Totor » UNIQUEMENT) : la conversation se
    # retrouve d'un jour à l'autre. L'aide et les canaux éphémères ne laissent rien.
    if req.canal == "chat" and not mode_aide and reply:
        derniere_question = next((m.content for m in reversed(req.messages) if m.role == "user"), "")
        enregistrer_echange_chat(db, user.id, _espace_chat(profile), derniere_question, reply)

    return {"reply": reply or "Desole, je n'ai pas pu generer de reponse."}


# ════════════════════════════════════════════════════════════════════════════
#  INTERMITTENT DU SPECTACLE — endpoints du module de suivi des 507h.
#  Suivi INDICATIF (ne remplace pas France Travail). Le calcul vit dans
#  intermittent_engine.py ; ici on ne fait que lire/écrire des activités et
#  appeler le moteur. Aucun calcul d'indemnisation en euros (niveau B = plus tard).
# ════════════════════════════════════════════════════════════════════════════

TYPES_ACTIVITE_INTERMITTENT = ("heures", "cachet_isole", "cachet_groupe", "formation", "enseignement",
                               "arret_maternite", "arret_accident", "arret_ald", "arret_suspension",
                               "arret_maladie_ordinaire", "arret_paternite",
                               # Salaire HORS intermittence (pub, mannequinat, régime général…) :
                               # 0h pour le moteur 507 (type inconnu de heures_de → 0, par construction),
                               # exclu des Congés Spectacles et de la projection AJ (listes blanches),
                               # visible dans le récap de revenus (demande testeuse 24/07/2026).
                               "autre_salaire")


class IntermittentActiviteRequest(BaseModel):
    date: date
    date_fin: Optional[date] = None
    type_activite: str
    nombre: float
    employeur: Optional[str] = None
    salaire_brut: Optional[float] = None
    # PAS prélevé sur ce contrat, recopié du bulletin de paie (donnée réelle).
    pas_montant: Optional[float] = None
    aem_recue: Optional[bool] = False
    estime: Optional[bool] = False
    aem_filename: Optional[str] = None
    aem_r2_key: Optional[str] = None
    metier: Optional[str] = None  # "artiste" | "technicien" | None (informatif, annexe 8/10)


def _metier_valide(m):
    """Ne laisse entrer que les deux valeurs connues — tout le reste devient None."""
    return m if m in ("artiste", "technicien") else None


class DateAnniversaireRequest(BaseModel):
    date_anniversaire: Optional[date] = None
    montant_journalier: Optional[float] = None


class SimulationContratRequest(BaseModel):
    date: date
    type_activite: str
    nombre: float


class AllocationRequest(BaseModel):
    annexe: str                      # "annexe8" | "annexe10"
    salaire_reference: float
    heures_reference: float


def _allocation_pour_profil(profile: Optional[Profile]) -> Optional[dict]:
    """
    Recalcule l'allocation journalière à partir des éléments stockés sur le profil
    (salaire de référence + heures + annexe, saisis depuis la notification France
    Travail). Applique la Loi X : `affichable` dit si Totor a le DROIT de montrer
    le montant. Compare aussi au montant officiel (montant_journalier) si présent.
    Retourne None si les éléments de calcul ne sont pas renseignés.
    """
    if not profile or profile.salaire_reference is None or profile.heures_reference is None \
            or not profile.annexe_allocation:
        return None
    try:
        res = ae.calculer_aj(profile.annexe_allocation, profile.salaire_reference, profile.heures_reference)
    except ValueError:
        return None
    affichable, raison = ae.branche_affichable(profile.annexe_allocation, res)
    out = {
        "affichable": affichable,
        "raison_non_affichable": raison,
        "annexe": profile.annexe_allocation,
        "salaire_reference": profile.salaire_reference,
        "heures_reference": profile.heures_reference,
        "aj_brute": res["aj_brute"],
        "aj_nette": res["aj_nette"],
        "partie_a": res["partie_a"],
        "partie_b": res["partie_b"],
        "partie_c": res["partie_c"],
        "retenue_retraite": res["retenue_retraite"],
        "plancher_applique": res["plancher_applique"],
        "plafond_applique": res["plafond_applique"],
    }
    # Comparaison avec le montant OFFICIEL lu sur l'ARE (jamais recalculé), si présent.
    if profile.montant_journalier is not None:
        ecart = round(res["aj_nette"] - profile.montant_journalier, 2)
        out["montant_officiel"] = profile.montant_journalier
        out["ecart_officiel"] = ecart
        out["coherent_officiel"] = abs(ecart) <= 0.50   # tolérance d'un demi-euro (arrondis)
    return out


def _activites_modele_vers_moteur(rows: list) -> list:
    """Convertit les lignes DB en objets Activite que le moteur comprend."""
    return [
        ie.Activite(date=r.date, type_activite=r.type_activite, nombre=r.nombre)
        for r in rows
    ]


def _resultat_vers_dict(res) -> dict:
    """Sérialise un ResultatIntermittent pour le frontend."""
    return {
        "total_heures": res.total_heures,
        "seuil": res.seuil,
        "manquant": res.manquant,
        "pourcentage": res.pourcentage,
        "droits_securises": res.droits_securises,
        "filet_atteint": res.filet_atteint,
        "hector_etat": res.hector_etat,
        "hector_message": res.hector_message,
        "verdict": res.verdict,
        "jours_avant_anniversaire": res.jours_avant_anniversaire,
        "date_anniversaire": res.date_anniversaire,
        "projection_disponible": getattr(res, "projection_disponible", False),
        "projection_plancher_heures": getattr(res, "projection_plancher_heures", None),
        "projection_plancher_manquant": getattr(res, "projection_plancher_manquant", None),
        "projection_plancher_securise": getattr(res, "projection_plancher_securise", None),
        "projection_avec_prevus_heures": getattr(res, "projection_avec_prevus_heures", None),
        "projection_avec_prevus_manquant": getattr(res, "projection_avec_prevus_manquant", None),
        "projection_avec_prevus_securise": getattr(res, "projection_avec_prevus_securise", None),
        "projection_a_des_contrats_futurs": getattr(res, "projection_a_des_contrats_futurs", False),
        "arret_estimation": getattr(res, "arret_estimation", False),
        "jours_allonges": getattr(res, "jours_allonges", 0),
        "detail_lignes": res.detail_lignes,
        "regles_appliquees": getattr(res, "regles_appliquees", []),
        "version_referentiel": getattr(res, "version_referentiel", ""),
        "avertissement": res.avertissement,
    }


@app.get("/intermittent/activites")
def list_intermittent_activites(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    rows = (
        db.query(IntermittentActivity)
        .filter(IntermittentActivity.user_id == user.id)
        .order_by(IntermittentActivity.date.desc())
        .all()
    )
    return [
        {
            "id": r.id,
            "date": r.date,
            "date_fin": r.date_fin,
            "employeur": r.employeur,
            "type_activite": r.type_activite,
            "nombre": r.nombre,
            "salaire_brut": r.salaire_brut,
            "pas_montant": r.pas_montant,
            "aem_recue": r.aem_recue,
            "estime": r.estime,
            "aem_filename": r.aem_filename,
            "a_document": bool(r.aem_r2_key),
            "source": r.source,
            "metier": r.metier,
            "doublon_ok": bool(r.doublon_ok),
        }
        for r in rows
    ]


@app.post("/intermittent/activite")
def add_intermittent_activite(
    req: IntermittentActiviteRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if req.type_activite not in TYPES_ACTIVITE_INTERMITTENT:
        raise HTTPException(status_code=400, detail="Type d'activite invalide")
    if req.nombre is None or req.nombre < 0:
        raise HTTPException(status_code=400, detail="Nombre invalide")
    row = IntermittentActivity(
        user_id=user.id,
        date=req.date,
        date_fin=(req.date_fin or None),
        employeur=(req.employeur or None),
        type_activite=req.type_activite,
        nombre=req.nombre,
        salaire_brut=req.salaire_brut,
        pas_montant=req.pas_montant,
        aem_recue=bool(req.aem_recue),
        estime=bool(req.estime),
        aem_filename=(req.aem_filename or None),
        aem_r2_key=(req.aem_r2_key or None),
        source=("ocr" if req.aem_recue else "manuel"),
        metier=_metier_valide(req.metier),
    )
    db.add(row)
    db.commit()
    return {"ok": True, "id": row.id}


@app.post("/intermittent/aem/extract")
async def extract_aem(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Lit une AEM (photo ou PDF) via Claude Vision et renvoie les champs détectés.
    Ne crée rien : le front affiche le résultat pour vérification, puis appelle
    /intermittent/activite avec les valeurs validées par l'utilisateur."""
    allowed_ext = (".pdf", ".jpg", ".jpeg", ".png", ".webp")
    if not file.filename or not file.filename.lower().endswith(allowed_ext):
        raise HTTPException(status_code=400, detail="Format non supporté (PDF, JPG, PNG).")

    # Quota freemium (mensuel pour les gratuits) + garde-fou anti-abus journalier.
    _consommer_quota(db, user, "aem_scan", AI_AEM_DAILY_LIMIT)

    tmp_dir = tempfile.mkdtemp()
    file_path = os.path.join(tmp_dir, os.path.basename(file.filename))
    try:
        # Écriture bornée : on lit par blocs et on s'arrête net au-delà de la taille max
        # (anti-DoS : on ne charge jamais un fichier géant en mémoire ni sur disque).
        taille = 0
        with open(file_path, "wb") as f:
            while True:
                chunk = await file.read(1024 * 1024)  # 1 Mo
                if not chunk:
                    break
                taille += len(chunk)
                if taille > AEM_MAX_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=f"Fichier trop volumineux (max {AEM_MAX_BYTES // (1024*1024)} Mo). "
                               "Réduis la taille de la photo et réessaie.",
                    )
                f.write(chunk)

        try:
            aems = extract_aem_data(file_path)
        except HTTPException:
            raise
        except Exception as e:
            # Vigie fondateur (23/07/2026, « plus jamais ce bug ») : chaque échec
            # DÉFINITIF de scan (après les 3 tentatives) remonte à Sentry — motif et
            # extension seulement, JAMAIS le contenu du document ni le nom du fichier.
            try:
                sentry_sdk.capture_message(
                    f"Scan AEM en échec ({os.path.splitext(file.filename)[1].lower()}) : {billing.redact_secrets(str(e))}",
                    level="warning",
                )
            except Exception:
                pass  # la vigie ne doit jamais casser le scan lui-même
            raise HTTPException(status_code=422, detail=billing.redact_secrets(str(e)) or "Impossible de lire cette AEM.")

        # Conserve le document original sur R2 (si configuré). Le même document peut contenir
        # plusieurs AEM : on stocke le fichier une fois et on lie la même clé à chacune.
        r2_key = None
        if r2_storage.R2_ENABLED:
            try:
                r2_key = r2_storage.upload_aem(file_path, str(user.id), file.filename)
            except Exception:
                # L'échec du stockage ne doit pas bloquer le scan : les données restent exploitables.
                r2_key = None
        for a in aems:
            a["aem_r2_key"] = r2_key

        # Format de réponse : on renvoie toujours une liste sous "aems".
        # (Le front gère 1 ou plusieurs attestations à valider.)
        return {"aems": aems}
    finally:
        # Nettoyage systématique du fichier temporaire (évite l'accumulation sur disque).
        shutil.rmtree(tmp_dir, ignore_errors=True)


# Adresse qui reçoit les documents signalés illisibles (lecture humaine).
SUPPORT_EMAIL = os.environ.get("SUPPORT_EMAIL", "vanilliabusiness@gmail.com")


@app.post("/intermittent/aem/signalement")
async def signaler_document_illisible(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Quand le scan n'a pas su lire un document, l'utilisateur peut l'envoyer pour
    lecture humaine. Le fichier part dans le coffre R2 sous le préfixe de l'utilisateur
    (donc couvert par la suppression RGPD, comme ses AEM) et un email prévient l'équipe
    avec un lien signé temporaire — le contenu du document ne transite jamais par l'email."""
    allowed_ext = (".pdf", ".jpg", ".jpeg", ".png", ".webp")
    if not file.filename or not file.filename.lower().endswith(allowed_ext):
        raise HTTPException(status_code=400, detail="Format non supporté (PDF, JPG, PNG).")
    if not r2_storage.R2_ENABLED:
        raise HTTPException(status_code=503, detail="L'envoi n'est pas disponible pour le moment. Réessaie un peu plus tard.")

    # Garde-fou anti-abus : quelques envois par jour suffisent largement.
    _verifier_et_incrementer_quota_ia(db, user.id, "aem_signalement", 5)

    tmp_dir = tempfile.mkdtemp()
    file_path = os.path.join(tmp_dir, os.path.basename(file.filename))
    try:
        taille = 0
        with open(file_path, "wb") as f:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                taille += len(chunk)
                if taille > AEM_MAX_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=f"Fichier trop volumineux (max {AEM_MAX_BYTES // (1024*1024)} Mo). "
                               "Réduis la taille de la photo et réessaie.",
                    )
                f.write(chunk)
        try:
            key = r2_storage.upload_aem(file_path, str(user.id), file.filename)
        except Exception:
            raise HTTPException(status_code=502, detail="L'envoi a échoué. Réessaie dans un moment.")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    lien = r2_storage.get_signed_url(key, expires_seconds=72 * 3600)
    nom_fichier = html.escape(file.filename)
    corps = f"""
    <div style="font-family: sans-serif; max-width: 520px; margin: 0 auto;">
      <h2 style="color:#0A2540;">Un document attend une lecture humaine</h2>
      <p>Un utilisateur a envoyé un document que le scan n'a pas su lire.</p>
      <ul style="color:#0A2540; font-size:14px; line-height:1.7;">
        <li>Utilisateur : {html.escape(user.email or "")}</li>
        <li>Fichier : {nom_fichier}</li>
      </ul>
      <p>
        <a href="{lien}" style="background:#378ADD; color:white; padding:12px 20px;
           border-radius:8px; text-decoration:none; display:inline-block;">
          Ouvrir le document (lien valable 72h)
        </a>
      </p>
      <p style="color:#6B7A8D; font-size:13px;">
        Document sensible (peut contenir un NIR) : à consulter, jamais à transférer.
        Le fichier reste dans le coffre R2 sous le préfixe de l'utilisateur.
      </p>
    </div>
    """
    if not send_email(SUPPORT_EMAIL, "TOTOR — document à vérifier (lecture humaine)", corps):
        raise HTTPException(status_code=502, detail="L'envoi a échoué. Réessaie dans un moment.")
    return {"ok": True}


@app.post("/intermittent/are/extract")
async def extract_are(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Lit une attestation France Travail (ARE) via Claude Vision : date anniversaire +
    montant journalier. Ne stocke rien — le front affiche pour vérification, puis confirme
    via /profile/date-anniversaire. Action ponctuelle → simple garde-fou anti-abus journalier
    (pas de quota freemium, pour ne pas pénaliser l'inscription)."""
    allowed_ext = (".pdf", ".jpg", ".jpeg", ".png", ".webp")
    if not file.filename or not file.filename.lower().endswith(allowed_ext):
        raise HTTPException(status_code=400, detail="Format non supporté (PDF, JPG, PNG).")

    _verifier_et_incrementer_quota_ia(db, user.id, "are_scan", AI_AEM_DAILY_LIMIT)

    tmp_dir = tempfile.mkdtemp()
    file_path = os.path.join(tmp_dir, os.path.basename(file.filename))
    try:
        taille = 0
        with open(file_path, "wb") as f:
            while True:
                chunk = await file.read(1024 * 1024)  # 1 Mo
                if not chunk:
                    break
                taille += len(chunk)
                if taille > AEM_MAX_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=f"Fichier trop volumineux (max {AEM_MAX_BYTES // (1024*1024)} Mo). "
                               "Réduis la taille de la photo et réessaie.",
                    )
                f.write(chunk)

        try:
            data = extract_are_data(file_path)
        except HTTPException:
            raise
        except Exception as e:
            # Repli PDF -> image : certains PDF d'ARE (scannés/atypiques) passent mieux
            # en image. Ne s'exécute QU'APRÈS l'échec du chemin normal (donc jamais de
            # régression : au pire on renvoie l'erreur d'origine ci-dessous).
            if file_path.lower().endswith(".pdf"):
                try:
                    from pdf2image import convert_from_path
                    pages = convert_from_path(file_path, first_page=1, last_page=1)
                    if pages:
                        img_path = os.path.join(tmp_dir, "are_page1.png")
                        pages[0].save(img_path, "PNG")
                        return extract_are_data(img_path)
                except Exception:
                    pass  # le repli a échoué -> on renvoie l'erreur d'origine
            raise HTTPException(status_code=422, detail=billing.redact_secrets(str(e)) or "Impossible de lire cette attestation.")

        return data
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@app.put("/intermittent/activite/{activite_id}")
def update_intermittent_activite(
    activite_id: str,
    req: IntermittentActiviteRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if req.type_activite not in TYPES_ACTIVITE_INTERMITTENT:
        raise HTTPException(status_code=400, detail="Type d'activite invalide")
    if req.nombre is None or req.nombre < 0:
        raise HTTPException(status_code=400, detail="Nombre invalide")
    row = (
        db.query(IntermittentActivity)
        .filter(
            IntermittentActivity.id == activite_id,
            IntermittentActivity.user_id == user.id,
        )
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Activite introuvable")
    row.date = req.date
    row.date_fin = req.date_fin or None
    row.type_activite = req.type_activite
    row.nombre = req.nombre
    row.employeur = req.employeur or None
    if req.salaire_brut is not None:
        row.salaire_brut = req.salaire_brut
    # PAS : mis à jour SEULEMENT s'il est fourni. Le formulaire d'édition rapide
    # ne touche pas aux montants → on ne doit pas écraser le PAS déjà saisi.
    # (Cohérent avec salaire_brut ci-dessus.)
    if req.pas_montant is not None:
        row.pas_montant = req.pas_montant
    # On met à jour le statut "estimé" (passe à False quand l'utilisateur confirme l'AEM réelle).
    row.estime = bool(req.estime)
    row.metier = _metier_valide(req.metier)
    # Réconciliation estimé → AEM (24/07/2026) : quand un scan REMPLACE une ligne
    # estimée, il apporte ses marqueurs de document. Additifs : jamais effacés
    # quand ils sont absents (le formulaire d'édition n'y touche pas).
    if req.aem_recue:
        row.aem_recue = True
    if req.aem_filename:
        row.aem_filename = req.aem_filename
    if req.aem_r2_key:
        row.aem_r2_key = req.aem_r2_key
    db.commit()
    return {"ok": True}


@app.post("/intermittent/activite/{activite_id}/doublon-ok")
def acquitter_doublon_activite(
    activite_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """L'utilisateur a vérifié une alerte « doublon possible » et confirme que ce
    n'est PAS un doublon : on acquitte l'alerte définitivement pour cette ligne
    (demande testeuse 23/07/2026 : une alerte doit pouvoir être tranchée)."""
    row = (
        db.query(IntermittentActivity)
        .filter(
            IntermittentActivity.id == activite_id,
            IntermittentActivity.user_id == user.id,
        )
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Activite introuvable")
    row.doublon_ok = True
    db.commit()
    return {"ok": True}


@app.delete("/intermittent/activite/{activite_id}")
def delete_intermittent_activite(
    activite_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = (
        db.query(IntermittentActivity)
        .filter(
            IntermittentActivity.id == activite_id,
            IntermittentActivity.user_id == user.id,
        )
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Activite introuvable")
    # RGPD : on retire aussi le document original de R2, le cas échéant.
    if row.aem_r2_key:
        r2_storage.delete_file(row.aem_r2_key)
    db.delete(row)
    db.commit()
    return {"ok": True}


@app.get("/intermittent/activite/{activite_id}/document")
def get_aem_document_url(
    activite_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Renvoie une URL signée temporaire (1h) pour consulter le document AEM original.
    Le fichier n'est jamais public : seul son propriétaire peut obtenir une URL, valable 1h."""
    row = (
        db.query(IntermittentActivity)
        .filter(
            IntermittentActivity.id == activite_id,
            IntermittentActivity.user_id == user.id,
        )
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Activite introuvable")
    if not row.aem_r2_key:
        raise HTTPException(status_code=404, detail="Aucun document conservé pour cette activité.")
    if not r2_storage.R2_ENABLED:
        raise HTTPException(status_code=503, detail="Stockage indisponible.")
    try:
        url = r2_storage.get_signed_url(row.aem_r2_key, expires_seconds=3600)
    except Exception as e:
        raise HTTPException(status_code=500, detail="Impossible de générer le lien.")
    return {"url": url}


@app.post("/profile/date-anniversaire")
def save_date_anniversaire(
    req: DateAnniversaireRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    profile = db.query(Profile).filter(Profile.user_id == user.id).first()
    if not profile:
        raise HTTPException(status_code=404, detail="Profil introuvable")
    profile.date_anniversaire = req.date_anniversaire
    # Montant journalier : on ne l'écrase que s'il est fourni (None côté requête = champ
    # absent → on garde l'existant ; le front l'envoie explicitement quand il vient de l'ARE).
    if req.montant_journalier is not None:
        profile.montant_journalier = req.montant_journalier
    db.commit()
    return {"ok": True, "date_anniversaire": profile.date_anniversaire, "montant_journalier": profile.montant_journalier}


# Statuts de compte gérés par TOTOR (verticales du moteur de décision).
STATUTS_COMPTE = ("auto_entrepreneur", "intermittent")


class StatutRequest(BaseModel):
    statut: str


@app.post("/profile/statut")
def save_statut(
    req: StatutRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Change UNIQUEMENT le statut du profil. Ne touche à aucun autre champ
    (activité, périodicité, etc.) — réversible et sans effet de bord."""
    if req.statut not in STATUTS_COMPTE:
        raise HTTPException(status_code=400, detail="Statut inconnu")
    profile = db.query(Profile).filter(Profile.user_id == user.id).first()
    if not profile:
        raise HTTPException(status_code=404, detail="Profil introuvable")
    profile.statut = req.statut
    db.commit()
    return {"ok": True, "statut": profile.statut}


@app.post("/profile/complete-onboarding")
def complete_onboarding(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Marque l'onboarding comme terminé, sans toucher au reste du profil.
    Utilisé par le flux intermittent qui n'a pas le formulaire AE classique."""
    profile = db.query(Profile).filter(Profile.user_id == user.id).first()
    if not profile:
        raise HTTPException(status_code=404, detail="Profil introuvable")
    profile.onboarding_complete = True
    db.commit()
    return {"ok": True}


# ─── Trouver des cachets & des heures : offres France Travail ───────────────────
# Public (pas de données perso) : la licence France Travail impose un accès libre,
# sans compte ni paiement — la landing publique appelle donc cette route directement.
# Cache mémoire ~20 min par combo de filtres pour ménager le quota FT (10 appels/s).
# En cas d'échec FT → 502 propre, JAMAIS de mocks.
_OFFRES_CACHE = {}          # (role_type, contract_type, lieu, rayon) -> (timestamp, offres)
_OFFRES_TTL = 20 * 60       # 20 minutes
# Anti-abus : fenêtre glissante par IP. Le cache absorbe déjà le trafic normal ;
# cette limite ne vise que les rafales anormales (scraping, boucle).
_OFFRES_RL = {}             # ip -> liste de timestamps récents
_OFFRES_RL_MAX = 30         # requêtes max par IP
_OFFRES_RL_FEN = 60         # sur 60 secondes


def _offres_ratelimit(request: Request, now: float):
    ip = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip() \
        or (request.client.host if request.client else "?")
    horodatages = [t for t in _OFFRES_RL.get(ip, []) if now - t < _OFFRES_RL_FEN]
    if len(horodatages) >= _OFFRES_RL_MAX:
        _OFFRES_RL[ip] = horodatages
        raise HTTPException(status_code=429, detail="Trop de requêtes, réessaie dans une minute.")
    horodatages.append(now)
    _OFFRES_RL[ip] = horodatages
    # Ménage : on ne garde jamais plus de quelques milliers d'IP en mémoire.
    if len(_OFFRES_RL) > 5000:
        for vieille_ip in [k for k, v in _OFFRES_RL.items() if not v or now - v[-1] > _OFFRES_RL_FEN]:
            _OFFRES_RL.pop(vieille_ip, None)


@app.get("/intermittent/offres")
def get_intermittent_offres(
    request: Request,
    role_type: str = "",
    contract_type: str = "",
    lieu: str = "",
    rayon: int = 20,
):
    import time as _time
    import logging as _logging
    import francetravail_offres as ft

    key = (role_type or "", contract_type or "", (lieu or "").lower().strip(), int(rayon or 20))
    now = _time.time()
    _offres_ratelimit(request, now)
    cached = _OFFRES_CACHE.get(key)
    if cached and now - cached[0] < _OFFRES_TTL:
        return {"offres": cached[1], "source": "France Travail"}

    try:
        offres = ft.search_offres(
            role_type=role_type, contract_type=contract_type, lieu=lieu, rayon=rayon
        )
    except Exception as e:
        # On ne loggue jamais d'éventuels secrets — juste le type d'erreur.
        _logging.getLogger("francetravail").warning("Echec offres FT: %s", type(e).__name__)
        # Cache de secours : si FT échoue (429, panne) et qu'on a des offres même
        # périmées pour ce filtre, on les sert plutôt qu'une erreur. Ce sont de
        # vraies offres FT, juste moins fraîches — jamais de mocks.
        if cached:
            return {"offres": cached[1], "source": "France Travail"}
        raise HTTPException(status_code=502, detail="Impossible de récupérer les offres pour le moment.")

    _OFFRES_CACHE[key] = (now, offres)
    return {"offres": offres, "source": "France Travail"}


@app.get("/intermittent/estimation-mois")
def estimation_mois_intermittent(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Estimation du versement France Travail pour le MOIS CIVIL EN COURS, à partir
    de l'AJ de la carte allocation (validée au centime, backtest n°1) et des
    activités saisies dans le mois (moteur du mois calé sur le guide officiel).
    Décision Camille 24/07/2026 : lancé en mode ESTIMATION assumée AVANT le
    backtest sur relevé réel — la carte le dit et promet de se caler dessus.
    Réservé TOTOR Veille ({verrou: true} pour les gratuits, vitrine côté front).
    Loi X : MÊME discipline d'affichage que la carte allocation (branche validée
    seulement : annexe 10, ≤ 60 €/jour — zone sans CSG, net fiable)."""
    if not billing.is_premium(db, user):
        return {"verrou": True}
    profile = db.query(Profile).filter(Profile.user_id == user.id).first()
    alloc = _allocation_pour_profil(profile)
    if alloc is None:
        return {"verrou": False, "ok": False, "raison": "allocation_manquante"}
    if not alloc["affichable"]:
        return {"verrou": False, "ok": False, "raison": alloc["raison_non_affichable"]}
    res_aj = ae.calculer_aj(profile.annexe_allocation, profile.salaire_reference, profile.heures_reference)
    rows = (
        db.query(IntermittentActivity)
        .filter(IntermittentActivity.user_id == user.id)
        .all()
    )
    activites = [
        {"date": r.date, "type_activite": r.type_activite, "nombre": r.nombre, "salaire_brut": r.salaire_brut}
        for r in rows
    ]
    auj = date.today()
    out = ae.estimer_mois_civil(profile.annexe_allocation, res_aj, activites, auj.year, auj.month)
    out.update({
        "verrou": False,
        "ok": True,
        "aj_brute": res_aj["aj_brute"],
        "aj_nette": res_aj["aj_nette"],
        "montant_officiel": profile.montant_journalier,
        "coherent_officiel": alloc.get("coherent_officiel"),
    })
    return out


@app.get("/intermittent/projection-aj")
def projection_aj_renouvellement(
    cachets_sup: int = 0,
    brut_cachet: Optional[float] = None,
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    """Projection de l'allocation journalière au PROCHAIN renouvellement, calculée
    depuis les activités déjà déclarées (moteur AJ validé). Réservé TOTOR Veille :
    c'est la promesse « je recalcule ton allocation » du paywall. Les gratuits
    reçoivent {verrou: true}, sans chiffre (le front affiche la vitrine)."""
    if not billing.is_premium(db, user):
        return {"verrou": True}
    profile = db.query(Profile).filter(Profile.user_id == user.id).first()
    aujourd_hui = date.today()
    fin = (
        profile.date_anniversaire
        if profile and profile.date_anniversaire and profile.date_anniversaire >= aujourd_hui
        else aujourd_hui
    )
    rows = (
        db.query(IntermittentActivity)
        .filter(IntermittentActivity.user_id == user.id)
        .all()
    )
    activites = [
        {
            "date": r.date,
            "type_activite": r.type_activite,
            "nombre": r.nombre,
            "salaire_brut": r.salaire_brut,
            "metier": r.metier,
        }
        for r in rows
    ]
    out = ae.projeter_renouvellement(activites, fin, cachets_sup=cachets_sup, brut_cachet=brut_cachet)
    out["verrou"] = False
    out["date_anniversaire"] = (
        profile.date_anniversaire.isoformat()
        if profile and profile.date_anniversaire
        else None
    )
    return out


@app.get("/intermittent/cockpit")
def get_intermittent_cockpit(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    """État complet du cockpit intermittent, calculé par le moteur 507h."""
    profile = db.query(Profile).filter(Profile.user_id == user.id).first()
    date_anniv = profile.date_anniversaire if profile else None
    rows = (
        db.query(IntermittentActivity)
        .filter(IntermittentActivity.user_id == user.id)
        .all()
    )
    res = ie.calculer(
        _activites_modele_vers_moteur(rows),
        aujourdhui=date.today(),
        date_anniversaire=date_anniv,
    )
    out = _resultat_vers_dict(res)
    # Montant journalier (lu sur l'ARE, stocké sur le profil) — affiché tel quel au cockpit.
    out["montant_journalier"] = profile.montant_journalier if profile else None
    # Allocation RECALCULÉE (Loi X : `affichable` décide si le montant peut être montré).
    out["allocation"] = _allocation_pour_profil(profile)
    # Congés Spectacles : estimation de l'ICP sur l'exercice en cours (1er avril → 31 mars).
    _cs_deb, _cs_fin = cs.exercice_en_cours(date.today())
    cs_data = cs.calculer(rows, _cs_deb, _cs_fin)
    cs_data["exercice_debut"] = _cs_deb.isoformat()
    cs_data["exercice_fin"] = _cs_fin.isoformat()
    out["conges_spectacles"] = cs_data
    # PAS prélevé : SOMME des montants RÉELS recopiés du bulletin (jamais brut × taux).
    # Année civile (l'impôt sur le revenu est annuel). None si aucun montant saisi
    # cette année → le cockpit n'affiche alors rien.
    _annee_pas = date.today().year
    _pas_total, _pas_saisi = 0.0, False
    for _r in rows:
        if _r.pas_montant is not None and _r.date and _r.date.year == _annee_pas:
            _pas_total += float(_r.pas_montant)
            _pas_saisi = True
    out["pas_preleve"] = (
        {"annee": _annee_pas, "montant": round(_pas_total, 2)} if _pas_saisi else None
    )
    return out


@app.post("/intermittent/allocation")
def save_allocation(
    req: AllocationRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Enregistre les éléments de calcul de l'allocation (salaire de référence + heures
    + annexe, lus sur la notification France Travail) et renvoie l'AJ recalculée,
    encadrée par la Loi X (cf. _allocation_pour_profil).
    """
    if req.annexe not in ("annexe8", "annexe10"):
        raise HTTPException(status_code=400, detail="Annexe invalide (annexe8 ou annexe10)")
    if req.salaire_reference < 0 or req.heures_reference < 0:
        raise HTTPException(status_code=400, detail="Valeurs négatives refusées")
    profile = db.query(Profile).filter(Profile.user_id == user.id).first()
    if not profile:
        raise HTTPException(status_code=404, detail="Profil introuvable")
    profile.annexe_allocation = req.annexe
    profile.salaire_reference = req.salaire_reference
    profile.heures_reference = req.heures_reference
    db.commit()
    db.refresh(profile)
    return _allocation_pour_profil(profile)


@app.post("/intermittent/simuler")
def simuler_contrat_intermittent(
    req: SimulationContratRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Simule l'impact d'un contrat hypothétique sur les 507h (niveau A + C)."""
    if req.type_activite not in TYPES_ACTIVITE_INTERMITTENT:
        raise HTTPException(status_code=400, detail="Type d'activite invalide")
    profile = db.query(Profile).filter(Profile.user_id == user.id).first()
    date_anniv = profile.date_anniversaire if profile else None
    rows = (
        db.query(IntermittentActivity)
        .filter(IntermittentActivity.user_id == user.id)
        .all()
    )
    contrat = ie.Activite(date=req.date, type_activite=req.type_activite, nombre=req.nombre)
    sim = ie.simuler_contrat(
        _activites_modele_vers_moteur(rows),
        contrat,
        aujourdhui=date.today(),
        date_anniversaire=date_anniv,
    )
    return sim


# ════════════════════════════════════════════════════════════════════════
#  BILLING — abonnement Stripe. Toute la logique vit dans billing.py.
#  Garde-fou clé : le premium ne s'active QUE via /billing/webhook (signé).
# ════════════════════════════════════════════════════════════════════════
class CheckoutRequest(BaseModel):
    promo_code: Optional[str] = None
    mode: Optional[str] = None      # "auto_entrepreneur" | "intermittent" (pour revenir dans le bon mode)
    origin: Optional[str] = None    # origine du front (pour revenir sur le bon domaine)
    plan: Optional[str] = None      # "annuel" pour le tarif à l'année ; sinon mensuel par défaut


class PromoRequest(BaseModel):
    code: str


@app.post("/billing/create-checkout-session")
def billing_create_checkout(
    req: CheckoutRequest = Body(default=CheckoutRequest()),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Crée une Checkout Session Stripe (abonnement récurrent). Renvoie l'URL à ouvrir."""
    try:
        url = billing.create_checkout_session(db, user, req.promo_code, app_mode=req.mode, origin=req.origin, plan=req.plan)
    except ValueError as e:
        if str(e) == "pionnier_complet":
            raise HTTPException(status_code=409, detail="Les 100 places Pionnier sont prises. Les autres formules restent disponibles.")
        raise HTTPException(status_code=400, detail="Le paiement n'a pas pu démarrer. Réessaie dans un instant.")
    except Exception as e:
        # On LOGGE l'erreur réelle (masquée) côté serveur, mais on ne renvoie JAMAIS
        # l'exception brute au client : elle peut contenir une clé secrète.
        print(f"[CHECKOUT ERROR] {type(e).__name__}: {billing.redact_secrets(e)}", flush=True)
        raise HTTPException(status_code=400, detail="Le paiement n'a pas pu démarrer. Réessaie dans un instant.")
    return {"url": url}


@app.get("/billing/offres")
def billing_offres(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """État des offres pour la page d'abonnement : places Pionnier RÉELLES restantes.
    À 100 payants réels, pionnier_ouvert passe à false et la page ferme l'offre."""
    return billing.offre_pionnier(db)


@app.post("/quota/achat-simulation")
def quota_achat_simulation(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Mode Achat (freemium 1.0.1) : compte une simulation. Gratuit = 5 par mois,
    TOTOR Veille = illimité. Le front appelle AVANT d'afficher le verdict ;
    402 premium_requis quand le quota gratuit est épuisé."""
    return quotas_freemium.consommer_simulation_achat(db, user)


@app.post("/billing/create-portal-session")
def billing_create_portal(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Customer Portal : l'utilisateur gère / annule lui-même son abonnement."""
    try:
        url = billing.create_portal_session(db, user)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        print(f"[PORTAL ERROR] {type(e).__name__}: {billing.redact_secrets(e)}", flush=True)
        raise HTTPException(status_code=400, detail="Impossible d'ouvrir la gestion de l'abonnement pour le moment.")
    return {"url": url}


@app.post("/billing/webhook")
async def billing_webhook(request: Request, db: Session = Depends(get_db)):
    """Webhook Stripe — signature vérifiée + idempotence (dans billing.process_webhook).
    SEUL endroit qui active/désactive le premium suite à un paiement."""
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")
    try:
        return billing.process_webhook(db, payload, sig)
    except Exception as e:
        # On LOGGE l'exception exacte (visible dans les logs Railway) pour diagnostic.
        import traceback
        print(f"[WEBHOOK ERROR] {type(e).__name__}: {e}", flush=True)
        traceback.print_exc()
        # Signature invalide / payload illisible / erreur de traitement → 400.
        # Stripe réémettra l'event (le traitement est idempotent).
        raise HTTPException(status_code=400, detail=f"Webhook rejeté: {type(e).__name__}: {e}")


@app.post("/billing/revenuecat-webhook")
async def billing_revenuecat_webhook(request: Request, db: Session = Depends(get_db)):
    """Webhook RevenueCat — le premium des caisses Apple (StoreKit) et Google
    (Play Billing). Auth par header Authorization (secret partagé), traitement
    idempotent dans revenuecat_webhook.traiter_evenement. Le compteur Pionnier
    n'est PAS affecté (il ne lit que les abonnements Stripe au prix Pionnier)."""
    if not revenuecat_webhook.verifier_auth(request.headers.get("authorization", "")):
        raise HTTPException(status_code=401, detail="Non autorisé")
    try:
        payload = await request.json()
        return revenuecat_webhook.traiter_evenement(db, payload)
    except HTTPException:
        raise
    except Exception as e:
        print(f"[REVENUECAT WEBHOOK ERROR] {type(e).__name__}: {e}", flush=True)
        raise HTTPException(status_code=400, detail="Webhook rejeté")


@app.post("/billing/apply-promo")
def billing_apply_promo(
    req: PromoRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Code maison : 'tester' active le premium directement, 'influencer' renvoie le coupon."""
    return billing.apply_promo(db, user, (req.code or "").strip())


@app.get("/health")
def health():
    return {"status": "ok", "timestamp": datetime.now().isoformat()}

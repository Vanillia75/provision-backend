"""
API principale de l'application de provisionnement des cotisations.
Lancer avec : uvicorn api:app --reload
"""

import os
import html
import asyncio
import shutil
import tempfile
import requests as http_requests
from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Request, Body
from fastapi.responses import Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session
from google.oauth2 import id_token as google_id_token
from google.auth.transport import requests as google_requests

from database import Base, engine, get_db, SessionLocal
from models import User, Profile, IncomeEntry, ClientInvoice, Expense, Contact, Quote, IntermittentActivity, AIUsage, LoginAttempt, FiscalSettings
from auth import (
    hash_password, verify_password, create_token, get_current_user,
    create_purpose_token, verify_purpose_token,
)
from emailing import send_reset_password_email, send_verification_email, send_invoice_email, send_email
from invoice_pdf import generate_invoice_pdf
from legal_mentions import get_franchise_vat_mention, append_ei_mention, resolve_fiscal_settings, compute_invoice_totals, format_vat_rate
from numerotation import compute_next_numero, normalize_numero_depart
from tax_engine import estimate, STATUTS_DISPONIBLES, STATUTS_A_VENIR, AUTO_ENTREPRENEUR_RATES
from projection import projeter_tresorerie
from invoice_extractor import extract_invoice_data
from aem_extractor import extract_aem_data, extract_are_data
import r2_storage
import intermittent_engine as ie
import allocation_engine as ae
import conges_spectacles as cs
from insee_lookup import lookup_siret, SiretLookupError
import billing
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
    "https://hector-app.fr",
    "https://www.hector-app.fr",
    "http://localhost:5173",  # developpement local (Vite)
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Module de connexion bancaire (Powens, lecture seule DSP2).
from powens import router as bank_router
app.include_router(bank_router)


# ----------------------------------------------------------------
# Schemas
# ----------------------------------------------------------------

class RegisterRequest(BaseModel):
    email: EmailStr
    password: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class GoogleAuthRequest(BaseModel):
    credential: str


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

    user = User(email=req.email, password_hash=hash_password(req.password))
    db.add(user)
    db.commit()
    db.refresh(user)

    verify_token = create_purpose_token(user.id, "verify_email", expire_minutes=60 * 24)
    send_verification_email(user.email, verify_token)

    return AuthResponse(token=create_token(user.id), email=user.email)


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
        user = User(email=email, google_id=google_id, password_hash=None)
        db.add(user)
        db.commit()
        db.refresh(user)
    elif not user.google_id:
        user.google_id = google_id
        db.commit()

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
    user = db.query(User).filter(User.email == req.email).first()
    if user and user.password_hash:
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
    quotas = None if prem else {
        t: {"used": billing.usage_this_month(db, user.id, t), "limit": billing.free_quota_for(t)}
        for t in ("chat", "doc_scan", "aem_scan")
    }

    return {
        "statut": profile.statut,
        "activite": profile.activite,
        "periodicite": profile.periodicite,
        "acre": profile.acre,
        "versement_liberatoire": profile.versement_liberatoire,
        "date_creation_activite": profile.date_creation_activite,
        "onboarding_complete": profile.onboarding_complete,
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

    tmp_dir = tempfile.mkdtemp()
    file_path = os.path.join(tmp_dir, file.filename)
    with open(file_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    try:
        data = extract_invoice_data(file_path)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Impossible de lire la facture : {e}")

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
    date_emission: Optional[date] = None
    date_echeance: Optional[date] = None
    lignes: Optional[list[FactureLigne]] = None
    notes: Optional[str] = None


class InvoiceStatusRequest(BaseModel):
    statut: str


def _verifier_et_incrementer_quota_ia(db: Session, user_id: str, type_appel: str, limite: int):
    """
    Vérifie le quota IA du jour pour cet utilisateur et ce type d'appel.
    - Si la limite est atteinte : lève une HTTPException 429 (message Hector chaleureux).
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
    Le compteur du jour est incrémenté dans tous les cas (alimente le total mensuel).
    is_premium() reste la SEULE source de vérité.
    """
    if not billing.is_premium(db, user):
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
    _snapshot_fiscal(inv, db, user.id)   # fige le régime TVA courant sur la facture
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
        _snapshot_fiscal(inv, db, user.id)

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
    if was_brouillon and req.statut != "brouillon":
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


def _snapshot_fiscal(obj, db: Session, user_id: str) -> None:
    """
    Fige sur la facture/devis `obj` le régime TVA COURANT de l'utilisateur.
    Appelé à la création et tant que le document est en brouillon ; au passage à
    « émise » (Envoyée/Payée), c'est ce snapshot qui devient définitif et immuable.
    """
    f = _read_fiscal_settings(db, user_id)
    obj.vat_mode = f["vat_mode"]
    obj.vat_rate = f["vat_rate"]
    obj.vat_number = f["vat_number"]


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
    lettres (« Bien à vous, Prénom Nom — ENTREPRISE ») → le bloc doublonnerait.
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
      {f'<p style="color:#6B7A8D; font-size:12px;">{e(inv.notes)}</p>' if inv.notes else ""}
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
# L'utilisateur décide la RÈGLE une fois (profile.relance_auto_jours) ; Hector l'applique
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
    « Prénom Nom — ENTREPRISE » (l'entreprise seule en repli). None si le profil
    ne permet aucune signature → la relance de ce profil ne doit PAS partir.
    """
    if not profile:
        return None
    nom_personne = f"{profile.prenom or ''} {profile.nom or ''}".strip()
    entreprise = (profile.entreprise or "").strip()
    if nom_personne and entreprise:
        return f"{nom_personne} — {entreprise}"
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


@app.on_event("startup")
async def _demarrer_relances_auto():
    async def boucle():
        await asyncio.sleep(120)  # laisser l'app finir de démarrer
        while True:
            await asyncio.to_thread(_executer_relances_auto)
            await asyncio.sleep(6 * 3600)  # 4 passages par jour, dédupliqués par relance_envoyee_le
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
    _snapshot_fiscal(q, db, user.id)   # fige le régime TVA courant sur le devis
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
        _snapshot_fiscal(q, db, user.id)

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
    </div>
    """


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
    _snapshot_fiscal(inv, db, user.id)   # nouvelle facture (brouillon) → fige le régime courant
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


class ExpenseUpdateRequest(BaseModel):
    date: Optional[date] = None
    montant: Optional[float] = None
    categorie: Optional[str] = None
    description: Optional[str] = None


def _expense_to_dict(e: Expense) -> dict:
    return {
        "id": e.id,
        "date": e.date,
        "montant": e.montant,
        "categorie": e.categorie,
        "description": e.description,
        "source": e.source,
        "filename": e.filename,
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

    tmp_dir = tempfile.mkdtemp()
    file_path = os.path.join(tmp_dir, file.filename)
    with open(file_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    try:
        data = extract_invoice_data(file_path)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Impossible de lire la facture : {e}")

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


class ChatMessage(BaseModel):
    role: str
    content: str


class AssistantRequest(BaseModel):
    messages: list[ChatMessage]


@app.post("/assistant/chat")
def assistant_chat(
    req: AssistantRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not ANTHROPIC_API_KEY:
        raise HTTPException(status_code=500, detail="Assistant IA non configure")

    # Quota freemium (mensuel pour les gratuits) + garde-fou anti-abus journalier.
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

    if is_intermittent:
        system_prompt = (
            "Tu es Hector, le compagnon de confiance d'un intermittent du spectacle francais. "
            "Tu es un EXPERT du regime intermittent, et tu en es fier. Tu n'es pas une IA generaliste : "
            "tu es specialise, precis, et profondement honnete. La communaute des intermittents a "
            "l'habitude qu'on lui explique mal ses droits — toi, tu rends les choses CLAIRES. "
            "LA chose la plus importante de ta personnalite : tu n'es pas un conseiller qui assene une "
            "reponse, tu es un compagnon qui regarde la situation AVEC la personne. Tu es de son cote, "
            "tu veilles sur ses heures avec elle, tu l'accompagnes dans la duree. Glisse naturellement "
            "une touche de presence par reponse ('je garde un oeil sur ton compteur', 'on refait le "
            "point apres ton prochain contrat'), sans en abuser. "
            "Tu es fidele, calme, rassurant, jamais dans le jugement. Tu ne te re-presentes JAMAIS "
            "(la personne est dans l'app Hector) : reponds directement, sans preambule. "
            "Tu as une ame de chien fidele mais tu ne la joues JAMAIS de facon caricaturale : aucun "
            "aboiement, aucun jeu de mots canin, pas d'emojis pattes. "
            "Tu reponds en francais, clair et direct, en tutoyant, et tu vas a l'essentiel. "
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
            "qu'il soit isole ou consecutif : Hector applique 12h a TOUS les cachets. (Une ancienne regle "
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
            "le moteur de calcul ARE n'est pas encore valide cote Hector, et on refuse d'inventer un nombre. "
            "Voici la logique que tu peux expliquer : "
            "L'allocation journaliere (AJ) combine trois composantes — une partie liee a ton salaire de reference, "
            "une partie liee a ton nombre d'heures travaillees, et une partie fixe. Il existe un plancher (l'AJ "
            "ne descend pas sous un certain montant par jour) et un plafond. Tu peux dire si une situation tend "
            "plutot vers le haut ou le bas de la fourchette, mais SANS donner le montant en euros. "
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
            "mais pas non plus pour balancer des chiffres que tu ne peux pas garantir. Distingue deux registres : "
            "(1) LE COEUR QUE TU MAITRISES DE FACON SURE — les heures, les 507h, la fenetre de 12 mois glissants, "
            "la conversion des cachets en heures (12h), la clause de rattrapage (338-506h, 6 mois) : la-dessus tu "
            "es PRECIS, AFFIRMATIF, tu vas au fond, tu ne renvoies pas vers France Travail par reflexe. "
            "(2) LES MONTANTS ET PARAMETRES FINS — coefficients de l'allocation, planchers et plafonds en euros, "
            "franchises, jours indemnises, taux de cotisation, seuils chiffres des dispositifs : la-dessus tu "
            "expliques la LOGIQUE clairement, tu peux donner un ordre de grandeur VERBAL (plutot eleve, plutot bas), "
            "mais tu ne donnes PAS de chiffre final, et tu dis pourquoi : ce calcul depend de parametres precis "
            "et tu refuses d'inventer un nombre qui pourrait tromper. Tu orientes alors vers le simulateur officiel. "
            "Cette prudence n'est pas de la faiblesse : c'est ce qui rend Hector fiable. Mieux vaut un 'voici la "
            "logique, le chiffre exact se verifie ici' qu'un nombre approximatif faux. "
            "Si une information precise te manque pour conclure (par ex. tu ne sais pas son salaire de reference), "
            "tu POSES la bonne question pour avancer. "
            "Tu ne devines JAMAIS un chiffre reglementaire dont tu n'es pas sur : dans le doute sur une valeur "
            "exacte (un coefficient, un plafond, un seuil), tu dis l'ordre de grandeur si tu le connais ET que la "
            "valeur exacte est a confirmer sur le simulateur ou le site officiel — ou tu ne donnes pas le chiffre. "
            "\n\n"
            "Tu vis A L'INTERIEUR de l'app H€CTOR. Quand c'est utile, renvoie vers les sections : le Cockpit "
            "(le compteur 507h, la date anniversaire), 'Mes activites' (saisir ses cachets et heures), "
            "'Comprendre' (les fiches sur le regime). Ne recommande JAMAIS un outil concurrent payant. "
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
        "(la personne sait qui tu es, elle est dans l'app Hector) : reponds directement, sans "
        "'Salut je suis Hector' ni preambule. "
        "Quand une reponse est difficile (ex: 'non, n'achete pas ca maintenant'), reste honnete "
        "mais humain : pas de 'la reponse est non' sec. Plutot : 'avec X EUR aujourd'hui, je ne te "
        "le conseillerais pas pour le moment — attends tes prochaines rentrees et on regardera ca "
        "ensemble.' La personne doit sentir quelqu'un AVEC elle, pas un verdict. "
        "Tu as une ame de chien fidele, mais tu ne la joues JAMAIS de facon caricaturale : aucun "
        "aboiement, aucun jeu de mots canin, pas d'emojis pattes. Le meilleur Hector, ce n'est pas "
        "un chien qui parle — c'est ce que ton meilleur compagnon te repondrait s'il comprenait la "
        "fiscalite et tes comptes. "
        "Tu reponds en francais, clair et direct, en tutoyant, et tu vas a l'essentiel sans blabla. "
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
        "TRES IMPORTANT — tu vis A L'INTERIEUR de l'application H€CTOR, et tu connais ce "
        "qu'elle sait faire. Quand l'utilisateur demande une action que H€CTOR propose, tu "
        "le renvoies vers la bonne section de l'app, tu ne dis JAMAIS que tu ne peux pas et "
        "tu ne recommandes JAMAIS un outil concurrent (Indy, Freebe, Shine, Tiime, Abby, etc.). "
        "Voici ce que H€CTOR fait, vers quoi orienter l'utilisateur : "
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

    return {"reply": reply or "Desole, je n'ai pas pu generer de reponse."}


# ════════════════════════════════════════════════════════════════════════════
#  INTERMITTENT DU SPECTACLE — endpoints du module de suivi des 507h.
#  Suivi INDICATIF (ne remplace pas France Travail). Le calcul vit dans
#  intermittent_engine.py ; ici on ne fait que lire/écrire des activités et
#  appeler le moteur. Aucun calcul d'indemnisation en euros (niveau B = plus tard).
# ════════════════════════════════════════════════════════════════════════════

TYPES_ACTIVITE_INTERMITTENT = ("heures", "cachet_isole", "cachet_groupe", "formation", "enseignement",
                               "arret_maternite", "arret_accident", "arret_ald", "arret_suspension",
                               "arret_maladie_ordinaire", "arret_paternite")


class IntermittentActiviteRequest(BaseModel):
    date: date
    date_fin: Optional[date] = None
    type_activite: str
    nombre: float
    employeur: Optional[str] = None
    salaire_brut: Optional[float] = None
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
    Travail). Applique la Loi X : `affichable` dit si Hector a le DROIT de montrer
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
            "aem_recue": r.aem_recue,
            "estime": r.estime,
            "aem_filename": r.aem_filename,
            "a_document": bool(r.aem_r2_key),
            "source": r.source,
            "metier": r.metier,
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
    if not send_email(SUPPORT_EMAIL, "H€CTOR — document à vérifier (lecture humaine)", corps):
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
    # On met à jour le statut "estimé" (passe à False quand l'utilisateur confirme l'AEM réelle).
    row.estime = bool(req.estime)
    row.metier = _metier_valide(req.metier)
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


# Statuts de compte gérés par H€CTOR (verticales du moteur de décision).
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
# Public (pas de données perso). Cache mémoire ~20 min par combo de filtres pour
# ménager le quota FT (10 appels/s). En cas d'échec FT → 502 propre, JAMAIS de mocks.
_OFFRES_CACHE = {}          # (role_type, contract_type, lieu, rayon) -> (timestamp, offres)
_OFFRES_TTL = 20 * 60       # 20 minutes


@app.get("/intermittent/offres")
def get_intermittent_offres(
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
        raise HTTPException(status_code=502, detail="Impossible de récupérer les offres pour le moment.")

    _OFFRES_CACHE[key] = (now, offres)
    return {"offres": offres, "source": "France Travail"}


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
    except Exception as e:
        # On LOGGE l'erreur réelle (masquée) côté serveur, mais on ne renvoie JAMAIS
        # l'exception brute au client : elle peut contenir une clé secrète.
        print(f"[CHECKOUT ERROR] {type(e).__name__}: {billing.redact_secrets(e)}", flush=True)
        raise HTTPException(status_code=400, detail="Le paiement n'a pas pu démarrer. Réessaie dans un instant.")
    return {"url": url}


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

"""
API principale de l'application de provisionnement des cotisations.
Lancer avec : uvicorn api:app --reload
"""

import os
import shutil
import tempfile
import requests as http_requests
from datetime import date, datetime
from typing import Optional

from fastapi import FastAPI, Depends, HTTPException, UploadFile, File
from fastapi.responses import Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session
from google.oauth2 import id_token as google_id_token
from google.auth.transport import requests as google_requests

from database import Base, engine, get_db
from models import User, Profile, IncomeEntry, ClientInvoice, Expense, Contact, Quote
from auth import (
    hash_password, verify_password, create_token, get_current_user,
    create_purpose_token, verify_purpose_token,
)
from emailing import send_reset_password_email, send_verification_email, send_invoice_email
from invoice_pdf import generate_invoice_pdf
from tax_engine import estimate, STATUTS_DISPONIBLES, STATUTS_A_VENIR, AUTO_ENTREPRENEUR_RATES
from invoice_extractor import extract_invoice_data
from insee_lookup import lookup_siret, SiretLookupError

Base.metadata.create_all(bind=engine)

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

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


@app.post("/auth/login", response_model=AuthResponse)
def login(req: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == req.email).first()
    if not user or not user.password_hash or not verify_password(req.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Email ou mot de passe incorrect")

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
        "email": user.email,
        "email_verified": user.email_verified,
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


class SettingsRequest(BaseModel):
    reserve_securite: Optional[float] = None
    tmi: Optional[str] = None


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
):
    allowed_ext = (".pdf", ".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".webp")
    if not file.filename.lower().endswith(allowed_ext):
        raise HTTPException(status_code=400, detail="Format de fichier non supporte")

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
    date_emission: date
    date_echeance: Optional[date] = None
    lignes: list[FactureLigne] = []
    notes: Optional[str] = None
    statut: str = "brouillon"


class InvoiceUpdateRequest(BaseModel):
    client_nom: Optional[str] = None
    client_email: Optional[str] = None
    client_adresse: Optional[str] = None
    date_emission: Optional[date] = None
    date_echeance: Optional[date] = None
    lignes: Optional[list[FactureLigne]] = None
    notes: Optional[str] = None


class InvoiceStatusRequest(BaseModel):
    statut: str


def _montant_lignes(lignes: list) -> float:
    total = 0.0
    for l in lignes or []:
        q = l.get("quantite", 0) if isinstance(l, dict) else l.quantite
        p = l.get("prix_unitaire", 0) if isinstance(l, dict) else l.prix_unitaire
        total += (q or 0) * (p or 0)
    return round(total, 2)


def _next_numero(db: Session, user_id: str) -> str:
    year = date.today().year
    count = (
        db.query(ClientInvoice)
        .filter(ClientInvoice.user_id == user_id, ClientInvoice.numero.like(f"F-{year}-%"))
        .count()
    )
    return f"F-{year}-{count + 1:03d}"


def _invoice_to_dict(inv: ClientInvoice) -> dict:
    return {
        "id": inv.id,
        "numero": inv.numero,
        "client_nom": inv.client_nom,
        "client_email": inv.client_email,
        "client_adresse": inv.client_adresse,
        "date_emission": inv.date_emission,
        "date_echeance": inv.date_echeance,
        "date_paiement": inv.date_paiement,
        "montant": inv.montant,
        "statut": inv.statut,
        "lignes": inv.lignes,
        "notes": inv.notes,
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

    inv = ClientInvoice(
        user_id=user.id,
        numero=_next_numero(db, user.id),
        client_nom=req.client_nom,
        client_email=req.client_email,
        client_adresse=req.client_adresse,
        date_emission=req.date_emission,
        date_echeance=req.date_echeance,
        montant=montant,
        statut=req.statut,
        date_paiement=date.today() if req.statut == "payee" else None,
        lignes=lignes_dicts,
        notes=req.notes,
    )
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

    inv.statut = req.statut
    if req.statut == "payee" and not inv.date_paiement:
        inv.date_paiement = date.today()
    elif req.statut != "payee":
        inv.date_paiement = None

    db.commit()
    db.refresh(inv)
    return _invoice_to_dict(inv)


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
    nom = profile.entreprise or f"{profile.prenom or ''} {profile.nom or ''}".strip() or None
    mention = (
        "Auto-entrepreneur, dispensé d'immatriculation au RCS et au RM"
        if profile.statut == "auto_entrepreneur" else None
    )
    return {"nom": nom, "adresse": profile.adresse, "siret": profile.siret, "mention": mention}


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

    try:
        pdf_bytes = generate_invoice_pdf(_invoice_to_dict(inv), emitter)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur lors de la generation du PDF : {e}")

    filename = f"facture-{inv.numero}.pdf"
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


def _build_invoice_email_html(inv: ClientInvoice, req: "SendInvoiceRequest") -> str:
    lignes_html = ""
    for l in (inv.lignes or []):
        desc = l.get("description", "") if isinstance(l, dict) else l.description
        qte = l.get("quantite", 0) if isinstance(l, dict) else l.quantite
        pu = l.get("prix_unitaire", 0) if isinstance(l, dict) else l.prix_unitaire
        total_ligne = (qte or 0) * (pu or 0)
        lignes_html += f"""
        <tr>
          <td style="padding:8px 0; border-bottom:1px solid #EEF2F7;">{desc}</td>
          <td style="padding:8px 0; border-bottom:1px solid #EEF2F7; text-align:center;">{qte}</td>
          <td style="padding:8px 0; border-bottom:1px solid #EEF2F7; text-align:right;">{pu:.2f} €</td>
          <td style="padding:8px 0; border-bottom:1px solid #EEF2F7; text-align:right; font-weight:600;">{total_ligne:.2f} €</td>
        </tr>"""

    message_html = f'<p style="color:#3D4452;">{req.message}</p>' if req.message else ""
    echeance_html = (
        f'<p style="color:#6B7A8D; font-size:13px;">Échéance : {inv.date_echeance.strftime("%d/%m/%Y")}</p>'
        if inv.date_echeance else ""
    )

    return f"""
    <div style="font-family: sans-serif; max-width: 560px; margin: 0 auto;">
      <h2 style="color:#0A2540;">Facture {inv.numero}</h2>
      {message_html}
      <div style="background:#F7F9F5; border-radius:10px; padding:16px; margin:16px 0; font-size:13px; color:#5B6573;">
        <strong>{req.emitter_nom or ""}</strong><br/>
        {req.emitter_adresse or ""}<br/>
        {f"SIRET : {req.emitter_siret}" if req.emitter_siret else ""}
      </div>
      <p style="color:#6B7A8D; font-size:13px;">
        Émise le {inv.date_emission.strftime("%d/%m/%Y")} — destinée à {inv.client_nom}
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
      <div style="text-align:right; margin-top:16px; font-size:16px; font-weight:700; color:#0A2540;">
        Total TTC : {inv.montant:.2f} €
      </div>
      <p style="color:#8BA5C0; font-size:11px; margin-top:24px;">TVA non applicable — article 293 B du CGI.</p>
      {f'<p style="color:#6B7A8D; font-size:12px;">{inv.notes}</p>' if inv.notes else ""}
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

    html = _build_invoice_email_html(inv, req)
    ok = send_invoice_email(inv.client_email, f"Facture {inv.numero}", html)
    if not ok:
        raise HTTPException(status_code=502, detail="Erreur lors de l'envoi de l'email")

    if inv.statut == "brouillon":
        inv.statut = "envoyee"
    db.commit()
    db.refresh(inv)
    return _invoice_to_dict(inv)


# ----------------------------------------------------------------
# Devis
# ----------------------------------------------------------------

class QuoteCreateRequest(BaseModel):
    client_nom: str
    client_email: Optional[str] = None
    client_adresse: Optional[str] = None
    date_emission: date
    date_validite: Optional[date] = None
    lignes: list[FactureLigne] = []
    notes: Optional[str] = None
    statut: str = "brouillon"


class QuoteUpdateRequest(BaseModel):
    client_nom: Optional[str] = None
    client_email: Optional[str] = None
    client_adresse: Optional[str] = None
    date_emission: Optional[date] = None
    date_validite: Optional[date] = None
    lignes: Optional[list[FactureLigne]] = None
    notes: Optional[str] = None


class QuoteStatusRequest(BaseModel):
    statut: str


def _next_numero_devis(db: Session, user_id: str) -> str:
    year = date.today().year
    count = (
        db.query(Quote)
        .filter(Quote.user_id == user_id, Quote.numero.like(f"D-{year}-%"))
        .count()
    )
    return f"D-{year}-{count + 1:03d}"


def _quote_to_dict(q: Quote) -> dict:
    return {
        "id": q.id,
        "numero": q.numero,
        "client_nom": q.client_nom,
        "client_email": q.client_email,
        "client_adresse": q.client_adresse,
        "date_emission": q.date_emission,
        "date_validite": q.date_validite,
        "montant": q.montant,
        "statut": q.statut,
        "lignes": q.lignes,
        "notes": q.notes,
        "converted_invoice_id": q.converted_invoice_id,
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

    q = Quote(
        user_id=user.id,
        numero=_next_numero_devis(db, user.id),
        client_nom=req.client_nom,
        client_email=req.client_email,
        client_adresse=req.client_adresse,
        date_emission=req.date_emission,
        date_validite=req.date_validite,
        montant=montant,
        statut=req.statut,
        lignes=lignes_dicts,
        notes=req.notes,
    )
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

    q.statut = req.statut
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


def _build_quote_email_html(q: Quote, req: "SendInvoiceRequest") -> str:
    lignes_html = ""
    for l in (q.lignes or []):
        desc = l.get("description", "") if isinstance(l, dict) else l.description
        qte = l.get("quantite", 0) if isinstance(l, dict) else l.quantite
        pu = l.get("prix_unitaire", 0) if isinstance(l, dict) else l.prix_unitaire
        total_ligne = (qte or 0) * (pu or 0)
        lignes_html += f"""
        <tr>
          <td style="padding:8px 0; border-bottom:1px solid #EEF2F7;">{desc}</td>
          <td style="padding:8px 0; border-bottom:1px solid #EEF2F7; text-align:center;">{qte}</td>
          <td style="padding:8px 0; border-bottom:1px solid #EEF2F7; text-align:right;">{pu:.2f} €</td>
          <td style="padding:8px 0; border-bottom:1px solid #EEF2F7; text-align:right; font-weight:600;">{total_ligne:.2f} €</td>
        </tr>"""

    message_html = f'<p style="color:#3D4452;">{req.message}</p>' if req.message else ""
    validite_html = (
        f'<p style="color:#6B7A8D; font-size:13px;">Devis valable jusqu\'au {q.date_validite.strftime("%d/%m/%Y")}</p>'
        if q.date_validite else ""
    )

    return f"""
    <div style="font-family: sans-serif; max-width: 560px; margin: 0 auto;">
      <h2 style="color:#0A2540;">Devis {q.numero}</h2>
      {message_html}
      <div style="background:#F7F9F5; border-radius:10px; padding:16px; margin:16px 0; font-size:13px; color:#5B6573;">
        <strong>{req.emitter_nom or ""}</strong><br/>
        {req.emitter_adresse or ""}<br/>
        {f"SIRET : {req.emitter_siret}" if req.emitter_siret else ""}
      </div>
      <p style="color:#6B7A8D; font-size:13px;">
        Émis le {q.date_emission.strftime("%d/%m/%Y")} — destiné à {q.client_nom}
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
      <div style="text-align:right; margin-top:16px; font-size:16px; font-weight:700; color:#0A2540;">
        Total TTC : {q.montant:.2f} €
      </div>
      <p style="color:#8BA5C0; font-size:11px; margin-top:24px;">TVA non applicable — article 293 B du CGI.</p>
      {f'<p style="color:#6B7A8D; font-size:12px;">{q.notes}</p>' if q.notes else ""}
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

    html = _build_quote_email_html(q, req)
    ok = send_invoice_email(q.client_email, f"Devis {q.numero}", html)
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
        date_emission=date.today(),
        date_echeance=None,
        montant=q.montant,
        statut="brouillon",
        lignes=q.lignes,
        notes=q.notes,
    )
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
):
    allowed_ext = (".pdf", ".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".webp")
    if not file.filename.lower().endswith(allowed_ext):
        raise HTTPException(status_code=400, detail="Format de fichier non supporte")

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
            "message": f"Le statut {profile.statut.upper()} arrive bientot. "
            f"On vous previent des qu'il est pret.",
        }

    entries = db.query(IncomeEntry).filter(IncomeEntry.user_id == user.id).all()
    incomes = [(e.date, e.amount) for e in entries]

    paid_invoices = (
        db.query(ClientInvoice)
        .filter(ClientInvoice.user_id == user.id, ClientInvoice.statut == "payee")
        .all()
    )
    incomes += [(inv.date_paiement or inv.date_emission, inv.montant) for inv in paid_invoices]

    result = estimate(
        statut=profile.statut,
        activite=profile.activite,
        periodicite=profile.periodicite,
        acre=profile.acre,
        versement_liberatoire=profile.versement_liberatoire,
        incomes=incomes,
        today=date.today(),
    )

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

    system_prompt = (
        "Tu es Hector, le compagnon financier d'un travailleur independant francais. "
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
                "max_tokens": 600,
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
        raise HTTPException(status_code=502, detail=f"Erreur assistant IA : {e}")

    return {"reply": reply or "Desole, je n'ai pas pu generer de reponse."}


@app.get("/health")
def health():
    return {"status": "ok", "timestamp": datetime.now().isoformat()}

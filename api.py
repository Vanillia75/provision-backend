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
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session
from google.oauth2 import id_token as google_id_token
from google.auth.transport import requests as google_requests

from database import Base, engine, get_db
from models import User, Profile, IncomeEntry, ClientInvoice, Expense, Contact
from auth import hash_password, verify_password, create_token, get_current_user
from tax_engine import estimate, STATUTS_DISPONIBLES, STATUTS_A_VENIR, AUTO_ENTREPRENEUR_RATES
from invoice_extractor import extract_invoice_data
from insee_lookup import lookup_siret, SiretLookupError

Base.metadata.create_all(bind=engine)

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

STATUTS_FACTURE = ("brouillon", "envoyee", "payee", "impayee")

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
# Profil
# ----------------------------------------------------------------

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
        "prenom": profile.prenom,
        "nom": profile.nom,
        "telephone": profile.telephone,
        "entreprise": profile.entreprise,
        "depenses_mensuelles": profile.depenses_mensuelles,
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

    db.commit()
    return {"ok": True}


class ProfileDetailsRequest(BaseModel):
    prenom: Optional[str] = None
    nom: Optional[str] = None
    telephone: Optional[str] = None
    entreprise: Optional[str] = None
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
            "prenom": profile.prenom,
            "nom": profile.nom,
            "telephone": profile.telephone,
            "entreprise": profile.entreprise,
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
                f"Utilise ces vrais chiffres pour repondre precisement a ses questions (ex: combien il peut se verser, "
                f"depenser, ou quand il risque de depasser le plafond)."
            )
        except Exception:
            context = f"L'utilisateur est {profile.statut} en activite '{profile.activite}'."

    system_prompt = (
        "Tu es H.CTOR, un assistant fiscal expert pour les auto-entrepreneurs francais. "
        "Tu reponds de facon claire, concise et bienveillante, en francais. "
        f"{context} "
        "Tu donnes des conseils pratiques sur l'URSSAF, les cotisations, la TVA, l'ACRE, "
        "la declaration de revenus. Tu rappelles de consulter un comptable pour les cas complexes. "
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
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Erreur assistant IA : {e}")

    return {"reply": reply or "Desole, je n'ai pas pu generer de reponse."}


@app.get("/health")
def health():
    return {"status": "ok", "timestamp": datetime.now().isoformat()}

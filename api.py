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
from models import User, Profile, IncomeEntry
from auth import hash_password, verify_password, create_token, get_current_user
from tax_engine import estimate, STATUTS_DISPONIBLES, STATUTS_A_VENIR, AUTO_ENTREPRENEUR_RATES
from invoice_extractor import extract_invoice_data

Base.metadata.create_all(bind=engine)

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

app = FastAPI(title="API Provision Cotisations")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # a restreindre au domaine du frontend une fois connu
    allow_methods=["*"],
    allow_headers=["*"],
)


# ────────────────────────────────────────────────────────────
# Schemas
# ────────────────────────────────────────────────────────────

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


# ────────────────────────────────────────────────────────────
# Auth
# ────────────────────────────────────────────────────────────

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


# ────────────────────────────────────────────────────────────
# Profil
# ────────────────────────────────────────────────────────────

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


# ────────────────────────────────────────────────────────────
# Revenus
# ────────────────────────────────────────────────────────────

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


@app.post("/income/upload")
async def upload_invoice(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
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

    entry_date = data["date"].date() if data["date"] else date.today()

    entry = IncomeEntry(
        user_id=user.id,
        date=entry_date,
        amount=data["amount"],
        description=f"Facture importee : {file.filename}",
        source="facture",
        filename=file.filename,
    )
    db.add(entry)
    db.commit()

    return {
        "ok": True,
        "id": entry.id,
        "amount": data["amount"],
        "date": entry_date,
        "filename": file.filename,
    }


# ────────────────────────────────────────────────────────────
# Estimation des cotisations
# ────────────────────────────────────────────────────────────

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
        "Tu es H€CTOR, un assistant fiscal expert pour les auto-entrepreneurs francais. "
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

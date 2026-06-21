import os
import bcrypt
import jwt
from datetime import datetime, timedelta
from typing import Optional
from fastapi import Depends, HTTPException, Header
from sqlalchemy.orm import Session
from database import get_db
from models import User

JWT_SECRET = os.environ.get("JWT_SECRET", "change-me-in-production")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_DAYS = 30


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode(), password_hash.encode())


def create_token(user_id: str) -> str:
    payload = {
        "sub": user_id,
        "exp": datetime.utcnow() + timedelta(days=JWT_EXPIRE_DAYS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def get_current_user(
    authorization: Optional[str] = Header(None), db: Session = Depends(get_db)
) -> User:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Non authentifie")
    token = authorization.split(" ", 1)[1]
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Token invalide ou expire")
    user = db.query(User).filter(User.id == payload["sub"]).first()
    if not user:
        raise HTTPException(status_code=401, detail="Utilisateur introuvable")
    return user


# ----------------------------------------------------------------
# Tokens a usage unique : reinitialisation de mot de passe et
# verification d'email. Reutilisent le meme secret JWT, mais portent
# un champ "purpose" pour ne jamais etre confondus avec un token de
# session classique (cree par create_token ci-dessus).
# ----------------------------------------------------------------

def create_purpose_token(user_id: str, purpose: str, expire_minutes: int = 60) -> str:
    payload = {
        "sub": user_id,
        "purpose": purpose,
        "exp": datetime.utcnow() + timedelta(minutes=expire_minutes),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def verify_purpose_token(token: str, expected_purpose: str) -> str:
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.PyJWTError:
        raise HTTPException(status_code=400, detail="Lien invalide ou expire")
    if payload.get("purpose") != expected_purpose:
        raise HTTPException(status_code=400, detail="Lien invalide")
    return payload["sub"]

import uuid
from datetime import datetime, date
from sqlalchemy import Column, String, Float, Boolean, DateTime, Date, ForeignKey
from sqlalchemy.orm import relationship

from database import Base


def gen_uuid() -> str:
    return str(uuid.uuid4())


class User(Base):
    __tablename__ = "users"

    id = Column(String, primary_key=True, default=gen_uuid)
    email = Column(String, unique=True, nullable=False, index=True)
    password_hash = Column(String, nullable=True)
    google_id = Column(String, unique=True, nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    profile = relationship(
        "Profile", uselist=False, back_populates="user", cascade="all, delete-orphan"
    )
    incomes = relationship(
        "IncomeEntry", back_populates="user", cascade="all, delete-orphan"
    )


class Profile(Base):
    __tablename__ = "profiles"

    id = Column(String, primary_key=True, default=gen_uuid)
    user_id = Column(String, ForeignKey("users.id"), unique=True, nullable=False)

    statut = Column(String, nullable=False, default="auto_entrepreneur")
    activite = Column(String, nullable=True)
    periodicite = Column(String, nullable=False, default="mensuelle")
    acre = Column(Boolean, default=False)
    versement_liberatoire = Column(Boolean, default=False)
    date_creation_activite = Column(Date, nullable=True)
    onboarding_complete = Column(Boolean, default=False)

    siret = Column(String, nullable=True, index=True)
    raison_sociale = Column(String, nullable=True)

    user = relationship("User", back_populates="profile")


class IncomeEntry(Base):
    __tablename__ = "income_entries"

    id = Column(String, primary_key=True, default=gen_uuid)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    date = Column(Date, nullable=False)
    amount = Column(Float, nullable=False)
    description = Column(String, nullable=True)
    source = Column(String, default="manuel")
    filename = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="incomes")

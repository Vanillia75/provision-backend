import uuid
from datetime import datetime, date
from sqlalchemy import Column, String, Float, Boolean, DateTime, Date, ForeignKey, JSON
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
    email_verified = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    profile = relationship(
        "Profile", uselist=False, back_populates="user", cascade="all, delete-orphan"
    )
    incomes = relationship(
        "IncomeEntry", back_populates="user", cascade="all, delete-orphan"
    )
    client_invoices = relationship(
        "ClientInvoice", back_populates="user", cascade="all, delete-orphan"
    )
    expenses = relationship(
        "Expense", back_populates="user", cascade="all, delete-orphan"
    )
    contacts = relationship(
        "Contact", back_populates="user", cascade="all, delete-orphan"
    )
    quotes = relationship(
        "Quote", back_populates="user", cascade="all, delete-orphan"
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

    prenom = Column(String, nullable=True)
    nom = Column(String, nullable=True)
    telephone = Column(String, nullable=True)
    entreprise = Column(String, nullable=True)
    depenses_mensuelles = Column(Float, nullable=True)

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


# Statuts possibles : "brouillon", "envoyee", "payee", "impayee"
class ClientInvoice(Base):
    __tablename__ = "client_invoices"

    id = Column(String, primary_key=True, default=gen_uuid)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)

    numero = Column(String, nullable=False)
    client_nom = Column(String, nullable=False)
    client_email = Column(String, nullable=True)
    client_adresse = Column(String, nullable=True)

    date_emission = Column(Date, nullable=False)
    date_echeance = Column(Date, nullable=True)
    date_paiement = Column(Date, nullable=True)

    montant = Column(Float, nullable=False)
    statut = Column(String, nullable=False, default="brouillon")

    lignes = Column(JSON, nullable=True)
    notes = Column(String, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="client_invoices")


# Categories possibles : "logiciels", "abonnements", "taxi", "repas", "materiel",
# "coworking", "telephone_internet", "autre"
class Expense(Base):
    __tablename__ = "expenses"

    id = Column(String, primary_key=True, default=gen_uuid)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)

    date = Column(Date, nullable=False)
    montant = Column(Float, nullable=False)
    categorie = Column(String, nullable=False, default="autre")
    description = Column(String, nullable=True)
    source = Column(String, default="manuel")  # "manuel" ou "import"
    filename = Column(String, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="expenses")


class Contact(Base):
    __tablename__ = "contacts"

    id = Column(String, primary_key=True, default=gen_uuid)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)

    nom = Column(String, nullable=False)
    email = Column(String, nullable=True)
    siret = Column(String, nullable=True)
    adresse = Column(String, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="contacts")


# Statuts possibles : "brouillon", "envoye", "accepte", "refuse", "expire"
class Quote(Base):
    __tablename__ = "quotes"

    id = Column(String, primary_key=True, default=gen_uuid)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)

    numero = Column(String, nullable=False)
    client_nom = Column(String, nullable=False)
    client_email = Column(String, nullable=True)
    client_adresse = Column(String, nullable=True)

    date_emission = Column(Date, nullable=False)
    date_validite = Column(Date, nullable=True)

    montant = Column(Float, nullable=False)
    statut = Column(String, nullable=False, default="brouillon")

    lignes = Column(JSON, nullable=True)
    notes = Column(String, nullable=True)

    # Renseigne l'id de la facture creee si ce devis a ete converti
    converted_invoice_id = Column(String, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="quotes")

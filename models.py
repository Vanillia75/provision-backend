import uuid
from datetime import datetime, date
from sqlalchemy import Column, String, Float, Boolean, DateTime, Date, ForeignKey, JSON, Integer, Index
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
    intermittent_activities = relationship(
        "IntermittentActivity", back_populates="user", cascade="all, delete-orphan"
    )
    ai_usage = relationship(
        "AIUsage", back_populates="user", cascade="all, delete-orphan"
    )
    fiscal_settings = relationship(
        "FiscalSettings", uselist=False, back_populates="user", cascade="all, delete-orphan"
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
    # Date anniversaire des droits intermittent (échéance de renouvellement).
    # Saisie par l'utilisateur. Nullable : seuls les profils intermittents l'utilisent.
    date_anniversaire = Column(Date, nullable=True)
    montant_journalier = Column(Float, nullable=True)   # allocation journalière lue sur l'ARE, jamais recalculée
    # Éléments de calcul de l'allocation (saisis depuis la notification France Travail) :
    # servent à RECALCULER l'AJ (allocation_engine) et à la comparer au montant officiel.
    salaire_reference = Column(Float, nullable=True)    # SR : salaires bruts de la période de référence
    heures_reference = Column(Float, nullable=True)     # NHT : heures retenues sur la période
    annexe_allocation = Column(String, nullable=True)   # "annexe8" (technicien) | "annexe10" (artiste)
    onboarding_complete = Column(Boolean, default=False)

    siret = Column(String, nullable=True, index=True)
    raison_sociale = Column(String, nullable=True)
    adresse = Column(String, nullable=True)

    prenom = Column(String, nullable=True)
    nom = Column(String, nullable=True)
    telephone = Column(String, nullable=True)
    entreprise = Column(String, nullable=True)
    depenses_mensuelles = Column(Float, nullable=True)
    solde_bancaire = Column(Float, nullable=True)
    reserve_securite = Column(Float, nullable=True)
    tmi = Column(String, nullable=True)
    # Relances automatiques d'impayés : délai en jours (NULL = désactivé, le défaut).
    relance_auto_jours = Column(Integer, nullable=True)

    # Connexion bancaire Powens (agrégateur DSP2, lecture seule).
    # powens_token : token permanent de l'utilisateur côté Powens (sensible).
    # powens_user_id : id utilisateur Powens (debug / webhooks éventuels).
    # powens_connection_id : id de la connexion bancaire active (None = pas de banque reliée).
    powens_token = Column(String, nullable=True)
    powens_user_id = Column(String, nullable=True)
    powens_connection_id = Column(Integer, nullable=True)

    user = relationship("User", back_populates="profile")


class FiscalSettings(Base):
    """
    Paramètres fiscaux liés à la FACTURATION uniquement, isolés du moteur fiscal.

    Relation 1-1 avec l'utilisateur. Lu UNIQUEMENT par la facturation (jamais par
    tax_engine.py ni intermittent_engine.py). Un compte sans ligne ici est traité
    en franchise (voir legal_mentions.resolve_fiscal_settings). Table additive :
    sa création ne touche aucune table existante.
    """
    __tablename__ = "fiscal_settings"

    id = Column(String, primary_key=True, default=gen_uuid)
    user_id = Column(String, ForeignKey("users.id"), unique=True, nullable=False)

    # "franchise" (par défaut) | "assujetti"
    vat_mode = Column(String, nullable=False, default="franchise")
    vat_rate = Column(Float, nullable=False, default=20.0)
    vat_number = Column(String, nullable=True)

    # Point de départ de la numérotation des FACTURES (reprise d'une séquence existante).
    # Numéro complet attendu, ex. "F-2026-042". NULL = pas de reprise (séquence auto).
    # Sert de PLANCHER au générateur ; on ne descend jamais en dessous, jamais d'édition libre.
    facture_numero_depart = Column(String, nullable=True)

    user = relationship("User", back_populates="fiscal_settings")


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


# ============================================================================
#  INTERMITTENT — socle de données du module intermittent du spectacle.
#  Une ligne = une déclaration d'activité (un contrat, une période, un cachet).
#  Le calcul (conversion en heures, fenêtre glissante 12 mois, total vers 507h)
#  se fait dans le MOTEUR, jamais stocké en dur ici : si une règle change, on
#  ne touche qu'au moteur, pas aux données. Suivi INDICATIF, ne remplace pas
#  France Travail.
#
#  type_activite :
#    - "heures"        → techniciens (annexe 8) : nombre = heures réelles
#    - "cachet"        → 1 cachet (artiste, annexe 10) = 12h (converti par le moteur)
#  NOTE : les anciennes valeurs "cachet_isole" / "cachet_groupe" peuvent exister
#  en base (historique). Le moteur les compte toutes 12h désormais : la règle
#  "cachet groupé = 8h" a été abandonnée (cf. regles_intermittent).
#  nombre : nb d'heures si type="heures", sinon nb de cachets.
# ============================================================================
class IntermittentActivity(Base):
    __tablename__ = "intermittent_activities"

    id = Column(String, primary_key=True, default=gen_uuid)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)

    date = Column(Date, nullable=False)
    # Date de fin du contrat, le cas échéant (AEM couvrant une période, ex : du 12 au 17).
    # Sert UNIQUEMENT à l'affichage ("du X au Y"). Le calcul des 507h utilise 'date' (le début).
    # Nullable : un cachet/contrat d'un seul jour n'a pas de date de fin distincte.
    date_fin = Column(Date, nullable=True)
    employeur = Column(String, nullable=True)
    type_activite = Column(String, nullable=False, default="heures")
    nombre = Column(Float, nullable=False, default=0)

    # Salaire brut du contrat (renseigné à la saisie ou lu sur l'AEM). Sert au récap
    # d'actualisation France Travail. Nullable : pas toujours connu.
    salaire_brut = Column(Float, nullable=True)
    # Métier du contrat : "artiste" | "technicien" | NULL (non départagé). Renseigné à la
    # saisie (heures) ou proposé par le scan AEM (emploi occupé). INFORMATIF uniquement :
    # sert à la répartition annexe 8/10 affichée, ne touche AUCUN calcul d'heures ou d'AJ.
    metier = Column(String, nullable=True)
    # true si l'AEM (Attestation Employeur Mensuelle) correspondante a été reçue/scannée.
    # Sert à la check-list d'actualisation ("il te manque une AEM").
    aem_recue = Column(Boolean, nullable=False, default=False)
    # true si l'activité est une ESTIMATION (heures pas encore confirmées par l'AEM/la paie).
    # Permet de déclarer au plus juste en attendant le document réel, puis de régulariser.
    # Affichée avec un badge distinct ; ne doit jamais être présentée comme un chiffre certain.
    estime = Column(Boolean, nullable=False, default=False)
    # Nom du fichier AEM scanné, le cas échéant.
    aem_filename = Column(String, nullable=True)
    # Clé du fichier original stocké sur Cloudflare R2 (pour consultation / suppression RGPD).
    # Nullable : null si le document n'a pas été conservé (ancien scan, ou R2 désactivé).
    aem_r2_key = Column(String, nullable=True)

    source = Column(String, default="manuel")  # "manuel" ou "ocr" (AEM)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="intermittent_activities")


# Statuts possibles : "brouillon", "envoyee", "payee", "impayee"
class ClientInvoice(Base):
    __tablename__ = "client_invoices"
    # Garde-fou DB : un numéro de facture est unique par utilisateur (anti-doublon).
    __table_args__ = (Index("uq_client_invoices_user_numero", "user_id", "numero", unique=True),)

    id = Column(String, primary_key=True, default=gen_uuid)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)

    numero = Column(String, nullable=False)
    client_nom = Column(String, nullable=False)
    client_email = Column(String, nullable=True)
    client_adresse = Column(String, nullable=True)
    # Type de client + identifiants pro (facultatifs, affichés sur le doc si renseignés).
    # NULL → traité comme « particulier » (factures antérieures à cette fonctionnalité).
    client_type = Column(String, nullable=True, default="particulier")
    client_siret = Column(String, nullable=True)
    client_tva = Column(String, nullable=True)

    date_emission = Column(Date, nullable=False)
    date_echeance = Column(Date, nullable=True)
    date_paiement = Column(Date, nullable=True)

    montant = Column(Float, nullable=False)
    statut = Column(String, nullable=False, default="brouillon")

    lignes = Column(JSON, nullable=True)
    notes = Column(String, nullable=True)

    # Régime TVA FIGÉ au moment de l'émission (conformité : une facture émise est immuable).
    # NULL = facture antérieure à cette fonctionnalité → traitée en franchise (fallback).
    # `montant` reste toujours le HT ; ces champs ne servent qu'à l'affichage TVA/TTC.
    vat_mode = Column(String, nullable=True)
    vat_rate = Column(Float, nullable=True)
    vat_number = Column(String, nullable=True)

    # Le TTC encaissé a-t-il été ajouté au solde bancaire (sur confirmation explicite) ?
    # NULL/false = pas intégré (factures anciennes incluses). Empêche tout double-ajout.
    solde_integre = Column(Boolean, nullable=True)

    # Date d'envoi de la relance automatique (NULL = jamais relancée).
    # Garde-fou : une facture n'est JAMAIS relancée deux fois automatiquement.
    relance_envoyee_le = Column(DateTime, nullable=True)

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
    # Garde-fou DB : un numéro de devis est unique par utilisateur (anti-doublon).
    __table_args__ = (Index("uq_quotes_user_numero", "user_id", "numero", unique=True),)

    id = Column(String, primary_key=True, default=gen_uuid)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)

    numero = Column(String, nullable=False)
    client_nom = Column(String, nullable=False)
    client_email = Column(String, nullable=True)
    client_adresse = Column(String, nullable=True)
    # Type de client + identifiants pro (mêmes champs que ClientInvoice). NULL → particulier.
    client_type = Column(String, nullable=True, default="particulier")
    client_siret = Column(String, nullable=True)
    client_tva = Column(String, nullable=True)

    date_emission = Column(Date, nullable=False)
    date_validite = Column(Date, nullable=True)

    montant = Column(Float, nullable=False)
    statut = Column(String, nullable=False, default="brouillon")

    lignes = Column(JSON, nullable=True)
    notes = Column(String, nullable=True)

    # Renseigne l'id de la facture creee si ce devis a ete converti
    converted_invoice_id = Column(String, nullable=True)

    # Régime TVA figé (même logique que ClientInvoice). NULL → franchise (fallback).
    vat_mode = Column(String, nullable=True)
    vat_rate = Column(Float, nullable=True)
    vat_number = Column(String, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="quotes")


# ============================================================================
#  AI USAGE — compteur d'appels IA par utilisateur, par jour, par type.
#  Sert à plafonner la consommation (coût Anthropic borné) et à tracer l'usage.
#  Une ligne = (user, jour, type d'appel). On incrémente "count" à chaque appel.
#  type : "chat" (assistant Hector) | "aem_scan" (lecture AEM via Vision)
# ============================================================================
class AIUsage(Base):
    __tablename__ = "ai_usage"

    id = Column(String, primary_key=True, default=gen_uuid)
    user_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)
    jour = Column(Date, nullable=False, index=True)       # date du jour (UTC)
    type_appel = Column(String, nullable=False)            # "chat" | "aem_scan"
    count = Column(Float, nullable=False, default=0)
    updated_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="ai_usage")


# ============================================================================
#  LOGIN ATTEMPT — protection anti brute-force.
#  Une ligne par email tenté. On compte les échecs consécutifs et l'heure du
#  dernier échec : au-delà d'un seuil dans une fenêtre de temps, on bloque
#  temporairement les tentatives pour cet email. Réinitialisé à la 1re réussite.
# ============================================================================
class LoginAttempt(Base):
    __tablename__ = "login_attempts"

    id = Column(String, primary_key=True, default=gen_uuid)
    email = Column(String, nullable=False, index=True)
    echecs = Column(Float, nullable=False, default=0)        # nb d'échecs consécutifs
    dernier_echec = Column(DateTime, nullable=True)          # horodatage du dernier échec
    bloque_jusqua = Column(DateTime, nullable=True)          # blocage temporaire jusqu'à


# ============================================================================
#  SUBSCRIPTION — abonnement Stripe d'un utilisateur (1 ligne par user).
#  SOURCE DE VÉRITÉ du premium, lue par is_premium() (billing.py).
#  - source="stripe" : abonnement payant classique.
#  - source="comp"   : premium offert (code testeur), SANS Stripe.
#  Le premium n'est jamais activé ici à la main : uniquement via le webhook
#  signé (paiement) ou activate_comp_premium (code testeur).
# ============================================================================
class Subscription(Base):
    __tablename__ = "subscriptions"

    id = Column(String, primary_key=True, default=gen_uuid)
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)

    stripe_customer_id = Column(String, nullable=True, index=True)
    stripe_subscription_id = Column(String, nullable=True, index=True)

    plan = Column(String, nullable=False, default="free")        # "free" | "premium"
    status = Column(String, nullable=True)                       # statut Stripe, ou "comp"
    current_period_end = Column(DateTime, nullable=True)         # fin de période payée / offerte
    cancel_at_period_end = Column(Boolean, nullable=False, default=False)
    source = Column(String, nullable=False, default="stripe")    # "stripe" | "comp"

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship("User")


# ============================================================================
#  PROMO CODE — codes maison (influenceurs ET testeurs, même schéma).
#  - kind="influencer" : réduction appliquée VIA Stripe (stripe_coupon_id).
#  - kind="tester"     : premium offert DIRECTEMENT, sans carte ni Stripe.
# ============================================================================
class PromoCode(Base):
    __tablename__ = "promo_codes"

    id = Column(String, primary_key=True, default=gen_uuid)
    code = Column(String, unique=True, nullable=False, index=True)
    type = Column(String, nullable=False)                # "free_months" | "percent_off"
    value = Column(Float, nullable=True)                 # nb de mois OU pourcentage
    kind = Column(String, nullable=False)                # "influencer" | "tester"
    influencer_name = Column(String, nullable=True)
    stripe_coupon_id = Column(String, nullable=True)     # coupon Stripe (codes influenceurs)
    max_uses = Column(Integer, nullable=True)            # None = illimité
    times_used = Column(Integer, nullable=False, default=0)
    active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)


# ============================================================================
#  STRIPE EVENT — déduplication des webhooks (idempotence).
#  On enregistre l'id de chaque event traité : un même event reçu 2x est ignoré.
# ============================================================================
class StripeEvent(Base):
    __tablename__ = "stripe_events"

    event_id = Column(String, primary_key=True)          # "evt_..."
    type = Column(String, nullable=True)
    received_at = Column(DateTime, default=datetime.utcnow)

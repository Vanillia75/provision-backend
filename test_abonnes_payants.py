# ════════════════════════════════════════════════════════════════════════
#  Tests du compteur « abonnés payants » (argent réel).
#  Règle : compte TOUT paiement réel (Stripe/Apple/Google, premium, actif),
#  proches/VIP inclus (argent réel = légitime). Exclut UNIQUEMENT le sandbox,
#  le grâcieux (comp) et les abonnements non actifs (annulés).
# ════════════════════════════════════════════════════════════════════════
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from models import User, Subscription
import billing


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _payeur(db, *, is_test=False, source="apple", status="active",
            is_sandbox=False, plan="premium", email=None):
    u = User(email=email or f"u{datetime.utcnow().timestamp()}@ex.fr", is_test=is_test)
    db.add(u); db.commit(); db.refresh(u)
    db.add(Subscription(user_id=u.id, plan=plan, status=status,
                        source=source, is_sandbox=is_sandbox, created_at=datetime.utcnow()))
    db.commit()
    return u


def test_client_externe_compte(db):
    _payeur(db, is_test=False)
    assert billing.compter_abonnes_payants(db) == 1


def test_proche_qui_a_paye_compte_aussi(db):
    """LE changement : un proche/VIP qui paie de l'argent réel compte."""
    _payeur(db, is_test=True, source="apple")  # ex. la mère, vrai paiement Apple
    total, proches = billing.compter_abonnes_detail(db)
    assert total == 1 and proches == 1


def test_sandbox_jamais_compte(db):
    _payeur(db, is_test=False, is_sandbox=True)   # achat reviewer Apple
    assert billing.compter_abonnes_payants(db) == 0


def test_gracieux_comp_non_compte(db):
    _payeur(db, is_test=False, source="comp", status="comp")
    assert billing.compter_abonnes_payants(db) == 0


def test_abonnement_annule_non_compte(db):
    _payeur(db, is_test=False, status="canceled")
    assert billing.compter_abonnes_payants(db) == 0


def test_scenario_reel_totor(db):
    """Le cas concret : 1 client externe (Apple annuel) + 1 proche (mère, Apple)
    + 1 sandbox (reviewer) + 1 test Stripe annulé  ->  total 2, dont 1 proche."""
    _payeur(db, is_test=False, source="apple")                      # externe
    _payeur(db, is_test=True, source="apple")                       # proche (mère)
    _payeur(db, is_test=True, source="apple", is_sandbox=True)      # sandbox reviewer
    _payeur(db, is_test=True, source="stripe", status="canceled")  # vieux test annulé
    total, proches = billing.compter_abonnes_detail(db)
    assert total == 2 and proches == 1

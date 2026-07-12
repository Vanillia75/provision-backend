# ════════════════════════════════════════════════════════════════════════
#  Tests du webhook RevenueCat (revenuecat_webhook.py) — sqlite en mémoire.
#  Couvre : achat, renouvellement, annulation, remboursement, expiration,
#  protection d'un abonnement Stripe actif, utilisateur anonyme/inconnu.
# ════════════════════════════════════════════════════════════════════════
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from models import User, Subscription
import billing
import revenuecat_webhook as rc


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _user(db):
    u = User(email="testeur@exemple-hector.fr")
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _evt(user_id, type_evt, store="APP_STORE", exp_dans_jours=30):
    exp = datetime.utcnow() + timedelta(days=exp_dans_jours)
    return {"event": {
        "type": type_evt,
        "app_user_id": user_id,
        "store": store,
        "entitlement_ids": ["veille"],
        "expiration_at_ms": int(exp.timestamp() * 1000),
    }}


def test_achat_initial_apple_active_le_premium(db):
    u = _user(db)
    r = rc.traiter_evenement(db, _evt(u.id, "INITIAL_PURCHASE", "APP_STORE"))
    assert r["plan"] == "premium" and r["source"] == "apple"
    assert billing.is_premium(db, u) is True


def test_achat_google_active_le_premium(db):
    u = _user(db)
    rc.traiter_evenement(db, _evt(u.id, "INITIAL_PURCHASE", "PLAY_STORE"))
    row = db.query(Subscription).filter_by(user_id=u.id).first()
    assert row.source == "google" and billing.is_premium(db, u)


def test_renouvellement_prolonge(db):
    u = _user(db)
    rc.traiter_evenement(db, _evt(u.id, "INITIAL_PURCHASE", exp_dans_jours=1))
    rc.traiter_evenement(db, _evt(u.id, "RENEWAL", exp_dans_jours=31))
    row = db.query(Subscription).filter_by(user_id=u.id).first()
    assert row.current_period_end > datetime.utcnow() + timedelta(days=29)


def test_annulation_garde_le_premium_jusqu_a_la_fin(db):
    u = _user(db)
    rc.traiter_evenement(db, _evt(u.id, "INITIAL_PURCHASE", exp_dans_jours=20))
    rc.traiter_evenement(db, _evt(u.id, "CANCELLATION", exp_dans_jours=20))
    row = db.query(Subscription).filter_by(user_id=u.id).first()
    assert row.cancel_at_period_end is True
    assert billing.is_premium(db, u) is True   # il a payé jusqu'au bout du mois


def test_remboursement_coupe_tout_de_suite(db):
    u = _user(db)
    rc.traiter_evenement(db, _evt(u.id, "INITIAL_PURCHASE"))
    # Remboursement : CANCELLATION avec une expiration déjà passée.
    rc.traiter_evenement(db, _evt(u.id, "CANCELLATION", exp_dans_jours=-1))
    assert billing.is_premium(db, u) is False


def test_expiration_retire_le_premium(db):
    u = _user(db)
    rc.traiter_evenement(db, _evt(u.id, "INITIAL_PURCHASE"))
    rc.traiter_evenement(db, _evt(u.id, "EXPIRATION", exp_dans_jours=0))
    assert billing.is_premium(db, u) is False


def test_un_stripe_actif_n_est_jamais_ecrase(db):
    u = _user(db)
    db.add(Subscription(user_id=u.id, plan="premium", status="active", source="stripe",
                        current_period_end=datetime.utcnow() + timedelta(days=200)))
    db.commit()
    r = rc.traiter_evenement(db, _evt(u.id, "INITIAL_PURCHASE", "APP_STORE"))
    assert r["ignore"] == "abonnement_stripe_actif"
    row = db.query(Subscription).filter_by(user_id=u.id).first()
    assert row.source == "stripe"              # rien n'a bougé


def test_anonyme_et_inconnu_ignores_proprement(db):
    assert rc.traiter_evenement(db, _evt("$RCAnonymousID:abc", "INITIAL_PURCHASE"))["ignore"] == "utilisateur_anonyme"
    assert rc.traiter_evenement(db, _evt("id-fantome", "INITIAL_PURCHASE"))["ignore"] == "utilisateur_inconnu"


def test_auth_du_webhook(monkeypatch):
    monkeypatch.setattr(rc, "REVENUECAT_WEBHOOK_AUTH", "Bearer secret-totor")
    assert rc.verifier_auth("Bearer secret-totor") is True
    assert rc.verifier_auth("Bearer mauvais") is False
    assert rc.verifier_auth("") is False

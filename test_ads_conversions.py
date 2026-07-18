# ════════════════════════════════════════════════════════════════════════
#  Tests export conversions Google Ads (Chemin B, import hors ligne).
#  Regles : seulement les comptes NON test AVEC gclid ; « Abonnement web » =
#  Stripe actif non-sandbox ; l'in-app (apple) et les comptes test sont exclus.
# ════════════════════════════════════════════════════════════════════════
from datetime import datetime

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from models import User, Subscription
import api


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


@pytest.fixture(autouse=True)
def admin_ouvert(monkeypatch):
    # On teste la logique d'export, pas l'auth (couverte ailleurs).
    monkeypatch.setattr(api, "_admin_authed", lambda *a, **k: True)


def _user(db, email, gclid=None, is_test=False):
    u = User(email=email, gclid=gclid, is_test=is_test, created_at=datetime.utcnow())
    db.add(u); db.commit(); db.refresh(u)
    return u


def _sub(db, user, source="stripe", status="active", is_sandbox=False):
    db.add(Subscription(user_id=user.id, plan="premium", status=status,
                        source=source, is_sandbox=is_sandbox, created_at=datetime.utcnow()))
    db.commit()


def _csv(db):
    return api.admin_ads_conversions(request=None, key="x", days=90, db=db).body.decode()


def test_inscription_avec_gclid_est_exportee(db):
    _user(db, "a@ex.fr", gclid="GCL_A")
    out = _csv(db)
    assert "GCL_A,Inscription gratuite," in out


def test_sans_gclid_absent(db):
    _user(db, "b@ex.fr", gclid=None)
    out = _csv(db)
    assert "Inscription gratuite" not in out  # aucune ligne du tout


def test_compte_test_exclu(db):
    _user(db, "moi@ex.fr", gclid="GCL_TEST", is_test=True)
    out = _csv(db)
    assert "GCL_TEST" not in out


def test_abonnement_web_stripe_exporte(db):
    u = _user(db, "c@ex.fr", gclid="GCL_C")
    _sub(db, u, source="stripe", status="active")
    out = _csv(db)
    assert "GCL_C,Inscription gratuite," in out
    assert "GCL_C,Abonnement web," in out


def test_abonnement_inapp_apple_non_exporte(db):
    u = _user(db, "d@ex.fr", gclid="GCL_D")
    _sub(db, u, source="apple", status="active")  # in-app : pas de conversion web
    out = _csv(db)
    assert "GCL_D,Inscription gratuite," in out
    assert "Abonnement web" not in out


def test_format_google(db):
    _user(db, "e@ex.fr", gclid="GCL_E")
    out = _csv(db)
    assert out.startswith("Parameters:TimeZone=+0000")
    assert "Google Click ID,Conversion Name,Conversion Time,Conversion Value,Conversion Currency" in out


# ── La clé dédiée lecture seule (pour l'import programmé Google) ──────────

def test_cle_dediee_lecture_seule_donne_acces(db, monkeypatch):
    monkeypatch.setattr(api, "_admin_authed", lambda *a, **k: False)  # PAS admin
    monkeypatch.setenv("ADS_EXPORT_KEY", "ma-cle-ro")
    _user(db, "f@ex.fr", gclid="GCL_F")
    out = api.admin_ads_conversions(request=None, key="ma-cle-ro", days=90, db=db).body.decode()
    assert "GCL_F,Inscription gratuite," in out


def test_mauvaise_cle_donne_404(db, monkeypatch):
    monkeypatch.setattr(api, "_admin_authed", lambda *a, **k: False)
    monkeypatch.setenv("ADS_EXPORT_KEY", "ma-cle-ro")
    with pytest.raises(HTTPException) as e:
        api.admin_ads_conversions(request=None, key="mauvaise-cle", days=90, db=db)
    assert e.value.status_code == 404

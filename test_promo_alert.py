# ════════════════════════════════════════════════════════════════════════
#  Alerte fondateur quand un code cadeau (kind "tester") est utilisé.
#  Base SQLite en mémoire, email mocké : aucun réseau.
# ════════════════════════════════════════════════════════════════════════
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from models import User, PromoCode
import billing
import emailing


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _user(db, email="cadeau@exemple-hector.fr"):
    u = User(email=email)
    db.add(u); db.commit(); db.refresh(u)
    return u


def _code(db, code="TOTORMERCI", max_uses=15):
    pc = PromoCode(code=code, type="free_months", value=1, kind="tester",
                   max_uses=max_uses, times_used=0, active=True)
    db.add(pc); db.commit()
    return pc


def test_alerte_envoyee_a_chaque_utilisation(db, monkeypatch):
    alertes = []
    monkeypatch.setattr(emailing, "send_founder_promo_alert",
                        lambda code, count, max_uses, email: alertes.append((code, count, max_uses, email)) or True)
    _code(db)
    u1 = _user(db, "un@exemple-hector.fr")
    u2 = _user(db, "deux@exemple-hector.fr")
    r1 = billing.apply_promo(db, u1, "TOTORMERCI")
    r2 = billing.apply_promo(db, u2, "totor merci")   # variante tolérée
    assert r1["ok"] and r2["ok"]
    assert alertes == [("TOTORMERCI", 1, 15, "un@exemple-hector.fr"),
                       ("TOTORMERCI", 2, 15, "deux@exemple-hector.fr")]


def test_pas_d_alerte_sur_code_invalide(db, monkeypatch):
    alertes = []
    monkeypatch.setattr(emailing, "send_founder_promo_alert",
                        lambda *a: alertes.append(a) or True)
    u = _user(db)
    r = billing.apply_promo(db, u, "NIMPORTEQUOI")
    assert r["ok"] is False
    assert alertes == []


def test_echec_d_email_ne_bloque_pas_le_premium(db, monkeypatch):
    def boom(*a):
        raise RuntimeError("smtp en carafe")
    monkeypatch.setattr(emailing, "send_founder_promo_alert", boom)
    _code(db)
    u = _user(db)
    r = billing.apply_promo(db, u, "TOTORMERCI")
    assert r["ok"] is True and r["premium"] is True   # le cadeau passe quand même
    assert billing.is_premium(db, u) is True

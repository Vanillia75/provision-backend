# ════════════════════════════════════════════════════════════════════════
#  Quand le moteur du chat échoue, la personne ne paie RIEN (20/08/2026).
#  Le quota est consommé AVANT l'appel au modèle ; si l'appel casse, on
#  rembourse le message du jour, et la conversation (fil) si ce message
#  venait d'en ouvrir une. Jamais en dessous de zéro.
# ════════════════════════════════════════════════════════════════════════
import itertools
from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from models import User, AIUsage
from api import _rembourser_quota_chat


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


_numero = itertools.count(1)


def _personne(db):
    u = User(email=f"chat{next(_numero)}@ex.fr")
    db.add(u); db.commit(); db.refresh(u)
    return u


def _compteur(db, user, type_appel, valeur):
    db.add(AIUsage(user_id=user.id, jour=date.today(), type_appel=type_appel, count=valeur))
    db.commit()


def _lu(db, user, type_appel):
    u = (db.query(AIUsage)
           .filter(AIUsage.user_id == user.id, AIUsage.jour == date.today(),
                   AIUsage.type_appel == type_appel).first())
    return float(u.count) if u else 0.0


def test_le_message_est_rembourse(db):
    u = _personne(db)
    _compteur(db, u, "chat", 3)
    _rembourser_quota_chat(db, u.id, fil_aussi=False)
    assert _lu(db, u, "chat") == 2.0


def test_le_fil_est_rembourse_seulement_s_il_venait_d_ouvrir(db):
    u = _personne(db)
    _compteur(db, u, "chat", 1)
    _compteur(db, u, "chat_fil", 1)
    _rembourser_quota_chat(db, u.id, fil_aussi=True)
    assert _lu(db, u, "chat") == 0.0
    assert _lu(db, u, "chat_fil") == 0.0


def test_un_fil_deja_ouvert_n_est_pas_touche(db):
    u = _personne(db)
    _compteur(db, u, "chat", 2)
    _compteur(db, u, "chat_fil", 1)
    _rembourser_quota_chat(db, u.id, fil_aussi=False)
    assert _lu(db, u, "chat") == 1.0
    assert _lu(db, u, "chat_fil") == 1.0


def test_jamais_en_dessous_de_zero(db):
    u = _personne(db)
    _rembourser_quota_chat(db, u.id, fil_aussi=True)
    assert _lu(db, u, "chat") == 0.0
    assert _lu(db, u, "chat_fil") == 0.0

# ════════════════════════════════════════════════════════════════════════
#  Email de bienvenue J+1 (20/08/2026) : UN SEUL email, uniquement aux
#  comptes restés VIDES, inscrits il y a entre 20 h et 7 jours, jamais aux
#  comptes de test. La borne des 7 jours protège les vieux comptes le jour
#  où la fonction s'allume : personne ne reçoit un « bienvenue » des mois
#  après son inscription.
# ════════════════════════════════════════════════════════════════════════
import itertools
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from models import User, IntermittentActivity, AIUsage
from api import _bienvenue_candidats, _html_email_bienvenue


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


_numero = itertools.count(1)
MAINTENANT = datetime(2026, 8, 20, 12, 0, 0)


def _personne(db, *, il_y_a_heures, is_test=False, deja_servi=False):
    u = User(email=f"bienvenue{next(_numero)}@ex.fr", is_test=is_test,
             email_bienvenue_envoye=deja_servi,
             created_at=MAINTENANT - timedelta(hours=il_y_a_heures))
    db.add(u); db.commit(); db.refresh(u)
    return u


def test_un_compte_vide_de_la_veille_est_candidat(db):
    u = _personne(db, il_y_a_heures=24)
    assert [c.id for c in _bienvenue_candidats(db, MAINTENANT)] == [u.id]


def test_trop_recent_on_attend_encore(db):
    _personne(db, il_y_a_heures=5)
    assert _bienvenue_candidats(db, MAINTENANT) == []


def test_trop_ancien_on_ne_reveille_pas_les_vieux_comptes(db):
    _personne(db, il_y_a_heures=8 * 24)
    assert _bienvenue_candidats(db, MAINTENANT) == []


def test_un_compte_actif_n_est_pas_derange(db):
    u = _personne(db, il_y_a_heures=24)
    db.add(IntermittentActivity(user_id=u.id, date=MAINTENANT.date(),
                                type_activite="cachet_isole", nombre=1))
    db.commit()
    assert _bienvenue_candidats(db, MAINTENANT) == []


def test_meme_une_simple_conversation_compte_comme_de_l_activite(db):
    u = _personne(db, il_y_a_heures=24)
    db.add(AIUsage(user_id=u.id, jour=MAINTENANT.date(), type_appel="chat", count=1))
    db.commit()
    assert _bienvenue_candidats(db, MAINTENANT) == []


def test_jamais_deux_fois(db):
    _personne(db, il_y_a_heures=24, deja_servi=True)
    assert _bienvenue_candidats(db, MAINTENANT) == []


def test_jamais_les_comptes_de_test(db):
    _personne(db, il_y_a_heures=24, is_test=True)
    assert _bienvenue_candidats(db, MAINTENANT) == []


def test_le_texte_parle_le_bon_metier():
    inter = _html_email_bienvenue("intermittent")
    ae = _html_email_bienvenue("auto_entrepreneur")
    assert "AEM" in inter and "507" in inter
    assert "facture" in ae and "URSSAF" in ae
    for texte in (inter, ae):
        assert "le seul email de ce genre" in texte   # la promesse d'unicité est écrite
        assert "—" not in texte                        # jamais de tiret long

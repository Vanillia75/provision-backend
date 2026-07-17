# ════════════════════════════════════════════════════════════════════════
#  Tests « visite guidee deja vue » (/profile/walkthrough-vu).
#  Le bug d'origine : le marqueur vivait dans le navigateur (safeStorage), donc
#  une reinstallation de l'app ou un changement de telephone le perdait, et la
#  personne se retapait la visite. Ces tests verifient qu'il tient EN BASE.
# ════════════════════════════════════════════════════════════════════════
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from models import User, Profile
import api


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _user_avec_profil(db, walkthrough_vu=False):
    u = User(email="test@exemple-hector.fr")
    db.add(u)
    db.commit()
    db.refresh(u)
    db.add(Profile(user_id=u.id, onboarding_complete=True, walkthrough_vu=walkthrough_vu))
    db.commit()
    return u


def test_par_defaut_la_visite_n_a_pas_ete_vue(db):
    u = _user_avec_profil(db)
    p = db.query(Profile).filter(Profile.user_id == u.id).one()
    assert p.walkthrough_vu is False


def test_marquer_vu_tient_en_base(db):
    u = _user_avec_profil(db)

    api.marquer_walkthrough_vu(user=u, db=db)

    p = db.query(Profile).filter(Profile.user_id == u.id).one()
    assert p.walkthrough_vu is True


def test_reinstallation_le_marqueur_survit(db):
    """LE test qui compte : l'app est desinstallee (tout le local est perdu),
    reinstallee, on recharge le profil depuis le serveur -> il se souvient."""
    u = _user_avec_profil(db)
    api.marquer_walkthrough_vu(user=u, db=db)

    # Reinstallation : plus rien en local. On ne dispose QUE de ce que dit le serveur.
    db.expire_all()
    p = db.query(Profile).filter(Profile.user_id == u.id).one()

    assert p.walkthrough_vu is True  # la visite ne sera pas remontree


def test_marquer_deux_fois_ne_casse_rien(db):
    u = _user_avec_profil(db, walkthrough_vu=True)

    api.marquer_walkthrough_vu(user=u, db=db)

    p = db.query(Profile).filter(Profile.user_id == u.id).one()
    assert p.walkthrough_vu is True


def test_sans_profil_ne_plante_pas_et_ne_cree_rien(db):
    """Cas limite : quelqu'un qui n'a pas fini l'onboarding n'a pas de profil.
    On ne doit ni planter, ni fabriquer un profil vide."""
    u = User(email="sansprofil@exemple-hector.fr")
    db.add(u)
    db.commit()
    db.refresh(u)

    r = api.marquer_walkthrough_vu(user=u, db=db)

    assert r == {"ok": True}
    assert db.query(Profile).filter(Profile.user_id == u.id).first() is None

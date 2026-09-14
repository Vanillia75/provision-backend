# ════════════════════════════════════════════════════════════════════════
#  Diagnostic des arrêts enregistrés de travers (14/09/2026).
#
#  Cas réel : un congé maternité de 112 jours refusé sans message visible.
#  En remontant, le formulaire découpait tout arrêt « 1 par jour » en portant
#  sur chaque ligne le nombre TOTAL de jours. Un arrêt de 30 jours devenait 30
#  lignes à 30 jours : 4 500 h au lieu de 150 h dans le compteur des 507.
#
#  Ce diagnostic doit trouver EXACTEMENT ces cas-là, et jamais une saisie
#  légitime. C'est ce que ces tests verrouillent.
# ════════════════════════════════════════════════════════════════════════
from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import api
from database import Base
from models import IntermittentActivity, User


@pytest.fixture()
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _compte(db, email, is_test=False):
    u = User(email=email, is_test=is_test)
    db.add(u); db.commit()
    return u.id


def _suite(db, uid, jours, nombre, type_="arret_accident", depart=date(2026, 5, 1)):
    for i in range(jours):
        db.add(IntermittentActivity(user_id=uid, date=depart + timedelta(days=i),
                                    type_activite=type_, nombre=nombre))
    db.commit()


def test_trouve_l_empreinte_exacte_du_bug(db):
    """30 lignes consécutives portant chacune 30 jours : c'est le bug."""
    uid = _compte(db, "a@exemple-totor.fr")
    _suite(db, uid, jours=30, nombre=30)
    d = api._diagnostic_arrets(db)
    assert d["comptes_touches"] == 1
    assert d["series"] == 1
    serie = d["detail"][0]
    assert serie["jours"] == 30
    assert serie["heures_comptees"] == 30 * 30 * 5       # ce que le compteur affiche à tort
    assert serie["heures_justes"] == 30 * 5              # ce qu'il aurait dû afficher
    assert d["heures_en_trop"] == 30 * 30 * 5 - 30 * 5


def test_ignore_une_vraie_saisie_jour_par_jour(db):
    """Nombre = 1 sur chaque ligne : c'est une saisie légitime, pas le bug."""
    uid = _compte(db, "b@exemple-totor.fr")
    _suite(db, uid, jours=10, nombre=1)
    assert api._diagnostic_arrets(db)["series"] == 0


def test_ignore_un_arret_bien_enregistre_en_une_ligne(db):
    """La forme correcte depuis le correctif : une ligne, une date de fin."""
    uid = _compte(db, "c@exemple-totor.fr")
    db.add(IntermittentActivity(user_id=uid, date=date(2026, 3, 18), date_fin=date(2026, 7, 7),
                                type_activite="arret_maternite", nombre=112))
    db.commit()
    assert api._diagnostic_arrets(db)["series"] == 0


def test_ignore_une_suite_dont_la_longueur_ne_colle_pas(db):
    """5 lignes consécutives à 30 jours : étrange, mais PAS l'empreinte du bug.
    On ne devine pas, on ne signale que la certitude."""
    uid = _compte(db, "d@exemple-totor.fr")
    _suite(db, uid, jours=5, nombre=30)
    assert api._diagnostic_arrets(db)["series"] == 0


def test_ignore_les_comptes_de_test(db):
    uid = _compte(db, "moi@exemple-totor.fr", is_test=True)
    _suite(db, uid, jours=20, nombre=20)
    assert api._diagnostic_arrets(db)["series"] == 0


def test_arret_neutralise_compte_zero_heure(db):
    """Maladie ordinaire : 0 h dans le moteur, donc 0 h en trop même si la
    forme est fautive. On la signale sans gonfler l'addition."""
    uid = _compte(db, "e@exemple-totor.fr")
    _suite(db, uid, jours=4, nombre=4, type_="arret_maladie_ordinaire")
    d = api._diagnostic_arrets(db)
    assert d["series"] == 1
    assert d["heures_en_trop"] == 0

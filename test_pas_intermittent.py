# ════════════════════════════════════════════════════════════════════════
#  Tests « PAS prélevé » (Chemin A) — sqlite en mémoire.
#  Règle Loi X : on SOMME uniquement des montants RÉELS recopiés du bulletin.
#  JAMAIS de calcul brut × taux. Rien à l'écran si rien n'est saisi.
# ════════════════════════════════════════════════════════════════════════
from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from models import User, Profile, IntermittentActivity
import api


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _user(db):
    u = User(email="inter@exemple-hector.fr")
    db.add(u)
    db.commit()
    db.refresh(u)
    db.add(Profile(user_id=u.id, statut="intermittent"))
    db.commit()
    return u


def _req(d, pas=None):
    return api.IntermittentActiviteRequest(
        date=d, type_activite="cachet_isole", nombre=1,
        salaire_brut=800.0, pas_montant=pas,
    )


ANNEE = date.today().year


# ── Stockage : la donnée saisie est bien enregistrée ─────────────────────

def test_creation_enregistre_le_pas(db):
    u = _user(db)
    api.add_intermittent_activite(_req(date(ANNEE, 6, 1), pas=142.50), u, db)
    row = db.query(IntermittentActivity).filter_by(user_id=u.id).one()
    assert row.pas_montant == 142.50


def test_sans_pas_reste_none(db):
    u = _user(db)
    api.add_intermittent_activite(_req(date(ANNEE, 6, 1), pas=None), u, db)
    row = db.query(IntermittentActivity).filter_by(user_id=u.id).one()
    assert row.pas_montant is None


def test_liste_renvoie_le_pas(db):
    u = _user(db)
    api.add_intermittent_activite(_req(date(ANNEE, 6, 1), pas=90.0), u, db)
    liste = api.list_intermittent_activites(u, db)
    assert liste[0]["pas_montant"] == 90.0


def test_edition_met_a_jour_et_permet_d_effacer(db):
    u = _user(db)
    api.add_intermittent_activite(_req(date(ANNEE, 6, 1), pas=100.0), u, db)
    aid = db.query(IntermittentActivity).filter_by(user_id=u.id).one().id

    api.update_intermittent_activite(aid, _req(date(ANNEE, 6, 1), pas=125.0), u, db)
    assert db.query(IntermittentActivity).get(aid).pas_montant == 125.0

    # champ vidé par l'utilisateur → on efface bien
    api.update_intermittent_activite(aid, _req(date(ANNEE, 6, 1), pas=None), u, db)
    assert db.query(IntermittentActivity).get(aid).pas_montant is None


# ── Cockpit : SOMME de vraies données, année civile, rien si vide ────────

def test_cockpit_somme_l_annee_en_cours(db):
    u = _user(db)
    api.add_intermittent_activite(_req(date(ANNEE, 3, 1), pas=120.0), u, db)
    api.add_intermittent_activite(_req(date(ANNEE, 9, 1), pas=80.5), u, db)

    out = api.get_intermittent_cockpit(u, db)

    assert out["pas_preleve"] == {"annee": ANNEE, "montant": 200.5}


def test_cockpit_ignore_les_autres_annees(db):
    u = _user(db)
    # seul un PAS de l'an dernier -> rien à afficher cette année
    api.add_intermittent_activite(_req(date(ANNEE - 1, 6, 1), pas=300.0), u, db)

    out = api.get_intermittent_cockpit(u, db)

    assert out["pas_preleve"] is None


def test_cockpit_rien_si_aucun_pas_saisi(db):
    u = _user(db)
    api.add_intermittent_activite(_req(date(ANNEE, 6, 1), pas=None), u, db)

    out = api.get_intermittent_cockpit(u, db)

    assert out["pas_preleve"] is None

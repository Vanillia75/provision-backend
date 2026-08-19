# ════════════════════════════════════════════════════════════════════════
#  Tests du TARIF SOLIDAIRE (19/08/2026) : 4,99 €/mois pendant 12 mois,
#  sur l'honneur. La réserve de codes des stores se distribue UN par
#  personne, le même code est redonné pendant 11 mois (il est à usage
#  unique chez Apple/Google, le sien reste le sien), et passé ce délai on
#  en attribue un neuf : c'est la reconduction annuelle. Réserve vide =
#  None, jamais une exception : l'écran doit pouvoir dire « bientôt ».
# ════════════════════════════════════════════════════════════════════════
import itertools
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from models import User, SolidaireCode
from billing import obtenir_code_solidaire


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


_numero = itertools.count(1)


def _personne(db):
    u = User(email=f"solidaire{next(_numero)}@ex.fr")
    db.add(u); db.commit(); db.refresh(u)
    return u


def _reserve(db, plateforme, *codes):
    for c in codes:
        db.add(SolidaireCode(plateforme=plateforme, code=c))
    db.commit()


def test_un_code_par_personne_et_le_lien_qui_va_avec(db):
    _reserve(db, "apple", "AAA111")
    u = _personne(db)
    r = obtenir_code_solidaire(db, u, "apple")
    assert r["code"] == "AAA111"
    assert "apps.apple.com" in r["lien"] and "AAA111" in r["lien"]


def test_redemander_rend_le_meme_code_pas_un_deuxieme(db):
    """La reserve ne doit pas se vider parce que quelqu'un re-ouvre l'ecran."""
    _reserve(db, "apple", "AAA111", "BBB222")
    u = _personne(db)
    premier = obtenir_code_solidaire(db, u, "apple")
    second = obtenir_code_solidaire(db, u, "apple")
    assert premier["code"] == second["code"]
    restants = db.query(SolidaireCode).filter(SolidaireCode.attribue_a.is_(None)).count()
    assert restants == 1


def test_apres_onze_mois_un_nouveau_code_la_reconduction_annuelle(db):
    _reserve(db, "apple", "AAA111", "BBB222")
    u = _personne(db)
    premier = obtenir_code_solidaire(db, u, "apple")
    ancien = db.query(SolidaireCode).filter(SolidaireCode.code == premier["code"]).one()
    ancien.attribue_le = datetime.utcnow() - timedelta(days=360)
    db.commit()
    nouveau = obtenir_code_solidaire(db, u, "apple")
    assert nouveau["code"] != premier["code"]


def test_les_plateformes_ne_se_melangent_pas(db):
    _reserve(db, "google", "GGG333")
    u = _personne(db)
    assert obtenir_code_solidaire(db, u, "apple") is None
    r = obtenir_code_solidaire(db, u, "google")
    assert r["code"] == "GGG333" and "play.google.com" in r["lien"]


def test_reserve_vide_rend_none_jamais_une_exception(db):
    u = _personne(db)
    assert obtenir_code_solidaire(db, u, "apple") is None


def test_plateforme_inconnue_rend_none(db):
    u = _personne(db)
    assert obtenir_code_solidaire(db, u, "web") is None
    assert obtenir_code_solidaire(db, u, "n'importe quoi") is None


def test_deux_personnes_deux_codes_differents(db):
    _reserve(db, "apple", "AAA111", "BBB222")
    r1 = obtenir_code_solidaire(db, _personne(db), "apple")
    r2 = obtenir_code_solidaire(db, _personne(db), "apple")
    assert r1["code"] != r2["code"]

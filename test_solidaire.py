# ════════════════════════════════════════════════════════════════════════
#  LE TARIF SOLIDAIRE N'EXISTE PLUS (24/09/2026).
#
#  Rappel : du 19/08 au 24/09/2026, qui trouvait 9,99 €/mois trop cher
#  pouvait demander 4,99 €/mois pendant un an, sur l'honneur. Sur les
#  stores, ça passait par un code de réduction pioché dans une réserve.
#
#  Le 24/09, le prix public est tombé à 4,99 €/mois pour tout le monde :
#  le prix public EST devenu le prix solidaire, l'offre n'a plus d'objet.
#
#  Ces tests gravent les deux promesses qui restent :
#   1. on ne distribue plus AUCUN code (personne ne doit rien demander) ;
#   2. la réserve n'est pas consommée : les codes déjà remis restent
#      valables jusqu'au bout, et les autres dorment sans être brûlés.
# ════════════════════════════════════════════════════════════════════════
import itertools

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


@pytest.mark.parametrize("plateforme", ["apple", "google", "web", "n'importe quoi"])
def test_plus_aucun_code_n_est_distribue(db, plateforme):
    """Même avec une réserve pleine : le prix public est déjà le prix solidaire."""
    _reserve(db, "apple", "AAA111")
    _reserve(db, "google", "GGG333")
    assert obtenir_code_solidaire(db, _personne(db), plateforme) is None


def test_la_reserve_n_est_jamais_entamee(db):
    """Les codes déjà remis restent valables : on ne touche pas à la table."""
    _reserve(db, "apple", "AAA111", "BBB222")
    obtenir_code_solidaire(db, _personne(db), "apple")
    libres = db.query(SolidaireCode).filter(SolidaireCode.attribue_a.is_(None)).count()
    assert libres == 2

# -*- coding: utf-8 -*-
# ════════════════════════════════════════════════════════════════════════
#  « ET SI J'AJOUTE DES CACHETS ? » — LA SIMULATION DU MOIS.
#
#  Demande de Lucile, mot pour mot : « je suis pas tout à fait sûre de Pôle
#  emploi puisque j'ai encore trois cachets à faire, mais c'est possible qu'on
#  les annule. Donc pour l'instant, moi je les ai pas notés. Ce que j'aimerais,
#  c'est savoir : si je rajoute encore trois cachets, combien je vais toucher de
#  Pôle emploi, combien je vais toucher en salaire ? »
#
#  Deux exigences derrière cette phrase, et les tests vérifient les deux :
#   · elle ne doit RIEN saisir : des cachets incertains ne s'enregistrent pas ;
#   · elle doit voir les DEUX effets, l'allocation qui baisse (décalage) et le
#     salaire qui monte.
# ════════════════════════════════════════════════════════════════════════
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import api
from auth import hash_password
from database import Base, get_db
from models import IntermittentActivity, Profile, Subscription, User


@pytest.fixture()
def client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)

    def _db():
        s = TestSession()
        try:
            yield s
        finally:
            s.close()

    api.app.dependency_overrides[get_db] = _db
    yield TestClient(api.app), TestSession
    api.app.dependency_overrides.clear()


def _compte(TestSession, ratio=None, cachets_deja=0, brut_deja=0.0):
    s = TestSession()
    u = User(email="lucile@exemple-totor.fr", password_hash=hash_password("Secret-solide-1"),
             email_verified=True)
    s.add(u); s.commit()
    s.add(Profile(user_id=u.id, statut="intermittent", onboarding_complete=True,
                  montant_journalier=61.0, annexe_allocation="annexe10",
                  ratio_net_brut=ratio, ratio_net_brut_maj_le=date(2026, 6, 30) if ratio else None))
    s.add(Subscription(user_id=u.id, plan="premium", status="comp", source="comp"))
    if cachets_deja:
        s.add(IntermittentActivity(user_id=u.id, date=date.today().replace(day=5),
                                   type_activite="cachet_isole", nombre=cachets_deja,
                                   salaire_brut=brut_deja))
    s.commit()
    uid = u.id          # lu AVANT la fermeture : après, l'objet est détaché
    s.close()
    return uid


def _jeton(c):
    return {"Authorization": "Bearer " + c.post("/auth/login", json={
        "email": "lucile@exemple-totor.fr", "password": "Secret-solide-1"}).json()["token"]}


def test_sans_simulation_rien_ne_change(client):
    c, TS = client
    _compte(TS)
    d = c.get("/intermittent/estimation-mois", headers=_jeton(c)).json()
    assert d["simulation"] is None


def test_ajouter_des_cachets_fait_BAISSER_l_allocation(client):
    """Le décalage : plus on travaille, moins France Travail verse ce mois-là.
    C'est contre-intuitif, et c'est précisément ce qu'elle veut voir avant de
    dire oui à des dates."""
    c, TS = client
    _compte(TS)
    h = _jeton(c)
    base = c.get("/intermittent/estimation-mois", headers=h).json()
    sim = c.get("/intermittent/estimation-mois?cachets_sup=3&brut_cachet=450", headers=h).json()
    assert sim["simulation"]["net_estime"] < base["net_estime"]
    assert sim["simulation"]["ecart_allocation"] < 0


def test_elle_voit_AUSSI_le_salaire_que_ca_rapporte(client):
    c, TS = client
    _compte(TS)
    d = c.get("/intermittent/estimation-mois?cachets_sup=3&brut_cachet=450", headers=_jeton(c)).json()
    assert d["simulation"]["salaire_sup_brut"] == 1350.0


def test_avec_un_bulletin_connu_le_salaire_est_donne_en_NET(client):
    c, TS = client
    _compte(TS, ratio=0.78)
    d = c.get("/intermittent/estimation-mois?cachets_sup=3&brut_cachet=450", headers=_jeton(c)).json()
    assert d["simulation"]["salaire_sup_net"] == pytest.approx(1350.0 * 0.78, abs=0.01)


def test_sans_bulletin_on_n_invente_pas_de_net(client):
    c, TS = client
    _compte(TS)
    d = c.get("/intermittent/estimation-mois?cachets_sup=3&brut_cachet=450", headers=_jeton(c)).json()
    assert d["simulation"]["salaire_sup_net"] is None


def test_la_simulation_n_enregistre_RIEN(client):
    """Elle n'est pas sûre de ces cachets : ils ne doivent pas atterrir dans son
    dossier ni fausser son compteur d'heures."""
    c, TS = client
    uid = _compte(TS)
    h = _jeton(c)
    c.get("/intermittent/estimation-mois?cachets_sup=3&brut_cachet=450", headers=h)
    s = TS()
    n = s.query(IntermittentActivity).filter(IntermittentActivity.user_id == uid).count()
    s.close()
    assert n == 0, "des cachets simulés ont été enregistrés"


def test_une_demande_absurde_ne_casse_rien(client):
    c, TS = client
    _compte(TS)
    h = _jeton(c)
    for q in ("cachets_sup=-5", "cachets_sup=0", "cachets_sup=999&brut_cachet=1",
              "cachets_sup=3&brut_cachet=-10"):
        r = c.get(f"/intermittent/estimation-mois?{q}", headers=h)
        assert r.status_code == 200, q
    # 999 cachets : borné au plafond mensuel, jamais 999.
    d = c.get("/intermittent/estimation-mois?cachets_sup=999&brut_cachet=1", headers=h).json()
    assert d["simulation"]["cachets_sup"] <= 28

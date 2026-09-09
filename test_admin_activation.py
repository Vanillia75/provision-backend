# ════════════════════════════════════════════════════════════════════════
#  /admin/activation — les inscrits et l'entonnoir, par HTTPS.
#
#  Née le 09/09/2026 d'une panne : Windows (Smart App Control) a bloqué le
#  binaire Railway, non signé, qui était le SEUL chemin vers la base, la
#  vérification quotidienne comprise. Cette route rend les mêmes chiffres
#  sans outil local.
#
#  Ce qu'on garde sous surveillance ici : le VERROU (sans clé, la page
#  n'existe pas) et la DISCRÉTION (jamais une adresse entière).
# ════════════════════════════════════════════════════════════════════════
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import api
from database import Base, get_db
from models import AIUsage, IntermittentActivity, Profile, User

CLE = "cle-de-test-admin"


@pytest.fixture()
def ctx(monkeypatch):
    monkeypatch.setenv("ADMIN_STATS_KEY", CLE)
    monkeypatch.setattr(api.os.environ, "get", api.os.environ.get)
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
    s = TestSession()
    maintenant = datetime.utcnow()

    # 1) quelqu'un qui va jusqu'au bout
    actif = User(email="actif@exemple-totor.fr", email_verified=True, created_at=maintenant)
    s.add(actif); s.commit()
    s.add(Profile(user_id=actif.id, statut="intermittent", onboarding_complete=True, walkthrough_vu=True))
    s.add(IntermittentActivity(user_id=actif.id, date=maintenant.date(), type_activite="heures", nombre=8))
    s.add(AIUsage(user_id=actif.id, jour=maintenant.date(), type_appel="aem_scan", count=1))
    s.add(AIUsage(user_id=actif.id, jour=(maintenant - timedelta(days=1)).date(), type_appel="aem_scan", count=1))

    # 2) quelqu'un qui s'inscrit et repart : LA marche qu'on surveille
    vide = User(email="perdu@exemple-totor.fr", email_verified=True, created_at=maintenant)
    s.add(vide); s.commit()
    s.add(Profile(user_id=vide.id, statut="auto_entrepreneur", onboarding_complete=True))

    # 3) un compte de test, qui ne doit compter nulle part
    t = User(email="moi@exemple-totor.fr", is_test=True, created_at=maintenant)
    s.add(t); s.commit()
    s.close()

    with TestClient(api.app) as c:
        yield c
    api.app.dependency_overrides.clear()


def test_sans_cle_la_page_n_existe_pas(ctx):
    """404 et pas 401 : on ne révèle même pas que la page existe."""
    assert ctx.get("/admin/activation").status_code == 404
    assert ctx.get("/admin/activation?key=fausse").status_code == 404


def test_les_comptes_de_test_ne_comptent_pas(ctx):
    d = ctx.get(f"/admin/activation?key={CLE}&format=json").json()
    assert d["total"] == 2          # le compte is_test est exclu
    assert d["aujourdhui"] == 2


def test_l_entonnoir_montre_la_marche(ctx):
    d = ctx.get(f"/admin/activation?key={CLE}&format=json").json()
    etapes = dict((libelle, n) for libelle, n in d["entonnoir"])
    assert etapes["Compte créé"] == 2
    assert etapes["A fini l'inscription"] == 2
    assert etapes["A saisi SES données"] == 1      # un seul est allé au bout
    assert etapes["Est revenu un 2e jour"] == 1
    # Celui qui s'est inscrit puis n'a rien fait est bien compté comme bloqué.
    assert d["bloques_apres_inscription"] == 1
    assert d["dont_ont_essaye_un_scan"] == 0


def test_aucune_adresse_entiere_nulle_part(ctx):
    """La règle de discrétion s'applique AUSSI derrière la clé admin."""
    for url in (f"/admin/activation?key={CLE}", f"/admin/activation?key={CLE}&format=json"):
        corps = ctx.get(url).text
        assert "actif@exemple-totor.fr" not in corps
        assert "perdu@exemple-totor.fr" not in corps
        assert "act…@exemple-totor.fr" in corps


def test_la_page_html_s_affiche(ctx):
    r = ctx.get(f"/admin/activation?key={CLE}")
    assert r.status_code == 200
    assert "Inscrits et activation" in r.text
    assert "L'entonnoir" in r.text

# ════════════════════════════════════════════════════════════════════════
#  UNE FACTURE ÉMISE NE BOUGE PLUS (09/09/2026).
#
#  Trouvé par la vérification quotidienne : le bouton Modifier et la croix
#  étaient offerts sur TOUTES les factures, et le serveur les acceptait sans
#  regarder le statut. Un commentaire du code affirmait pourtant « une fois
#  émise, elle n'est plus modifiable ici » : le garde-fou qui suivait ne
#  protégeait en réalité que le régime de TVA figé, pas le document.
#
#  Ce que dit la loi (service-public.fr F23208) : une facture émise ne se
#  modifie plus, une facture réglée se corrige par une facture d'AVOIR, et
#  les factures se conservent 10 ans. Supprimer laisse en plus un TROU dans
#  la numérotation, ce qu'un contrôle cherche en premier.
#
#  Ce qui reste libre, et doit le rester : le BROUILLON (jamais émis), et la
#  vie de la facture une fois émise (statut, date de paiement). Sans ça on ne
#  pourrait plus marquer une facture comme payée, ce qui casserait le CA.
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
from models import ClientInvoice, Profile, User

EMAIL, MDP = "ae@exemple-totor.fr", "Secret-solide-1"


@pytest.fixture()
def ctx():
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
    u = User(email=EMAIL, password_hash=hash_password(MDP), email_verified=True)
    s.add(u)
    s.commit()
    s.add(Profile(user_id=u.id, statut="auto_entrepreneur", onboarding_complete=True))
    s.commit()
    s.close()

    with TestClient(api.app) as c:
        jeton = c.post("/auth/login", json={"email": EMAIL, "password": MDP}).json()["token"]
        c.headers["Authorization"] = f"Bearer {jeton}"
        yield c, TestSession
    api.app.dependency_overrides.clear()


def _creer_facture(c):
    r = c.post("/invoices", json={
        "client_nom": "Client X",
        "client_type": "particulier",
        "date_emission": str(date.today()),
        "lignes": [{"description": "Prestation", "quantite": 1, "prix_unitaire": 500.0}],
    })
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _emettre(c, fid, statut="envoyee"):
    r = c.patch(f"/invoices/{fid}/status", json={"statut": statut})
    assert r.status_code == 200, r.text
    return r.json()


# ── Le brouillon reste libre : c'est la contrepartie du verrou ───────────

def test_un_brouillon_se_modifie_encore(ctx):
    c, _ = ctx
    fid = _creer_facture(c)
    r = c.put(f"/invoices/{fid}", json={"client_nom": "Client corrigé"})
    assert r.status_code == 200
    assert r.json()["client_nom"] == "Client corrigé"


def test_un_brouillon_se_supprime_encore(ctx):
    c, _ = ctx
    fid = _creer_facture(c)
    assert c.delete(f"/invoices/{fid}").status_code == 200


# ── Une facture émise est gelée ──────────────────────────────────────────

@pytest.mark.parametrize("statut", ["envoyee", "payee", "impayee"])
def test_une_facture_emise_ne_se_modifie_plus(ctx, statut):
    c, _ = ctx
    fid = _creer_facture(c)
    _emettre(c, fid, statut)
    r = c.put(f"/invoices/{fid}", json={"client_nom": "Quelqu'un d'autre"})
    assert r.status_code == 409
    assert "avoir" in r.json()["detail"].lower()


@pytest.mark.parametrize("statut", ["envoyee", "payee", "impayee"])
def test_une_facture_emise_ne_se_supprime_plus(ctx, statut):
    c, _ = ctx
    fid = _creer_facture(c)
    _emettre(c, fid, statut)
    assert c.delete(f"/invoices/{fid}").status_code == 409
    # Et elle est TOUJOURS là : la conservation 10 ans n'est pas une opinion.
    assert any(f["id"] == fid for f in c.get("/invoices").json())


def test_le_montant_d_une_facture_payee_ne_bouge_pas(ctx):
    """Le cas qui coûte cher : réécrire les lignes d'une facture déjà encaissée."""
    c, _ = ctx
    fid = _creer_facture(c)
    _emettre(c, fid, "payee")
    r = c.put(f"/invoices/{fid}", json={
        "lignes": [{"description": "Prestation", "quantite": 1, "prix_unitaire": 5000.0}]})
    assert r.status_code == 409
    assert c.get("/invoices").json()[0]["montant"] == 500.0


# ── La porte à sens unique : le vrai piège ──────────────────────────────

def test_on_ne_repasse_pas_une_facture_emise_en_brouillon(ctx):
    """Sans ce garde-fou, tout le reste ne vaut rien : il suffirait de repasser
    la facture en brouillon pour la réécrire ou la supprimer ensuite."""
    c, _ = ctx
    fid = _creer_facture(c)
    _emettre(c, fid, "payee")
    r = c.patch(f"/invoices/{fid}/status", json={"statut": "brouillon"})
    assert r.status_code == 409
    assert c.get("/invoices").json()[0]["statut"] == "payee"


# ── Ce qui doit continuer de marcher ────────────────────────────────────

def test_on_peut_toujours_marquer_une_facture_comme_payee(ctx):
    """Si ce test casse, le chiffre d'affaires encaissé ne se met plus à jour."""
    c, _ = ctx
    fid = _creer_facture(c)
    _emettre(c, fid, "envoyee")
    r = c.patch(f"/invoices/{fid}/status", json={"statut": "payee"})
    assert r.status_code == 200
    assert r.json()["statut"] == "payee"
    assert r.json()["date_paiement"] is not None


def test_une_impayee_peut_redevenir_payee(ctx):
    """La vie d'une facture continue après l'émission : seul le retour au
    brouillon est interdit, pas les allers-retours entre statuts émis."""
    c, _ = ctx
    fid = _creer_facture(c)
    _emettre(c, fid, "impayee")
    assert c.patch(f"/invoices/{fid}/status", json={"statut": "payee"}).status_code == 200


def test_le_message_explique_quoi_faire(ctx):
    """On ne claque pas la porte : qui veut corriger doit savoir par où passer."""
    c, _ = ctx
    fid = _creer_facture(c)
    _emettre(c, fid, "payee")
    detail = c.delete(f"/invoices/{fid}").json()["detail"]
    assert "avoir" in detail.lower()
    assert "bonjour@montotor.fr" in detail
    assert "10 ans" in detail

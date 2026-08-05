# ════════════════════════════════════════════════════════════════════════
#  Tests de la double vérification (MFA/TOTP).
#  D'abord le moteur TOTP seul (RFC 6238), puis le parcours complet par
#  l'API : activation, connexion en deux temps, code de secours à usage
#  unique, désactivation, et les garde-fous (comptes Google, jeton-palier
#  qui ne doit jamais servir de session).
# ════════════════════════════════════════════════════════════════════════
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import api
import mfa
from auth import hash_password, create_purpose_token
from database import Base, get_db
from models import User, Profile


# ── Le moteur TOTP seul ──────────────────────────────────────────────────

def test_totp_code_stable_dans_sa_fenetre():
    secret = mfa.generer_secret()
    t = 1_754_500_000.0
    code = mfa._code_totp(secret, int(t // 30))
    assert mfa.verifier_code(secret, code, maintenant=t)
    # La même fenêtre à 2 secondes près : toujours bon.
    assert mfa.verifier_code(secret, code, maintenant=t + 2)


def test_totp_tolere_une_fenetre_de_derive():
    secret = mfa.generer_secret()
    t = 1_754_500_000.0
    code_avant = mfa._code_totp(secret, int(t // 30) - 1)
    code_apres = mfa._code_totp(secret, int(t // 30) + 1)
    assert mfa.verifier_code(secret, code_avant, maintenant=t)
    assert mfa.verifier_code(secret, code_apres, maintenant=t)


def test_totp_rejette_les_mauvais_codes():
    secret = mfa.generer_secret()
    t = 1_754_500_000.0
    bon = mfa._code_totp(secret, int(t // 30))
    faux = str((int(bon) + 1) % 1_000_000).zfill(6)
    assert not mfa.verifier_code(secret, faux, maintenant=t)
    assert not mfa.verifier_code(secret, "", maintenant=t)
    assert not mfa.verifier_code(secret, "abc123", maintenant=t)
    # Une fenêtre trop vieille (2 périodes) : refusée.
    vieux = mfa._code_totp(secret, int(t // 30) - 2)
    assert not mfa.verifier_code(secret, vieux, maintenant=t)


def test_codes_secours_a_usage_unique():
    codes = mfa.generer_codes_secours()
    assert len(codes) == 8 and all(len(c) == 9 and "-" in c for c in codes)
    haches = mfa.hacher_codes_secours(codes)
    restants = mfa.consommer_code_secours(codes[0], haches)
    assert restants is not None and len(restants) == 7
    # Le même code une seconde fois : refusé (il a été consommé).
    assert mfa.consommer_code_secours(codes[0], restants) is None
    # Tolérance de saisie : minuscules et tiret manquant acceptés.
    assert mfa.consommer_code_secours(codes[1].lower().replace("-", ""), restants) is not None


# ── Le parcours complet par l'API ───────────────────────────────────────

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


def _creer_compte(TestSession, email="mfa@exemple-totor.fr", mdp="Secret-solide-1"):
    s = TestSession()
    u = User(email=email, password_hash=hash_password(mdp))
    s.add(u)
    s.commit()
    s.add(Profile(user_id=u.id, statut="intermittent"))
    s.commit()
    uid = u.id
    s.close()
    return uid, email, mdp


def _code_courant(TestSession, uid):
    s = TestSession()
    secret = s.query(User).get(uid).mfa_secret
    s.close()
    import time
    return mfa._code_totp(secret, int(time.time() // 30))


def test_parcours_complet_activation_connexion_secours(client):
    c, TestSession = client
    uid, email, mdp = _creer_compte(TestSession)

    # Connexion normale : un seul temps tant que le MFA est inactif.
    r = c.post("/auth/login", json={"email": email, "password": mdp})
    assert r.status_code == 200 and r.json()["token"] and not r.json()["mfa_requise"]
    jeton = r.json()["token"]
    entetes = {"Authorization": f"Bearer {jeton}"}

    # Mise en route : mauvais mot de passe refusé, bon accepté.
    assert c.post("/auth/mfa/setup", json={"password": "faux"}, headers=entetes).status_code == 401
    r = c.post("/auth/mfa/setup", json={"password": mdp}, headers=entetes)
    assert r.status_code == 200
    assert r.json()["uri"].startswith("otpauth://totp/TOTOR")
    assert r.json()["qr"].startswith("data:image/svg+xml;base64,")

    # Activation : mauvais code refusé, bon code accepté, codes de secours remis.
    assert c.post("/auth/mfa/activate", json={"code": "000000"}, headers=entetes).status_code == 401
    r = c.post("/auth/mfa/activate", json={"code": _code_courant(TestSession, uid)}, headers=entetes)
    assert r.status_code == 200
    secours = r.json()["codes_secours"]
    assert len(secours) == 8

    # Le profil expose l'état.
    p = c.get("/profile", headers=entetes).json()
    assert p["mfa_enabled"] is True and p["mfa_disponible"] is True

    # Connexion en deux temps : le mot de passe seul ne suffit plus.
    r = c.post("/auth/login", json={"email": email, "password": mdp})
    assert r.status_code == 200
    corps = r.json()
    assert corps["mfa_requise"] is True and corps["token"] is None and corps["mfa_token"]

    # ⚠️ Le jeton-palier ne doit JAMAIS ouvrir l'app directement.
    assert c.get("/profile", headers={"Authorization": f"Bearer {corps['mfa_token']}"}).status_code == 401

    # Mauvais code : refusé. Bon code : session ouverte.
    assert c.post("/auth/mfa/verify", json={"mfa_token": corps["mfa_token"], "code": "123456"}).status_code == 401
    r = c.post("/auth/mfa/verify", json={"mfa_token": corps["mfa_token"], "code": _code_courant(TestSession, uid)})
    assert r.status_code == 200 and r.json()["token"]

    # Code de secours : il ouvre la session, puis devient inutilisable.
    r = c.post("/auth/login", json={"email": email, "password": mdp})
    palier = r.json()["mfa_token"]
    r = c.post("/auth/mfa/verify", json={"mfa_token": palier, "code": secours[0]})
    assert r.status_code == 200 and r.json()["token"]
    r = c.post("/auth/login", json={"email": email, "password": mdp})
    r = c.post("/auth/mfa/verify", json={"mfa_token": r.json()["mfa_token"], "code": secours[0]})
    assert r.status_code == 401

    # Désactivation : mot de passe + code exigés, puis la connexion redevient simple.
    jeton2 = None
    r = c.post("/auth/login", json={"email": email, "password": mdp})
    r = c.post("/auth/mfa/verify", json={"mfa_token": r.json()["mfa_token"], "code": _code_courant(TestSession, uid)})
    jeton2 = r.json()["token"]
    entetes2 = {"Authorization": f"Bearer {jeton2}"}
    assert c.post("/auth/mfa/disable", json={"password": mdp, "code": "999999"}, headers=entetes2).status_code == 401
    r = c.post("/auth/mfa/disable", json={"password": mdp, "code": _code_courant(TestSession, uid)}, headers=entetes2)
    assert r.status_code == 200
    r = c.post("/auth/login", json={"email": email, "password": mdp})
    assert r.json()["token"] and not r.json()["mfa_requise"]


def test_compte_google_ne_peut_pas_activer(client):
    c, TestSession = client
    s = TestSession()
    u = User(email="google@exemple-totor.fr", google_id="g-123", password_hash=None)
    s.add(u)
    s.commit()
    s.add(Profile(user_id=u.id, statut="auto_entrepreneur"))
    s.commit()
    uid = u.id
    s.close()
    from auth import create_token
    entetes = {"Authorization": f"Bearer {create_token(uid)}"}
    r = c.post("/auth/mfa/setup", json={"password": "peu importe"}, headers=entetes)
    assert r.status_code == 400
    p = c.get("/profile", headers=entetes).json()
    assert p["mfa_disponible"] is False


def test_jeton_palier_expire_ou_detourne(client):
    c, TestSession = client
    uid, email, mdp = _creer_compte(TestSession, email="palier@exemple-totor.fr")
    # Un jeton-palier forgé pour un autre usage : refusé par /auth/mfa/verify.
    mauvais = create_purpose_token(uid, "reset_password", expire_minutes=10)
    r = c.post("/auth/mfa/verify", json={"mfa_token": mauvais, "code": "123456"})
    assert r.status_code == 400

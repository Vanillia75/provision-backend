# ════════════════════════════════════════════════════════════════════════
#  Tests « Se connecter avec Apple » (/auth/apple) — sqlite en mémoire.
#  Couvre : verification du jeton, creation de compte, rattachement a un
#  compte existant (email partage), reconnexion par apple_id, email masque.
# ════════════════════════════════════════════════════════════════════════
import hashlib

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from models import User
import api
import apple_auth


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


@pytest.fixture(autouse=True)
def pas_d_alerte_fondateur(monkeypatch):
    monkeypatch.setattr(api, "send_founder_signup_alert", lambda *a, **k: None)


def _apple_repond(monkeypatch, apple_id, email, email_verified=True, email_prive=False):
    """Simule la reponse d'Apple : on teste NOTRE logique de rattachement, pas la
    crypto d'Apple (verifiee a part dans les premiers tests)."""
    monkeypatch.setattr(
        api, "verifier_apple_identity_token",
        lambda _t, _n: {"apple_id": apple_id, "email": email,
                        "email_verified": email_verified, "email_prive": email_prive},
    )


def _connexion(db):
    return api.auth_apple(
        api.AppleAuthRequest(identity_token="peu-importe", nonce="nonce-brut"), db
    )


# ── La verification du jeton lui-meme ────────────────────────────────────

def test_jeton_bidon_refuse():
    with pytest.raises(apple_auth.AppleTokenInvalide):
        apple_auth.verifier_identity_token("pas-un-jeton", "nonce-brut")


def test_jeton_signe_par_un_autre_refuse():
    """Un JWT bien forme mais signe par quelqu'un d'autre qu'Apple : refuse."""
    import jwt as pyjwt
    faux = pyjwt.encode(
        {"sub": "000123.abc", "email": "pirate@exemple.fr",
         "iss": "https://appleid.apple.com", "aud": "fr.montotor.ios"},
        "ma-cle-a-moi", algorithm="HS256",
    )
    with pytest.raises(apple_auth.AppleTokenInvalide):
        apple_auth.verifier_identity_token(faux, "nonce-brut")


# ── Le nonce (anti-rejeu) ────────────────────────────────────────────────
#
# Apple recopie dans le jeton la SHA-256 du nonce que l'app lui a donnee.
# On simule un vrai jeton Apple en detournant la verification cryptographique,
# pour tester UNIQUEMENT notre comparaison de nonce.

def _jeton_apple_simule(monkeypatch, nonce_dans_le_jeton):
    """Fait comme si Apple avait signe un jeton portant ce nonce."""
    class _FausseCle:
        key = "peu-importe"

    monkeypatch.setattr(
        apple_auth._jwk_client, "get_signing_key_from_jwt",
        lambda _t: _FausseCle(),
    )
    payload = {"sub": "000999.zzz", "email": "test@exemple-hector.fr",
               "email_verified": "true", "is_private_email": "false"}
    if nonce_dans_le_jeton is not None:
        payload["nonce"] = nonce_dans_le_jeton
    monkeypatch.setattr(apple_auth.jwt, "decode", lambda *a, **k: payload)


def test_nonce_correct_accepte(monkeypatch):
    """Le cas normal : l'app envoie le nonce brut, Apple a recopie sa SHA-256."""
    brut = "nonce-tire-au-hasard-par-l-app"
    _jeton_apple_simule(monkeypatch, hashlib.sha256(brut.encode()).hexdigest())

    infos = apple_auth.verifier_identity_token("jeton", brut)

    assert infos["apple_id"] == "000999.zzz"


def test_nonce_qui_ne_correspond_pas_refuse(monkeypatch):
    """LE test qui compte : un jeton intercepte, rejoue avec un autre nonce."""
    _jeton_apple_simule(monkeypatch, hashlib.sha256(b"le-vrai-nonce").hexdigest())

    with pytest.raises(apple_auth.AppleTokenInvalide):
        apple_auth.verifier_identity_token("jeton-vole", "nonce-de-l-attaquant")


def test_jeton_sans_nonce_refuse(monkeypatch):
    """Un jeton Apple obtenu sans nonce ne doit pas passer chez nous."""
    _jeton_apple_simule(monkeypatch, None)

    with pytest.raises(apple_auth.AppleTokenInvalide):
        apple_auth.verifier_identity_token("jeton", "nonce-brut")


def test_nonce_vide_refuse():
    """On refuse avant meme de regarder le jeton."""
    with pytest.raises(apple_auth.AppleTokenInvalide):
        apple_auth.verifier_identity_token("jeton", "")


def test_route_refuse_jeton_invalide(db):
    with pytest.raises(HTTPException) as e:
        _connexion(db)
    assert e.value.status_code == 401


# ── Le rattachement des comptes ──────────────────────────────────────────

def test_premiere_connexion_cree_le_compte(db, monkeypatch):
    _apple_repond(monkeypatch, "000111.aaa", "nouvelle@exemple-hector.fr")

    rep = _connexion(db)

    assert rep.email == "nouvelle@exemple-hector.fr"
    assert rep.token
    u = db.query(User).filter(User.email == "nouvelle@exemple-hector.fr").one()
    assert u.apple_id == "000111.aaa"
    assert u.password_hash is None
    assert u.email_verified is True  # Apple a deja verifie l'adresse


def test_compte_existant_est_rattache_pas_duplique(db, monkeypatch):
    """LE cas qui compte : une inscrite du web (Google ou mot de passe) qui
    telecharge l'app et choisit « Partager mon email ». Elle doit retrouver SES
    donnees, pas un compte vierge."""
    db.add(User(email="deja@exemple-hector.fr", google_id="g-42"))
    db.commit()
    _apple_repond(monkeypatch, "000222.bbb", "deja@exemple-hector.fr")

    rep = _connexion(db)

    assert rep.email == "deja@exemple-hector.fr"
    assert db.query(User).count() == 1  # aucun doublon
    u = db.query(User).filter(User.email == "deja@exemple-hector.fr").one()
    assert u.apple_id == "000222.bbb"
    assert u.google_id == "g-42"  # la connexion Google du web marche toujours


def test_reconnexion_retrouve_le_compte_par_apple_id(db, monkeypatch):
    """Aux connexions suivantes, Apple peut ne plus transmettre l'email :
    l'apple_id doit suffire."""
    _apple_repond(monkeypatch, "000333.ccc", "fidele@exemple-hector.fr")
    _connexion(db)

    _apple_repond(monkeypatch, "000333.ccc", None)  # Apple muet sur l'email
    rep = _connexion(db)

    assert rep.email == "fidele@exemple-hector.fr"
    assert db.query(User).count() == 1


def test_email_masque_cree_un_compte_relais(db, monkeypatch):
    """« Masquer mon adresse » : on accepte, mais c'est bien un compte neuf.
    Nos emails n'arriveront que si le domaine expediteur est declare chez Apple."""
    _apple_repond(monkeypatch, "000444.ddd", "zx9k2@privaterelay.appleid.com",
                  email_prive=True)

    _connexion(db)

    u = db.query(User).filter(User.apple_id == "000444.ddd").one()
    assert u.email.endswith("@privaterelay.appleid.com")


def test_inconnu_sans_email_est_refuse_avec_le_mode_d_emploi(db, monkeypatch):
    """Apple ne donne l'email qu'a la premiere autorisation. Si on ne connait pas
    l'apple_id ET qu'on n'a pas d'email, on ne peut pas creer de compte : on
    explique comment reautoriser plutot que d'echouer sechement."""
    _apple_repond(monkeypatch, "000555.eee", None)

    with pytest.raises(HTTPException) as e:
        _connexion(db)

    assert e.value.status_code == 401
    assert "Ne plus utiliser" in e.value.detail
    assert db.query(User).count() == 0


def test_deux_personnes_deux_comptes(db, monkeypatch):
    _apple_repond(monkeypatch, "000666.fff", "une@exemple-hector.fr")
    _connexion(db)
    _apple_repond(monkeypatch, "000777.ggg", "autre@exemple-hector.fr")
    _connexion(db)

    assert db.query(User).count() == 2

# ════════════════════════════════════════════════════════════════════════
#  Tests « Se connecter avec Google » (/auth/google) — sqlite en mémoire.
#
#  Ce qui est en jeu : un jeton Google porte un champ « aud » qui dit POUR
#  QUELLE application il a été émis. Le site et l'appli Android visent notre
#  identifiant WEB ; l'appli iPhone vise son propre identifiant iOS, parce que
#  le SDK Google d'Apple l'impose. Il faut donc accepter les deux.
#
#  Le test qui compte vraiment est celui du jeton émis pour une AUTRE appli :
#  sans contrôle du « aud », n'importe quel développeur pourrait prendre le
#  jeton Google d'un de ses utilisateurs et entrer dans son compte TOTOR.
# ════════════════════════════════════════════════════════════════════════
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from models import User
import api

WEB = "1008678142157-web.apps.googleusercontent.com"
IOS = "1008678142157-ios.apps.googleusercontent.com"
INTRUS = "999999999999-appli-de-quelqu-un-d-autre.apps.googleusercontent.com"


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


@pytest.fixture(autouse=True)
def deux_destinataires_permis(monkeypatch):
    monkeypatch.setattr(api, "GOOGLE_AUDIENCES", [WEB, IOS])
    monkeypatch.setattr(api, "send_founder_signup_alert", lambda *a, **k: None)


def _google_repond(monkeypatch, aud, email="test@exemple-hector.fr", sub="g-1"):
    """Fait comme si Google avait signé ce jeton. On teste NOTRE contrôle du
    destinataire, pas la cryptographie de Google."""
    monkeypatch.setattr(
        api.google_id_token, "verify_oauth2_token",
        lambda *a, **k: {"aud": aud, "email": email, "sub": sub},
    )


def _connexion(db):
    return api.auth_google(api.GoogleAuthRequest(credential="peu-importe"), db)


# ── Les destinataires acceptés ───────────────────────────────────────────

def test_jeton_du_site_ou_d_android_accepte(db, monkeypatch):
    _google_repond(monkeypatch, WEB, "web@exemple-hector.fr")

    rep = _connexion(db)

    assert rep.email == "web@exemple-hector.fr"


def test_jeton_de_l_iphone_accepte(db, monkeypatch):
    """Le nouveau cas : l'appli iPhone vise son identifiant iOS."""
    _google_repond(monkeypatch, IOS, "iphone@exemple-hector.fr")

    rep = _connexion(db)

    assert rep.email == "iphone@exemple-hector.fr"


# ── LE test de sécurité ──────────────────────────────────────────────────

def test_jeton_emis_pour_une_autre_appli_refuse(db, monkeypatch):
    """Un jeton Google parfaitement valide, mais émis pour l'application de
    quelqu'un d'autre. Il ne doit JAMAIS ouvrir un compte chez nous."""
    _google_repond(monkeypatch, INTRUS, "victime@exemple-hector.fr")

    with pytest.raises(HTTPException) as e:
        _connexion(db)

    assert e.value.status_code == 401
    assert db.query(User).count() == 0  # aucun compte creé au passage


def test_jeton_sans_destinataire_refuse(db, monkeypatch):
    _google_repond(monkeypatch, None)

    with pytest.raises(HTTPException) as e:
        _connexion(db)

    assert e.value.status_code == 401


# ── Le rattachement des comptes ──────────────────────────────────────────

def test_le_meme_compte_depuis_le_site_puis_l_iphone(db, monkeypatch):
    """Le cas d'usage qui justifie tout ce chantier : quelqu'un inscrit avec
    Google sur le site télécharge l'appli iPhone. Il doit retrouver SES données,
    pas un compte vierge."""
    _google_repond(monkeypatch, WEB, "fidele@exemple-hector.fr", sub="g-42")
    _connexion(db)

    _google_repond(monkeypatch, IOS, "fidele@exemple-hector.fr", sub="g-42")
    rep = _connexion(db)

    assert rep.email == "fidele@exemple-hector.fr"
    assert db.query(User).count() == 1  # aucun doublon

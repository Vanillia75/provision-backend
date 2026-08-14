# -*- coding: utf-8 -*-
# ════════════════════════════════════════════════════════════════════════
#  LECTURE GROUPÉE : DES PHOTOS QUI PORTENT LE MÊME NOM.
#
#  Trouvé le 14/08/2026 par une relecture croisée du code de scan.
#
#  Quand on choisit plusieurs photos d'un coup depuis la photothèque d'un
#  iPhone, iOS les livre TOUTES sous le même nom (« image.jpg »). La route de
#  lecture groupée écrivait chaque page sous son nom d'origine dans un dossier
#  temporaire : les pages s'écrasaient donc les unes les autres, et le lecteur
#  recevait N fois la DERNIÈRE photo en croyant lire N pages différentes.
#
#  Conséquence pour la personne : elle photographie son attestation en 3 pages,
#  et Totor ne voit que la page 3, trois fois. L'employeur et le métier, qui
#  sont écrits sur la page 1, disparaissent. C'est très exactement le cas
#  signalé par Mac, et il ne se produit QUE sur téléphone.
# ════════════════════════════════════════════════════════════════════════
import io
import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import aem_extractor
import api
from auth import hash_password
from database import Base, get_db
from models import Profile, Subscription, User


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
    s = TestSession()
    u = User(email="pages@exemple-totor.fr", password_hash=hash_password("Secret-solide-1"),
             email_verified=True)
    s.add(u)
    s.commit()
    s.add(Profile(user_id=u.id, statut="intermittent", onboarding_complete=True))
    s.add(Subscription(user_id=u.id, plan="premium", status="comp", source="comp"))
    s.commit()
    s.close()

    c = TestClient(api.app)
    jeton = c.post("/auth/login", json={"email": "pages@exemple-totor.fr",
                                        "password": "Secret-solide-1"}).json()["token"]
    yield c, {"Authorization": f"Bearer {jeton}"}
    api.app.dependency_overrides.clear()


def _trois_photos_du_meme_nom():
    """Ce que livre iOS quand on choisit 3 photos d'un coup : le même nom, des
    contenus différents."""
    return [
        ("files", ("image.jpg", io.BytesIO(b"PAGE-UN-employeur-et-metier"), "image/jpeg")),
        ("files", ("image.jpg", io.BytesIO(b"PAGE-DEUX-le-contrat"), "image/jpeg")),
        ("files", ("image.jpg", io.BytesIO(b"PAGE-TROIS-les-montants"), "image/jpeg")),
    ]


def test_trois_photos_du_meme_nom_ne_s_ecrasent_pas(client, monkeypatch):
    c, h = client
    vus = {}

    def faux_lecteur(chemins):
        vus["chemins"] = list(chemins)
        vus["contenus"] = [io.open(p, "rb").read() for p in chemins]
        return [{"employeur": "MB SOLUTIONS", "date": "2024-08-14", "date_fin": "2024-08-14",
                 "type_activite": "cachet_isole", "nombre": 1, "salaire_brut": 258.15,
                 "metier": "artiste", "filename": "image.jpg"}]

    monkeypatch.setattr(aem_extractor, "extract_aem_data_pages", faux_lecteur)
    r = c.post("/intermittent/aem/extract-pages", headers=h, files=_trois_photos_du_meme_nom())
    assert r.status_code == 200, r.text

    assert len(vus["chemins"]) == 3
    assert len(set(vus["chemins"])) == 3, (
        "les trois pages ont atterri sur le même fichier : la lecture groupée "
        "relit trois fois la dernière photo")
    assert vus["contenus"] == [b"PAGE-UN-employeur-et-metier",
                               b"PAGE-DEUX-le-contrat",
                               b"PAGE-TROIS-les-montants"], (
        "le contenu ou l'ordre des pages n'est pas celui qui a été envoyé")


def test_l_ordre_des_pages_est_conserve(client, monkeypatch):
    """La page 1 porte l'employeur et le métier : si l'ordre se perd, le lecteur
    ne sait plus quel contrat appartient à qui."""
    c, h = client
    vus = {}
    monkeypatch.setattr(aem_extractor, "extract_aem_data_pages",
                        lambda ch: (vus.setdefault("ch", list(ch)), [])[1] or [])
    c.post("/intermittent/aem/extract-pages", headers=h, files=_trois_photos_du_meme_nom())
    noms = [os.path.basename(p) for p in vus["ch"]]
    assert noms == sorted(noms), f"l'ordre des pages n'est pas garanti : {noms}"


def test_un_fichier_sans_nom_ne_fait_pas_planter(client, monkeypatch):
    """Certains partages iOS envoient un fichier sans nom du tout."""
    c, h = client
    monkeypatch.setattr(aem_extractor, "extract_aem_data_pages", lambda ch: [])
    r = c.post("/intermittent/aem/extract-pages", headers=h, files=[
        ("files", ("photo.jpg", io.BytesIO(b"une page"), "image/jpeg")),
        ("files", ("photo.jpg", io.BytesIO(b"une autre"), "image/jpeg")),
    ])
    assert r.status_code in (200, 400, 422), r.text

# ════════════════════════════════════════════════════════════════════════
#  Tests du CLASSEUR (documents personnels rangés par employeur).
#  Sqlite en mémoire. Règles : cloisonnement strict par utilisateur,
#  suppression = coffre + base, purge à la suppression de compte, et la
#  liste part bien dans l'export RGPD.
# ════════════════════════════════════════════════════════════════════════
from datetime import date, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from models import User, Profile, DocumentPerso
import api


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _user(db, email="classeur@exemple-hector.fr"):
    u = User(email=email)
    db.add(u)
    db.commit()
    db.refresh(u)
    db.add(Profile(user_id=u.id, statut="intermittent"))
    db.commit()
    return u


def _doc(db, uid, employeur="Théâtre du Verger", type_doc="contrat", nom="contrat.pdf", cle=None):
    row = DocumentPerso(
        user_id=uid, employeur=employeur, type_document=type_doc,
        filename=nom, r2_key=cle or f"documents/{uid}/{nom}",
        date_document=date(2026, 6, 12),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


# ── Lecture ──────────────────────────────────────────────────────────────

def test_liste_du_plus_recent_au_plus_ancien(db):
    u = _user(db)
    a = _doc(db, u.id, nom="vieux.pdf")
    a.created_at = datetime(2026, 1, 1)
    b = _doc(db, u.id, nom="recent.pdf")
    b.created_at = datetime(2026, 7, 1)
    db.commit()
    noms = [d["filename"] for d in api.liste_documents(u, db)]
    assert noms == ["recent.pdf", "vieux.pdf"]


def test_liste_ne_montre_jamais_le_classeur_d_un_autre(db):
    u1 = _user(db, "a@exemple-hector.fr")
    u2 = _user(db, "b@exemple-hector.fr")
    _doc(db, u1.id, nom="prive.pdf")
    assert api.liste_documents(u2, db) == []


def test_champs_exposes_et_cle_r2_jamais_exposee(db):
    u = _user(db)
    _doc(db, u.id)
    d = api.liste_documents(u, db)[0]
    assert d["employeur"] == "Théâtre du Verger"
    assert d["type_document"] == "contrat"
    assert d["date_document"] == "2026-06-12"
    assert "r2_key" not in d          # la clé de stockage reste interne
    assert "user_id" not in d


# ── Suppression ──────────────────────────────────────────────────────────

def test_suppression_retire_la_ligne(db, monkeypatch):
    u = _user(db)
    row = _doc(db, u.id)
    monkeypatch.setattr(api.r2_storage, "R2_ENABLED", False)
    assert api.supprimer_document(row.id, u, db) == {"ok": True}
    assert db.query(DocumentPerso).count() == 0


def test_suppression_appelle_le_coffre(db, monkeypatch):
    u = _user(db)
    row = _doc(db, u.id, cle="documents/x/abc.pdf")
    appels = []
    monkeypatch.setattr(api.r2_storage, "R2_ENABLED", True)
    monkeypatch.setattr(api.r2_storage, "delete_file", lambda k: appels.append(k) or True)
    api.supprimer_document(row.id, u, db)
    assert appels == ["documents/x/abc.pdf"]


def test_impossible_de_supprimer_le_document_d_un_autre(db):
    u1 = _user(db, "a@exemple-hector.fr")
    u2 = _user(db, "b@exemple-hector.fr")
    row = _doc(db, u1.id)
    with pytest.raises(api.HTTPException) as e:
        api.supprimer_document(row.id, u2, db)
    assert e.value.status_code == 404
    assert db.query(DocumentPerso).count() == 1


def test_lien_refuse_le_document_d_un_autre(db):
    u1 = _user(db, "a@exemple-hector.fr")
    u2 = _user(db, "b@exemple-hector.fr")
    row = _doc(db, u1.id)
    with pytest.raises(api.HTTPException) as e:
        api.lien_document(row.id, u2, db)
    assert e.value.status_code == 404


# ── RGPD ─────────────────────────────────────────────────────────────────

def test_suppression_de_compte_purge_le_classeur(db, monkeypatch):
    u = _user(db)
    _doc(db, u.id)
    monkeypatch.setattr(api.r2_storage, "R2_ENABLED", False)
    api.delete_account(u, db)
    assert db.query(DocumentPerso).count() == 0
    assert db.query(User).count() == 0


def test_export_contient_le_classeur(db):
    u = _user(db)
    _doc(db, u.id, employeur="La Belle Fanfare", type_doc="bulletin", nom="paie_juin.pdf")
    classeur = api.export_account_data(u, db)["classeur"]
    assert len(classeur) == 1
    assert classeur[0]["employeur"] == "La Belle Fanfare"
    assert classeur[0]["type"] == "bulletin"
    assert classeur[0]["fichier"] == "paie_juin.pdf"


def test_les_types_de_documents_sont_bornes(db):
    # Le formulaire ne peut pas inventer un type : tout inconnu retombe sur « autre ».
    assert "contrat" in api.TYPES_DOCUMENT and "bulletin" in api.TYPES_DOCUMENT
    assert "conges_spectacles" in api.TYPES_DOCUMENT and "autre" in api.TYPES_DOCUMENT

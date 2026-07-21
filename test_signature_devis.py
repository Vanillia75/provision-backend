# ════════════════════════════════════════════════════════════════════════
#  Signature électronique des devis (acceptation en ligne par jeton).
#  Base SQLite en mémoire, R2 coupé : on teste la PREUVE (hash, horodatage,
#  IP, email) et l'idempotence (la première preuve fait foi, jamais réécrite).
# ════════════════════════════════════════════════════════════════════════
from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from models import User, Profile, Quote
import api
import r2_storage


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _devis(db, statut="envoye", token="jeton-de-test-suffisamment-long"):
    u = User(email="ae@exemple-hector.fr")
    db.add(u); db.commit(); db.refresh(u)
    db.add(Profile(user_id=u.id, statut="auto_entrepreneur", entreprise="Studio Test", adresse="1 rue X", siret="99008620900014"))
    q = Quote(user_id=u.id, numero="D-2026-001", client_nom="Client Y", client_email="client@exemple-hector.fr",
              date_emission=date(2026, 7, 21), montant=300.0, statut=statut,
              lignes=[{"description": "Prestation", "quantite": 1, "prix_unitaire": 300.0}],
              signature_token=token)
    db.add(q); db.commit(); db.refresh(q)
    return q


def test_acceptation_enregistre_la_preuve_complete(db, monkeypatch):
    monkeypatch.setattr(r2_storage, "R2_ENABLED", False)
    q = _devis(db)
    ok = api._accepter_devis(db, q, "203.0.113.7", "Mozilla/5.0 (test)")
    assert ok is True
    assert q.statut == "accepte"
    assert q.signe_le is not None
    assert q.signe_ip == "203.0.113.7"
    assert q.signe_user_agent == "Mozilla/5.0 (test)"
    assert q.signe_email == "client@exemple-hector.fr"
    assert q.signe_hash and len(q.signe_hash) == 64        # SHA-256 hex


def test_idempotent_la_premiere_preuve_fait_foi(db, monkeypatch):
    monkeypatch.setattr(r2_storage, "R2_ENABLED", False)
    q = _devis(db)
    api._accepter_devis(db, q, "203.0.113.7", "UA-1")
    premier_hash, premiere_date = q.signe_hash, q.signe_le
    ok2 = api._accepter_devis(db, q, "198.51.100.9", "UA-2")   # deuxième clic
    assert ok2 is False
    assert q.signe_hash == premier_hash and q.signe_le == premiere_date
    assert q.signe_ip == "203.0.113.7"                     # rien n'a été réécrit


def test_jeton_inconnu_ou_trop_court(db):
    _devis(db, token="jeton-valide-pour-la-recherche-x")
    assert api._devis_par_token(db, "jeton-valide-pour-la-recherche-x") is not None
    assert api._devis_par_token(db, "inconnu-mais-assez-long-quand-meme") is None
    assert api._devis_par_token(db, "court") is None
    assert api._devis_par_token(db, "") is None


def test_echec_r2_ne_bloque_pas_l_acceptation(db, monkeypatch):
    monkeypatch.setattr(r2_storage, "R2_ENABLED", True)
    def boom(*a, **k):
        raise RuntimeError("R2 en carafe")
    monkeypatch.setattr(r2_storage, "upload_devis_signe", boom)
    q = _devis(db)
    assert api._accepter_devis(db, q, "203.0.113.7", "UA") is True
    assert q.statut == "accepte" and q.signe_hash            # preuve en base quand même
    assert q.signe_pdf_key is None

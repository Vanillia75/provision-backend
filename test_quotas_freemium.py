# ════════════════════════════════════════════════════════════════════════
#  Tests du moteur de quotas freemium 1.0.1 (quotas_freemium.py)
#  Base SQLite en mémoire : aucun réseau, aucun Stripe, aucune prod.
# ════════════════════════════════════════════════════════════════════════
from datetime import date, datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from models import User, AIUsage, ClientInvoice, Quote
import quotas_freemium as qf


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _user(db, jours_anciennete=0):
    u = User(email=f"test-{jours_anciennete}@exemple-hector.fr",
             created_at=datetime.utcnow() - timedelta(days=jours_anciennete))
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _marquer_message_chat(db, user, il_y_a_heures=0):
    """Simule un message de chat envoyé il y a N heures (ligne AIUsage 'chat')."""
    quand = datetime.utcnow() - timedelta(hours=il_y_a_heures)
    db.add(AIUsage(user_id=user.id, jour=quand.date(), type_appel="chat", count=1, updated_at=quand))
    db.commit()


def _attendre_402(fn, fonction_attendue):
    with pytest.raises(HTTPException) as exc:
        fn()
    assert exc.value.status_code == 402
    assert exc.value.detail["code"] == "premium_requis"
    assert exc.value.detail["fonction"] == fonction_attendue


# ─── Chat par conversation ───────────────────────────────────────────────

def test_nouvel_inscrit_a_6_conversations(db):
    u = _user(db, jours_anciennete=5)   # dans ses 30 premiers jours
    for _ in range(6):
        qf.consommer_fil_chat(db, u)    # 6 nouveaux fils passent
    _attendre_402(lambda: qf.consommer_fil_chat(db, u), "chat")


def test_ancien_inscrit_a_3_conversations_par_mois(db):
    u = _user(db, jours_anciennete=60)  # hors premier mois
    for _ in range(3):
        qf.consommer_fil_chat(db, u)
    _attendre_402(lambda: qf.consommer_fil_chat(db, u), "chat")


def test_les_messages_d_un_fil_actif_ne_consomment_rien(db):
    u = _user(db, jours_anciennete=60)
    for _ in range(3):
        qf.consommer_fil_chat(db, u)    # quota épuisé
    # Un message récent existe (il y a 1h) : on est DANS un fil -> ça passe,
    # même quota épuisé. Les allers-retours de Totor ne coûtent jamais rien.
    _marquer_message_chat(db, u, il_y_a_heures=1)
    qf.consommer_fil_chat(db, u)        # ne lève pas
    assert qf.etat_chat(db, u)["utilises"] == 3   # et ne consomme pas

def test_apres_24h_c_est_une_nouvelle_conversation(db):
    u = _user(db, jours_anciennete=60)
    _marquer_message_chat(db, u, il_y_a_heures=30)   # dernier échange avant-hier
    etat = qf.etat_chat(db, u)
    assert etat["fil_actif"] is False                 # fil expiré -> le prochain message consomme


# ─── Factures & devis : 5 créations/mois en gratuit, jamais rétroactif ──

_NUM = {"n": 0}

def _facture(db, user):
    _NUM["n"] += 1
    db.add(ClientInvoice(user_id=user.id, numero=f"F-{_NUM['n']}", client_nom="Client", montant=100, date_emission=date.today()))
    db.commit()


def test_facture_bloquee_a_la_6e_du_mois(db):
    u = _user(db)
    for _ in range(5):
        _facture(db, u)
    _attendre_402(lambda: qf.verifier_creation_document(db, u, "facture"), "facture_quota")


def test_les_anciennes_factures_ne_comptent_pas(db):
    u = _user(db)
    for _ in range(5):
        _facture(db, u)
    # On vieillit tout : créées le mois dernier -> le quota du mois est vierge.
    for inv in db.query(ClientInvoice).all():
        inv.created_at = datetime.utcnow() - timedelta(days=40)
    db.commit()
    qf.verifier_creation_document(db, u, "facture")   # ne lève pas


def test_devis_bloque_a_la_6e_du_mois(db):
    u = _user(db)
    for i in range(5):
        db.add(Quote(user_id=u.id, numero=f"D-{i}", client_nom="Client", montant=50, date_emission=date.today()))
    db.commit()
    _attendre_402(lambda: qf.verifier_creation_document(db, u, "devis"), "devis_quota")


def test_premium_cree_sans_limite(db, monkeypatch):
    u = _user(db)
    for _ in range(9):
        _facture(db, u)
    monkeypatch.setattr(qf.billing, "is_premium", lambda *_: True)
    qf.verifier_creation_document(db, u, "facture")   # ne lève pas


# ─── Mode Achat : 5 simulations/mois en gratuit ─────────────────────────

def test_mode_achat_5_simulations_puis_mur(db):
    u = _user(db)
    for i in range(5):
        r = qf.consommer_simulation_achat(db, u)
        assert r["utilisees"] == i + 1
    _attendre_402(lambda: qf.consommer_simulation_achat(db, u), "achat_simu")


def test_mode_achat_illimite_en_premium(db, monkeypatch):
    u = _user(db)
    monkeypatch.setattr(qf.billing, "is_premium", lambda *_: True)
    for _ in range(20):
        assert qf.consommer_simulation_achat(db, u) == {"illimite": True}

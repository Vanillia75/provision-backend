# ════════════════════════════════════════════════════════════════════════
#  Tests de l'historique « Parle à Totor » (demande testeuse du 24/07/2026).
#  Sqlite en mémoire. Règles : chaque espace garde son fil, la lecture ne
#  touche à aucun quota, l'effacement ne touche que l'espace courant, la
#  suppression de compte et l'export RGPD embarquent bien l'historique.
# ════════════════════════════════════════════════════════════════════════
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from models import User, Profile, ChatMessage
import api


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _user(db, statut="intermittent", email="chat@exemple-hector.fr"):
    u = User(email=email)
    db.add(u)
    db.commit()
    db.refresh(u)
    db.add(Profile(user_id=u.id, statut=statut))
    db.commit()
    return u


# ── Enregistrement ───────────────────────────────────────────────────────

def test_enregistre_question_et_reponse(db):
    u = _user(db)
    api.enregistrer_echange_chat(db, u.id, "intermittent", "Combien d'heures il me reste ?", "Il te reste 100h.")
    rows = db.query(ChatMessage).filter_by(user_id=u.id).all()
    assert [r.role for r in rows] == ["user", "assistant"]
    assert all(r.espace == "intermittent" for r in rows)


def test_enregistrement_jamais_bloquant(db):
    # Une erreur d'écriture (ici : violation de clé étrangère, utilisateur inexistant)
    # doit être avalée : la réponse part quand même, et la session reste utilisable.
    u = _user(db)
    api.enregistrer_echange_chat(db, "id-inexistant", "intermittent", "q", "r")  # ne doit pas lever
    api.enregistrer_echange_chat(db, u.id, "intermittent", "q", "r")
    assert db.query(ChatMessage).filter_by(user_id=u.id).count() == 2


def test_contenu_tronque_a_8000(db):
    u = _user(db)
    api.enregistrer_echange_chat(db, u.id, "intermittent", "q" * 10000, "r" * 10000)
    for r in db.query(ChatMessage).filter_by(user_id=u.id).all():
        assert len(r.content) == 8000


# ── Lecture ──────────────────────────────────────────────────────────────

def test_historique_ordre_chronologique_et_paires(db):
    u = _user(db)
    t0 = datetime(2026, 7, 23, 10, 0, 0)
    # Deux échanges, dont un où question et réponse partagent le MÊME horodatage :
    # la question doit rester avant la réponse.
    db.add(ChatMessage(user_id=u.id, espace="intermittent", role="assistant", content="r1", created_at=t0))
    db.add(ChatMessage(user_id=u.id, espace="intermittent", role="user", content="q1", created_at=t0))
    db.add(ChatMessage(user_id=u.id, espace="intermittent", role="user", content="q2", created_at=t0 + timedelta(hours=1)))
    db.add(ChatMessage(user_id=u.id, espace="intermittent", role="assistant", content="r2", created_at=t0 + timedelta(hours=1)))
    db.commit()
    out = api.chat_historique(u, db)
    assert out["espace"] == "intermittent"
    assert [m["content"] for m in out["messages"]] == ["q1", "r1", "q2", "r2"]
    assert out["messages"][0]["date"].startswith("2026-07-23")


def test_historique_separe_les_espaces(db):
    u = _user(db, statut="intermittent")
    db.add(ChatMessage(user_id=u.id, espace="intermittent", role="user", content="mes 507h"))
    db.add(ChatMessage(user_id=u.id, espace="auto_entrepreneur", role="user", content="ma TVA"))
    db.commit()
    out = api.chat_historique(u, db)
    assert [m["content"] for m in out["messages"]] == ["mes 507h"]
    # La même personne passée en mode AE retrouve l'AUTRE fil.
    db.query(Profile).filter_by(user_id=u.id).first().statut = "auto_entrepreneur"
    db.commit()
    out = api.chat_historique(u, db)
    assert [m["content"] for m in out["messages"]] == ["ma TVA"]


def test_historique_ne_montre_jamais_un_autre_compte(db):
    u1 = _user(db, email="a@exemple-hector.fr")
    u2 = _user(db, email="b@exemple-hector.fr")
    db.add(ChatMessage(user_id=u1.id, espace="intermittent", role="user", content="secret de u1"))
    db.commit()
    assert api.chat_historique(u2, db)["messages"] == []


def test_historique_plafonne_a_200(db):
    u = _user(db)
    t0 = datetime(2026, 1, 1)
    for i in range(250):
        db.add(ChatMessage(user_id=u.id, espace="intermittent", role="user",
                           content=f"m{i}", created_at=t0 + timedelta(minutes=i)))
    db.commit()
    out = api.chat_historique(u, db)
    assert len(out["messages"]) == 200
    # Ce sont bien les 200 PLUS RÉCENTS, en ordre chronologique.
    assert out["messages"][0]["content"] == "m50"
    assert out["messages"][-1]["content"] == "m249"


# ── Effacement ───────────────────────────────────────────────────────────

def test_effacer_ne_touche_que_l_espace_courant(db):
    u = _user(db, statut="intermittent")
    db.add(ChatMessage(user_id=u.id, espace="intermittent", role="user", content="a"))
    db.add(ChatMessage(user_id=u.id, espace="intermittent", role="assistant", content="b"))
    db.add(ChatMessage(user_id=u.id, espace="auto_entrepreneur", role="user", content="c"))
    db.commit()
    out = api.effacer_chat_historique(u, db)
    assert out == {"ok": True, "supprimes": 2}
    restants = db.query(ChatMessage).filter_by(user_id=u.id).all()
    assert len(restants) == 1 and restants[0].espace == "auto_entrepreneur"


def test_effacer_ne_touche_pas_les_autres_comptes(db):
    u1 = _user(db, email="a@exemple-hector.fr")
    u2 = _user(db, email="b@exemple-hector.fr")
    db.add(ChatMessage(user_id=u2.id, espace="intermittent", role="user", content="garde-moi"))
    db.commit()
    api.effacer_chat_historique(u1, db)
    assert db.query(ChatMessage).filter_by(user_id=u2.id).count() == 1


# ── RGPD : suppression de compte + export ────────────────────────────────

def test_suppression_de_compte_purge_le_chat(db):
    u = _user(db)
    db.add(ChatMessage(user_id=u.id, espace="intermittent", role="user", content="à purger"))
    db.commit()
    api.delete_account(u, db)
    assert db.query(ChatMessage).count() == 0
    assert db.query(User).count() == 0


def test_export_contient_les_conversations(db):
    u = _user(db)
    api.enregistrer_echange_chat(db, u.id, "intermittent", "ma question", "ma réponse")
    data = api.export_account_data(u, db)
    convs = data["conversations_totor"]
    assert [c["role"] for c in convs] == ["user", "assistant"]
    assert convs[0]["contenu"] == "ma question"
    assert convs[0]["espace"] == "intermittent"

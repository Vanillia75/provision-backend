# ════════════════════════════════════════════════════════════════════════
#  Tests du contrôle d'accès de la secrétaire vocale (voice_access.py).
#  Base SQLite en mémoire : aucun réseau, aucune prod.
#  Plan A = caller ID (Profile.telephone) ; Plan B = code à 6 chiffres calculé.
# ════════════════════════════════════════════════════════════════════════
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from models import User, Profile, Subscription
import voice_access as va


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _abonne(db, tel="+33 6 12 34 56 78", statut="active", plan="premium"):
    u = User(email="abo@exemple-hector.fr")
    db.add(u); db.commit(); db.refresh(u)
    db.add(Profile(user_id=u.id, telephone=tel))
    db.add(Subscription(user_id=u.id, plan=plan, status=statut, source="stripe"))
    db.commit()
    return u


# ── Le code du jour ────────────────────────────────────────────────────────
def test_code_du_jour_six_chiffres_et_stable():
    c = va.code_du_jour("user-abc")
    assert len(c) == 6 and c.isdigit()
    assert va.code_du_jour("user-abc") == c            # stable dans la journée
    assert va.code_du_jour("user-xyz") != c            # différent selon l'abonné


@pytest.mark.parametrize("saisi", [
    "+33 6 12 34 56 78", "0612345678", "0033612345678", "06.12.34.56.78", "+33612345678",
])
def test_normalisation_tous_formats_identiques(saisi):
    assert va.normaliser_tel(saisi) == "612345678"


# ── PLAN A : caller ID ─────────────────────────────────────────────────────
def test_caller_id_abonne_actif_passe(db):
    _abonne(db, tel="0612345678")
    assert va.verifier_abonnement(db, "+33612345678").startswith("ABONNE_ACTIF")


def test_caller_id_inconnu_demande_le_code(db):
    _abonne(db, tel="0612345678")
    assert va.verifier_abonnement(db, "+33699998888").startswith("NUMERO_NON_ABONNE")


def test_caller_id_non_abonne_meme_si_profil(db):
    # profil avec numéro mais abonnement 'free' -> pas d'accès
    _abonne(db, tel="0612345678", plan="free", statut="canceled")
    assert va.verifier_abonnement(db, "0612345678").startswith("NUMERO_NON_ABONNE")


def test_caller_id_absent_demande_le_code(db):
    assert va.verifier_abonnement(db, "").startswith("NUMERO_INCONNU")


# ── PLAN B : code à 6 chiffres ─────────────────────────────────────────────
def test_code_valide_passe(db):
    u = _abonne(db)
    bon = va.code_du_jour(u.id)
    assert va.verifier_code(db, bon, call_id="appel-1").startswith("CODE_VALIDE")


def test_code_invalide_puis_verrou_apres_3_essais(db):
    _abonne(db)
    r1 = va.verifier_code(db, "000000", call_id="appel-2")
    r2 = va.verifier_code(db, "000001", call_id="appel-2")
    r3 = va.verifier_code(db, "000002", call_id="appel-2")
    assert r1.startswith("CODE_INVALIDE") and "2 essais" in r1
    assert r2.startswith("CODE_INVALIDE") and "1 essai" in r2
    assert r3.startswith("BLOQUE")
    # un 4e essai, même avec le bon code, reste bloqué pour cet appel
    u = db.query(User).first()
    assert va.verifier_code(db, va.code_du_jour(u.id), call_id="appel-2").startswith("BLOQUE")


def test_code_mal_forme_compte_comme_essai(db):
    _abonne(db)
    assert va.verifier_code(db, "12", call_id="appel-3").startswith("CODE_MAL_FORME")


def test_verrou_isole_par_appel(db):
    u = _abonne(db)
    va.verifier_code(db, "000000", call_id="appel-A")
    va.verifier_code(db, "000000", call_id="appel-A")
    va.verifier_code(db, "000000", call_id="appel-A")   # appel-A bloqué
    # un AUTRE appel n'est pas impacté : le bon code passe
    assert va.verifier_code(db, va.code_du_jour(u.id), call_id="appel-B").startswith("CODE_VALIDE")

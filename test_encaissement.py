# ════════════════════════════════════════════════════════════════════════
#  Paiement en ligne des factures (Stripe Connect) — logique du webhook.
#  Base SQLite en mémoire, aucun appel réseau : on teste que la facture ne
#  passe « payée » QUE sur confirmation réelle (Loi X), et l'attente SEPA.
# ════════════════════════════════════════════════════════════════════════
from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from models import User, FiscalSettings, ClientInvoice
import encaissement


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _facture(db, statut="envoyee", acct="acct_TEST123"):
    u = User(email="ae@exemple-hector.fr")
    db.add(u); db.commit(); db.refresh(u)
    db.add(FiscalSettings(user_id=u.id, stripe_account_id=acct))
    inv = ClientInvoice(user_id=u.id, numero="F-2026-042", client_nom="Client Z",
                        date_emission=date(2026, 7, 22), montant=200.0, statut=statut,
                        payment_token="jeton-paiement-suffisamment-long")
    db.add(inv); db.commit(); db.refresh(inv)
    return inv


def _evt(inv, type_evt, payment_status, account="acct_TEST123"):
    return {
        "type": type_evt,
        "account": account,
        "data": {"object": {
            "metadata": {"invoice_id": inv.id, "payment_token": inv.payment_token},
            "client_reference_id": inv.id,
            "payment_status": payment_status,
        }},
    }


def test_carte_payee_immediatement(db):
    inv = _facture(db)
    res = encaissement.traiter_evenement_connect(db, _evt(inv, "checkout.session.completed", "paid"))
    assert res["resultat"] == "payee"
    assert inv.statut == "payee" and inv.date_paiement is not None
    assert inv.paiement_en_cours is False


def test_sepa_lance_ne_paie_PAS_la_facture(db):
    # Loi X : le prélèvement est parti mais PAS confirmé -> jamais « payée ».
    inv = _facture(db)
    res = encaissement.traiter_evenement_connect(db, _evt(inv, "checkout.session.completed", "unpaid"))
    assert res["resultat"] == "sepa_en_cours"
    assert inv.statut == "envoyee"           # inchangé !
    assert inv.paiement_en_cours is True


def test_sepa_confirme_paie_la_facture(db):
    inv = _facture(db)
    encaissement.traiter_evenement_connect(db, _evt(inv, "checkout.session.completed", "unpaid"))
    res = encaissement.traiter_evenement_connect(db, _evt(inv, "checkout.session.async_payment_succeeded", "paid"))
    assert res["resultat"] == "payee"
    assert inv.statut == "payee" and inv.paiement_en_cours is False


def test_sepa_echoue_leve_l_attente_sans_payer(db):
    inv = _facture(db)
    encaissement.traiter_evenement_connect(db, _evt(inv, "checkout.session.completed", "unpaid"))
    res = encaissement.traiter_evenement_connect(db, _evt(inv, "checkout.session.async_payment_failed", "unpaid"))
    assert res["resultat"] == "echec_sepa"
    assert inv.statut == "envoyee" and inv.paiement_en_cours is False


def test_deja_payee_jamais_retouchee(db):
    inv = _facture(db, statut="payee")
    res = encaissement.traiter_evenement_connect(db, _evt(inv, "checkout.session.completed", "paid"))
    assert res["ignore"] == "deja_payee"


def test_evenement_d_un_autre_compte_ignore(db):
    # Sécurité : un événement venant d'un compte connecté ÉTRANGER au
    # propriétaire de la facture ne touche à rien.
    inv = _facture(db, acct="acct_TEST123")
    res = encaissement.traiter_evenement_connect(db, _evt(inv, "checkout.session.completed", "paid", account="acct_AUTRE"))
    assert res["ignore"] == "compte_inattendu"
    assert inv.statut == "envoyee"


def test_evenements_non_geres_ignores(db):
    inv = _facture(db)
    res = encaissement.traiter_evenement_connect(db, {"type": "payment_intent.created", "data": {"object": {}}})
    assert "ignore" in res
    assert inv.statut == "envoyee"


def test_proprietaire_sans_compte_connecte_jamais_payee(db):
    # Contrôle OBLIGATOIRE (échec fermé) : le propriétaire n'a pas de compte
    # connecté enregistré -> aucun événement, même signé, ne paie sa facture.
    inv = _facture(db, acct=None)
    res = encaissement.traiter_evenement_connect(db, _evt(inv, "checkout.session.completed", "paid"))
    assert res["ignore"] == "compte_inattendu"
    assert inv.statut == "envoyee"


def test_replay_evenement_deja_traite_ignore(db):
    # Stripe relivre parfois un événement déjà traité : il ne doit JAMAIS
    # rejouer. Cas concret : un vieux « completed » (SEPA lancé) relivré APRÈS
    # l'échec du prélèvement ne doit pas réafficher « prélèvement en cours ».
    inv = _facture(db)
    lance = _evt(inv, "checkout.session.completed", "unpaid"); lance["id"] = "evt_lance_1"
    echec = _evt(inv, "checkout.session.async_payment_failed", "unpaid"); echec["id"] = "evt_echec_1"
    encaissement.traiter_evenement_connect(db, lance)
    encaissement.traiter_evenement_connect(db, echec)
    res = encaissement.traiter_evenement_connect(db, lance)   # re-livraison
    assert res["ignore"] == "deja_traite"
    assert inv.paiement_en_cours is False    # l'échec reste l'état affiché

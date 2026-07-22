# -*- coding: utf-8 -*-
"""
encaissement.py — Paiement en ligne des factures (Stripe Connect).

Modèle choisi (décision 21/07/2026, vérifié à la source) :
  - Comptes connectés STANDARD (dashboard Stripe complet) : 0 € de frais Connect
    pour TOTOR, Stripe gère les tarifs, les risques et le KYC.
  - CHARGES DIRECTES : le client paie directement le compte de l'utilisateur ;
    l'argent ne transite JAMAIS par TOTOR (aucun statut ACPR requis).
  - AUCUNE commission TOTOR (application_fee) : on vend l'abonnement, pas le flux.

La facture ne passe « payée » que sur confirmation RÉELLE par webhook :
  - carte : checkout.session.completed avec payment_status == "paid" ;
  - prélèvement SEPA : la confirmation arrive plus tard via
    checkout.session.async_payment_succeeded (~7 jours) ; entre les deux, la
    facture est marquée « paiement en cours », jamais « payée » (Loi X).
"""
import os
from datetime import date, datetime

import stripe
from sqlalchemy.orm import Session

from models import ClientInvoice, FiscalSettings, StripeEvent, User

stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "").strip()

# Secret du webhook CONNECT (événements des comptes connectés). Distinct du
# webhook plateforme existant (abonnements TOTOR).
STRIPE_CONNECT_WEBHOOK_SECRET = os.environ.get("STRIPE_CONNECT_WEBHOOK_SECRET", "").strip()

BASE_URL = os.environ.get("SIGNATURE_BASE_URL", "https://www.montotor.fr")


def _g(obj, key, default=None):
    """Lecture robuste d'un champ Stripe : marche que `obj` soit un dict OU un objet
    Stripe (StripeObject de stripe-python v15 n'expose plus .get(), seulement
    l'attribut). Même recette que billing._g (piège déjà rencontré en juin)."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


# ── Compte connecté de l'utilisateur ────────────────────────────────────────
def creer_compte_connecte(user: User) -> str:
    """Crée le compte Stripe STANDARD (FR) de l'utilisateur. Renvoie acct_..."""
    acct = stripe.Account.create(type="standard", country="FR", email=user.email)
    return acct.id


def lien_onboarding(account_id: str) -> str:
    """Lien (usage unique) vers le formulaire d'inscription hébergé par Stripe."""
    link = stripe.AccountLink.create(
        account=account_id,
        refresh_url=f"{BASE_URL}/?stripe_onboarding=refresh",
        return_url=f"{BASE_URL}/?stripe_onboarding=retour",
        type="account_onboarding",
    )
    return link.url


def statut_compte(account_id: str) -> dict:
    """État du compte connecté. `actif` = peut encaisser des paiements.
    ⚠️ Le retour du client sur return_url ne prouve RIEN : seule cette
    vérification (charges_enabled) fait foi."""
    acct = stripe.Account.retrieve(account_id)
    return {
        "actif": bool(_g(acct, "charges_enabled")),
        "dossier_complet": bool(_g(acct, "details_submitted")),
    }


# ── Session de paiement d'une facture (charge directe) ──────────────────────
def creer_session_paiement(inv: ClientInvoice, ttc: float, account_id: str, mode: str) -> str:
    """Crée une session Stripe Checkout SUR le compte connecté (charge directe)
    pour le TTC de la facture. `mode` : "card" ou "sepa". Renvoie l'URL de la
    page de paiement Stripe. Le montant est le TTC AFFICHÉ (snapshot TVA),
    jamais recalculé ailleurs."""
    methodes = ["card"] if mode == "card" else ["sepa_debit"]
    session = stripe.checkout.Session.create(
        mode="payment",
        payment_method_types=methodes,
        line_items=[{
            "quantity": 1,
            "price_data": {
                "currency": "eur",
                "unit_amount": int(round(ttc * 100)),
                "product_data": {"name": f"Facture {inv.numero}"},
            },
        }],
        customer_email=inv.client_email or None,
        client_reference_id=inv.id,
        metadata={"invoice_id": inv.id, "payment_token": inv.payment_token or ""},
        success_url=f"{BASE_URL}/paiement/{inv.payment_token}/merci",
        cancel_url=f"{BASE_URL}/paiement/{inv.payment_token}",
        stripe_account=account_id,
    )
    return session.url


# ── Webhook Connect : la seule source de vérité du « payé » ────────────────
def construire_evenement(payload: bytes, signature: str):
    """Vérifie la signature du webhook Connect et renvoie l'événement Stripe."""
    return stripe.Webhook.construct_event(payload, signature, STRIPE_CONNECT_WEBHOOK_SECRET)


def traiter_evenement_connect(db: Session, event) -> dict:
    """Reflète l'état de paiement d'une facture depuis un événement Checkout
    survenu sur un compte connecté. Idempotent : une facture déjà payée n'est
    jamais retouchée. Renvoie un dict de compte rendu (pour les logs/tests)."""
    type_evt = event["type"]
    if type_evt not in ("checkout.session.completed",
                       "checkout.session.async_payment_succeeded",
                       "checkout.session.async_payment_failed"):
        return {"ok": True, "ignore": type_evt}

    # Déduplication : Stripe peut relivrer un même événement (retries). Un
    # événement déjà traité ne doit jamais rejouer (ex. un vieux « completed »
    # SEPA relivré APRÈS un échec remettrait « prélèvement en cours » à tort).
    evt_id = _g(event, "id")
    if evt_id and db.query(StripeEvent).filter(StripeEvent.event_id == evt_id).first():
        return {"ok": True, "ignore": "deja_traite"}

    session = event["data"]["object"]
    meta = _g(session, "metadata") or {}
    invoice_id = _g(meta, "invoice_id") or _g(session, "client_reference_id")
    if not invoice_id:
        return {"ok": True, "ignore": "sans_invoice_id"}

    inv = db.query(ClientInvoice).filter(ClientInvoice.id == invoice_id).first()
    if not inv:
        return {"ok": True, "ignore": "facture_inconnue"}
    if inv.statut == "payee":
        return {"ok": True, "ignore": "deja_payee"}

    # Sécurité : l'événement doit venir DU compte connecté du propriétaire.
    # Contrôle OBLIGATOIRE (échec fermé) : si le propriétaire n'a pas de compte
    # connecté enregistré, aucun événement ne peut payer sa facture — sinon un
    # tiers pourrait la faire passer « payée » depuis SON propre compte Standard
    # (le webhook Connect partage un seul secret pour tous les comptes).
    acct = _g(event, "account")
    fs = db.query(FiscalSettings).filter(FiscalSettings.user_id == inv.user_id).first()
    if not (acct and fs and fs.stripe_account_id and acct == fs.stripe_account_id):
        return {"ok": True, "ignore": "compte_inattendu"}

    if type_evt == "checkout.session.async_payment_failed":
        inv.paiement_en_cours = False
        _marquer_traite(db, evt_id, type_evt)
        db.commit()
        return {"ok": True, "resultat": "echec_sepa", "invoice_id": inv.id}

    if _g(session, "payment_status") == "paid":
        # Argent réellement encaissé (carte tout de suite, ou SEPA confirmé).
        inv.statut = "payee"
        inv.date_paiement = inv.date_paiement or date.today()
        inv.paiement_en_cours = False
        _marquer_traite(db, evt_id, type_evt)
        db.commit()
        return {"ok": True, "resultat": "payee", "invoice_id": inv.id}

    # completed mais payment_status == "unpaid" : prélèvement SEPA lancé,
    # confirmation dans ~7 jours. On l'affiche, on n'encaisse rien.
    inv.paiement_en_cours = True
    _marquer_traite(db, evt_id, type_evt)
    db.commit()
    return {"ok": True, "resultat": "sepa_en_cours", "invoice_id": inv.id}


def _marquer_traite(db: Session, evt_id, type_evt) -> None:
    """Enregistre l'événement comme traité (commit fait par l'appelant, dans la
    MÊME transaction que l'effet sur la facture)."""
    if evt_id:
        db.add(StripeEvent(event_id=evt_id, type=type_evt))

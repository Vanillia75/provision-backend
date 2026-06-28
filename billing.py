"""
billing.py — Abonnement Stripe pour H€CTOR.

Toute la logique de facturation est isolée ici.

GARDE-FOUS (non négociables) :
  1. Le premium ne s'active QUE via le webhook signé (ou un code testeur).
     Le retour success_url n'active RIEN.
  2. La signature du webhook est vérifiée (construct_event + STRIPE_WEBHOOK_SECRET).
  3. Toutes les clés sont en variables d'environnement, jamais en dur.
  4. Idempotence : chaque event traité est enregistré (table stripe_events).

is_premium() est la SEULE source de vérité pour les quotas.
"""

import os
from datetime import datetime, timedelta

import stripe
from sqlalchemy import extract
from sqlalchemy.orm import Session

from models import Subscription, PromoCode, StripeEvent, User, AIUsage

# ── Configuration (100 % variables d'environnement) ──
stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_PRICE_PREMIUM = os.environ.get("STRIPE_PRICE_PREMIUM", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
FRONTEND_URL = os.environ.get("FRONTEND_URL", "https://hector-app.fr")

# Domaines autorisés pour le retour de paiement. On renvoie l'utilisateur EXACTEMENT sur
# le domaine d'où il vient (pour préserver sa session/localStorage), mais jamais sur une
# URL arbitraire (sécurité : pas d'open redirect via Stripe).
ALLOWED_RETURN_ORIGINS = {
    "https://hector-app.fr",
    "https://www.hector-app.fr",
    "http://localhost:5173",
}


def _safe_return_base(origin: str | None) -> str:
    return origin if (origin and origin in ALLOWED_RETURN_ORIGINS) else FRONTEND_URL

# ── Quotas freemium MENSUELS (surchargeables par env) ──
FREE_AEM_SCAN_PER_MONTH = int(os.environ.get("FREE_AEM_SCAN_PER_MONTH", "2"))
FREE_CHAT_PER_MONTH = int(os.environ.get("FREE_CHAT_PER_MONTH", "3"))
FREE_DOC_SCAN_PER_MONTH = int(os.environ.get("FREE_DOC_SCAN_PER_MONTH", "3"))

# Statuts Stripe qui donnent droit au premium (+ "comp" = offert testeur).
GRANTING_STATUSES = ("active", "trialing", "comp")


# ════════════════════════════════════════════════════════════════════════
#  RÈGLE PREMIUM — source de vérité unique
# ════════════════════════════════════════════════════════════════════════
def is_premium(db: Session, user: User) -> bool:
    """Premium = plan 'premium' ET statut actif ET période non expirée."""
    sub = db.query(Subscription).filter(Subscription.user_id == user.id).first()
    if not sub or sub.plan != "premium":
        return False
    if sub.status not in GRANTING_STATUSES:
        return False
    if sub.current_period_end is not None and sub.current_period_end < datetime.utcnow():
        return False
    return True


def usage_this_month(db: Session, user_id: str, type_appel: str) -> int:
    """Somme des appels d'un type pour le MOIS courant (quotas freemium = mensuels).

    On réutilise la table ai_usage (1 ligne par jour) : on somme les lignes du mois.
    """
    now = datetime.utcnow()
    rows = (
        db.query(AIUsage)
        .filter(
            AIUsage.user_id == user_id,
            AIUsage.type_appel == type_appel,
            extract("year", AIUsage.jour) == now.year,
            extract("month", AIUsage.jour) == now.month,
        )
        .all()
    )
    return int(sum(r.count for r in rows))


def free_quota_for(type_appel: str) -> int:
    return {
        "aem_scan": FREE_AEM_SCAN_PER_MONTH,
        "chat": FREE_CHAT_PER_MONTH,
        "doc_scan": FREE_DOC_SCAN_PER_MONTH,
    }.get(type_appel, 0)


# ════════════════════════════════════════════════════════════════════════
#  Helpers abonnement
# ════════════════════════════════════════════════════════════════════════
def get_or_create_subscription(db: Session, user: User) -> Subscription:
    sub = db.query(Subscription).filter(Subscription.user_id == user.id).first()
    if not sub:
        sub = Subscription(user_id=user.id, plan="free", source="stripe")
        db.add(sub)
        db.commit()
        db.refresh(sub)
    return sub


def _g(obj, key, default=None):
    """Lecture robuste d'un champ Stripe : marche que `obj` soit un dict OU un objet
    Stripe (StripeObject de stripe-python v15 n'expose plus .get(), seulement l'attribut)."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _ts_to_dt(ts):
    return datetime.utcfromtimestamp(ts) if ts else None


def _apply_stripe_subscription(db: Session, stripe_sub: dict):
    """Met à jour notre Subscription à partir d'un objet subscription Stripe.
    Idempotent : on ne fait que refléter l'état envoyé par Stripe."""
    customer_id = _g(stripe_sub, "customer")
    sub_id = _g(stripe_sub, "id")
    status = _g(stripe_sub, "status")

    row = (
        db.query(Subscription).filter(Subscription.stripe_subscription_id == sub_id).first()
        or db.query(Subscription).filter(Subscription.stripe_customer_id == customer_id).first()
    )
    if not row:
        return  # aucun user rattaché (ne devrait pas arriver après checkout.completed)

    # current_period_end : au top-level dans les anciennes versions API, sur l'item d'abonnement
    # dans les versions récentes (2025+). On tente les deux.
    cpe = _g(stripe_sub, "current_period_end")
    if cpe is None:
        items = _g(stripe_sub, "items")
        data = _g(items, "data") or []
        if data:
            cpe = _g(data[0], "current_period_end")

    row.stripe_customer_id = customer_id
    row.stripe_subscription_id = sub_id
    row.status = status
    row.plan = "premium" if status in GRANTING_STATUSES else "free"
    row.current_period_end = _ts_to_dt(cpe)
    row.cancel_at_period_end = bool(_g(stripe_sub, "cancel_at_period_end"))
    row.source = "stripe"
    row.updated_at = datetime.utcnow()
    db.commit()


def activate_comp_premium(db: Session, user: User, months=None):
    """Premium OFFERT (code testeur) : actif tout de suite, SANS Stripe.
    months=None => premium À VIE : current_period_end reste NULL, donc is_premium()
    renvoie True indéfiniment (aucune expiration)."""
    sub = get_or_create_subscription(db, user)
    sub.plan = "premium"
    sub.status = "comp"
    sub.source = "comp"
    sub.current_period_end = None if months is None else (datetime.utcnow() + timedelta(days=30 * months))
    sub.cancel_at_period_end = False
    sub.updated_at = datetime.utcnow()
    db.commit()


def premium_source(db: Session, user: User):
    """'stripe' | 'comp' | None — d'où vient le premium (pour adapter l'UI :
    un premium 'comp' (offert) n'a pas d'abonnement Stripe à gérer)."""
    if not is_premium(db, user):
        return None
    sub = db.query(Subscription).filter(Subscription.user_id == user.id).first()
    return sub.source if sub else None


# ════════════════════════════════════════════════════════════════════════
#  Checkout & Portal
# ════════════════════════════════════════════════════════════════════════
def create_checkout_session(db: Session, user: User, promo_code: str | None = None,
                            app_mode: str | None = None, origin: str | None = None) -> str:
    """Crée une Checkout Session (abonnement récurrent) et renvoie son URL.
    NB : l'activation du premium se fera au WEBHOOK, pas au retour de cette URL.
    `app_mode` (auto_entrepreneur/intermittent) et `origin` permettent de revenir sur le
    bon domaine ET dans le bon mode après le paiement."""
    sub = get_or_create_subscription(db, user)

    base = _safe_return_base(origin)
    mode_q = f"&mode={app_mode}" if app_mode else ""
    params = {
        "mode": "subscription",
        "line_items": [{"price": STRIPE_PRICE_PREMIUM, "quantity": 1}],
        "client_reference_id": user.id,
        "success_url": f"{base}/?billing=success{mode_q}",
        "cancel_url": f"{base}/?billing=cancel",
        "metadata": {"user_id": user.id},
        "subscription_data": {"metadata": {"user_id": user.id}},
    }
    if sub.stripe_customer_id:
        params["customer"] = sub.stripe_customer_id
    else:
        params["customer_email"] = user.email

    # Code influenceur : on attache le coupon Stripe correspondant.
    if promo_code:
        pc = _valid_promo(db, promo_code)
        if pc and pc.kind == "influencer" and pc.stripe_coupon_id:
            params["discounts"] = [{"coupon": pc.stripe_coupon_id}]

    session = stripe.checkout.Session.create(**params)
    return session.url


def create_portal_session(db: Session, user: User) -> str:
    """Customer Portal : l'utilisateur gère/annule lui-même son abonnement."""
    sub = get_or_create_subscription(db, user)
    if not sub.stripe_customer_id:
        raise ValueError("Aucun client Stripe pour cet utilisateur.")
    session = stripe.billing_portal.Session.create(
        customer=sub.stripe_customer_id,
        return_url=f"{FRONTEND_URL}/?nav=abonnement",
    )
    return session.url


# ════════════════════════════════════════════════════════════════════════
#  WEBHOOK — le cœur. Signature vérifiée + idempotence.
# ════════════════════════════════════════════════════════════════════════
def process_webhook(db: Session, payload: bytes, sig_header: str):
    """Vérifie la signature, déduplique, puis applique l'event.
    Lève stripe.error.SignatureVerificationError si la signature est invalide."""
    event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)

    # Idempotence : event déjà traité -> on sort sans rien refaire.
    event_id = event["id"]
    if db.query(StripeEvent).filter(StripeEvent.event_id == event_id).first():
        return {"status": "duplicate_ignored"}

    etype = event["type"]
    obj = event["data"]["object"]

    if etype == "checkout.session.completed":
        user_id = _g(obj, "client_reference_id") or _g(_g(obj, "metadata"), "user_id")
        customer_id = _g(obj, "customer")
        sub_id = _g(obj, "subscription")
        row = db.query(Subscription).filter(Subscription.user_id == user_id).first() if user_id else None
        if row:
            row.stripe_customer_id = customer_id
            row.stripe_subscription_id = sub_id
            row.updated_at = datetime.utcnow()
            db.commit()
        # On récupère l'abonnement complet pour refléter statut + période.
        if sub_id:
            _apply_stripe_subscription(db, stripe.Subscription.retrieve(sub_id))

    elif etype in ("customer.subscription.updated", "customer.subscription.deleted"):
        _apply_stripe_subscription(db, obj)

    # Enregistré APRÈS traitement réussi : si crash avant ce point, Stripe réémettra
    # l'event et on le retraitera (le traitement est idempotent, donc sans danger).
    db.add(StripeEvent(event_id=event_id, type=etype))
    db.commit()
    return {"status": "ok", "type": etype}


# ════════════════════════════════════════════════════════════════════════
#  Codes promo (influenceurs + testeurs)
# ════════════════════════════════════════════════════════════════════════
def _valid_promo(db: Session, code: str) -> PromoCode | None:
    code = (code or "").strip().upper()   # robustesse : codes saisis en minuscules sur mobile
    pc = db.query(PromoCode).filter(PromoCode.code == code, PromoCode.active == True).first()  # noqa: E712
    if not pc:
        return None
    if pc.max_uses is not None and pc.times_used >= pc.max_uses:
        return None
    return pc


def apply_promo(db: Session, user: User, code: str) -> dict:
    """Valide un code maison.
    - testeur  -> active le premium DIRECTEMENT (sans Stripe).
    - influenceur -> renvoie le coupon à attacher à la Checkout Session.
    """
    pc = _valid_promo(db, code)
    if not pc:
        return {"ok": False, "reason": "code_invalide"}

    if pc.kind == "tester":
        if pc.type == "lifetime":
            activate_comp_premium(db, user, months=None)   # premium à vie
            months = None
        else:
            months = int(pc.value) if pc.value else 12
            activate_comp_premium(db, user, months=months)
        pc.times_used += 1   # usage unique : avec max_uses=1, le code devient invalide ensuite
        db.commit()
        return {"ok": True, "kind": "tester", "premium": True, "months": months, "lifetime": pc.type == "lifetime"}

    # influenceur : on ne touche pas au premium ici, on l'appliquera au Checkout.
    return {"ok": True, "kind": "influencer", "premium": False, "coupon": pc.stripe_coupon_id}

"""
billing.py — Abonnement Stripe pour TOTOR.

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
import re
from datetime import datetime, timedelta

import stripe
from sqlalchemy import extract
from sqlalchemy.orm import Session

from models import Subscription, PromoCode, StripeEvent, User, AIUsage

# ── Configuration (100 % variables d'environnement) ──
# .strip() OBLIGATOIRE : certains hébergeurs (Railway) conservent un espace ou un
# retour à la ligne invisible en fin de valeur. Sur une clé, ce caractère rend
# l'en-tête HTTP invalide (« Invalid header value ... \n ») et Stripe refuse tout.
stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "").strip()
STRIPE_PRICE_PREMIUM = os.environ.get("STRIPE_PRICE_PREMIUM", "").strip()          # mensuel 9,99 €
STRIPE_PRICE_PREMIUM_ANNUAL = os.environ.get("STRIPE_PRICE_PREMIUM_ANNUAL", "").strip()  # annuel 79 €
# Pionnier : 44,99 €/an VERROUILLÉ À VIE, réservé aux 100 premiers payants réels.
# ⚠️ RÈGLE ABSOLUE : un abonné Pionnier garde ce prix indéfiniment tant qu'il reste
# abonné. Ne JAMAIS migrer son abonnement vers un autre prix, même lors d'une hausse.
STRIPE_PRICE_PIONNIER = os.environ.get("STRIPE_PRICE_PIONNIER", "").strip()
PIONNIER_LIMITE = 100
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "").strip()
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


def redact_secrets(text) -> str:
    """Masque toute clé secrète dans un texte destiné aux logs ou aux messages d'erreur,
    pour qu'un secret ne fuite jamais en clair : Stripe (sk_/rk_/whsec_) ET Anthropic (sk-ant-…)."""
    s = re.sub(r"sk-ant-[A-Za-z0-9_-]+", "sk-ant-***", str(text))   # clé Anthropic (x-api-key)
    # [A-Za-z0-9_]+ (underscore INCLUS) : sinon "sk_live_XXXX" ne masque que "sk_live" et laisse "_XXXX".
    return re.sub(r"(sk|rk|whsec)_[A-Za-z0-9_]+", r"\1_***", s)     # clés Stripe (sk_live_/sk_test_/rk_/whsec_)

# ── Quotas freemium MENSUELS (surchargeables par env) ──
FREE_AEM_SCAN_PER_MONTH = int(os.environ.get("FREE_AEM_SCAN_PER_MONTH", "5"))
FREE_CHAT_PER_MONTH = int(os.environ.get("FREE_CHAT_PER_MONTH", "3"))
FREE_DOC_SCAN_PER_MONTH = int(os.environ.get("FREE_DOC_SCAN_PER_MONTH", "5"))

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

    old_plan = row.plan  # pour détecter la bascule free -> premium (nouvel abonné)
    row.stripe_customer_id = customer_id
    row.stripe_subscription_id = sub_id
    row.status = status
    row.plan = "premium" if status in GRANTING_STATUSES else "free"
    row.current_period_end = _ts_to_dt(cpe)
    row.cancel_at_period_end = bool(_g(stripe_sub, "cancel_at_period_end"))
    row.source = "stripe"
    row.updated_at = datetime.utcnow()
    db.commit()

    # Nouvel abonné payant : alerte fondateur UNE seule fois (à la bascule vers premium,
    # pas aux renouvellements), et JAMAIS pour un compte de test maison : seuls les
    # vrais paiements comptent. Best-effort : ne doit jamais casser le webhook.
    if old_plan != "premium" and row.plan == "premium":
        try:
            u = db.query(User).filter(User.id == row.user_id).first()
            if u is not None and not bool(getattr(u, "is_test", False)):
                from emailing import send_founder_subscriber_alert
                send_founder_subscriber_alert(compter_abonnes_payants(db), u.email)
        except Exception:
            pass


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


def trial_days_left(db: Session, user: User):
    """Jours restants d'essai Premium (abonnement Stripe en statut 'trialing'), arrondi
    au jour supérieur ; None si l'utilisateur n'est pas en période d'essai.
    L'essai 14 j se fait AU CHECKOUT (carte enregistrée + trial_period_days=14, prélèvement
    auto ensuite sauf annulation ; Stripe envoie un rappel avant la fin)."""
    sub = db.query(Subscription).filter(Subscription.user_id == user.id).first()
    if not sub or sub.status != "trialing" or not sub.current_period_end:
        return None
    secs = (sub.current_period_end - datetime.utcnow()).total_seconds()
    if secs <= 0:
        return 0
    return int((secs + 86399) // 86400)   # arrondi au jour supérieur


def premium_source(db: Session, user: User):
    """'stripe' | 'comp' | None — d'où vient le premium (pour adapter l'UI :
    un premium 'comp' (offert) n'a pas d'abonnement Stripe à gérer)."""
    if not is_premium(db, user):
        return None
    sub = db.query(Subscription).filter(Subscription.user_id == user.id).first()
    return sub.source if sub else None


def compter_abonnes_payants(db: Session) -> int:
    """Nombre d'abonnés PAYANTS RÉELS (ARGENT RÉEL), toutes caisses confondues
    (Stripe, Apple, Google) : premium, statut actif. On EXCLUT uniquement les
    transactions SANDBOX/test des stores (reviewer Apple, TestFlight — fausses).
    On COMPTE en revanche les proches/VIP qui ont RÉELLEMENT payé : de l'argent
    réel = un client légitime, même si le compte est marqué `is_test`. C'est LE
    chiffre des alertes fondateur et du dashboard."""
    total, _ = compter_abonnes_detail(db)
    return total


def compter_abonnes_detail(db: Session):
    """(total, proches) : total des vrais payeurs, dont `proches` = comptes marqués
    `is_test` qui ont NÉANMOINS réellement payé (famille/VIP). Sert à l'affichage
    transparent « dont X proches » sur le dashboard."""
    q = (
        db.query(Subscription)
        .join(User, User.id == Subscription.user_id)
        .filter(
            Subscription.is_sandbox.is_(False),           # jamais les achats sandbox/test
            Subscription.plan == "premium",
            Subscription.status.in_(("active", "trialing")),  # abonnement réellement en cours
            Subscription.source.in_(("stripe", "apple", "google")),  # exclut les comp (grâcieux)
        )
    )
    total = q.count()
    proches = q.filter(User.is_test.is_(True)).count()
    return total, proches


# ════════════════════════════════════════════════════════════════════════
#  Pionnier : compteur RÉEL (Loi X pricing : jamais de fausse rareté)
# ════════════════════════════════════════════════════════════════════════
_PIONNIER_CACHE = {"t": 0.0, "n": 0}
_PIONNIER_TTL = 60  # secondes : la page d'abonnement peut interroger souvent


def compter_pionniers(db: Session) -> int:
    """Nombre d'abonnements Pionnier PAYANTS RÉELS : lus chez Stripe (source de
    vérité, prix Pionnier, statuts qui donnent le premium), moins les comptes de
    test de la maison (User.is_test via le customer). Cache mémoire 60 s."""
    import time as _time
    now = _time.time()
    if now - _PIONNIER_CACHE["t"] < _PIONNIER_TTL:
        return _PIONNIER_CACHE["n"]
    if not STRIPE_PRICE_PIONNIER or not stripe.api_key:
        return 0
    n = 0
    try:
        subs = stripe.Subscription.list(price=STRIPE_PRICE_PIONNIER, status="all", limit=100)
        for s in subs.auto_paging_iter():
            if _g(s, "status") not in GRANTING_STATUSES:
                continue
            cust = _g(s, "customer")
            row = db.query(Subscription).filter(Subscription.stripe_customer_id == cust).first()
            if row:
                u = db.query(User).filter(User.id == row.user_id).first()
                if u is not None and bool(getattr(u, "is_test", False)):
                    continue  # compte de test maison : ne compte pas une place
            n += 1
    except Exception:
        # Stripe injoignable : on sert la dernière valeur connue plutôt que 0
        return _PIONNIER_CACHE["n"]
    _PIONNIER_CACHE["t"] = now
    _PIONNIER_CACHE["n"] = n
    return n


def offre_pionnier(db: Session) -> dict:
    """État de l'offre Pionnier pour la page d'abonnement : places restantes
    (compteur réel) et ouverture. À 100 payants réels, l'offre se ferme seule."""
    pris = compter_pionniers(db)
    restantes = max(0, PIONNIER_LIMITE - pris)
    return {
        "pionnier_configure": bool(STRIPE_PRICE_PIONNIER),
        "pionnier_ouvert": bool(STRIPE_PRICE_PIONNIER) and restantes > 0,
        "pionnier_restantes": restantes,
        "pionnier_limite": PIONNIER_LIMITE,
    }


# ════════════════════════════════════════════════════════════════════════
#  Checkout & Portal
# ════════════════════════════════════════════════════════════════════════
def create_checkout_session(db: Session, user: User, promo_code: str | None = None,
                            app_mode: str | None = None, origin: str | None = None,
                            plan: str | None = None) -> str:
    """Crée une Checkout Session (abonnement récurrent) et renvoie son URL.
    NB : l'activation du premium se fera au WEBHOOK, pas au retour de cette URL.
    `app_mode` (auto_entrepreneur/intermittent) et `origin` permettent de revenir sur le
    bon domaine ET dans le bon mode après le paiement.
    `plan` = "mensuel" (défaut) | "annuel" | "pionnier" (44,99 €/an à vie, 100 premiers)."""
    sub = get_or_create_subscription(db, user)

    # Tarif selon le plan demandé.
    if plan == "pionnier" and STRIPE_PRICE_PIONNIER:
        # Garde-fou serveur : l'offre se ferme au 100e payant réel, même si la page
        # affichée était en retard. (Compteur réel : Loi X, pas de fausse rareté.)
        if offre_pionnier(db)["pionnier_restantes"] <= 0:
            raise ValueError("pionnier_complet")
        price_id = STRIPE_PRICE_PIONNIER
    elif plan == "annuel" and STRIPE_PRICE_PREMIUM_ANNUAL:
        price_id = STRIPE_PRICE_PREMIUM_ANNUAL
    else:
        price_id = STRIPE_PRICE_PREMIUM

    base = _safe_return_base(origin)
    mode_q = f"&mode={app_mode}" if app_mode else ""
    params = {
        "mode": "subscription",
        "line_items": [{"price": price_id, "quantity": 1}],
        "client_reference_id": user.id,
        "success_url": f"{base}/?billing=success{mode_q}",
        "cancel_url": f"{base}/?billing=cancel",
        "metadata": {"user_id": user.id},
        # PAS d'essai avec carte (décision PRICING.md 10/07) : le gratuit à vie tient
        # lieu d'essai. L'abonnement démarre et prélève tout de suite.
        "subscription_data": {"metadata": {"user_id": user.id, "plan_demande": plan or "mensuel"}},
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
def _canon(code: str) -> str:
    """Forme canonique d'un code : alphanumérique seul, en majuscules.
    Insensible à la casse, aux espaces ET aux séparateurs.
    "vip-0001", "VIP 0001", "VIP-0001", "VIP0001", "vip_0001" → tous "VIP0001"."""
    return re.sub(r"[^A-Z0-9]", "", (code or "").upper())


def _valid_promo(db: Session, code: str) -> PromoCode | None:
    canon = _canon(code)
    if not canon:
        return None
    # Table minuscule (quelques dizaines de codes) → comparaison sur forme canonique.
    for pc in db.query(PromoCode).filter(PromoCode.active == True).all():  # noqa: E712
        if _canon(pc.code) == canon:
            if pc.max_uses is not None and pc.times_used >= pc.max_uses:
                return None
            return pc
    return None


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

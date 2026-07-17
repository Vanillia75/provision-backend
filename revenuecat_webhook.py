# ════════════════════════════════════════════════════════════════════════
#  WEBHOOK REVENUECAT — le statut premium des caisses Apple et Google.
#
#  Principe (spec paiement in-app) : trois canaux (Stripe web, Apple, Google),
#  UN SEUL statut premium par compte, et le backend est le juge unique.
#  RevenueCat valide les reçus des stores et nous pousse chaque événement
#  (achat, renouvellement, annulation, expiration, remboursement...) ; nous ne
#  faisons que REFLÉTER cet état dans la table Subscription, comme pour Stripe.
#
#  Identité : l'app fait Purchases.logIn(user.id) → app_user_id = NOTRE user.id.
#  Les identifiants anonymes ($RCAnonymousID:...) sont ignorés (pas de compte).
#
#  Anti-écrasement : si le compte a DÉJÀ un abonnement Stripe actif, un événement
#  de store ne le remplace pas (l'app empêche de toute façon le double achat :
#  un premium voit « TOTOR Veille est actif », jamais un bouton d'achat).
# ════════════════════════════════════════════════════════════════════════
import os
from datetime import datetime

from sqlalchemy.orm import Session

from models import Subscription, User

# Secret partagé : à coller dans RevenueCat (Projet → Integrations → Webhooks →
# Authorization header). Toute requête sans ce header exact est refusée.
REVENUECAT_WEBHOOK_AUTH = os.environ.get("REVENUECAT_WEBHOOK_AUTH", "").strip()

# Événements qui DONNENT (ou confirment) l'accès.
EVENEMENTS_ACCORDANTS = {
    "INITIAL_PURCHASE", "RENEWAL", "UNCANCELLATION", "PRODUCT_CHANGE",
    "SUBSCRIPTION_EXTENDED", "NON_RENEWING_PURCHASE",
}
# Événements qui RETIRENT l'accès (tout de suite).
EVENEMENTS_RETIRANTS = {"EXPIRATION"}
# CANCELLATION = désabonnement (ou remboursement) : l'accès court jusqu'à
# expiration_at_ms ; on reflète la date et le drapeau, is_premium fait le reste.

_STORES = {"APP_STORE": "apple", "MAC_APP_STORE": "apple", "PLAY_STORE": "google"}


def verifier_auth(header_autorisation: str) -> bool:
    """Compare le header Authorization au secret partagé (configuré des deux côtés)."""
    return bool(REVENUECAT_WEBHOOK_AUTH) and header_autorisation == REVENUECAT_WEBHOOK_AUTH


def traiter_evenement(db: Session, payload: dict) -> dict:
    """Traite un événement webhook RevenueCat. Idempotent : on reflète un état."""
    event = (payload or {}).get("event") or {}
    type_evt = event.get("type", "")
    app_user_id = event.get("app_user_id") or ""

    # Utilisateur anonyme (achat avant login — ne doit pas arriver, l'app se
    # connecte à RevenueCat après l'auth) : on ignore proprement.
    if not app_user_id or app_user_id.startswith("$RCAnonymousID"):
        return {"ok": True, "ignore": "utilisateur_anonyme"}

    user = db.query(User).filter(User.id == app_user_id).first()
    if not user:
        # Compte supprimé entre-temps, ou id inconnu : on répond 200 pour que
        # RevenueCat ne réessaie pas en boucle, mais on le dit.
        return {"ok": True, "ignore": "utilisateur_inconnu"}

    source = _STORES.get(event.get("store", ""), None)
    if source is None:
        # STRIPE (si un jour branché côté RevenueCat) ou store inconnu : notre
        # webhook Stripe direct fait déjà foi, on ne double pas.
        return {"ok": True, "ignore": f"store_non_gere:{event.get('store')}"}

    # SANDBOX = achat de test des stores (reviewer Apple, TestFlight, tests
    # internes). Le premium est accordé quand même (le testeur doit voir l'app
    # débloquée), mais l'abonnement est marqué : il ne comptera jamais dans les
    # stats ni les places Pionnier, et ne déclenche pas d'alerte fondateur.
    sandbox = (event.get("environment") or "").upper() == "SANDBOX"

    exp_ms = event.get("expiration_at_ms")
    expiration = datetime.utcfromtimestamp(exp_ms / 1000.0) if exp_ms else None

    row = db.query(Subscription).filter(Subscription.user_id == user.id).first()
    if not row:
        row = Subscription(user_id=user.id, plan="free", source=source)
        db.add(row)

    # Un abonnement STRIPE actif ne se fait jamais écraser par un événement store.
    stripe_actif = (
        row.source == "stripe"
        and row.status in ("active", "trialing")
        and (row.current_period_end is None or row.current_period_end > datetime.utcnow())
    )
    if stripe_actif:
        return {"ok": True, "ignore": "abonnement_stripe_actif"}

    ancien_plan = row.plan

    if type_evt in EVENEMENTS_ACCORDANTS:
        row.plan = "premium"
        row.status = "active"
        row.source = source
        row.is_sandbox = sandbox
        row.current_period_end = expiration
        row.cancel_at_period_end = False
    elif type_evt == "CANCELLATION":
        # Désabonnement (ou remboursement) : le renouvellement est coupé.
        # S'il reste du temps payé, is_premium le respecte via current_period_end ;
        # un remboursement immédiat arrive avec une expiration déjà passée.
        row.source = source
        row.cancel_at_period_end = True
        if expiration is not None:
            row.current_period_end = expiration
        if expiration is not None and expiration <= datetime.utcnow():
            row.plan = "free"
            row.status = "canceled"
    elif type_evt in EVENEMENTS_RETIRANTS:
        row.plan = "free"
        row.status = "expired"
        row.source = source
        row.current_period_end = expiration
    elif type_evt == "BILLING_ISSUE":
        # Problème de carte : les stores ont leur période de grâce, on ne coupe
        # rien nous-mêmes ; l'EXPIRATION arrivera si rien n'est réglé.
        row.source = source
    else:
        # TEST, TRANSFER, etc. : rien à refléter.
        return {"ok": True, "ignore": f"evenement_non_gere:{type_evt}"}

    row.updated_at = datetime.utcnow()
    db.commit()

    # Nouvel abonné payant via un store : même alerte fondateur que Stripe.
    # JAMAIS pour un achat sandbox ni un compte de test maison : seuls les vrais
    # paiements en production comptent (et grignotent les places Pionnier).
    if ancien_plan != "premium" and row.plan == "premium" and not sandbox and not bool(getattr(user, "is_test", False)):
        try:
            from billing import compter_abonnes_payants
            from emailing import send_founder_subscriber_alert
            send_founder_subscriber_alert(compter_abonnes_payants(db), user.email)
        except Exception:
            pass

    return {"ok": True, "plan": row.plan, "source": row.source, "type": type_evt}

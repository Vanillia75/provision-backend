# ════════════════════════════════════════════════════════════════════════
#  QUOTAS FREEMIUM 1.0.1 — la carte officielle (CARTE_FREEMIUM.md) en code.
#
#  Principes (doctrine) :
#   • Le gratuit crée l'habitude, on ne fait jamais payer la donnée.
#   • ⭐ Le CHAT se compte par CONVERSATION (un fil ≈ 24h), JAMAIS par message :
#     les questions de précision de Totor ne consomment rien.
#   • 6 conversations offertes les 30 premiers jours, puis 3 par mois.
#   • Factures & devis : 5 CRÉATIONS par mois en gratuit — jamais rétroactif,
#     l'existant reste intact et consultable.
#   • Mode Achat : 5 simulations par mois en gratuit.
#   • Premium (TOTOR Veille) : tout illimité (seuls les garde-fous anti-abus
#     journaliers, côté coût, restent).
# ════════════════════════════════════════════════════════════════════════
import os
from datetime import date, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

import billing
from models import AIUsage, ClientInvoice, Quote, User

# Fenêtre d'un fil de conversation : un message envoyé moins de N heures après le
# précédent appartient AU MÊME fil (aucune consommation de quota).
CHAT_FIL_FENETRE_H = int(os.environ.get("CHAT_FIL_FENETRE_H", "24"))
FREE_CHAT_FILS_PREMIER_MOIS = int(os.environ.get("FREE_CHAT_FILS_PREMIER_MOIS", "6"))
FREE_CHAT_FILS_PAR_MOIS = int(os.environ.get("FREE_CHAT_FILS_PAR_MOIS", "3"))
FREE_FACTURES_PAR_MOIS = int(os.environ.get("FREE_FACTURES_PAR_MOIS", "5"))
FREE_DEVIS_PAR_MOIS = int(os.environ.get("FREE_DEVIS_PAR_MOIS", "5"))
FREE_ACHAT_SIMU_PAR_MOIS = int(os.environ.get("FREE_ACHAT_SIMU_PAR_MOIS", "5"))

# Type AIUsage dédié au comptage des FILS (les messages restent comptés sous
# "chat" pour le suivi de coût ; aucun changement de schéma).
TYPE_FIL = "chat_fil"
TYPE_ACHAT = "achat_simu"


def _erreur_premium(fonction: str, message: str) -> HTTPException:
    """Mur doux : le front attrape code=premium_requis et ouvre l'invitation Totor."""
    return HTTPException(status_code=402, detail={
        "code": "premium_requis", "fonction": fonction, "message": message,
    })


def _debut_mois(aujourdhui: date) -> date:
    return aujourdhui.replace(day=1)


def _somme_usage(db: Session, user_id: str, type_appel: str, depuis: date) -> int:
    total = (
        db.query(func.coalesce(func.sum(AIUsage.count), 0))
        .filter(AIUsage.user_id == user_id, AIUsage.type_appel == type_appel, AIUsage.jour >= depuis)
        .scalar()
    )
    return int(total or 0)


def _incrementer(db: Session, user_id: str, type_appel: str):
    aujourdhui = date.today()
    ligne = (
        db.query(AIUsage)
        .filter(AIUsage.user_id == user_id, AIUsage.jour == aujourdhui, AIUsage.type_appel == type_appel)
        .first()
    )
    if ligne:
        ligne.count = int(ligne.count) + 1
        ligne.updated_at = datetime.utcnow()
    else:
        db.add(AIUsage(user_id=user_id, jour=aujourdhui, type_appel=type_appel, count=1))
    db.commit()


# ────────────────────────────────────────────────────────────────────────
#  CHAT PAR CONVERSATION
# ────────────────────────────────────────────────────────────────────────
def _dernier_message_chat(db: Session, user_id: str):
    """Horodatage du dernier message de chat (updated_at de la dernière ligne 'chat')."""
    return (
        db.query(func.max(AIUsage.updated_at))
        .filter(AIUsage.user_id == user_id, AIUsage.type_appel == "chat")
        .scalar()
    )


def _premier_mois(user: User) -> bool:
    cree = getattr(user, "created_at", None)
    return bool(cree) and (datetime.utcnow() - cree) <= timedelta(days=30)


def etat_chat(db: Session, user: User) -> dict:
    """État du quota de conversations pour l'UI (et pour les tests)."""
    if _premier_mois(user):
        limite = FREE_CHAT_FILS_PREMIER_MOIS
        depuis = (user.created_at or datetime.utcnow()).date()
        periode = "premier_mois"
    else:
        limite = FREE_CHAT_FILS_PAR_MOIS
        depuis = _debut_mois(date.today())
        periode = "mensuel"
    utilises = _somme_usage(db, user.id, TYPE_FIL, depuis)
    dernier = _dernier_message_chat(db, user.id)
    fil_actif = bool(dernier) and (datetime.utcnow() - dernier) < timedelta(hours=CHAT_FIL_FENETRE_H)
    return {"limite": limite, "utilises": utilises, "fil_actif": fil_actif, "periode": periode}


def consommer_fil_chat(db: Session, user: User):
    """À appeler AVANT chaque message de chat d'un utilisateur GRATUIT.

    - Message dans un fil encore actif (< 24h depuis le dernier) : passe, gratuit.
    - Nouveau fil : consomme 1 conversation ; au-delà de la limite → 402 premium_requis.
    Les PREMIUM ne passent jamais ici (illimités).
    """
    etat = etat_chat(db, user)
    if etat["fil_actif"]:
        return  # même conversation : les allers-retours ne coûtent rien
    if etat["utilises"] >= etat["limite"]:
        raise _erreur_premium(
            "chat",
            "On a bien discuté ce mois-ci ! Pour continuer à me parler sans compter, "
            "laisse-moi veiller sur toi en illimité.",
        )
    _incrementer(db, user.id, TYPE_FIL)


# ────────────────────────────────────────────────────────────────────────
#  FACTURES & DEVIS : 5 créations par mois en gratuit (jamais rétroactif)
# ────────────────────────────────────────────────────────────────────────
def verifier_creation_document(db: Session, user: User, quel: str):
    """quel = "facture" | "devis". À appeler AVANT la création. Ne touche jamais
    aux documents existants (consultation, PDF, envoi de l'existant : intacts)."""
    if billing.is_premium(db, user):
        return
    debut = datetime.combine(_debut_mois(date.today()), datetime.min.time())
    if quel == "facture":
        n = db.query(ClientInvoice).filter(ClientInvoice.user_id == user.id, ClientInvoice.created_at >= debut).count()
        limite, fonction = FREE_FACTURES_PAR_MOIS, "facture_quota"
        message = ("Tu as créé tes 5 factures gratuites du mois, joli rythme ! "
                   "Laisse-moi m'occuper de ta facturation sans limite.")
    else:
        n = db.query(Quote).filter(Quote.user_id == user.id, Quote.created_at >= debut).count()
        limite, fonction = FREE_DEVIS_PAR_MOIS, "devis_quota"
        message = ("Tu as créé tes 5 devis gratuits du mois. "
                   "Laisse-moi m'occuper de tes devis sans limite.")
    if n >= limite:
        raise _erreur_premium(fonction, message)


# ────────────────────────────────────────────────────────────────────────
#  FONCTIONS D'INTELLIGENCE (Option A du 12/07) : « on fait payer
#  l'intelligence et la responsabilité retirée, jamais la donnée ».
#  Les jours COMPTÉS par employeur restent visibles pour tous (la donnée) ;
#  la surveillance (quota + alerte plafond) est TOTOR Veille.
# ────────────────────────────────────────────────────────────────────────
def verifier_surveillance_quotas_employeur(db: Session, user: User):
    """À appeler avant d'enregistrer un quota d'employeur (la surveillance)."""
    if billing.is_premium(db, user):
        return
    raise _erreur_premium(
        "quotas_employeur",
        "La surveillance de tes jours par employeur est une fonction TOTOR Veille. "
        "Je compte, je compare au plafond de chaque boîte, et je te préviens avant que ça coince. 🔓",
    )


def projection_verrouillee(db: Session, user: User) -> bool:
    """« Je regarde ton mois prochain » : True si le compte gratuit doit voir
    le teaser au lieu de la projection (drapeau doux, jamais une erreur)."""
    return not billing.is_premium(db, user)


# ────────────────────────────────────────────────────────────────────────
#  MODE ACHAT : 5 simulations par mois en gratuit
# ────────────────────────────────────────────────────────────────────────
def consommer_simulation_achat(db: Session, user: User) -> dict:
    """Compte une simulation du Mode Achat. Renvoie l'état pour l'UI."""
    if billing.is_premium(db, user):
        return {"illimite": True}
    debut = _debut_mois(date.today())
    utilisees = _somme_usage(db, user.id, TYPE_ACHAT, debut)
    if utilisees >= FREE_ACHAT_SIMU_PAR_MOIS:
        raise _erreur_premium(
            "achat_simu",
            "Tu as fait tes 5 simulations gratuites du mois. "
            "Laisse-moi répondre à « puis-je l'acheter ? » autant que tu veux.",
        )
    _incrementer(db, user.id, TYPE_ACHAT)
    return {"illimite": False, "utilisees": utilisees + 1, "limite": FREE_ACHAT_SIMU_PAR_MOIS}

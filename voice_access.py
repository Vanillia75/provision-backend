# -*- coding: utf-8 -*-
"""
voice_access.py — Contrôle d'accès de la secrétaire vocale (réservée aux abonnés).

Deux portes, dans cet ordre (cf. plan validé le 2026-07-20) :

  PLAN A — caller ID : au début de l'appel, on compare le numéro que Vapi
      transmet au numéro de facturation des abonnés (Profile.telephone).
      Si un abonné ACTIF a ce numéro, on continue sans rien demander.

  PLAN B — code à 6 chiffres : si le numéro ne matche pas, l'appelant tape le
      code affiché dans l'app (DTMF). Le code n'est PAS stocké : il est CALCULÉ
      (HMAC de l'id abonné + le jour), donc il tourne chaque jour tout seul et
      n'exige aucune migration de base. Verrou après 3 essais ratés par appel.

Si aucune des deux ne passe : la secrétaire explique que le service est réservé
aux abonnés et termine l'appel (message renvoyé au modèle Vapi).

Aucune donnée perso n'est divulguée ici : on renvoie seulement un statut d'accès
(sous forme de message que le modèle vocal interprète).
"""
import hmac
import hashlib
import re
import time
from datetime import date, timedelta

from sqlalchemy.orm import Session

import billing
from auth import JWT_SECRET
from models import User, Profile, Subscription

CODE_DIGITS = 6
_MOD = 10 ** CODE_DIGITS

# Anti-force-brute : nb d'essais de code autorisés par appel, puis verrou.
_MAX_ESSAIS = 3
_TENTATIVES: dict = {}     # call_id -> {"n": int, "t": float(epoch)}
_TENTATIVES_TTL = 3600     # on oublie un appel au bout d'1 h


# ── Le code du jour ────────────────────────────────────────────────────────
def code_du_jour(user_id: str, jour: date = None) -> str:
    """Code à 6 chiffres, stable sur une journée, dérivé de l'id abonné.
    Jamais stocké : recalculé à l'identique côté app et côté vérification."""
    jour = jour or date.today()
    msg = f"voice-code|{user_id}|{jour.isoformat()}".encode()
    digest = hmac.new(JWT_SECRET.encode(), msg, hashlib.sha256).digest()
    n = int.from_bytes(digest[:8], "big") % _MOD
    return str(n).zfill(CODE_DIGITS)


# ── Normalisation d'un numéro FR (pour comparer caller ID ↔ saisie libre) ───
def normaliser_tel(s: str) -> str:
    """Réduit un numéro à ses 9 chiffres nationaux, quel que soit le format
    (« +33 6… », « 0033… », « 06 12 34 56 78 »)."""
    if not s:
        return ""
    d = re.sub(r"\D", "", s)
    if d.startswith("0033"):
        d = d[4:]
    elif d.startswith("33") and len(d) > 9:
        d = d[2:]
    d = d.lstrip("0")
    return d[-9:]


def _abonnes_actifs(db: Session):
    """Utilisateurs ayant un abonnement premium RÉELLEMENT actif (via billing.is_premium)."""
    subs = db.query(Subscription).filter(Subscription.plan == "premium").all()
    actifs = []
    for s in subs:
        u = db.query(User).filter(User.id == s.user_id).first()
        if u and billing.is_premium(db, u):
            actifs.append(u)
    return actifs


# ── PLAN A ─────────────────────────────────────────────────────────────────
def verifier_abonnement(db: Session, telephone: str) -> str:
    """Compare le numéro de l'appelant aux abonnés actifs. Renvoie un message
    que le modèle vocal interprète (jeton en MAJUSCULES en tête)."""
    tel = normaliser_tel(telephone)
    if len(tel) < 6:
        return ("NUMERO_INCONNU. Je n'ai pas ton numéro. Demande poliment le code à "
                "six chiffres affiché dans l'application TOTOR, à taper sur le clavier.")
    for p in db.query(Profile).filter(Profile.telephone.isnot(None)).all():
        if normaliser_tel(p.telephone) == tel:
            u = db.query(User).filter(User.id == p.user_id).first()
            if u and billing.is_premium(db, u):
                return ("ABONNE_ACTIF. Ce numéro est celui d'un abonné à jour. Continue "
                        "normalement, ne demande AUCUN code, accueille chaleureusement.")
    return ("NUMERO_NON_ABONNE. Ce numéro ne correspond pas à un abonné. Demande poliment "
            "le code à six chiffres affiché dans l'application TOTOR, à taper sur le clavier.")


# ── PLAN B ─────────────────────────────────────────────────────────────────
def _purge():
    now = time.time()
    for k in [k for k, v in _TENTATIVES.items() if now - v["t"] > _TENTATIVES_TTL]:
        _TENTATIVES.pop(k, None)


def verifier_code(db: Session, code: str, call_id: str = None) -> str:
    """Vérifie le code tapé pour l'appel en cours. Verrou après 3 essais ratés."""
    _purge()
    key = call_id or "sans-appel"
    st = _TENTATIVES.get(key) or {"n": 0, "t": time.time()}
    if st["n"] >= _MAX_ESSAIS:
        return ("BLOQUE. Trop d'essais. Explique poliment que la secrétaire vocale est "
                "réservée aux abonnés TOTOR, invite à s'abonner dans l'application, puis "
                "termine l'appel.")

    code = re.sub(r"\D", "", code or "")
    hier = date.today() - timedelta(days=1)  # tolérance au passage de minuit

    def _echec(msg_tete):
        st["n"] += 1
        st["t"] = time.time()
        _TENTATIVES[key] = st
        restants = max(0, _MAX_ESSAIS - st["n"])
        if restants == 0:
            return ("BLOQUE. Trop d'essais. Explique poliment que la secrétaire vocale est "
                    "réservée aux abonnés TOTOR, invite à s'abonner dans l'application, puis "
                    "termine l'appel.")
        essais = "essai" if restants == 1 else "essais"
        return f"{msg_tete} Il reste {restants} {essais}. Redemande le code à six chiffres."

    if len(code) != CODE_DIGITS:
        return _echec("CODE_MAL_FORME. Le code doit faire six chiffres.")

    for u in _abonnes_actifs(db):
        if code in (code_du_jour(u.id), code_du_jour(u.id, hier)):
            _TENTATIVES.pop(key, None)
            return ("CODE_VALIDE. Abonné confirmé. Continue normalement, accueille "
                    "chaleureusement.")

    return _echec("CODE_INVALIDE.")

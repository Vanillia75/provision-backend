# -*- coding: utf-8 -*-
"""Règles pures de la connexion bancaire (sans dépendance base/API) — testables seules.

Constat réel (test du 09/07/2026 sur comptes BRED via Enable Banking) : un compte
peut renvoyer PLUSIEURS soldes (types et dates différents). Règle : on affiche le
solde comptable (CLBD/closingBooked) le plus récent ; à défaut le solde daté le
plus récent."""


def choisir_solde(balances: list):
    """Choisit LE solde à afficher parmi les soldes renvoyés. None si illisible."""
    if not balances:
        return None

    def cle(s):
        est_clbd = 1 if (s.get("balance_type") or "").upper() in ("CLBD", "CLOSINGBOOKED") else 0
        date_ref = s.get("reference_date") or s.get("last_change_date_time") or ""
        return (est_clbd, str(date_ref))

    meilleur = max(balances, key=cle)
    montant = (meilleur.get("balance_amount") or {}).get("amount")
    try:
        return float(montant)
    except (TypeError, ValueError):
        return None


def choisir_compte(accounts: list):
    """Choisit le compte principal d'une session : compte courant (CACC) d'abord,
    sinon le premier. None si la liste est vide."""
    if not accounts:
        return None
    courants = [a for a in accounts if (a.get("cash_account_type") or "").upper() == "CACC"]
    return (courants or accounts)[0]

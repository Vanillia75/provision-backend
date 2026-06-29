"""
Mentions légales centralisées pour la facturation (factures + devis).

Objectif : UN seul endroit pour les mentions réglementaires, afin d'éviter
toute divergence entre le PDF, l'email facture et l'email devis, et de
faciliter les mises à jour à la source.

⚠️ Ce module est PUR (aucun import du moteur fiscal, ni de la base) : il ne
fait AUCUN calcul de TVA et ne déplace aucune logique fiscale existante.
"""

import re
from datetime import date
from typing import Optional

# Modes de TVA côté facturation (table fiscal_settings).
FRANCHISE = "franchise"
ASSUJETTI = "assujetti"


def get_franchise_vat_mention(invoice_date: Optional[date] = None) -> str:
    """
    Mention TVA pour un émetteur en franchise en base de TVA (cas par défaut
    de l'auto-entrepreneur sous les seuils). Ne calcule aucune TVA.

    `invoice_date` est accepté dès maintenant (signature stable) car une
    éventuelle bascule de référence légale dépendra de la date d'émission.
    """
    # TODO (avant 2026-09-01) : vérifier à la source officielle (BOFiP /
    # Service Public) si l'art. 293 B du CGI bascule vers l'art. L.223-3 du
    # CIBS. Ne rien changer sans source.
    return "TVA non applicable, art. 293 B du CGI"


def append_ei_mention(nom: Optional[str], statut: Optional[str]) -> Optional[str]:
    """
    Suffixe « – EI » (Entrepreneur Individuel) au nom de l'émetteur : mention
    légale obligatoire depuis 2022 pour les entrepreneurs individuels, dont les
    auto-entrepreneurs.

    - Ne s'applique qu'aux entrepreneurs individuels (statut auto_entrepreneur),
      jamais aux sociétés.
    - Préserve la valeur existante (nom commercial OU « Prénom Nom »).
    - Ne duplique pas la mention si elle est déjà présente.
    """
    if not nom or statut != "auto_entrepreneur":
        return nom
    if re.search(r"\bEI\b\s*$", nom.strip()):
        return nom
    return f"{nom} – EI"


def resolve_fiscal_settings(row) -> dict:
    """
    Lecture tolérante des paramètres fiscaux de facturation, AVEC FALLBACK.

    `row` : une instance de models.FiscalSettings, ou None.
    Si None (compte existant sans ligne fiscal_settings), on considère
    l'émetteur en FRANCHISE en base de TVA — c'est le comportement historique
    par défaut. Aucun calcul n'est déclenché ici.
    """
    if row is None:
        return {"vat_mode": FRANCHISE, "vat_rate": 20.0, "vat_number": None}
    return {
        "vat_mode": row.vat_mode or FRANCHISE,
        "vat_rate": row.vat_rate if row.vat_rate is not None else 20.0,
        "vat_number": row.vat_number,
    }

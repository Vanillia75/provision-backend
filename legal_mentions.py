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

# Modes de TVA côté facturation.
# - fiscal_settings (réglage global de l'émetteur) : FRANCHISE ou ASSUJETTI uniquement.
# - Snapshot FACTURE/DEVIS (vat_mode figé sur le document) : peut aussi valoir
#   ASSUJETTI_UE / ASSUJETTI_EXPORT — le cas « client professionnel à l'étranger »
#   se décide document par document, jamais dans les réglages.
FRANCHISE = "franchise"
ASSUJETTI = "assujetti"
ASSUJETTI_UE = "assujetti_ue"          # client pro dans l'UE (hors France) : autoliquidation
ASSUJETTI_EXPORT = "assujetti_export"  # client pro hors UE : prestation hors champ français

# Sources vérifiées le 08/07/2026 (impots.gouv.fr « Prestations entre assujettis »,
# entreprendre.service-public.gouv.fr F37527, BOFiP BOI-TVA-DECLA-30-20-20-30) :
# prestations de services B2B → lieu = pays du preneur (art. 259-1 du CGI), donc
# facture SANS TVA française. Client UE : mention « Autoliquidation » obligatoire
# (CGI ann. II, art. 242 nonies A, I-13°) + n° TVA des DEUX parties + DES à déposer.
# Client hors UE : la seule mention 259-1 suffit. Les VENTES DE BIENS à l'étranger
# (262 ter I / 262 I) sont un autre sujet, hors périmètre ici (nos AE vendent des services).
MENTION_HORS_FRANCE = "TVA non applicable, art. 259-1 du CGI"
MENTION_AUTOLIQUIDATION = "Autoliquidation"


def get_franchise_vat_mention(invoice_date: Optional[date] = None) -> str:
    """
    Mention TVA pour un émetteur en franchise en base de TVA (cas par défaut
    de l'auto-entrepreneur sous les seuils). Ne calcule aucune TVA.

    `invoice_date` est accepté dès maintenant (signature stable) car une
    éventuelle bascule de référence légale dépendra de la date d'émission.
    """
    # Vérifié le 2026-07-10 : la mention « TVA non applicable, art. 293 B du CGI »
    # reste la référence obligatoire en 2026 (aucune bascule vers le CIBS pour
    # la mention). À re-vérifier lors du rituel de janvier avec les autres taux.
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
        return {"vat_mode": FRANCHISE, "vat_rate": 20.0, "vat_number": None, "facture_numero_depart": None}
    return {
        "vat_mode": row.vat_mode or FRANCHISE,
        "vat_rate": row.vat_rate if row.vat_rate is not None else 20.0,
        "vat_number": row.vat_number,
        # getattr : `row` peut être une facture (snapshot) sans ce champ → None, sans planter.
        "facture_numero_depart": getattr(row, "facture_numero_depart", None),
    }


def format_vat_rate(rate) -> str:
    """Formate un taux de TVA pour l'affichage : 20.0 -> '20', 5.5 -> '5,5'."""
    s = f"{float(rate):.2f}".rstrip("0").rstrip(".")
    return s.replace(".", ",")


def compute_invoice_totals(montant_ht, fiscal, invoice_date=None) -> dict:
    """
    Totaux d'AFFICHAGE d'une facture/devis à partir du montant HT (= Σ quantité ×
    prix_unitaire, jamais modifié, jamais le TTC). UNE seule logique : la franchise
    est le cas particulier à taux 0 (HT = TTC), l'assujetti applique vat_rate.

    ⚠️ Purement pour l'affichage (PDF / email / UI). Ne touche pas `montant` en base,
    donc n'a AUCUN effet sur le CA URSSAF (/estimate lit `montant` = HT).

    Retourne : {mode, ht, rate, tva, ttc, vat_number, mention}
    - franchise : tva=0, ttc=ht, mention 293 B, pas de n° TVA.
    - assujetti : tva = ht × rate/100, ttc = ht + tva, n° TVA émetteur, PAS de mention 293 B.
    """
    ht = round(float(montant_ht or 0), 2)
    fiscal = fiscal or {}
    mode = fiscal.get("vat_mode")
    if mode == ASSUJETTI:
        rate = fiscal.get("vat_rate")
        rate = 20.0 if rate is None else float(rate)
        tva = round(ht * rate / 100.0, 2)
        return {
            "mode": ASSUJETTI, "ht": ht, "rate": rate,
            "tva": tva, "ttc": round(ht + tva, 2),
            "vat_number": fiscal.get("vat_number"),
            "mention": None,
        }
    if mode in (ASSUJETTI_UE, ASSUJETTI_EXPORT):
        # Client professionnel à l'étranger : 0 % de TVA française, HT = TTC.
        # Le n° TVA de l'ÉMETTEUR reste affiché (obligatoire pour l'autoliquidation).
        mention = MENTION_HORS_FRANCE
        if mode == ASSUJETTI_UE:
            mention = f"{MENTION_HORS_FRANCE} · {MENTION_AUTOLIQUIDATION}"
        return {
            "mode": mode, "ht": ht, "rate": 0.0,
            "tva": 0.0, "ttc": ht,
            "vat_number": fiscal.get("vat_number"),
            "mention": mention,
        }
    return {
        "mode": FRANCHISE, "ht": ht, "rate": 0.0,
        "tva": 0.0, "ttc": ht,
        "vat_number": None,
        "mention": get_franchise_vat_mention(invoice_date),
    }

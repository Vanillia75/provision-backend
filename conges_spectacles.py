"""
conges_spectacles.py — Estimation de l'indemnité Congés Spectacles (Audiens).

RÈGLE : l'indemnité (ICP) = 10 % des salaires BRUTS cumulés sur l'exercice
(1er avril → 31 mars). Backtestée au centime sur 2 bordereaux Audiens réels
(2023-2024 et 2024-2025). Le net social ≈ 76,95 % du brut (péremption annuelle).
Cf. CONGES_SPECTACLES_ETUDE.md.

Loi X : c'est un montant en € → ESTIMATION. Le brut est backtesté ; le net est une
approximation prudente. L'assiette dépend de la complétude des bruts saisis
(seules les AEM scannées ou saisies avec montant comptent) → on signale l'incomplétude.

Fonctions pures, aucune lecture DB. Travaille sur des objets ayant .date,
.type_activite et .salaire_brut (les lignes IntermittentActivity conviennent).
"""
from datetime import date

from regles_intermittent import valeur_de

TAUX_ICP = valeur_de("congesSpectaclesTaux")              # 0.10
RATIO_NET = valeur_de("congesSpectaclesRatioNetSocial")  # 0.7695

# Seules les activités de TRAVAIL portent un salaire brut (les arrêts, la formation
# et l'enseignement sont assimilés : aucun salaire → hors assiette Congés Spectacles).
TYPES_TRAVAIL = ("cachet_isole", "cachet_groupe", "cachet", "heures")


def exercice_en_cours(d: date) -> tuple:
    """Exercice Congés Spectacles (1er avril → 31 mars) contenant la date `d`."""
    if d.month >= 4:
        return date(d.year, 4, 1), date(d.year + 1, 3, 31)
    return date(d.year - 1, 4, 1), date(d.year, 3, 31)


def calculer(activites: list, debut: date, fin: date) -> dict:
    """
    Somme les salaires bruts des activités de travail dans [debut, fin],
    applique 10 % (ICP brut) puis le ratio net social. Signale l'incomplétude
    (activités de travail sans salaire_brut renseigné → assiette sous-estimée).
    """
    assiette = 0.0
    sans_brut = 0
    for a in activites:
        d = getattr(a, "date", None)
        if d is None or d < debut or d > fin:
            continue
        if getattr(a, "type_activite", "") not in TYPES_TRAVAIL:
            continue
        brut = getattr(a, "salaire_brut", None)
        if brut is None:
            sans_brut += 1
            continue
        assiette += max(0.0, float(brut))

    icp_brut = round(assiette * TAUX_ICP, 2)
    icp_net = round(icp_brut * RATIO_NET, 2)
    return {
        "exercice_debut": debut,
        "exercice_fin": fin,
        "assiette": round(assiette, 2),
        "icp_brut": icp_brut,
        "icp_net": icp_net,
        "assiette_incomplete": sans_brut > 0,
        "activites_sans_brut": sans_brut,
        "estimation": True,
    }

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

# ─── PLAFOND CONVENTIONNEL DE L'ASSIETTE (ajouté le 15/08/2026) ──────────────
#  Article D.7121-37 du code du travail : les partenaires sociaux d'une branche
#  peuvent limiter la base de calcul de la cotisation Congés Spectacles à un
#  plafond d'indemnité JOURNALIÈRE. Fiche Audiens « Plafonds conventionnels des
#  congés payés des artistes et techniciens intermittents du spectacle ».
#  Plafonds en vigueur : 272 €/jour pour les entreprises au service de la
#  création et de l'événement (spectacle vivant et événement, de très loin le
#  cas le plus courant) ; 375 € pour metteur en scène, chorégraphe et maître de
#  ballet ; 860 € pour chef d'orchestre et concertiste soliste.
#  ⚠️ On ne connaît PAS la catégorie professionnelle de la personne. On applique
#  donc le plafond GÉNÉRAL, le plus bas : l'estimation peut être inférieure à ce
#  qu'elle touchera, jamais supérieure. C'est le bon sens d'erreur pour un
#  montant qu'on annonce. Quand le plafond mord, on le SIGNALE.
#  Le backtest au centime sur deux bordereaux Audiens réels reste valide : ces
#  deux cas étaient sous le plafond, qui ne mord qu'au-delà de 272 € par jour.
PLAFOND_JOURNALIER_GENERAL = 272.0


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
    plafond_applique = False
    ecarte_par_plafond = 0.0
    for a in activites:
        d = getattr(a, "date", None)
        if d is None or d < debut or d > fin:
            continue
        t = getattr(a, "type_activite", "")
        if t not in TYPES_TRAVAIL:
            continue
        brut = getattr(a, "salaire_brut", None)
        if brut is None:
            sans_brut += 1
            continue
        brut = max(0.0, float(brut))

        # ⚠️ PLAFOND CONVENTIONNEL (ajouté le 15/08/2026, trouvé par l'audit).
        #  L'assiette de la cotisation Congés Spectacles est PLAFONNÉE par jour
        #  (article D.7121-37 du code du travail, fiche Audiens « Plafonds
        #  conventionnels des congés payés »). On sommait le brut intégral, donc
        #  on surestimait l'indemnité des artistes très bien payés.
        #  On ne l'applique QUE sur les cachets, seuls cas où le montant par jour
        #  se déduit sans supposition (brut ÷ nombre de cachets).
        nb = float(getattr(a, "nombre", 0) or 0)
        if t in ("cachet_isole", "cachet_groupe", "cachet") and nb > 0:
            par_jour = brut / nb
            if par_jour > PLAFOND_JOURNALIER_GENERAL:
                retenu = PLAFOND_JOURNALIER_GENERAL * nb
                ecarte_par_plafond += brut - retenu
                plafond_applique = True
                brut = retenu
        assiette += brut

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
        # Vrai quand le plafond conventionnel a rogné l'assiette. L'écran doit
        # alors dire que le montant réel peut être PLUS élevé, parce que le
        # plafond dépend de la catégorie (272 € en général, 375 € pour un metteur
        # en scène, 860 € pour un chef d'orchestre) et qu'on ne la connaît pas.
        "plafond_journalier_applique": plafond_applique,
        "brut_ecarte_par_plafond": round(ecarte_par_plafond, 2),
        "estimation": True,
    }

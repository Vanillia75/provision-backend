"""
allocation_engine.py — Moteur de l'allocation journalière (ARE annexes 8 et 10).

═══════════════════════════════════════════════════════════════════════════════
 RÈGLE D'OR : ce moteur donne une ESTIMATION indicative, jamais le chiffre
 officiel — seul France Travail notifie des droits. Il applique les formules
 publiées (cf. MOTEUR_AJ_SOURCES.md, sources tracées dans regles_intermittent.py)
 et, dès qu'une branche non validée par un cas réel est empruntée (CSG au-delà
 de 60 €, arrondis fractionnaires du mois), il POSE UN DRAPEAU au lieu d'affirmer.

 Validation : exemples officiels 6 et 12 du guide France Travail + BACKTEST RÉEL
 n°1 du 2026-07-03 (annexe 10 : 51,18 € nets calculés = 51,18 € notifiés, 0,00 €
 d'écart). Cf. test_allocation.py — écrit AVANT ce fichier.

 Comme intermittent_engine.py : aucune lecture de base de données, fonctions
 pures, testables isolément. Tous les paramètres viennent du référentiel central.
═══════════════════════════════════════════════════════════════════════════════
"""
import math

from regles_intermittent import valeur_de

AJ_MIN = valeur_de("ajMinimale")                       # 31,96 €
PLAFOND_AJ = valeur_de("allocationPlafondAJ")          # 174,80 €
_RETENUE = valeur_de("allocationRetenueRetraiteComp")  # {taux, seuilExoneration, seuilCsg}
_CSG = valeur_de("allocationCsgCrds")                  # {csgPlein, csgReduit, crds, assiette}
_PMSS = valeur_de("pmssMensuel")                       # {montant, annee, coefPlafondCumul}

_PARAMS = {
    "annexe8": valeur_de("allocationParametresAnnexe8"),
    "annexe10": valeur_de("allocationParametresAnnexe10"),
}

AVERTISSEMENT = (
    "Estimation indicative basée sur les formules publiées par France Travail. "
    "Seule la notification officielle de France Travail fait foi."
)


def _params(annexe: str) -> dict:
    p = _PARAMS.get(annexe)
    if p is None:
        raise ValueError(f"Annexe inconnue : {annexe!r} (attendu 'annexe8' ou 'annexe10')")
    return p


# ─────────────────────────────────────────────────────────────────────────────
#  LOI X — DROIT D'AFFICHER UN MONTANT.
#  Un montant en euros ne peut être MONTRÉ à l'utilisateur que si la branche de
#  calcul empruntée a été validée sur un cas réel documenté (une vraie notification
#  France Travail comparée au calcul d'Hector — registre : MOTEUR_AJ_SOURCES.md §6).
#  Aujourd'hui, une seule branche est validée : annexe 10 (artistes), AJ ≤ 60 €
#  (backtest n°1, 2026-07-03, 0,00 € d'écart). Tout le reste — annexe 8, AJ > 60 €
#  (donc CSG, dont l'assiette n'est pas confirmée) — reste CALCULÉ en interne mais
#  JAMAIS affiché. Le moteur calcule ; la Loi décide de ce qu'on montre.
# ─────────────────────────────────────────────────────────────────────────────
BRANCHE_VALIDEE_AJ_MAX_ANNEXE10 = 60.0  # au-delà, la CSG entre en jeu (non validée)


def branche_affichable(annexe: str, resultat: dict) -> tuple:
    """
    Retourne (affichable: bool, raison: str|None).
    raison explique, quand ce n'est PAS affichable, pourquoi — pour qu'Hector puisse
    dire honnêtement « je préfère ne pas te donner de chiffre » plutôt qu'approximer.
    """
    if annexe != "annexe10":
        return (False, "technicien")  # annexe 8 : aucune notification réelle ne l'a encore jugée
    if resultat.get("nette_estimee") or resultat["aj_brute"] > BRANCHE_VALIDEE_AJ_MAX_ANNEXE10:
        return (False, "au_dela_60")  # CSG en jeu : assiette non validée sur cas réel
    return (True, None)


# ─────────────────────────────────────────────────────────────────────────────
#  L'ALLOCATION JOURNALIÈRE : AJ = A + B + C, puis brut → net.
#  Schéma d'arrondi de France Travail (décodé sur l'exemple officiel 6 ET le
#  backtest réel n°1) : chaque partie A/B/C est TRONQUÉE au centime (39,8094 →
#  39,80 et non 39,81), tandis que la retenue retraite est ARRONDIE au centime
#  (1,2483 → 1,25, confirmé par le net officiel 51,18 €).
# ─────────────────────────────────────────────────────────────────────────────
def _tronque(x: float) -> float:
    """Troncature au centime (pas d'arrondi) — schéma des parties A, B, C."""
    return math.floor(x * 100 + 1e-9) / 100
def calculer_aj(annexe: str, sr: float, nht: float) -> dict:
    """
    annexe : "annexe8" (techniciens) ou "annexe10" (artistes).
    sr     : salaire de référence (salaires bruts annexes 8/10 de la période).
    nht    : heures travaillées retenues (SANS les heures assimilées formation/
             enseignement : elles comptent pour les 507h mais pas pour le montant).
    """
    p = _params(annexe)
    sr = max(0.0, float(sr or 0))
    nht = max(0.0, float(nht or 0))

    partie_a = _tronque(AJ_MIN * (p["coefSR"] * min(sr, p["plafondSR"])
                                  + p["coefSRAuDela"] * max(0.0, sr - p["plafondSR"])) / p["diviseurA"])
    partie_b = _tronque(AJ_MIN * (p["coefNHT"] * min(nht, p["seuilNHT"])
                                  + p["coefNHTAuDela"] * max(0.0, nht - p["seuilNHT"])) / p["diviseurB"])
    partie_c = _tronque(AJ_MIN * p["coefC"])

    brute = round(partie_a + partie_b + partie_c, 2)
    plancher_applique = brute < p["plancherAJ"]
    if plancher_applique:
        brute = p["plancherAJ"]
    plafond_applique = brute > PLAFOND_AJ
    if plafond_applique:
        brute = PLAFOND_AJ

    # Salaire journalier moyen — sert à la retenue retraite complémentaire.
    sjm = round(sr / (nht / p["diviseurSJM"]), 2) if nht > 0 else 0.0

    # Brut → net. La branche CSG (> 60 €) n'est pas validée par un cas réel :
    # le net est alors marqué comme estimation (le moteur n'affirme pas).
    retenue_retraite = 0.0
    retenue_csg_crds = 0.0
    nette_estimee = False
    if brute > _RETENUE["seuilExoneration"]:
        retenue_retraite = round(_RETENUE["taux"] * sjm, 2)
    if brute > _RETENUE["seuilCsg"]:
        taux_csg_crds = _CSG["csgPlein"] + _CSG["crds"]
        retenue_csg_crds = round(brute * _CSG["assiette"] * taux_csg_crds, 2)
        nette_estimee = True

    nette = round(brute - retenue_retraite - retenue_csg_crds, 2)

    return {
        "annexe": annexe,
        "partie_a": partie_a,
        "partie_b": partie_b,
        "partie_c": partie_c,
        "aj_brute": brute,
        "plancher_applique": plancher_applique,
        "plafond_applique": plafond_applique,
        "sjm": sjm,
        "retenue_retraite": retenue_retraite,
        "retenue_csg_crds": retenue_csg_crds,
        "aj_nette": nette,
        "nette_estimee": nette_estimee,
        "avertissement": AVERTISSEMENT,
    }


# ─────────────────────────────────────────────────────────────────────────────
#  LE MOIS TYPE : combien de jours sont payés ce mois-ci.
#  Guide France Travail p.16-17 (exemple 12 vérifié) :
#    jours travaillés = heures du mois / 8 (A8) ou / 10 (A10) — cachet = 12h ;
#    seuil de non-indemnisation : 26 j (A8) / 27 j (A10) ;
#    jours non indemnisables = jours travaillés × 1,4 (A8) ou × 1,3 (A10) ;
#    ARE du mois = AJ brute × jours indemnisables, plafonnée : ARE + salaires
#    bruts ≤ 118 % du PMSS.
# ─────────────────────────────────────────────────────────────────────────────
def calculer_mois(
    annexe: str,
    aj_brute: float,
    heures_mois: float,
    remunerations_brutes: float,
    jours_calendaires: int,
    pmss_mensuel: float = None,
) -> dict:
    p = _params(annexe)
    heures_mois = max(0.0, float(heures_mois or 0))
    remunerations_brutes = max(0.0, float(remunerations_brutes or 0))

    if pmss_mensuel is None:
        pmss_mensuel = _PMSS["montant"]
    plafond_cumul = round(pmss_mensuel * _PMSS["coefPlafondCumul"], 2)

    # Jours travaillés du mois (le résultat officiel est "arrondi au nombre entier
    # obtenu" ; l'arrondi exact des cas fractionnaires n'est pas confirmé par un
    # cas réel → drapeau d'approximation dès qu'un chiffre ne tombe pas juste).
    jt_brut = heures_mois / p["diviseurSJM"]
    jours_travailles = int(math.floor(jt_brut + 0.5))
    jni_brut = jt_brut * p["coefDecalage"]
    jours_non_indemnisables = int(math.floor(jni_brut + 0.5))
    arrondi_approximatif = (jt_brut != int(jt_brut)) or (jni_brut != int(jni_brut))

    seuil_atteint = jours_travailles >= p["seuilJoursMois"]

    if seuil_atteint:
        return {
            "annexe": annexe,
            "jours_travailles": jours_travailles,
            "seuil_atteint": True,
            "jours_non_indemnisables": jours_non_indemnisables,
            "jours_indemnisables": 0,
            "are_avant_plafond": 0.0,
            "plafond_cumul": plafond_cumul,
            "plafond_cumul_applique": False,
            "are_versee": 0.0,
            "arrondi_approximatif": arrondi_approximatif,
            "avertissement": AVERTISSEMENT,
        }

    jours_indemnisables = max(0, int(jours_calendaires) - jours_non_indemnisables)
    are_avant_plafond = round(aj_brute * jours_indemnisables, 2)

    plafond_cumul_applique = False
    are_versee = are_avant_plafond
    if remunerations_brutes >= plafond_cumul:
        # Les salaires seuls dépassent le plafond : aucune indemnisation.
        plafond_cumul_applique = True
        are_versee = 0.0
    elif remunerations_brutes + are_avant_plafond > plafond_cumul:
        plafond_cumul_applique = True
        are_versee = round(plafond_cumul - remunerations_brutes, 2)

    return {
        "annexe": annexe,
        "jours_travailles": jours_travailles,
        "seuil_atteint": False,
        "jours_non_indemnisables": jours_non_indemnisables,
        "jours_indemnisables": jours_indemnisables,
        "are_avant_plafond": are_avant_plafond,
        "plafond_cumul": plafond_cumul,
        "plafond_cumul_applique": plafond_cumul_applique,
        "are_versee": are_versee,
        "arrondi_approximatif": arrondi_approximatif,
        "avertissement": AVERTISSEMENT,
    }

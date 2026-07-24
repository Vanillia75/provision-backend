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
#  France Travail comparée au calcul de Totor — registre : MOTEUR_AJ_SOURCES.md §6).
#  Aujourd'hui, une seule branche est validée : annexe 10 (artistes), AJ ≤ 60 €
#  (backtest n°1, 2026-07-03, 0,00 € d'écart). Tout le reste — annexe 8, AJ > 60 €
#  (donc CSG, dont l'assiette n'est pas confirmée) — reste CALCULÉ en interne mais
#  JAMAIS affiché. Le moteur calcule ; la Loi décide de ce qu'on montre.
# ─────────────────────────────────────────────────────────────────────────────
BRANCHE_VALIDEE_AJ_MAX_ANNEXE10 = 60.0  # au-delà, la CSG entre en jeu (non validée)


def branche_affichable(annexe: str, resultat: dict) -> tuple:
    """
    Retourne (affichable: bool, raison: str|None).
    raison explique, quand ce n'est PAS affichable, pourquoi — pour que Totor puisse
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
#  PROJECTION AU PROCHAIN RENOUVELLEMENT (demande testeuse 23/07/2026)
#  À partir des activités DÉJÀ déclarées de la fenêtre de référence, on projette
#  l'AJ que donnerait la formule si le dossier était examiné tel quel, et une
#  courbe « et si j'ajoute N cachets » (au cachet moyen RÉEL de l'utilisateur).
#  Loi X : fonction PRUDENTE — pas de chiffre si les bruts sont trop incomplets,
#  annexe indéterminée → estimation BASSE des deux annexes, jamais l'inverse.
# ─────────────────────────────────────────────────────────────────────────────
_TYPES_TRAVAIL = ("heures", "cachet_isole", "cachet_groupe", "cachet")
_COMPLETUDE_MIN = 80  # % d'heures couvertes par un brut, en dessous on refuse de projeter


def projeter_renouvellement(activites: list, fin, cachets_sup: int = 0, brut_cachet=None) -> dict:
    """
    activites : liste de dicts {date (date), type_activite, nombre, salaire_brut, metier}.
    fin       : fin de la fenêtre de référence (date anniversaire si connue et à venir,
                sinon aujourd'hui). La fenêtre = les 365 jours qui la précèdent.
    cachets_sup / brut_cachet : simulation optionnelle « et si j'ajoute N cachets à
                X € chacun ? » (X = cachet moyen réel si absent). Le bloc `simulation`
                de la réponse obéit à la MÊME Loi X : hors branche validée, pas de
                chiffre, une raison honnête.

    Seules les heures TRAVAILLÉES entrent dans le montant (formation/enseignement/
    arrêts comptent pour les 507h mais PAS pour l'AJ — docstring de calculer_aj).
    """
    from datetime import timedelta

    debut = fin - timedelta(days=365)
    sel = [
        a for a in activites
        if a.get("date") and debut <= a["date"] <= fin and a.get("type_activite") in _TYPES_TRAVAIL
    ]

    def _h(a):
        n = max(0.0, float(a.get("nombre") or 0))
        return n if a["type_activite"] == "heures" else n * 12.0

    nht = sum(_h(a) for a in sel)
    if nht <= 0:
        return {"ok": False, "raison": "aucune_activite"}

    heures_avec_brut = sum(_h(a) for a in sel if a.get("salaire_brut") is not None)
    sr = sum(float(a["salaire_brut"]) for a in sel if a.get("salaire_brut") is not None)
    completude = round(100 * heures_avec_brut / nht)
    if completude < _COMPLETUDE_MIN:
        return {"ok": False, "raison": "bruts_incomplets", "completude": completude, "nht": round(nht, 1)}

    # Annexe : les cachets sont artiste par nature ; les heures votent par leur métier.
    h_artiste = sum(_h(a) for a in sel if a["type_activite"] != "heures" or a.get("metier") == "artiste")
    h_technicien = sum(_h(a) for a in sel if a["type_activite"] == "heures" and a.get("metier") == "technicien")
    if h_artiste > 0 or h_technicien > 0:
        annexe = "annexe10" if h_artiste >= h_technicien else "annexe8"
        indeterminee = False
        res = calculer_aj(annexe, sr=sr, nht=nht)
    else:
        # Aucune heure départagée : on retient la PLUS BASSE des deux annexes (prudence).
        a8 = calculer_aj("annexe8", sr=sr, nht=nht)
        a10 = calculer_aj("annexe10", sr=sr, nht=nht)
        res = a8 if a8["aj_brute"] <= a10["aj_brute"] else a10
        annexe = res["annexe"]
        indeterminee = True

    socle = {
        "ok": True,
        "annexe": annexe,
        "annexe_indeterminee": indeterminee,
        "nht": round(nht, 1),
        "sr": round(sr, 2),
        "completude": completude,
        "fenetre_debut": debut.isoformat(),
        "fenetre_fin": fin.isoformat(),
        "avertissement": AVERTISSEMENT,
    }

    # Loi X : MÊME discipline d'affichage que la carte allocation (branche_affichable).
    # Hors branche validée (annexe 8, ou > 60 €/jour) → AUCUN chiffre, raison honnête.
    affichable, raison_affichable = branche_affichable(annexe, res)
    if not affichable:
        socle.update({"affichable": False, "raison_non_affichable": raison_affichable})
        return socle

    # Cachet moyen RÉEL (cachets avec brut) — sert d'hypothèse de la courbe.
    nb_cachets_brut = sum(float(a.get("nombre") or 0) for a in sel
                          if a["type_activite"] != "heures" and a.get("salaire_brut") is not None)
    brut_cachets = sum(float(a["salaire_brut"]) for a in sel
                       if a["type_activite"] != "heures" and a.get("salaire_brut") is not None)
    if nb_cachets_brut > 0:
        brut_moyen_cachet = round(brut_cachets / nb_cachets_brut, 2)
    else:
        brut_moyen_cachet = round(sr / nht * 12.0, 2)  # équivalent 12h au tarif moyen réel

    # La courbe s'ARRÊTE au premier point hors branche validée (> 60 € : CSG non
    # vérifiée sur cas réel) — on le dit plutôt que d'extrapoler.
    points = []
    courbe_plafonnee = False
    for n in range(0, 9):
        p = calculer_aj(annexe, sr=sr + n * brut_moyen_cachet, nht=nht + n * 12.0)
        p_ok, _ = branche_affichable(annexe, p)
        if not p_ok:
            courbe_plafonnee = True
            break
        points.append({"cachets": n, "aj_brute": p["aj_brute"]})

    socle.update({
        "affichable": True,
        "raison_non_affichable": None,
        "aj_brute": res["aj_brute"],
        "aj_nette": res["aj_nette"],
        "plancher_applique": res["plancher_applique"],
        "plafond_applique": res["plafond_applique"],
        "brut_moyen_cachet": brut_moyen_cachet,
        "points": points,
        "courbe_plafonnee_60": courbe_plafonnee,
    })

    # Simulation « et si j'ajoute N cachets à X € ? » (demande testeuse 24/07) —
    # bornée pour rester raisonnable, et soumise à la même discipline d'affichage.
    if cachets_sup and cachets_sup > 0:
        n = min(int(cachets_sup), 200)
        unitaire = float(brut_cachet) if brut_cachet not in (None, "", 0) else brut_moyen_cachet
        unitaire = max(0.0, min(unitaire, 100000.0))
        sim = calculer_aj(annexe, sr=sr + n * unitaire, nht=nht + n * 12.0)
        sim_ok, sim_raison = branche_affichable(annexe, sim)
        bloc = {"cachets": n, "brut_cachet": round(unitaire, 2), "affichable": sim_ok, "raison_non_affichable": sim_raison}
        if sim_ok:
            bloc.update({"aj_brute": sim["aj_brute"], "aj_nette": sim["aj_nette"], "plafond_applique": sim["plafond_applique"]})
        socle["simulation"] = bloc

    return socle


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
    franchise_cp_restante: float = 0.0,
    franchise_cp_totale: float = 0.0,
    franchise_salaires_restante: float = 0.0,
    franchise_salaires_totale: float = 0.0,
) -> dict:
    p = _params(annexe)
    heures_mois = max(0.0, float(heures_mois or 0))
    remunerations_brutes = max(0.0, float(remunerations_brutes or 0))

    if pmss_mensuel is None:
        pmss_mensuel = _PMSS["montant"]
    plafond_cumul = round(pmss_mensuel * _PMSS["coefPlafondCumul"], 2)

    # Jours travaillés et jours non indemnisables : France Travail TRONQUE au
    # nombre entier (« arrondi au nombre entier obtenu ») — confirmé sur relevé
    # RÉEL (cas n°2, annexe 8, 14/04/2026 : 6,04 → 6 j ; 10,5 → 10 j). On garde
    # malgré tout le drapeau d'approximation sur les cas fractionnaires : la
    # troncature n'a pas encore été observée sur un relevé annexe 10.
    jt_brut = heures_mois / p["diviseurSJM"]
    jours_travailles = int(jt_brut + 1e-9)
    jni_brut = jt_brut * p["coefDecalage"]
    jours_non_indemnisables = int(jni_brut + 1e-9)
    arrondi_approximatif = (jt_brut != int(jt_brut)) or (jni_brut != int(jni_brut))

    seuil_atteint = jours_travailles >= p["seuilJoursMois"]

    if seuil_atteint:
        return {
            "annexe": annexe,
            "jours_travailles": jours_travailles,
            "seuil_atteint": True,
            "jours_non_indemnisables": jours_non_indemnisables,
            "jours_indemnisables": 0,
            "franchise_cp_imputee": 0,
            "franchise_salaires_imputee": 0,
            "are_avant_plafond": 0.0,
            "plafond_cumul": plafond_cumul,
            "plafond_cumul_applique": False,
            "are_versee": 0.0,
            "arrondi_approximatif": arrondi_approximatif,
            "avertissement": AVERTISSEMENT,
        }

    jours_indemnisables = max(0, int(jours_calendaires) - jours_non_indemnisables)

    # Franchises (annexe X art. 29 §1 et 31 §2, texte 2016 en vigueur) :
    # seuls les jours indemnisables servent à leur computation, ordre = congés
    # payés puis salaires. CP : 2 j/mois si le total acquis est < 24 j, 3 j/mois
    # au-delà, jusqu'à épuisement. Salaires : total réparti sur les 8 premiers
    # mois (total/8, arrondi supérieur), le reliquat est reporté ensuite.
    franchise_cp_imputee = 0
    if franchise_cp_restante > 0 and jours_indemnisables > 0:
        rythme_cp = 3 if franchise_cp_totale > 24 else 2
        franchise_cp_imputee = int(min(rythme_cp, math.ceil(franchise_cp_restante), jours_indemnisables))
        jours_indemnisables -= franchise_cp_imputee

    franchise_salaires_imputee = 0
    if franchise_salaires_restante > 0 and jours_indemnisables > 0:
        quota_salaires = math.ceil(franchise_salaires_totale / 8) if franchise_salaires_totale > 0 else franchise_salaires_restante
        franchise_salaires_imputee = int(min(quota_salaires, math.ceil(franchise_salaires_restante), jours_indemnisables))
        jours_indemnisables -= franchise_salaires_imputee

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
        "franchise_cp_imputee": franchise_cp_imputee,
        "franchise_salaires_imputee": franchise_salaires_imputee,
        "are_avant_plafond": are_avant_plafond,
        "plafond_cumul": plafond_cumul,
        "plafond_cumul_applique": plafond_cumul_applique,
        "are_versee": are_versee,
        "arrondi_approximatif": arrondi_approximatif,
        "avertissement": AVERTISSEMENT,
    }


# ─────────────────────────────────────────────────────────────────────────────
#  ESTIMATION DU MOIS CIVIL (décision Camille 24/07/2026 : lancé en mode
#  ESTIMATION assumée, AVANT le backtest sur un relevé de situation réel — le
#  premier relevé partagé servira à se caler, la carte le dit à l'utilisateur).
#  S'appuie sur calculer_mois (guide FT, exemple officiel 12) et sur l'AJ de la
#  carte allocation (validée au centime par le backtest n°1).
#  Loi X : l'appelant vérifie branche_affichable AVANT d'appeler — on reste donc
#  en zone validée (annexe 10, ≤ 60 €), où la CSG est nulle et le net fiable.
#  Franchises non stockées côté profil : comptées à 0 (dit dans la carte).
# ─────────────────────────────────────────────────────────────────────────────
def estimer_mois_civil(annexe: str, res_aj: dict, activites: list, annee: int, mois: int) -> dict:
    """
    res_aj    : résultat de calculer_aj — la MÊME AJ que la carte allocation.
    activites : dicts {date, type_activite, nombre, salaire_brut} ; seules celles
                du mois civil (annee, mois) comptent. Travail = _TYPES_TRAVAIL
                (formation exclue du décalage) ; autre_salaire = 0 heure mais son
                brut compte pour le plafond mensuel de cumul (toutes les paies).
    """
    import calendar as _cal
    jours_cal = _cal.monthrange(annee, mois)[1]

    heures = 0.0
    bruts = 0.0
    bruts_manquants = False
    autre_salaire_mois = False
    nb_travail = 0
    for a in activites:
        d = a.get("date")
        if not d or d.year != annee or d.month != mois:
            continue
        t = a.get("type_activite")
        n = max(0.0, float(a.get("nombre") or 0))
        if t in _TYPES_TRAVAIL:
            nb_travail += 1
            heures += n if t == "heures" else n * 12.0
            if a.get("salaire_brut") is None:
                bruts_manquants = True
            else:
                bruts += float(a["salaire_brut"])
        elif t == "autre_salaire" and a.get("salaire_brut") is not None:
            # Compté pour le plafond de cumul. En revanche, France Travail
            # convertit AUSSI ces salaires en heures (÷ SMIC) pour le décalage :
            # le SMIC horaire n'est pas dans le référentiel sourcé, donc on ne
            # convertit PAS (pas de constante inventée) — on SIGNALE à la place
            # que le versement réel sera un peu plus bas (sens honnête).
            bruts += float(a["salaire_brut"])
            autre_salaire_mois = True

    m = calculer_mois(
        annexe,
        aj_brute=res_aj["aj_brute"],
        heures_mois=heures,
        remunerations_brutes=bruts,
        jours_calendaires=jours_cal,
    )

    # Brut → net du mois. En zone validée (≤ 60 €), pas de CSG : le net du jour
    # est aj_nette (retenue retraite comprise). Si le plafond de cumul a rogné
    # le brut, on convertit au prorata (approximation, signalée comme telle).
    prorata_plafond = False
    if m["are_versee"] <= 0:
        net = 0.0
    elif not m["plafond_cumul_applique"]:
        net = round(m["jours_indemnisables"] * res_aj["aj_nette"], 2)
    else:
        prorata_plafond = True
        net = round(m["are_versee"] * (res_aj["aj_nette"] / res_aj["aj_brute"]), 2) if res_aj["aj_brute"] > 0 else 0.0

    approximatif = bool(m["arrondi_approximatif"] or bruts_manquants or prorata_plafond or autre_salaire_mois)

    return {
        **m,
        "annee": annee,
        "mois": mois,
        "jours_calendaires": jours_cal,
        "heures_mois": round(heures, 1),
        "remunerations_brutes": round(bruts, 2),
        "bruts_manquants": bruts_manquants,
        "autre_salaire_non_decale": autre_salaire_mois,
        "activites_travail_mois": nb_travail,
        "net_estime": net,
        "prorata_plafond": prorata_plafond,
        "approximatif": approximatif,
    }

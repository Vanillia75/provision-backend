"""
════════════════════════════════════════════════════════════════════════════
 RÉFÉRENTIEL DES RÈGLES DU RÉGIME INTERMITTENT DU SPECTACLE (backend Python)
════════════════════════════════════════════════════════════════════════════

 Jumeau de regles_intermittent.js (frontend). Les DEUX fichiers doivent
 toujours porter les mêmes valeurs : c'est l'unique source de vérité du
 régime, déclinée dans les deux langages. Un test de cohérence automatique
 (test_coherence_intermittent.py + GitHub Actions) échoue si les deux
 fichiers divergent sur une règle commune.

 verifie = True  → confirmé par une source officielle (texte réglementaire,
                   ARTCENA, France Travail) ET/OU par une intermittente de
                   longue expérience. La provenance est tracée dans "source".
 verifie = False → valeur non confirmée, ou point qui demande l'avis d'un
                   expert / d'un organisme (France Travail, Audiens).

 Tant que verifie=False, le moteur reste prudent : pas d'affirmation définitive,
 et toute règle douteuse est exclue du calcul (jamais "devinée").

 Dernière revue : 2026-06 (Unédic, France Travail Guide Intermittent, ARTCENA
 Précis juridique annexes VIII et X, article 3 de l'annexe X, Circulaire
 Unédic n°2018-04). Validation terrain par une intermittente (20 ans).
════════════════════════════════════════════════════════════════════════════
"""

VERSION_REFERENTIEL = {
    "version": "2026.07",
    "revue": "2026-06-26",
    "note": "Règles-clés du décompte des heures validées (source officielle + "
            "intermittente 20 ans). Montants et dispositifs annexes encore à valider.",
}

REGLES = {

    # ── OUVERTURE / RENOUVELLEMENT DES DROITS ──
    "seuilHeures": {
        "valeur": 507,
        "libelle": "Seuil d'ouverture de droits",
        "source": "Annexes 8 et 10 au règlement d'assurance chômage ; ARTCENA ; validé par intermittente (20 ans)",
        "version": "2026.07",
        "dateAppli": "en vigueur",
        "verifie": True,
        "commentaire": "Heures minimales (ou assimilées) à réunir pour ouvrir des droits. Confirmé France Travail et ARTCENA.",
    },
    "periodeReferenceJours": {
        "valeur": 365,
        "libelle": "Période de référence (première admission et réadmission)",
        "source": "ARTCENA (annexes VIII et X) ; France Travail Guide Intermittent ; validé par intermittente (20 ans)",
        "version": "2026.07",
        "dateAppli": "en vigueur",
        "verifie": True,
        "commentaire": "Les 507h se cherchent sur les 12 mois (365 jours) glissants précédant la dernière fin de contrat retenue. Vaut aussi pour la réadmission (date anniversaire au terme d'un délai de 12 mois). En réadmission, si les 507h ne sont pas atteintes, la période PEUT être allongée avec majoration (42h/30j au-delà du 365e jour) — cas spécial NON géré par le moteur pour l'instant.",
    },
    "dureeIndemnisationJours": {
        "valeur": 365,
        "libelle": "Durée d'indemnisation",
        "source": "Unédic ; France Travail ; validé par intermittente (20 ans)",
        "version": "2026.07",
        "dateAppli": "en vigueur",
        "verifie": True,
        "commentaire": "Droits ouverts jusqu'à une date anniversaire (terme d'un délai de 12 mois après la fin de contrat ayant ouvert les droits), avec réexamen des droits à cette date.",
    },

    # ── CONVERSION CACHET → HEURES ──
    "cachetHeures": {
        "valeur": 12,
        "libelle": "Forfait d'un cachet (artiste, annexe 10)",
        "source": "Article 3 de l'annexe X ; ARTCENA ; validé par intermittente (20 ans)",
        "version": "2026.07",
        "dateAppli": "en vigueur",
        "verifie": True,
        "commentaire": "Chaque cachet d'artiste (annexe 10) est systématiquement converti en 12h par France Travail (article 3 de l'annexe X). Il n'existe PLUS de distinction entre cachets isolés et groupés : tous comptent 12h. 43 cachets = 516h. Les techniciens (annexe 8) sont décomptés à l'heure réelle.",
    },
    "cachetGroupeHeures_HISTORIQUE": {
        "valeur": 8,
        "libelle": "Cachet groupé = 8h — RÈGLE HISTORIQUE, NE PAS UTILISER",
        "source": "Convention ancienne (abrogée) — conservée pour mémoire",
        "version": "obsolète",
        "dateAppli": "ancienne convention",
        "verifie": False,
        "aValiderUrgent": False,
        "nePasUtiliser": True,
        "commentaire": "⚠️ HISTORIQUE — conservé pour mémoire uniquement. La distinction 'cachet groupé = 8h' a été SUPPRIMÉE : France Travail convertit désormais TOUS les cachets à 12h (article 3 annexe X, confirmé ARTCENA). Cette règle n'est PAS utilisée dans le calcul.",
    },

    # ── CLAUSE DE RATTRAPAGE / FILET ──
    "rattrapageSeuilMin": {
        "valeur": 338,
        "libelle": "Seuil minimal de la clause de rattrapage (filet)",
        "source": "ARTCENA (annexes VIII et X) ; Circulaire Unédic n°2018-04 ; validé par intermittente (20 ans)",
        "version": "2026.07",
        "dateAppli": "2018-02-07",
        "verifie": True,
        "commentaire": "Au moins 338h au cours des 12 derniers mois précédant la date anniversaire : c'est UNE des deux conditions de la clause de rattrapage (voir rattrapageOuverturesMin pour la seconde). Avoir 338h NE SUFFIT PAS à lui seul.",
    },
    "rattrapageDureeMois": {
        "valeur": 6,
        "libelle": "Durée maximale de la clause de rattrapage",
        "source": "ARTCENA ; Circulaire Unédic n°2018-04 ; validé par intermittente (20 ans)",
        "version": "2026.07",
        "dateAppli": "2018-02-07",
        "verifie": True,
        "commentaire": "Période d'indemnisation maximale de 6 mois au titre de la clause. Date anniversaire inchangée. Décision irrévocable une fois activée.",
    },
    "rattrapageOuverturesMin": {
        "valeur": 5,
        "libelle": "Condition d'éligibilité — ouvertures de droits (clause de rattrapage)",
        "source": "ARTCENA (Précis juridique annexes VIII et X) ; Circulaire Unédic n°2018-04",
        "version": "2026.07",
        "dateAppli": "2018-02-07",
        "verifie": True,
        "commentaire": "SECONDE condition de la clause, CUMULATIVE avec les 338h : justifier d'au moins 5 années d'affiliation (5 × 507h) OU 5 ouvertures de droits au cours des 10 années précédant la fin de contrat. Le moteur ne dispose pas de l'historique des ouvertures : il ne doit donc PAS affirmer le filet acquis sur la seule base des 338h.",
    },

    # ── À VALIDER PAR EXPERT (présentes pour l'affichage front, non utilisées par le moteur) ──
    # Ces règles sont déclarées ici pour que le .py reste l'UNIQUE source : le .js est
    # généré à partir de ce fichier (cf. generer_regles_js.py). Elles ne sont PAS utilisées
    # dans le calcul des heures (verifie:false) et attendent une validation experte.
    "formationPlafondNouvelleAdmission": {
        "valeur": 338,
        "libelle": "Plafond d'heures de formation suivie assimilées",
        "source": "Unédic — annexes VIII et X au règlement d'assurance chômage (heures de "
                  "formation assimilées dans la limite des 2/3 du nombre d'heures requis) ; "
                  "ARTCENA, Précis juridique annexes VIII et X. Sourcé le 2026-07-03.",
        "version": "2026.07",
        "dateAppli": "en vigueur",
        "verifie": True,
        "commentaire": "Les heures de formation SUIVIE comptent comme des heures de travail dans la "
                       "limite des 2/3 du seuil requis : 2/3 × 507 = 338h. Plafond GLOBAL sur la fenêtre "
                       "de 12 mois (pas par formation). Conséquence : la formation seule ne peut jamais "
                       "ouvrir des droits (338 < 507). Le plafond de 338h est PARTAGÉ avec l'enseignement "
                       "dispensé (cf. enseignementPlafond) : formation + enseignement ≤ 338h.",
    },
    "enseignementPlafond": {
        "valeur": 70,
        "libelle": "Plafond d'heures d'enseignement dispensé assimilées",
        "source": "Guide France Travail Intermittents p.8-9 (heures d'enseignement artistique/technique "
                  "limitées à 70h, 120h si ≥50 ans ; total formation + enseignement ≤ 338h). Sourcé le 2026-07-03.",
        "version": "2026.07",
        "dateAppli": "en vigueur",
        "verifie": True,
        "commentaire": "Les heures d'enseignement DISPENSÉ (artiste/technicien enseignant dans un "
                       "établissement agréé) comptent heure pour heure, plafonnées à 70h. Le cas 120h "
                       "(≥50 ans à la fin de contrat) est HORS V1 : Totor n'a pas la date de naissance. "
                       "Sous-plafond de 70h ET plafond partagé de 338h avec la formation (le total des deux "
                       "ne peut dépasser 338h). Conditions FT (contrat + fiches de paie) non vérifiables → estimation.",
    },
    "congesSpectaclesTaux": {
        "valeur": 0.10,
        "libelle": "Taux de l'indemnité Congés Spectacles (ICP) — % des bruts de l'exercice",
        "source": "Audiens Congés Spectacles ; backtesté sur 2 bordereaux réels (exercices "
                  "2023-2024 et 2024-2025) : ICP brut = 10 % des bruts au centime. Cf. CONGES_SPECTACLES_ETUDE.md.",
        "version": "2026.07",
        "dateAppli": "en vigueur",
        "verifie": True,
        "commentaire": "L'indemnité de congés payés (ICP) versée par Audiens = 10 % des salaires "
                       "BRUTS cumulés sur l'exercice (1er avril → 31 mars). Validé au centime sur 2 années "
                       "réelles (7 381 € → 738,10 € ; 10 055 € → 1 005,50 €). Reste une estimation à l'écran "
                       "car dépend de la complétude des bruts saisis (montant = € → Loi X).",
    },
    "congesSpectaclesRatioNetSocial": {
        "valeur": 0.7695,
        "libelle": "Ratio net social / brut de l'ICP Congés Spectacles",
        "source": "Bordereaux Audiens réels 2023-2024 et 2024-2025 : net/brut = 76,95 % (cotisations "
                  "salariales ≈ 23,05 %) — identique sur les 2 années. Cf. CONGES_SPECTACLES_ETUDE.md.",
        "version": "2026.07",
        "dateAppli": "2025",
        "verifie": True,
        "commentaire": "Le net social (AVANT impôts) ≈ 76,95 % de l'ICP brute. PÉREMPTION ANNUELLE : les "
                       "taux de cotisation peuvent évoluer → re-valider ce ratio sur un bordereau frais "
                       "chaque avril (Loi X, clause de péremption). Le net-net (après PAS) dépend du taux "
                       "d'imposition personnel → jamais estimé. Prudence renforcée : afficher « ~ », pas au centime.",
    },
    "assimilationArretParJour": {
        "valeur": 5,
        "libelle": "Heures assimilées par jour d'arrêt (maternité, adoption, AT/MP, ALD, suspension de contrat)",
        "source": "Guide France Travail Intermittents p.8 (suspension de contrat) et p.9 "
                  "(maternité/adoption/AT/ALD hors contrat) ; matermittentes.com. "
                  "Sourcé le 2026-07-03 — cf. MOTEUR_ARRETS_SOURCES.md.",
        "version": "2026.07",
        "dateAppli": "en vigueur",
        "verifie": True,
        "commentaire": "Certains arrêts INDEMNISÉS comptent comme du travail à raison de 5h par jour "
                       "calendaire (week-ends inclus), SANS plafond. Concerne : maternité/adoption "
                       "(hors contrat, Sécu ou Audiens), AT/MP, ALD (hors contrat, + ouverture "
                       "antérieure), et tout arrêt PENDANT un contrat. La maladie ordinaire HORS "
                       "contrat n'assimile PAS d'heures (elle neutralise/allonge la période — hors "
                       "périmètre V1). Paternité : hors V1 (guide = neutralisation, sources divergentes). "
                       "Conditions (indemnisation, retravailler après) non vérifiables par le moteur → "
                       "l'apport d'arrêt est marqué ESTIMATION tant qu'un dossier réel ne l'a pas validé.",
    },
    "ajMinimale": {
        "valeur": 31.96,
        "libelle": "Allocation journalière minimale (paramètre de calcul)",
        "source": "Unédic — Paramètres utiles avril 2025 ; Guide France Travail Intermittents "
                  "(31,96 € depuis le 01/07/2023). Cf. MOTEUR_AJ_SOURCES.md.",
        "version": "2026.07",
        "dateAppli": "2023-07-01",
        "verifie": True,
        "commentaire": "Paramètre fixe des formules A, B, C de l'AJ. Évolue avec le SMIC : "
                       "à réviser à chaque revalorisation (dernier contrôle 2026-07-03). "
                       "Validé par backtest réel à 0,00 € d'écart (cf. MOTEUR_AJ_SOURCES.md §6).",
    },

    # ── MOTEUR ALLOCATION JOURNALIÈRE (chantier AJ — cf. MOTEUR_AJ_SOURCES.md) ──
    # Source commune : Guide officiel France Travail « Intermittents du spectacle »
    # p.11 (formules), p.12 (brut→net), p.16-17 (mois type) ; Unédic Paramètres utiles
    # avril 2025. Backtest réel n°1 (annexe 10) : 0,00 € d'écart le 2026-07-03.
    "allocationParametresAnnexe8": {
        "valeur": {
            "coefSR": 0.42, "plafondSR": 14400, "coefSRAuDela": 0.05, "diviseurA": 5000,
            "coefNHT": 0.26, "seuilNHT": 720, "coefNHTAuDela": 0.08, "diviseurB": 507,
            "coefC": 0.40, "plancherAJ": 38.0, "diviseurSJM": 8,
            "seuilJoursMois": 26, "coefDecalage": 1.4,
        },
        "libelle": "Paramètres de l'allocation journalière — annexe 8 (techniciens)",
        "source": "Guide France Travail Intermittents p.11, 12, 16-17 (exemples 6 et 12 vérifiés)",
        "version": "2026.07",
        "dateAppli": "en vigueur",
        "verifie": True,
        "commentaire": "A = AJmin×[0,42×SR(≤14400)+0,05×au-delà]/5000 ; B = AJmin×[0,26×NHT(≤720h)"
                       "+0,08×au-delà]/507 ; C = AJmin×0,40. Plancher 38 €. SJM = SR/(NHT/8). "
                       "Mois : jours travaillés = heures/8, seuil 26 j, décalage ×1,4.",
    },
    "allocationParametresAnnexe10": {
        "valeur": {
            "coefSR": 0.36, "plafondSR": 13700, "coefSRAuDela": 0.05, "diviseurA": 5000,
            "coefNHT": 0.26, "seuilNHT": 690, "coefNHTAuDela": 0.08, "diviseurB": 507,
            "coefC": 0.70, "plancherAJ": 44.0, "diviseurSJM": 10,
            "seuilJoursMois": 27, "coefDecalage": 1.3,
        },
        "libelle": "Paramètres de l'allocation journalière — annexe 10 (artistes)",
        "source": "Guide France Travail Intermittents p.11, 12, 16-17 ; backtest réel 0,00 € d'écart (2026-07-03)",
        "version": "2026.07",
        "dateAppli": "en vigueur",
        "verifie": True,
        "commentaire": "A = AJmin×[0,36×SR(≤13700)+0,05×au-delà]/5000 ; B = AJmin×[0,26×NHT(≤690h)"
                       "+0,08×au-delà]/507 ; C = AJmin×0,70. Plancher 44 €. SJM = SR/(NHT/10). "
                       "Mois : jours travaillés = heures/10, seuil 27 j, décalage ×1,3.",
    },
    "allocationPlafondAJ": {
        "valeur": 155.77,
        "libelle": "Plafond de l'allocation journalière (annexes 8 et 10)",
        "source": "MESURÉ le 2026-07-27 sur le simulateur officiel France Travail "
                  "(simucalcul.pole-emploi-services.fr) : l'allocation reste bloquée à 155,77 € "
                  "pour un salaire de référence de 320 000, 350 000, 500 000 et 1 000 000 €, "
                  "annexe 8 comme annexe 10.",
        "version": "2026.07",
        "dateAppli": "2026",
        "verifie": True,
        "commentaire": "⚠️ VALEUR CORRIGÉE le 27/07/2026. On avait 174,80 € (guide France Travail "
                       "p.11, daté du 01/01/2024) : ce chiffre est PÉRIMÉ ou ne concerne pas les "
                       "annexes 8/10. Le simulateur officiel plafonne à 155,77 €, et notre formule "
                       "colle au centime jusqu'à 300 000 € de salaire de référence (153,70 contre "
                       "153,71). Sans cette correction, un artiste très bien payé (au-delà d'environ "
                       "306 500 € sur la période) aurait vu une allocation SUPÉRIEURE à la réalité : "
                       "exactement ce que la Loi X interdit. Revalorisé périodiquement : revérifier "
                       "sur le simulateur au rituel de janvier.",
    },
    "allocationRetenueRetraiteComp": {
        "valeur": {"taux": 0.0093, "seuilExoneration": 31.96, "seuilCsg": 60.0},
        "libelle": "Retenue retraite complémentaire sur l'AJ (0,93 % du SJM)",
        "source": "Guide France Travail Intermittents p.12 ; validé par backtest réel (retenue 1,25 € exacte)",
        "version": "2026.07",
        "dateAppli": "en vigueur",
        "verifie": True,
        "commentaire": "AJ ≤ 31,96 € : aucune retenue. Au-delà : retenue 0,93 % × SJM, EXACTE au "
                       "centime sur les 14 cas du simulateur officiel (27/07/2026), y compris à "
                       "91,72 € de retenue. Quand ce qui reste dépasse 60 €, s'ajoutent CSG et "
                       "CRDS, elles-mêmes écrêtées (cf. allocationCsgCrds et allocationPlancherNetCsg).",
    },
    "allocationCsgCrds": {
        "valeur": {"csgPlein": 0.062, "csgReduit": 0.038, "crds": 0.005, "assiette": 0.9825},
        "libelle": "CSG/CRDS sur l'allocation journalière",
        "source": "Guide France Travail p.12 (taux) ; assiette 98,25 % CONFIRMÉE le 2026-07-27 "
                  "contre le simulateur officiel France Travail (simucalcul.pole-emploi-services.fr), "
                  "11 cas, annexes 8 et 10, écart 0,00 €.",
        "version": "2026.07",
        "dateAppli": "en vigueur",
        "verifie": True,
        "commentaire": "Taux 6,2 % (CSG) + 0,5 % (CRDS) = 6,7 %, appliqués sur 98,25 % de l'allocation "
                       "APRÈS retenue retraite complémentaire (et non avant : vérifié, 71,45 x 98,25 % "
                       "x 6,7 % = 4,70 € exactement ce qu'annonce France Travail). Le résultat est "
                       "ensuite ÉCRÊTÉ par allocationPlancherNetCsg : voir cette entrée.",
    },
    "allocationPlancherNetCsg": {
        "valeur": 62.00,
        "libelle": "Plancher net : la CSG/CRDS ne peut pas faire descendre l'allocation sous ce montant",
        "source": "Déduit puis VÉRIFIÉ au centime le 2026-07-27 sur le simulateur officiel France "
                  "Travail : trois allocations brutes différentes (63,27 / 64,64 / 65,99 €) donnent "
                  "toutes un net de 62,00 € exactement. SMIC brut mensuel ÷ 30, arrondi à l'euro "
                  "(1 867,02 ÷ 30 = 62,23 → 62 depuis le 01/06/2026).",
        "version": "2026.07",
        "dateAppli": "2026-06-01",
        "verifie": True,
        "commentaire": "Règle : on calcule la CSG théorique, puis on la RABOTE de façon que le net ne "
                       "tombe jamais sous 62,00 €. Si l'allocation brute est déjà sous ce plancher, la "
                       "retenue est de ZÉRO (vérifié : brute 60,55 € et 61,91 € donnent CSG 0,00 €). "
                       "⚠️ CETTE VALEUR EST CELLE D'AUJOURD'HUI SEULEMENT. Le plancher SUIT LE SMIC : "
                       "il valait 61,00 € du 01/01/2026 au 31/05/2026 (observé sur deux relevés réels), "
                       "et est passé à 62,00 € avec la revalorisation du SMIC du 01/06/2026. Pour "
                       "calculer un mois PASSÉ, ne pas lire cette valeur : appeler plancher_net_csg(date). "
                       "ÉVOLUE AVEC LE SMIC : à revérifier au rituel de janvier, sur le simulateur.",
    },
    "pmssMensuel": {
        "valeur": {"montant": 3925.0, "annee": 2025, "coefPlafondCumul": 1.18},
        "libelle": "Plafond mensuel de la sécurité sociale (pour le plafond de cumul ARE + salaires)",
        "source": "Unédic — Paramètres utiles avril 2025 (PMSS 2025 = 3 925 €) ; guide FT p.17 (cumul ≤ 118 % du PMSS)",
        "version": "2026.07",
        "dateAppli": "2025-01-01",
        "verifie": True,
        "commentaire": "Cumul mensuel ARE + rémunérations brutes plafonné à 118 % du PMSS. "
                       "Valeur 2025 — à réviser chaque 1er janvier (l'exemple 12 du guide utilise "
                       "le PMSS 2024 = 3 864 € → plafond 4 559,52 €, vérifié).",
    },
    "franchiseCongesParJours": {
        "valeur": {"jours": 2.5, "parTravailles": 24, "plafond": 30},
        "libelle": "Franchise congés payés",
        "source": "Unédic ; ARTCENA — à confirmer expert",
        "version": "2026.07",
        "dateAppli": "en vigueur",
        "verifie": False,
        "frontOnly": True,
        "commentaire": "2,5 jours non indemnisés par tranche de 24 jours travaillés, plafonnés à 30 jours. "
                       "À confirmer par un expert avant tout usage.",
    },
}


def get_regle(cle: str):
    """Renvoie la règle complète (valeur + traçabilité), ou None."""
    r = REGLES.get(cle)
    if r is None:
        print(f"[regles_intermittent] Règle inconnue : {cle!r}")
    return r


def valeur_de(cle: str):
    """Renvoie juste la valeur d'une règle (raccourci pour les calculs)."""
    r = REGLES.get(cle)
    return r["valeur"] if r else None


# ─────────────────────────────────────────────────────────────────────────────
#  LE PLANCHER CSG DANS LE TEMPS (ajouté le 2026-08-06)
#
#  Découvert sur un relevé France Travail réel : ce plancher n'est PAS une
#  constante, il suit le SMIC. Un même dossier montrait 61,00 € net par jour en
#  mai 2026 puis 61,88 € en juin (plus aucune CSG prélevée ce mois-là), sans que
#  rien n'ait changé dans ses droits. La seule chose qui avait bougé, c'est le
#  SMIC, revalorisé le 1er juin 2026.
#
#  La règle qui recoupe toutes les observations : plancher = SMIC brut mensuel
#  divisé par 30, arrondi à l'euro.
#
#  ⚠️ Avant cette correction, la valeur 62,00 était écrite en dur sans date :
#  toute estimation d'un mois antérieur à juin 2026 utilisait donc 62 au lieu de
#  61. C'est ce qui a fait douter une testeuse de l'ensemble de nos chiffres.
#
#  ⚠️ À REBRANCHER À CHAQUE REVALORISATION DU SMIC (rituel de janvier).
#  Pour ajouter une entrée : SMIC brut mensuel ÷ 30, arrondi à l'euro, puis la
#  confronter au simulateur officiel avant de la marquer vérifiée.
# ─────────────────────────────────────────────────────────────────────────────
PLANCHER_NET_CSG_HISTORIQUE = [
    {
        "depuis": "2026-06-01",
        "valeur": 62.00,
        "smic_mensuel": 1867.02,
        "verifie": True,
        "source": "SMIC 12,31 €/h au 01/06/2026 → 1 867,02 ÷ 30 = 62,23 → 62. "
                  "VÉRIFIÉ au centime le 2026-07-27 sur le simulateur officiel France "
                  "Travail : trois allocations brutes (63,27 / 64,64 / 65,99 €) donnent "
                  "toutes 62,00 € net. Corroboré par un relevé réel de juin 2026 où la "
                  "CSG n'est plus prélevée du tout (net 61,88 €, déjà sous le plancher).",
    },
    {
        "depuis": "2026-01-01",
        "valeur": 61.00,
        "smic_mensuel": 1823.03,
        "verifie": True,
        "source": "SMIC 12,02 €/h au 01/01/2026 → 1 823,03 ÷ 30 = 60,77 → 61. "
                  "OBSERVÉ sur deux relevés France Travail réels du même dossier "
                  "(avril et mai 2026) : allocation brute 63,69 €, net exactement "
                  "61,00 €/jour les deux fois.",
    },
]


def plancher_net_csg(le=None) -> float:
    """Le plancher applicable à une date donnée (par défaut : aujourd'hui).

    `le` accepte une date, un datetime, ou une chaîne « AAAA-MM-JJ ». Pour une
    date antérieure à la plus ancienne entrée connue, on renvoie cette plus
    ancienne valeur : mieux vaut le dernier plancher sourcé qu'un chiffre
    inventé, et TOTOR n'estime de toute façon pas de mois d'avant 2026.
    """
    from datetime import date as _date, datetime as _datetime

    if le is None:
        jour = _date.today().isoformat()
    elif isinstance(le, str):
        jour = le[:10]
    elif isinstance(le, _datetime):
        jour = le.date().isoformat()
    elif isinstance(le, _date):
        jour = le.isoformat()
    else:
        jour = str(le)[:10]

    for entree in PLANCHER_NET_CSG_HISTORIQUE:      # du plus récent au plus ancien
        if jour >= entree["depuis"]:
            return entree["valeur"]
    return PLANCHER_NET_CSG_HISTORIQUE[-1]["valeur"]


def tracer(cle: str) -> str:
    """Construit une phrase de traçabilité pour le bouton 'Pourquoi ?'."""
    r = REGLES.get(cle)
    if not r:
        return ""
    fiabilite = "" if r.get("verifie") else " (valeur documentée, validation experte en cours)"
    return f"{r['libelle']} : {r['valeur']}. Source : {r['source']}, version {r['version']}{fiabilite}."

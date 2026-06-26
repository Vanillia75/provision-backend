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
        "libelle": "Plafond d'heures de formation assimilées (nouvelle admission)",
        "source": "France Travail ; Unédic — à confirmer expert",
        "version": "2026.07",
        "dateAppli": "en vigueur",
        "verifie": False,
        "frontOnly": True,
        "commentaire": "Les heures de formation comptent comme assimilées jusqu'à 2/3 du total requis "
                       "(≈338h pour une nouvelle admission). À confirmer par un expert avant tout usage.",
    },
    "ajMinimale": {
        "valeur": 31.96,
        "libelle": "Allocation journalière minimale (paramètre de calcul)",
        "source": "Unédic — Paramètres Utiles — à confirmer expert",
        "version": "2026.07",
        "dateAppli": "à confirmer",
        "verifie": False,
        "frontOnly": True,
        "commentaire": "Paramètre fixe de la formule de l'AJ. Évolue avec le SMIC. Hector ne calcule "
                       "pas les euros : ne pas utiliser sans validation experte.",
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


def tracer(cle: str) -> str:
    """Construit une phrase de traçabilité pour le bouton 'Pourquoi ?'."""
    r = REGLES.get(cle)
    if not r:
        return ""
    fiabilite = "" if r.get("verifie") else " (valeur documentée, validation experte en cours)"
    return f"{r['libelle']} : {r['valeur']}. Source : {r['source']}, version {r['version']}{fiabilite}."

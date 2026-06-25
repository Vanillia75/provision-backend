"""
════════════════════════════════════════════════════════════════════════════
 RÉFÉRENTIEL DES RÈGLES DU RÉGIME INTERMITTENT DU SPECTACLE (backend Python)
════════════════════════════════════════════════════════════════════════════

 Jumeau de regles_intermittent.js (frontend). Les DEUX fichiers doivent
 toujours porter les mêmes valeurs : c'est l'unique source de vérité du
 régime, déclinée dans les deux langages.

 Chaque règle porte : valeur, libellé, source officielle, version, date
 d'application, statut de vérification (verifie), et un commentaire humain.

 verifie = True  → confirmé par un expert du régime (conseiller France Travail,
                   comptable spécialisé, syndicat). AUCUNE règle n'est encore ici.
 verifie = False → valeur issue de lectures de sources officielles (Unédic,
                   France Travail, circulaire Unédic 2018-04) mais PAS encore
                   confirmée par un expert terrain.

 Tant que verifie=False, le moteur reste prudent : pas d'affirmation définitive
 sur des montants, et toute règle douteuse est exclue du calcul (pas "devinée").

 Dernière revue documentaire : 2026-06 (Unédic, France Travail Guide
 Intermittent, Circulaire Unédic n°2018-04, Paramètres Utiles Unédic 04/2025).
════════════════════════════════════════════════════════════════════════════
"""

VERSION_REFERENTIEL = {
    "version": "2026.06",
    "revue": "2026-06-25",
    "note": "Revue documentaire. Validation experte du régime non encore réalisée.",
}

REGLES = {

    # ── OUVERTURE / RENOUVELLEMENT DES DROITS ──
    "seuilHeures": {
        "valeur": 507,
        "libelle": "Seuil d'ouverture de droits",
        "source": "Annexes 8 et 10 au règlement d'assurance chômage ; Unédic",
        "version": "2026.06",
        "dateAppli": "en vigueur",
        "verifie": False,
        "commentaire": "Heures minimales (ou assimilées) à réunir pour ouvrir des droits.",
    },
    "periodeReferenceJours": {
        "valeur": 365,
        "libelle": "Période de référence (première admission)",
        "source": "Annexes 8 et 10 ; Unédic",
        "version": "2026.06",
        "dateAppli": "en vigueur",
        "verifie": False,
        "commentaire": "Les 507h se cherchent sur les 12 mois (365 jours) glissants précédant "
                       "la dernière fin de contrat retenue.",
    },
    "dureeIndemnisationJours": {
        "valeur": 365,
        "libelle": "Durée d'indemnisation",
        "source": "Unédic ; France Travail",
        "version": "2026.06",
        "dateAppli": "en vigueur",
        "verifie": False,
        "commentaire": "Droits ouverts 12 mois jusqu'à la date anniversaire (lendemain du "
                       "dernier jour travaillé ayant ouvert les droits).",
    },

    # ── CONVERSION CACHET → HEURES ──
    "cachetHeures": {
        "valeur": 12,
        "libelle": "Forfait d'un cachet (artiste, annexe 10)",
        "source": "France Travail — Guide Intermittent ; ipresta.fr (annexe 10)",
        "version": "2026.06",
        "dateAppli": "en vigueur",
        "verifie": False,
        "statut": "validé provisoirement — à confirmer par expert terrain",
        "commentaire": "RÈGLE ACTUELLE : un cachet d'artiste (annexe 10) compte 12h dans le "
                       "décompte des droits. 43 cachets = 516h. Les techniciens (annexe 8) "
                       "sont décomptés à l'heure réelle. Hector applique 12h à TOUS les "
                       "cachets, par prudence, tant qu'aucun expert n'a confirmé un autre forfait.",
    },
    "cachetGroupeHeures_HISTORIQUE": {
        "valeur": 8,
        "libelle": "Cachet groupé = 8h — RÈGLE HISTORIQUE, NE PAS UTILISER",
        "source": "Wikipédia (source secondaire) — convention ancienne",
        "version": "obsolète",
        "dateAppli": "ancienne convention",
        "verifie": False,
        "aValiderUrgent": True,
        "nePasUtiliser": True,
        "commentaire": "⚠️ HISTORIQUE — conservé pour mémoire uniquement. La règle 'cachet "
                       "groupé consécutif = 8h' provient d'une source secondaire et correspond "
                       "vraisemblablement à une convention antérieure. ELLE N'EST PAS utilisée "
                       "dans le calcul actuel (tous les cachets à 12h). À clarifier avec un "
                       "conseiller France Travail Spectacle avant toute réintroduction.",
    },

    # ── CLAUSE DE RATTRAPAGE / FILET ──
    "rattrapageSeuilMin": {
        "valeur": 338,
        "libelle": "Seuil minimal de la clause de rattrapage (filet)",
        "source": "Circulaire Unédic n°2018-04 du 07/02/2018",
        "version": "2026.06",
        "dateAppli": "2018-02-07",
        "verifie": False,
        "commentaire": "Entre 338h et 506h à la date anniversaire, la clause de rattrapage peut "
                       "prolonger la période pour compléter les heures manquantes.",
    },
    "rattrapageDureeMois": {
        "valeur": 6,
        "libelle": "Durée maximale de la clause de rattrapage",
        "source": "Circulaire Unédic n°2018-04",
        "version": "2026.06",
        "dateAppli": "2018-02-07",
        "verifie": False,
        "commentaire": "6 mois maximum pour compléter. Date anniversaire inchangée. "
                       "Décision irrévocable une fois activée.",
    },
    "rattrapageOuverturesMin": {
        "valeur": 5,
        "libelle": "Condition d'éligibilité — ouvertures de droits",
        "source": "Circulaire Unédic n°2018-04",
        "version": "2026.06",
        "dateAppli": "2018-02-07",
        "verifie": False,
        "commentaire": "Éligibilité : au moins 338h ET 5 ouvertures de droits sur 10 ans.",
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

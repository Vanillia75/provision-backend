"""
intermittent_engine.py — Moteur de calcul des droits intermittent du spectacle.

═══════════════════════════════════════════════════════════════════════════════
 RÈGLE D'OR : ce moteur fait un SUIVI INDICATIF, il ne remplace JAMAIS France
 Travail. Il calcule ce qui est calculable de façon fiable (conversion en heures,
 fenêtre glissante, total vers 507h) et n'invente jamais un chiffre incertain.
 AUCUN calcul d'indemnisation en euros ici (niveau B = plus tard, après
 validation terrain). Ce moteur ne fait que le niveau A (les heures) et le
 niveau C (le verdict sur les droits).
═══════════════════════════════════════════════════════════════════════════════

 Règles 2026 (centralisées dans regles_intermittent.py — sourcées, datées) :
   - 507 heures sur 12 mois GLISSANTS pour ouvrir/renouveler les droits.
   - Conversion : 1 cachet (artiste, annexe 10) = 12h ; heures réelles
     (techniciens annexe 8) telles quelles. NOTE : la règle historique
     "cachet groupé = 8h" est ABANDONNÉE (source douteuse) — tous les cachets
     comptent 12h tant qu'un expert n'a pas confirmé un autre forfait.
   - Clause de rattrapage / filet : palier à 338h (Circulaire Unédic 2018-04).
   - Paliers d'évolution d'Hector (émotionnel, ancré dans le réel) :
       100h chiot · 250h ado · 338h filet · 400h adulte · 507h niche complète.

 Le moteur ne lit JAMAIS la base de données : on lui passe une liste d'activités
 (date, type, nombre) et il rend un résultat. Testable isolément, sans backend.
"""

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

from regles_intermittent import valeur_de, tracer, VERSION_REFERENTIEL


# ─────────────────────────────────────────────────────────────────────────────
#  CONSTANTES MÉTIER — toutes issues du référentiel central (source unique).
#  Ne JAMAIS écrire un chiffre réglementaire en dur ici : tout passe par
#  regles_intermittent.py, qui porte la valeur, sa source et sa version.
# ─────────────────────────────────────────────────────────────────────────────
VERSION = VERSION_REFERENTIEL["version"]
DERNIERE_VERIFICATION = VERSION_REFERENTIEL["revue"]

SEUIL_DROITS = valeur_de("seuilHeures")          # 507
FENETRE_JOURS = valeur_de("periodeReferenceJours")  # 365
SEUIL_FILET = valeur_de("rattrapageSeuilMin")    # 338

# Conversion cachet → heures. Tous les cachets comptent 12h (la règle historique
# "cachet groupé = 8h" est abandonnée, cf. regles_intermittent.py).
HEURES_CACHET = valeur_de("cachetHeures")        # 12

# Paliers d'évolution d'Hector (seuil en heures → état). Trié croissant.
# Le palier "filet" est aligné sur le seuil de la clause de rattrapage (référentiel).
PALIERS_HECTOR = [
    (0,             "oeuf",    "On démarre. Chaque heure compte, je note tout."),
    (100,           "chiot",   "Premier palier passé. Doucement mais sûrement."),
    (250,           "ado",     "Tu es à mi-chemin, le rythme est bon."),
    (SEUIL_FILET,   "filet",   "Tu as passé les 338h : une des deux conditions du filet (clause de rattrapage) est remplie. L'autre dépend de ton historique d'ouvertures de droits."),
    (400,           "adulte",  "On y est presque, je le sens."),
    (SEUIL_DROITS,  "niche",   "On l'a fait. Tes droits sont sécurisés. Tellement fier de nous."),
]

AVERTISSEMENT = (
    "Suivi indicatif basé sur les heures que tu déclares. Ne remplace pas le "
    "décompte officiel de France Travail. Vérifie toujours auprès d'eux."
)


# ─────────────────────────────────────────────────────────────────────────────
#  ENTRÉE / SORTIE
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class Activite:
    """Une déclaration d'activité brute (telle que stockée en base)."""
    date: date
    type_activite: str   # "heures" | "cachet_isole" | "cachet_groupe"
    nombre: float        # heures si type="heures", sinon nb de cachets


@dataclass
class ResultatIntermittent:
    total_heures: float
    seuil: int
    manquant: float
    pourcentage: float            # 0..100+ (peut dépasser 100)
    droits_securises: bool        # total >= 507
    filet_atteint: bool           # total >= 338 (SEUIL D'HEURES du filet franchi ;
                                  # PAS le filet acquis : la 2e condition — 5 ouvertures
                                  # sur 10 ans — n'est pas vérifiable par le moteur)
    hector_etat: str              # "oeuf"|"chiot"|"ado"|"filet"|"adulte"|"niche"
    hector_message: str
    verdict: str                  # niveau C : phrase de synthèse bienveillante
    jours_avant_anniversaire: Optional[int] = None
    date_anniversaire: Optional[date] = None
    detail_lignes: list = field(default_factory=list)  # pour la transparence
    regles_appliquees: list = field(default_factory=list)  # trace réglementaire (Pourquoi ?)
    version_referentiel: str = ""
    avertissement: str = AVERTISSEMENT


# ─────────────────────────────────────────────────────────────────────────────
#  CONVERSION : une activité → un nombre d'heures (déterministe)
# ─────────────────────────────────────────────────────────────────────────────
def heures_de(activite: Activite) -> float:
    t = activite.type_activite
    n = max(0.0, float(activite.nombre or 0))
    # Tous les cachets comptent 12h. On gère aussi "cachet_groupe" pour les
    # activités historiques déjà en base : elles sont désormais comptées 12h
    # comme les autres (la règle "8h" est abandonnée, cf. référentiel).
    if t in ("cachet_isole", "cachet_groupe", "cachet"):
        return n * HEURES_CACHET
    if t == "heures":
        return n
    # Type inconnu : on ne devine pas, on compte 0 (le moteur n'invente jamais).
    return 0.0


# ─────────────────────────────────────────────────────────────────────────────
#  PALIER D'HECTOR selon le total d'heures
# ─────────────────────────────────────────────────────────────────────────────
def etat_hector(total_heures: float) -> tuple:
    etat, message = PALIERS_HECTOR[0][1], PALIERS_HECTOR[0][2]
    for seuil, e, msg in PALIERS_HECTOR:
        if total_heures >= seuil:
            etat, message = e, msg
    return etat, message


# ─────────────────────────────────────────────────────────────────────────────
#  VERDICT (niveau C) — le conseil bienveillant d'Hector, basé sur les HEURES
#  uniquement (jamais sur des euros). C'est l'âme du produit.
# ─────────────────────────────────────────────────────────────────────────────
def construire_verdict(total: float, manquant: float, jours_restants: Optional[int]) -> str:
    if total >= SEUIL_DROITS:
        return "Tes droits sont sécurisés. Tu as tes 507h. Profite, je veille."
    if total >= SEUIL_FILET:
        base = (
            f"Il te manque {int(round(manquant))}h pour tes {SEUIL_DROITS}h. "
            f"Bonne nouvelle : tu as passé les {SEUIL_FILET}h, c'est une des conditions de la "
            f"clause de rattrapage (le \"filet\"). L'autre condition dépend de ton historique "
            f"(avoir déjà ouvert des droits 5 fois sur 10 ans) : si c'est ton cas, le filet peut "
            f"jouer. Vérifie ce point avec France Travail pour en être sûr."
        )
    else:
        base = f"Il te manque {int(round(manquant))}h pour sécuriser tes droits."
    if jours_restants is not None:
        if jours_restants <= 0:
            base += " Ta date anniversaire est dépassée — fais le point avec France Travail."
        elif jours_restants <= 60:
            base += f" Plus que {jours_restants} jours avant ta date anniversaire : c'est le moment de garder un œil serré."
        else:
            base += f" Tu as encore {jours_restants} jours avant ta date anniversaire, on a le temps de s'organiser."
    return base


# ─────────────────────────────────────────────────────────────────────────────
#  CALCUL PRINCIPAL
# ─────────────────────────────────────────────────────────────────────────────
def calculer(
    activites: list,
    aujourdhui: Optional[date] = None,
    date_anniversaire: Optional[date] = None,
) -> ResultatIntermittent:
    """
    activites : liste d'Activite (données brutes).
    aujourdhui : date de référence (par défaut = today). Sert à la fenêtre glissante.
    date_anniversaire : échéance des droits (saisie par l'utilisateur), optionnelle.

    Fenêtre glissante : on ne compte que les activités des 365 derniers jours.
    Ce qui sort de la fenêtre est ignoré (mais reste en base = historique).
    """
    if aujourdhui is None:
        aujourdhui = date.today()
    borne_basse = aujourdhui - timedelta(days=FENETRE_JOURS)

    total = 0.0
    detail = []
    for a in activites:
        # On ne compte que ce qui est dans la fenêtre glissante (et pas dans le futur).
        if a.date is None:
            continue
        if a.date < borne_basse or a.date > aujourdhui:
            continue
        h = heures_de(a)
        total += h
        # Trace : quelle règle de conversion a transformé cette activité en heures.
        if a.type_activite == "heures":
            regle_ligne = "Heures réelles (technicien, annexe 8) : comptées telles quelles."
        else:
            regle_ligne = tracer("cachetHeures")
        detail.append({
            "date": a.date.isoformat(),
            "type": a.type_activite,
            "nombre": a.nombre,
            "heures": h,
            "regle": regle_ligne,
        })

    total = round(total, 2)
    manquant = max(0.0, SEUIL_DROITS - total)
    pourcentage = round(total / SEUIL_DROITS * 100, 1) if SEUIL_DROITS else 0.0
    etat, message = etat_hector(total)

    jours_restants = None
    if date_anniversaire is not None:
        jours_restants = (date_anniversaire - aujourdhui).days

    verdict = construire_verdict(total, manquant, jours_restants)

    # Trace réglementaire globale (pour le bouton "Pourquoi ?") : les règles-clés
    # qui ont servi au calcul, avec leur source et leur version.
    regles_appliquees = [
        tracer("seuilHeures"),
        tracer("cachetHeures"),
        tracer("periodeReferenceJours"),
        tracer("rattrapageSeuilMin"),
    ]

    return ResultatIntermittent(
        total_heures=total,
        seuil=SEUIL_DROITS,
        manquant=round(manquant, 2),
        pourcentage=pourcentage,
        droits_securises=total >= SEUIL_DROITS,
        filet_atteint=total >= SEUIL_FILET,
        hector_etat=etat,
        hector_message=message,
        verdict=verdict,
        jours_avant_anniversaire=jours_restants,
        date_anniversaire=date_anniversaire,
        detail_lignes=detail,
        regles_appliquees=regles_appliquees,
        version_referentiel=VERSION,
    )


# ─────────────────────────────────────────────────────────────────────────────
#  SIMULATION D'IMPACT (niveau A + C) — "si j'accepte ce contrat, ça change quoi ?"
#  On ajoute une activité hypothétique et on recalcule. 100% déterministe.
# ─────────────────────────────────────────────────────────────────────────────
def simuler_contrat(
    activites: list,
    contrat: Activite,
    aujourdhui: Optional[date] = None,
    date_anniversaire: Optional[date] = None,
) -> dict:
    avant = calculer(activites, aujourdhui, date_anniversaire)
    apres = calculer(activites + [contrat], aujourdhui, date_anniversaire)
    gain = round(apres.total_heures - avant.total_heures, 2)
    return {
        "heures_ajoutees": gain,
        "total_avant": avant.total_heures,
        "total_apres": apres.total_heures,
        "manquant_apres": apres.manquant,
        "securise_apres": apres.droits_securises,
        "verdict": apres.verdict,
    }

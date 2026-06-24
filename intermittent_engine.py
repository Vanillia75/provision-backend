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

 Règles 2026 (vérifiées, à revérifier chaque année — réformes assurance chômage) :
   - 507 heures sur 12 mois GLISSANTS pour ouvrir/renouveler les droits.
   - Conversion : 1 cachet isolé = 12h, 1 cachet groupé = 8h, heures réelles
     (techniciens annexe 8) telles quelles.
   - Clause de rattrapage / filet : palier symbolique à 338h.
   - Paliers d'évolution d'Hector (émotionnel, ancré dans le réel) :
       100h chiot · 250h ado · 338h filet · 400h adulte · 507h niche complète.

 Le moteur ne lit JAMAIS la base de données : on lui passe une liste d'activités
 (date, type, nombre) et il rend un résultat. Testable isolément, sans backend.
"""

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional


# ─────────────────────────────────────────────────────────────────────────────
#  CONSTANTES MÉTIER (sourcées, datées — modifier UNIQUEMENT ici si la loi change)
# ─────────────────────────────────────────────────────────────────────────────
VERSION = "2026.1"
DERNIERE_VERIFICATION = "2026-06-24"

SEUIL_DROITS = 507          # heures requises sur 12 mois glissants
FENETRE_JOURS = 365         # fenêtre glissante de 12 mois

HEURES_CACHET_ISOLE = 12
HEURES_CACHET_GROUPE = 8

# Paliers d'évolution d'Hector (seuil en heures → état). Trié croissant.
PALIERS_HECTOR = [
    (0,   "oeuf",    "On démarre. Chaque heure compte, je note tout."),
    (100, "chiot",   "Premier palier passé. Doucement mais sûrement."),
    (250, "ado",     "Tu es à mi-chemin, le rythme est bon."),
    (338, "filet",   "Filet de sécurité atteint : tu es protégé même si tu n'atteins pas 507h."),
    (400, "adulte",  "On y est presque, je le sens."),
    (507, "niche",   "On l'a fait. Tes droits sont sécurisés. Tellement fier de nous."),
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
    filet_atteint: bool           # total >= 338
    hector_etat: str              # "oeuf"|"chiot"|"ado"|"filet"|"adulte"|"niche"
    hector_message: str
    verdict: str                  # niveau C : phrase de synthèse bienveillante
    jours_avant_anniversaire: Optional[int] = None
    date_anniversaire: Optional[date] = None
    detail_lignes: list = field(default_factory=list)  # pour la transparence
    avertissement: str = AVERTISSEMENT


# ─────────────────────────────────────────────────────────────────────────────
#  CONVERSION : une activité → un nombre d'heures (déterministe)
# ─────────────────────────────────────────────────────────────────────────────
def heures_de(activite: Activite) -> float:
    t = activite.type_activite
    n = max(0.0, float(activite.nombre or 0))
    if t == "cachet_isole":
        return n * HEURES_CACHET_ISOLE
    if t == "cachet_groupe":
        return n * HEURES_CACHET_GROUPE
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
    if total >= 338:
        base = f"Il te manque {int(round(manquant))}h pour tes 507h, mais tu as déjà ton filet de sécurité (338h passées)."
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
        detail.append({
            "date": a.date.isoformat(),
            "type": a.type_activite,
            "nombre": a.nombre,
            "heures": h,
        })

    total = round(total, 2)
    manquant = max(0.0, SEUIL_DROITS - total)
    pourcentage = round(total / SEUIL_DROITS * 100, 1) if SEUIL_DROITS else 0.0
    etat, message = etat_hector(total)

    jours_restants = None
    if date_anniversaire is not None:
        jours_restants = (date_anniversaire - aujourdhui).days

    verdict = construire_verdict(total, manquant, jours_restants)

    return ResultatIntermittent(
        total_heures=total,
        seuil=SEUIL_DROITS,
        manquant=round(manquant, 2),
        pourcentage=pourcentage,
        droits_securises=total >= SEUIL_DROITS,
        filet_atteint=total >= 338,
        hector_etat=etat,
        hector_message=message,
        verdict=verdict,
        jours_avant_anniversaire=jours_restants,
        date_anniversaire=date_anniversaire,
        detail_lignes=detail,
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

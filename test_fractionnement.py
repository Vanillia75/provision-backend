"""
test_fractionnement.py — Neutralisation/allongement de la période de référence
(le « fractionnement » du concurrent).

Écrits AVANT le code moteur. Sources : cf. MOTEUR_PERIODE_REFERENCE_SOURCES.md.

RÈGLE (guide FT p.5) : une maladie ordinaire entre deux contrats (y compris le
congé paternité), indemnisée, est NEUTRALISÉE : elle n'ajoute AUCUNE heure, mais
allonge la fenêtre de 365 jours d'autant (10 j d'arrêt = 10 j de plus pour chercher
les 507h, en remontant plus loin). C'est le mécanisme B (distinct de l'assimilation
5h/jour, mécanisme A, déjà livré).

Types neutralisés (mécanisme B) : arret_maladie_ordinaire, arret_paternite.
Ils comptent 0h. Tout allongement pose `arret_estimation = True` (branche non
validée + conditions non vérifiables).
"""
from datetime import date, timedelta

from intermittent_engine import Activite, calculer, heures_de

AUJOURDHUI = date(2026, 7, 3)  # fenêtre de base : [2025-07-03, 2026-07-03]


def J(d):
    return date.fromisoformat(d)


# ── Un arrêt neutralisé ne compte AUCUNE heure ───────────────────────────────
def test_neutralise_zero_heure():
    assert heures_de(Activite(date=AUJOURDHUI, type_activite="arret_maladie_ordinaire", nombre=20)) == 0.0
    assert heures_de(Activite(date=AUJOURDHUI, type_activite="arret_paternite", nombre=25)) == 0.0


# ── L'allongement fait entrer un contrat sinon hors fenêtre ──────────────────
def test_maladie_allonge_la_fenetre():
    # Cachet daté 13 j AVANT la borne de base (2025-06-20 < 2025-07-03) → hors fenêtre normalement.
    cachet = Activite(date=J("2025-06-20"), type_activite="cachet_isole", nombre=1)  # 12h
    # Maladie de 20 j dans la fenêtre → recule la borne de 20 j → le cachet rentre.
    maladie = Activite(date=J("2025-08-01"), type_activite="arret_maladie_ordinaire", nombre=20)

    sans = calculer([cachet], aujourdhui=AUJOURDHUI)
    assert sans.total_heures == 0.0  # cachet hors fenêtre de base

    avec = calculer([cachet, maladie], aujourdhui=AUJOURDHUI)
    assert avec.total_heures == 12.0             # le cachet est désormais compté
    assert avec.jours_allonges == 20
    assert avec.arret_estimation is True


def test_paternite_allonge_aussi():
    cachet = Activite(date=J("2025-06-25"), type_activite="cachet_isole", nombre=1)  # hors base de 8 j
    pat = Activite(date=J("2025-09-01"), type_activite="arret_paternite", nombre=25)
    r = calculer([cachet, pat], aujourdhui=AUJOURDHUI)
    assert r.total_heures == 12.0
    assert r.jours_allonges == 25


def test_sans_arret_neutralise_fenetre_inchangee():
    cachet = Activite(date=J("2025-06-20"), type_activite="cachet_isole", nombre=1)  # hors fenêtre
    r = calculer([cachet], aujourdhui=AUJOURDHUI)
    assert r.total_heures == 0.0        # pas d'allongement → reste hors fenêtre
    assert r.jours_allonges == 0
    assert r.arret_estimation is False


def test_arret_neutralise_hors_fenetre_n_allonge_pas():
    # Une maladie ANTÉRIEURE à la borne de base ne compte pas pour l'allongement.
    maladie = Activite(date=J("2025-06-01"), type_activite="arret_maladie_ordinaire", nombre=30)
    cachet = Activite(date=J("2026-01-10"), type_activite="cachet_isole", nombre=1)  # dans la fenêtre
    r = calculer([cachet, maladie], aujourdhui=AUJOURDHUI)
    assert r.jours_allonges == 0
    assert r.total_heures == 12.0  # juste le cachet


def test_neutralise_n_ajoute_pas_d_heures_meme_dans_la_fenetre():
    maladie = Activite(date=J("2026-03-01"), type_activite="arret_maladie_ordinaire", nombre=15)
    r = calculer([maladie], aujourdhui=AUJOURDHUI)
    assert r.total_heures == 0.0       # 0h (neutralisation, pas assimilation)
    assert r.jours_allonges == 15
    assert r.arret_estimation is True


# ── Cohabitation avec le mécanisme A (assimilation 5h/jour) ──────────────────
def test_melange_neutralise_et_assimile():
    # Maternité (assimile 560h) + maladie ordinaire (allonge, 0h) : total = 560h.
    mat = Activite(date=J("2026-02-27"), type_activite="arret_maternite", nombre=112)   # 560h
    mal = Activite(date=J("2025-09-15"), type_activite="arret_maladie_ordinaire", nombre=10)  # 0h, +10j
    r = calculer([mat, mal], aujourdhui=AUJOURDHUI)
    assert r.total_heures == 560.0
    assert r.jours_allonges == 10
    assert r.arret_estimation is True

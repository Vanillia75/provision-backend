"""
test_conges_spectacles.py — Estimation de l'indemnité Congés Spectacles (Audiens).

Écrits AVANT le code. Backtest sur 2 bordereaux Audiens RÉELS :
  - exercice 2023-2024 : assiette 7 381 € → ICP brut 738,10 € (10 % exact), net 567,94 €
  - exercice 2024-2025 : assiette 10 055 € → ICP brut 1 005,50 € (10 % exact), net 773,78 €
Le net est une estimation (≈ 76,95 % du brut) → tolérance dans les tests.
Cf. CONGES_SPECTACLES_ETUDE.md.
"""
from datetime import date
from types import SimpleNamespace

from conges_spectacles import calculer, exercice_en_cours


def A(d, brut, type_activite="cachet_isole"):
    return SimpleNamespace(date=date.fromisoformat(d), type_activite=type_activite, salaire_brut=brut)


# ── Fenêtre de l'exercice (1 avril → 31 mars) ────────────────────────────────
def test_exercice_en_cours():
    assert exercice_en_cours(date(2026, 7, 3)) == (date(2026, 4, 1), date(2027, 3, 31))
    assert exercice_en_cours(date(2026, 2, 10)) == (date(2025, 4, 1), date(2026, 3, 31))
    assert exercice_en_cours(date(2026, 4, 1)) == (date(2026, 4, 1), date(2027, 3, 31))


# ── BACKTESTS RÉELS (Audiens) ────────────────────────────────────────────────
def test_backtest_2023_2024():
    r = calculer([A("2023-06-01", 7381.0)], date(2023, 4, 1), date(2024, 3, 31))
    assert r["assiette"] == 7381.0
    assert r["icp_brut"] == 738.10               # 10 % au centime
    assert abs(r["icp_net"] - 567.94) <= 1.0     # net estimé (~76,95 %)


def test_backtest_2024_2025():
    r = calculer([A("2024-06-01", 10055.0)], date(2024, 4, 1), date(2025, 3, 31))
    assert r["icp_brut"] == 1005.50
    assert abs(r["icp_net"] - 773.78) <= 1.0


# ── Somme de plusieurs activités ─────────────────────────────────────────────
def test_somme_plusieurs_activites():
    r = calculer([A("2024-05-01", 500.0), A("2024-09-01", 2640.0), A("2024-12-01", 476.0)],
                 date(2024, 4, 1), date(2025, 3, 31))
    assert r["assiette"] == 3616.0
    assert r["icp_brut"] == 361.60


# ── Fenêtre : hors exercice ignoré ───────────────────────────────────────────
def test_hors_exercice_ignore():
    r = calculer([A("2023-01-01", 5000.0)], date(2024, 4, 1), date(2025, 3, 31))
    assert r["assiette"] == 0.0
    assert r["icp_brut"] == 0.0


# ── Incomplétude signalée (bruts manquants) ──────────────────────────────────
def test_incompletude_signalee():
    r = calculer([A("2024-05-01", 500.0), A("2024-06-01", None)], date(2024, 4, 1), date(2025, 3, 31))
    assert r["assiette"] == 500.0
    assert r["assiette_incomplete"] is True
    assert r["activites_sans_brut"] == 1


def test_complet_pas_de_drapeau():
    r = calculer([A("2024-05-01", 500.0)], date(2024, 4, 1), date(2025, 3, 31))
    assert r["assiette_incomplete"] is False


# ── Seules les activités de TRAVAIL portent un salaire ───────────────────────
def test_arrets_formation_ne_comptent_pas():
    acts = [A("2024-05-01", 500.0),
            A("2024-06-01", 999.0, "arret_maternite"),
            A("2024-07-01", 999.0, "formation"),
            A("2024-08-01", 999.0, "enseignement")]
    r = calculer(acts, date(2024, 4, 1), date(2025, 3, 31))
    assert r["assiette"] == 500.0   # seul le cachet compte


def test_toujours_estimation():
    r = calculer([A("2024-05-01", 500.0)], date(2024, 4, 1), date(2025, 3, 31))
    assert r["estimation"] is True

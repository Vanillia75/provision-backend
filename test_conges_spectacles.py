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


# ── PLAFOND CONVENTIONNEL DE L'ASSIETTE (ajouté le 15/08/2026) ──────────────
#  Trouvé par l'audit à la source. On sommait le brut INTÉGRAL, sans plafond,
#  alors que l'assiette de la cotisation Congés Spectacles est plafonnée par
#  jour (article D.7121-37, fiche Audiens). On surestimait donc l'indemnité des
#  artistes très bien payés, c'est-à-dire qu'on leur annonçait de l'argent qui
#  n'arriverait pas.

from conges_spectacles import PLAFOND_JOURNALIER_GENERAL


class _Act:
    def __init__(self, jour, type_activite, nombre, salaire_brut):
        self.date = jour
        self.type_activite = type_activite
        self.nombre = nombre
        self.salaire_brut = salaire_brut


_DEBUT, _FIN = date(2026, 4, 1), date(2027, 3, 31)


def test_un_cachet_ordinaire_n_est_pas_plafonne():
    """Le cas de tout le monde : le plafond ne mord pas, et le calcul
    backtesté au centime sur deux bordereaux Audiens réels reste intact."""
    # 3 cachets a 250 EUR = 750 EUR : sous le plafond de 272 EUR/jour.
    r = calculer([_Act(date(2026, 5, 10), "cachet_isole", 3, 750.0)], _DEBUT, _FIN)
    assert r["assiette"] == 750.0
    assert r["icp_brut"] == 75.0
    assert r["plafond_journalier_applique"] is False


def test_un_cachet_tres_bien_paye_est_plafonne_a_272_par_jour():
    # 2 cachets à 500 € = 1 000 €, mais l'assiette est plafonnée à 272 €/jour.
    r = calculer([_Act(date(2026, 5, 10), "cachet_isole", 2, 1000.0)], _DEBUT, _FIN)
    assert r["assiette"] == PLAFOND_JOURNALIER_GENERAL * 2
    assert r["plafond_journalier_applique"] is True
    assert r["brut_ecarte_par_plafond"] == 1000.0 - PLAFOND_JOURNALIER_GENERAL * 2


def test_le_plafond_ne_touche_pas_les_heures():
    """On ne sait pas déduire un montant PAR JOUR depuis des heures : on ne
    plafonne donc pas, plutôt que de supposer."""
    r = calculer([_Act(date(2026, 5, 10), "heures", 100, 5000.0)], _DEBUT, _FIN)
    assert r["assiette"] == 5000.0
    assert r["plafond_journalier_applique"] is False


def test_l_ecart_est_dit_pour_que_l_ecran_puisse_le_nuancer():
    """Le plafond dépend de la catégorie (272 / 375 / 860 €) et on ne la
    connaît pas : quand il mord, la personne doit pouvoir le savoir."""
    r = calculer([_Act(date(2026, 5, 10), "cachet_isole", 1, 900.0)], _DEBUT, _FIN)
    assert r["plafond_journalier_applique"] is True
    assert r["brut_ecarte_par_plafond"] > 0

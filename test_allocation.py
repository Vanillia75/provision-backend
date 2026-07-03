"""
test_allocation.py — Moteur de l'allocation journalière (chantier AJ).

Écrits AVANT le code moteur (discipline du moteur sacré). Les réponses attendues
viennent de sources qui font autorité (cf. MOTEUR_AJ_SOURCES.md) :

  1. Guide officiel France Travail Intermittents, exemple 6 (p.11) : technicien
     annexe 8, 800 h, 18 000 € → AJ brute 64,78 €.
  2. Guide officiel, exemple 12 (p.17) : mois type annexe 8, AJ 140 €, 80 h et
     4 000 € bruts, avril 2024 (30 j, plafond cumul 4 559,52 €) → ARE 559,52 €.
  3. BACKTEST RÉEL n°1 : notification France Travail du 29/06/2026 (annexe 10),
     SR 8 537,10 €, NHT 636 h → AJ nette officielle 51,18 €. Vérifié à 0,00 €.

Règle d'or : le moteur ne doit jamais AFFIRMER un chiffre incertain — quand une
branche non validée par un cas réel est utilisée (CSG au-delà de 60 €, arrondis
fractionnaires du mois type), le résultat porte un drapeau d'estimation.
"""
import math

from allocation_engine import calculer_aj, calculer_mois, branche_affichable


# ─────────────────────────────────────────────────────────────────────────────
#  1. L'ALLOCATION JOURNALIÈRE — cas officiels et réels
# ─────────────────────────────────────────────────────────────────────────────
def test_exemple_officiel_guide_annexe_8():
    """Guide France Travail, exemple 6 : 800 h, 18 000 € → 64,78 € brute."""
    r = calculer_aj("annexe8", sr=18000.0, nht=800.0)
    assert r["partie_a"] == 39.80
    assert r["partie_b"] == 12.20
    assert r["partie_c"] == 12.78
    assert r["aj_brute"] == 64.78


def test_backtest_reel_annexe_10_51_18():
    """BACKTEST RÉEL n°1 — notification du 29/06/2026 : 51,18 € nets, au centime."""
    r = calculer_aj("annexe10", sr=8537.10, nht=636.0)
    assert r["partie_a"] == 19.64
    assert r["partie_b"] == 10.42
    assert r["partie_c"] == 22.37
    assert r["aj_brute"] == 52.43
    assert r["retenue_retraite"] == 1.25
    assert r["aj_nette"] == 51.18
    assert r["nette_estimee"] is False, "branche ≤ 60 € validée par cas réel : chiffre exact"


def test_plancher_annexe_10():
    """Toute petite activité : l'AJ ne descend jamais sous 44 € (artistes)."""
    r = calculer_aj("annexe10", sr=1000.0, nht=50.0)
    assert r["aj_brute"] == 44.0
    assert r["plancher_applique"] is True


def test_plancher_annexe_8():
    r = calculer_aj("annexe8", sr=1000.0, nht=50.0)
    assert r["aj_brute"] == 38.0
    assert r["plancher_applique"] is True


def test_plafond_aj():
    """Très gros salaires : l'AJ est plafonnée à 174,80 €."""
    r = calculer_aj("annexe8", sr=500000.0, nht=2000.0)
    assert r["aj_brute"] == 174.80
    assert r["plafond_applique"] is True


def test_aj_sous_31_96_aucune_retenue():
    """AJ brute ≤ 31,96 € : net = brut (mais plancher 38/44 s'applique avant —
    ce cas ne peut donc exister qu'en théorie ; le moteur doit rester cohérent)."""
    r = calculer_aj("annexe10", sr=1000.0, nht=50.0)
    # plancher 44 € > 31,96 → retenue retraite due
    assert r["aj_nette"] == round(44.0 - r["retenue_retraite"], 2)


def test_aj_au_dela_de_60_marquee_estimation():
    """Au-delà de 60 €, la CSG entre en jeu : assiette non validée par un cas réel
    → le net doit être marqué comme ESTIMATION (Loi I : on n'affirme pas)."""
    r = calculer_aj("annexe8", sr=18000.0, nht=800.0)  # brute 64,78 > 60
    assert r["aj_brute"] == 64.78
    assert r["nette_estimee"] is True
    assert r["aj_nette"] < r["aj_brute"]


def test_annexe_inconnue_refusee():
    """Le moteur n'invente jamais : annexe inconnue → erreur explicite."""
    try:
        calculer_aj("annexe12", sr=10000.0, nht=500.0)
        assert False, "aurait dû lever une erreur"
    except ValueError:
        pass


# ─────────────────────────────────────────────────────────────────────────────
#  2. LE MOIS TYPE — combien de jours payés ce mois-ci
# ─────────────────────────────────────────────────────────────────────────────
def test_exemple_officiel_guide_mois_type():
    """Guide France Travail, exemple 12 : A8, AJ 140 €, 80 h + 4 000 € bruts,
    avril 2024 (30 j), PMSS 2024 = 3 864 € → plafond 4 559,52 € → ARE 559,52 €."""
    r = calculer_mois("annexe8", aj_brute=140.0, heures_mois=80.0,
                      remunerations_brutes=4000.0, jours_calendaires=30,
                      pmss_mensuel=3864.0)
    assert r["jours_travailles"] == 10
    assert r["seuil_atteint"] is False
    assert r["jours_non_indemnisables"] == 14
    assert r["jours_indemnisables"] == 16
    assert r["are_avant_plafond"] == 2240.0
    assert r["plafond_cumul"] == 4559.52
    assert r["plafond_cumul_applique"] is True
    assert r["are_versee"] == 559.52


def test_mois_sans_activite():
    """Aucune heure : tous les jours du mois sont indemnisables."""
    r = calculer_mois("annexe10", aj_brute=52.43, heures_mois=0.0,
                      remunerations_brutes=0.0, jours_calendaires=31)
    assert r["jours_indemnisables"] == 31
    assert r["are_versee"] == round(52.43 * 31, 2)
    assert r["plafond_cumul_applique"] is False


def test_mois_seuil_non_indemnisation_annexe_8():
    """26 jours de travail (208 h / 8) atteints → aucune indemnisation le mois."""
    r = calculer_mois("annexe8", aj_brute=64.78, heures_mois=208.0,
                      remunerations_brutes=5000.0, jours_calendaires=30)
    assert r["seuil_atteint"] is True
    assert r["are_versee"] == 0.0


def test_mois_artiste_cachets():
    """Artiste, 5 cachets (60 h) dans un mois de 30 jours, AJ 52,43 € :
    jours travaillés = 60/10 = 6 ; décalage = 6 × 1,3 = 7,8 ; le résultat
    fractionnaire est marqué comme approximatif (arrondi non sourcé)."""
    r = calculer_mois("annexe10", aj_brute=52.43, heures_mois=60.0,
                      remunerations_brutes=800.0, jours_calendaires=30)
    assert r["jours_travailles"] == 6
    assert r["arrondi_approximatif"] is True  # 7,8 jours : l'arrondi exact n'est pas sourcé
    # L'ARE reste dans une fourchette plausible : entre (30-8) et (30-7) jours
    assert round(52.43 * 22, 2) <= r["are_versee"] <= round(52.43 * 23, 2)


def test_mois_exact_pas_de_drapeau():
    """Quand tout tombe juste (exemple 12), aucun drapeau d'approximation."""
    r = calculer_mois("annexe8", aj_brute=140.0, heures_mois=80.0,
                      remunerations_brutes=4000.0, jours_calendaires=30,
                      pmss_mensuel=3864.0)
    assert r["arrondi_approximatif"] is False


# ─────────────────────────────────────────────────────────────────────────────
#  3. LOI X — ce qu'Hector a le DROIT d'afficher
# ─────────────────────────────────────────────────────────────────────────────
def test_affichable_artiste_sous_60_le_cas_reel():
    """La seule branche validée sur cas réel : annexe 10, AJ ≤ 60 € (cas Héloïse)."""
    r = calculer_aj("annexe10", sr=8537.10, nht=636.0)  # 51,18 € net
    affichable, raison = branche_affichable("annexe10", r)
    assert affichable is True
    assert raison is None


def test_non_affichable_technicien():
    """Annexe 8 : aucune notification réelle ne l'a jugée → jamais affiché (Loi X)."""
    r = calculer_aj("annexe8", sr=18000.0, nht=800.0)
    affichable, raison = branche_affichable("annexe8", r)
    assert affichable is False
    assert raison == "technicien"


def test_non_affichable_au_dela_60():
    """Artiste mais AJ > 60 € : la CSG entre en jeu, assiette non validée → interdit."""
    r = calculer_aj("annexe10", sr=60000.0, nht=900.0)  # AJ brute > 60
    assert r["aj_brute"] > 60
    affichable, raison = branche_affichable("annexe10", r)
    assert affichable is False
    assert raison == "au_dela_60"


def test_mois_remunerations_seules_depassent_plafond():
    """Si les salaires seuls dépassent le plafond de cumul : ARE = 0 (guide p.17)."""
    r = calculer_mois("annexe8", aj_brute=140.0, heures_mois=80.0,
                      remunerations_brutes=5000.0, jours_calendaires=30,
                      pmss_mensuel=3864.0)
    assert r["are_versee"] == 0.0
    assert r["plafond_cumul_applique"] is True

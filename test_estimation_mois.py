# ════════════════════════════════════════════════════════════════════════
#  Tests de l'estimation du mois civil (estimer_mois_civil).
#  Décision Camille 24/07/2026 : écran mensuel lancé en mode ESTIMATION
#  assumée avant backtest sur relevé réel. Discipline conservée : zone
#  validée seulement (annexe 10, ≤ 60 €, sans CSG), drapeau `approximatif`
#  dès qu'un arrondi non sourcé, un brut manquant ou un prorata entre en jeu.
# ════════════════════════════════════════════════════════════════════════
from datetime import date

from allocation_engine import calculer_aj, estimer_mois_civil

# La même AJ que les tests de projection : artiste, 40 cachets à 130 €,
# zone validée (aj_brute ≤ 60 €, donc CSG nulle et net fiable).
RES_AJ = calculer_aj("annexe10", sr=5200.0, nht=480.0)


def _cachet(d, nombre=1, brut=100.0):
    return {"date": d, "type_activite": "cachet_isole", "nombre": nombre, "salaire_brut": brut}


def test_mois_type_artiste():
    # Juillet 2026 (31 j), 3 cachets (36 h) : jt = 36/10 = 3,6 → TRONQUÉ à 3 ;
    # décalage = 3,6 × 1,3 = 4,68 → TRONQUÉ à 4 ; indemnisables = 31 − 4 = 27.
    # (Troncature = règle observée sur relevé réel, cas n°2 : 6,04 → 6 ; 10,5 → 10.)
    acts = [_cachet(date(2026, 7, 10), nombre=3, brut=300.0)]
    r = estimer_mois_civil("annexe10", RES_AJ, acts, 2026, 7)
    assert r["jours_calendaires"] == 31
    assert r["heures_mois"] == 36.0
    assert r["jours_travailles"] == 3
    assert r["jours_non_indemnisables"] == 4
    assert r["jours_indemnisables"] == 27
    assert r["net_estime"] == round(27 * RES_AJ["aj_nette"], 2)
    # Fractionnaire : la troncature n'est pas encore observée sur un relevé
    # annexe 10 → on l'assume comme estimation.
    assert r["approximatif"] is True


def test_troncature_conforme_au_releve_reel():
    # Le cas VALIDÉ du relevé du 14/04/2026 (annexe 8) : 34,5 h → (34,5/8) × 1,4
    # = 6,04 → 6 jours retenus par France Travail (tronqué, pas arrondi).
    r = estimer_mois_civil("annexe8", RES_AJ, [
        {"date": date(2026, 7, 8), "type_activite": "heures", "nombre": 34.5, "salaire_brut": 500.0},
    ], 2026, 7)
    assert r["jours_non_indemnisables"] == 6


def test_mois_vide_tout_indemnise():
    r = estimer_mois_civil("annexe10", RES_AJ, [], 2026, 7)
    assert r["jours_indemnisables"] == 31
    assert r["net_estime"] == round(31 * RES_AJ["aj_nette"], 2)
    assert r["approximatif"] is False


def test_fevrier_28_jours():
    r = estimer_mois_civil("annexe10", RES_AJ, [], 2026, 2)
    assert r["jours_calendaires"] == 28
    assert r["net_estime"] == round(28 * RES_AJ["aj_nette"], 2)


def test_activites_des_autres_mois_ignorees():
    acts = [_cachet(date(2026, 6, 30), nombre=10), _cachet(date(2026, 8, 1), nombre=10)]
    r = estimer_mois_civil("annexe10", RES_AJ, acts, 2026, 7)
    assert r["heures_mois"] == 0.0
    assert r["jours_indemnisables"] == 31


def test_formation_exclue_du_decalage():
    acts = [{"date": date(2026, 7, 5), "type_activite": "formation", "nombre": 100, "salaire_brut": None}]
    r = estimer_mois_civil("annexe10", RES_AJ, acts, 2026, 7)
    assert r["heures_mois"] == 0.0
    assert r["jours_indemnisables"] == 31
    assert r["bruts_manquants"] is False  # la formation n'a pas de brut à réclamer


def test_autre_salaire_est_converti_en_heures_et_decale():
    """⚠️ COMPORTEMENT CHANGÉ LE 15/08/2026, après l'audit à la source.

    Avant, un salaire hors spectacle comptait pour le plafond de cumul mais ne
    décalait AUCUN jour, faute d'un SMIC horaire sourcé dans le référentiel. Le
    choix était honnête mais il sous-estimait le décalage.

    Guide France Travail p.16 : « nombre d'heures = rémunération mensuelle brute
    / SMIC horaire ». Le SMIC est désormais au référentiel, historisé par date
    (12,31 €/h depuis le 01/06/2026)."""
    acts = [{"date": date(2026, 7, 3), "type_activite": "autre_salaire", "nombre": 1, "salaire_brut": 50000.0}]
    r = estimer_mois_civil("annexe10", RES_AJ, acts, 2026, 7)
    assert r["heures_mois"] > 0.0, "le salaire hors spectacle doit désormais compter des heures"
    assert abs(r["heures_mois"] - 50000.0 / 12.31) < 1.0
    # L'allocation tombe a zero, comme avant. Mais la RAISON a change, et c'est
    # plus juste : ce ne sont plus les euros qui saturent le plafond de cumul,
    # ce sont les HEURES equivalentes qui ne laissent aucun jour indemnisable.
    # C'est exactement ce que fait France Travail.
    assert r["are_versee"] == 0.0
    assert r["net_estime"] == 0.0
    assert r["approximatif"] is True


def test_petit_salaire_hors_spectacle_decale_a_proportion():
    """1 231 € bruts au SMIC horaire = 100 heures, donc du décalage réel."""
    acts = [{"date": date(2026, 7, 3), "type_activite": "autre_salaire", "nombre": 1, "salaire_brut": 1231.0}]
    r = estimer_mois_civil("annexe10", RES_AJ, acts, 2026, 7)
    assert abs(r["heures_mois"] - 100.0) < 0.5, r["heures_mois"]


def test_brut_manquant_leve_le_drapeau():
    acts = [_cachet(date(2026, 7, 10), nombre=3, brut=None)]
    r = estimer_mois_civil("annexe10", RES_AJ, acts, 2026, 7)
    assert r["bruts_manquants"] is True
    assert r["approximatif"] is True


def test_prorata_plafond_signale():
    # Plafond de cumul partiellement rogné : le net passe par un prorata → signalé.
    acts = [{"date": date(2026, 7, 3), "type_activite": "autre_salaire", "nombre": 1, "salaire_brut": 4500.0}]
    r = estimer_mois_civil("annexe10", RES_AJ, acts, 2026, 7)
    if r["plafond_cumul_applique"] and r["are_versee"] > 0:
        assert r["prorata_plafond"] is True
        assert r["approximatif"] is True
        assert r["net_estime"] < round(r["jours_indemnisables"] * RES_AJ["aj_nette"], 2)


def test_seuil_mois_trop_travaille():
    # 27 jours travaillés (270 h en annexe 10) → seuil atteint, rien d'indemnisé.
    acts = [_cachet(date(2026, 7, 1), nombre=23, brut=2300.0)]
    r = estimer_mois_civil("annexe10", RES_AJ, acts, 2026, 7)
    assert r["seuil_atteint"] is True
    assert r["net_estime"] == 0.0


def test_le_net_du_jour_est_celui_de_la_carte_allocation():
    # Cohérence app : le net mensuel est EXACTEMENT jours × aj_nette de la carte
    # allocation (même moteur, même chiffre, pas deux vérités).
    r = estimer_mois_civil("annexe10", RES_AJ, [], 2026, 7)
    assert r["net_estime"] == round(r["jours_indemnisables"] * RES_AJ["aj_nette"], 2)

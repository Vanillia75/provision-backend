# ════════════════════════════════════════════════════════════════════════
#  « Ton mois » au TAUX OFFICIEL (retour réel du 04/08/2026) : quand le
#  profil n'a pas le trio SR/heures/annexe mais possède le montant officiel
#  importé de l'ARE, la carte calcule quand même, avec ce taux-là.
#  Backtest gagné le même jour (cas réel n°4) : 6 cachets en juillet,
#  31 − 9 jours de décalage = 22 j × 45,29 = 996,38 € = versement réel.
# ════════════════════════════════════════════════════════════════════════
from datetime import date

import allocation_engine as ae
from api import _annexe_depuis_activites


class _Row:
    """Ligne d'activité minimale, comme le modèle DB la présente."""
    def __init__(self, type_activite, nombre, metier=None):
        self.type_activite = type_activite
        self.nombre = nombre
        self.metier = metier


def test_backtest_juillet_au_taux_officiel():
    activites = [
        {"date": date(2026, 7, 3), "type_activite": "cachet_isole", "nombre": 3, "salaire_brut": 300.0},
        {"date": date(2026, 7, 13), "type_activite": "cachet_isole", "nombre": 1, "salaire_brut": 100.0},
        {"date": date(2026, 7, 15), "type_activite": "cachet_isole", "nombre": 1, "salaire_brut": 180.0},
        {"date": date(2026, 7, 18), "type_activite": "cachet_isole", "nombre": 1, "salaire_brut": 200.0},
    ]
    # Le taux officiel remplace le recalcul : c'est exactement le repli du endpoint.
    res_aj = {"aj_brute": 45.29, "aj_nette": 45.29}
    m = ae.estimer_mois_civil("annexe10", res_aj, activites, 2026, 7)
    assert m["heures_mois"] == 72.0
    assert m["jours_indemnisables"] == 22
    assert m["net_estime"] == 996.38


def test_annexe_votee_par_les_cachets():
    # Des cachets = artiste par nature -> annexe 10.
    rows = [_Row("cachet_isole", 3), _Row("cachet_isole", 1)]
    assert _annexe_depuis_activites(rows) == "annexe10"


def test_annexe_votee_par_les_heures_technicien():
    rows = [_Row("heures", 100, metier="technicien")]
    assert _annexe_depuis_activites(rows) == "annexe8"


def test_annexe_indecidable_on_redemande():
    # Heures sans métier départagé + formation : aucun vote -> None (on ne devine pas).
    rows = [_Row("heures", 50), _Row("formation", 20)]
    assert _annexe_depuis_activites(rows) is None

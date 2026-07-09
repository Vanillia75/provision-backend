# -*- coding: utf-8 -*-
"""Tests du moteur de la Paie d'Hector (salaire lissé AE)."""
from paie_engine import calculer_paie


def test_paie_reguliere():
    """6 mois stables autour de 1 850 : la paie recommandée colle à la médiane."""
    r = calculer_paie([1800, 1900, 1850, 1820, 1880, 1860])
    assert r["historique_suffisant"] is True
    assert r["recommande"] == 1850  # médiane (1850+1860)/2 = 1855 → dizaine inf.
    assert r["prudent"] <= r["recommande"] <= r["maximum"]


def test_paie_montagnes_russes():
    """Un profil 4000/800 : la médiane lisse, le prudent protège des creux."""
    r = calculer_paie([4000, 800, 3500, 900, 4200, 1000])
    assert r["recommande"] == 2250  # médiane (1000+3500)/2
    assert r["prudent"] == 900      # moyenne des 3 plus faibles (800+900+1000)/3
    assert r["maximum"] == 2250     # dernier mois (1000) < médiane → médiane


def test_maximum_suit_un_bon_dernier_mois():
    r = calculer_paie([1500, 1600, 1400, 1550, 1500, 4260])
    assert r["maximum"] == 4260
    assert r["recommande"] < r["maximum"]


def test_historique_insuffisant():
    """Moins de 3 mois avec de l'activité : pas de paie, Totor le dit."""
    r = calculer_paie([0, 0, 0, 0, 2000, 1800])
    assert r["historique_suffisant"] is False
    assert r["recommande"] is None


def test_trois_mois_suffisent():
    r = calculer_paie([0, 0, 0, 1200, 1400, 1300])
    assert r["historique_suffisant"] is True


def test_jamais_negatif_et_dizaines():
    r = calculer_paie([1234.56, 987.65, 1111.11, 1555.55, 1333.33, 1444.44])
    for cle in ("prudent", "recommande", "maximum"):
        assert r[cle] >= 0
        assert r[cle] % 10 == 0


def test_liste_vide():
    r = calculer_paie([])
    assert r["historique_suffisant"] is False


def test_ordre_toujours_garanti():
    r = calculer_paie([100, 5000, 100, 5000, 100, 5000])
    assert r["prudent"] <= r["recommande"] <= r["maximum"]

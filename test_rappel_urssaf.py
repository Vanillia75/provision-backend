# -*- coding: utf-8 -*-
"""Tests du calcul de la période URSSAF à déclarer (rappel d'échéance AE).

Règle : le CA du mois M se déclare pendant M+1 (limite = dernier jour de M+1) ;
le trimestre civil écoulé se déclare le mois suivant sa fin (échéances fin
janvier, avril, juillet, octobre)."""
from datetime import date

from tax_engine import periode_urssaf_a_declarer


def test_mensuelle_mois_courant():
    label, limite = periode_urssaf_a_declarer(date(2026, 7, 20), "mensuelle")
    assert label == "juin 2026"
    assert limite == date(2026, 7, 31)


def test_mensuelle_janvier_bascule_annee():
    label, limite = periode_urssaf_a_declarer(date(2026, 1, 25), "mensuelle")
    assert label == "décembre 2025"
    assert limite == date(2026, 1, 31)


def test_trimestrielle_mois_echeance():
    label, limite = periode_urssaf_a_declarer(date(2026, 7, 20), "trimestrielle")
    assert label == "2e trimestre 2026"
    assert limite == date(2026, 7, 31)


def test_trimestrielle_janvier_t4_annee_precedente():
    label, limite = periode_urssaf_a_declarer(date(2026, 1, 20), "trimestrielle")
    assert label == "4e trimestre 2025"
    assert limite == date(2026, 1, 31)


def test_trimestrielle_hors_echeance():
    assert periode_urssaf_a_declarer(date(2026, 8, 20), "trimestrielle") is None


def test_premier_trimestre_ordinal():
    label, _ = periode_urssaf_a_declarer(date(2026, 4, 20), "trimestrielle")
    assert label == "1er trimestre 2026"


def test_fevrier_dernier_jour():
    _, limite = periode_urssaf_a_declarer(date(2026, 2, 21), "mensuelle")
    assert limite == date(2026, 2, 28)

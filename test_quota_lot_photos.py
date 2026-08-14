# -*- coding: utf-8 -*-
# ════════════════════════════════════════════════════════════════════════
#  UN DOCUMENT PHOTOGRAPHIÉ EN PLUSIEURS PAGES NE COÛTE QU'UN SCAN.
#
#  Trouvé le 14/08/2026 par la relecture croisée. Le code de la lecture groupée
#  affirmait par écrit « UN SEUL scan decompte : c'est un seul document, meme
#  s'il tient en 3 photos »… mais les trois lectures page par page qui la
#  précèdent en avaient déjà décompté trois. Une FCTU photographiée en 3 pages
#  coûtait donc 4 scans sur les 5 du quota mensuel gratuit.
#
#  Pour un abonné, aucune conséquence. Pour un compte gratuit, c'était presque
#  tout son mois pour un seul document, sans qu'il comprenne pourquoi.
# ════════════════════════════════════════════════════════════════════════
import pytest

from api import POIDS_MINIMUM_APPEL, poids_dans_le_lot


def test_un_document_ordinaire_coute_un_scan_entier():
    assert poids_dans_le_lot(0) == 1.0     # aucun lot annoncé
    assert poids_dans_le_lot(1) == 1.0     # un seul fichier : ce n'est pas un lot


def test_trois_photos_d_un_meme_document_coutent_un_scan_en_tout():
    """3 lectures page par page + 1 rattrapage groupé = 4 appels, 1 scan."""
    p = poids_dans_le_lot(3)
    assert round(3 * p + p, 6) == 1.0


def test_deux_photos_aussi():
    p = poids_dans_le_lot(2)
    assert round(2 * p + p, 6) == 1.0


def test_un_gros_lot_reste_facture_un_minimum():
    """Plancher volontaire : douze photos coûtent cher à lire, elles ne peuvent
    pas revenir à presque rien, sinon le garde-fou anti-abus perd son sens."""
    p = poids_dans_le_lot(12)
    assert p == POIDS_MINIMUM_APPEL
    assert 12 * p + p > 1.0


def test_le_poids_ne_descend_jamais_a_zero():
    for n in (0, 1, 2, 3, 5, 12, 100, -4):
        assert poids_dans_le_lot(n) >= POIDS_MINIMUM_APPEL


def test_valeurs_absurdes_traitees_comme_un_document_ordinaire():
    assert poids_dans_le_lot(None) == 1.0
    assert poids_dans_le_lot(-1) == 1.0

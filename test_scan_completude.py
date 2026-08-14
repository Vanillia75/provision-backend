# ════════════════════════════════════════════════════════════════════════
#  MESURER CE QUE LE SCAN N'A PAS LU.
#
#  Les échecs DURS d'un scan remontaient déjà à Sentry depuis le 23/07/2026.
#  Les lectures PARTIELLES, non : une AEM dont on tirait la date mais pas le
#  salaire brut était acceptée en silence, l'utilisateur recevait un formulaire
#  troué, et rien ne le comptait.
#
#  Conséquence : à la question « est-ce qu'il lit vraiment tout ? », personne
#  ne pouvait répondre. On ne savait pas distinguer un scan parfait d'un scan
#  troué que l'utilisateur avait rebouché à la main.
#
#  Ces tests figent la mesure. Aucun appel réseau, aucun modèle : on teste le
#  comptage, pas la lecture.
# ════════════════════════════════════════════════════════════════════════
import pytest

from aem_extractor import CHAMPS_ATTENDUS, completude

COMPLETE = {"employeur": "Théâtre du Nord", "date": "2026-07-10",
            "nombre": 3, "salaire_brut": 1200.0}


def test_une_aem_complete_ne_declenche_rien():
    b = completude([COMPLETE])
    assert b["entrees"] == 1 and b["completes"] == 1
    assert b["incompletes"] == 0 and b["manquants"] == {}


def test_le_salaire_brut_manquant_est_compte():
    """Le cas le plus frequent, et le plus couteux : sans brut, pas d'allocation."""
    sans_brut = dict(COMPLETE, salaire_brut=None)
    b = completude([sans_brut])
    assert b["incompletes"] == 1
    assert b["manquants"] == {"salaire_brut": 1}


@pytest.mark.parametrize("champ", CHAMPS_ATTENDUS)
def test_chaque_champ_manquant_est_repere(champ):
    troue = dict(COMPLETE)
    troue[champ] = None
    b = completude([troue])
    assert b["manquants"].get(champ) == 1, f"{champ} non repere"


def test_un_zero_compte_comme_manquant_sur_les_nombres():
    """0 cachet ou 0 EUR n'est pas une lecture, c'est un trou."""
    b = completude([dict(COMPLETE, nombre=0, salaire_brut=0)])
    assert b["manquants"] == {"nombre": 1, "salaire_brut": 1}


def test_une_chaine_vide_compte_comme_manquante():
    b = completude([dict(COMPLETE, employeur="")])
    assert b["manquants"] == {"employeur": 1}


def test_plusieurs_aem_sont_agregees():
    b = completude([COMPLETE, dict(COMPLETE, salaire_brut=None),
                    dict(COMPLETE, salaire_brut=None, employeur=None)])
    assert b["entrees"] == 3 and b["completes"] == 1 and b["incompletes"] == 2
    assert b["manquants"] == {"salaire_brut": 2, "employeur": 1}


@pytest.mark.parametrize("entree", [None, [], [None], [{}], [{"employeur": "X"}]])
def test_la_mesure_ne_leve_jamais_d_erreur(entree):
    """Elle surveille le scan : elle ne doit surtout pas le casser."""
    b = completude(entree)
    assert set(b) == {"entrees", "completes", "incompletes", "manquants"}

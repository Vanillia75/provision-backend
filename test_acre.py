# ════════════════════════════════════════════════════════════════════════
#  ACRE : l'exonération dépend de la DATE DE CRÉATION, jamais de la date
#  du jour. Décret du 6 février 2026 : 50 % pour les micro-entreprises
#  créées avant le 01/07/2026, 25 % seulement pour celles créées après.
#  Source vérifiée le 06/08/2026 sur service-public.fr.
#
#  Ces tests existent parce que le moteur a appliqué 50 % à tout le monde
#  jusqu'au 06/08/2026 : les créations de juillet 2026 étaient
#  sous-provisionnées de moitié, soit la mauvaise surprise que TOTOR évite.
# ════════════════════════════════════════════════════════════════════════
from datetime import date

import pytest

from tax_engine import (
    ACRE_BASCULE,
    ACRE_EXONERATION_APRES,
    ACRE_EXONERATION_AVANT,
    AUTO_ENTREPRENEUR_RATES,
    acre_part_a_payer,
    estimate_auto_entrepreneur,
)


# ── Les repères officiels, verrouillés ──────────────────────────────────

def test_les_reperes_officiels_ne_bougent_pas():
    assert ACRE_BASCULE == date(2026, 7, 1)
    assert ACRE_EXONERATION_AVANT == 0.50
    assert ACRE_EXONERATION_APRES == 0.25


# ── La règle de calcul, cas par cas ─────────────────────────────────────

@pytest.mark.parametrize("creation, part_due, commentaire", [
    (date(2025, 9, 15), 0.50, "créée bien avant la bascule"),
    (date(2026, 6, 30), 0.50, "créée la veille de la bascule"),
    (date(2026, 7, 1), 0.75, "créée le jour même de la bascule"),
    (date(2026, 7, 2), 0.75, "créée juste après"),
    (date(2027, 3, 10), 0.75, "créée bien après"),
])
def test_part_de_cotisations_encore_due(creation, part_due, commentaire):
    assert acre_part_a_payer(creation) == pytest.approx(part_due), commentaire


def test_date_de_creation_inconnue_se_trompe_du_cote_PRUDENT():
    """⚠️ CHOIX INVERSÉ LE 15/08/2026, après l'audit à la source.

    On gardait 50 % « le cas majoritaire ». Deux raisons de changer :
    l'application ne demandait la date de création NULLE PART, donc la branche
    d'après la bascule était morte et TOUT LE MONDE était calculé à 50 %, y
    compris les créations récentes qui n'y ont pas droit ; et se tromper dans ce
    sens fait SOUS-PROVISIONNER l'URSSAF, exactement la mauvaise surprise que
    TOTOR existe pour éviter.

    Sans date, on suppose donc le taux le moins favorable. Mettre trop de côté
    se rattrape ; ne pas assez en mettre, non."""
    assert acre_part_a_payer(None) == pytest.approx(0.75)


# ── L'effet réel sur ce que l'utilisateur doit provisionner ─────────────

def _estimation(acre, creation, ca=30000.0):
    """Un chiffre d'affaires encaissé aujourd'hui, en profession libérale (BNC)."""
    aujourd_hui = date(2026, 8, 6)
    return estimate_auto_entrepreneur(
        activite="bnc",
        periodicite="mensuelle",
        acre=acre,
        versement_liberatoire=False,
        incomes=[(aujourd_hui, ca)],
        today=aujourd_hui,
        date_creation_activite=creation,
    ).detail


def test_une_creation_de_juillet_provisionne_plus_qu_une_creation_de_juin():
    """LE bug corrigé le 06/08/2026 : les deux provisionnaient pareil."""
    juin = _estimation(acre=True, creation=date(2026, 6, 1))["cotisations_sociales"]
    juillet = _estimation(acre=True, creation=date(2026, 7, 15))["cotisations_sociales"]
    assert juillet > juin
    # Trois quarts contre une moitié : le rapport doit être exactement 1,5.
    assert juillet == pytest.approx(juin * 1.5, rel=1e-6)


def test_sans_acre_la_date_de_creation_ne_change_rien():
    avant = _estimation(acre=False, creation=date(2026, 6, 1))
    apres = _estimation(acre=False, creation=date(2026, 7, 15))
    assert avant["cotisations_sociales"] == pytest.approx(apres["cotisations_sociales"])


def test_les_montants_exacts_pour_un_bnc_a_30000_euros():
    """Chiffres posés en dur : si le taux BNC bouge, ce test le dit."""
    taux_plein = AUTO_ENTREPRENEUR_RATES["bnc"]["cotisations"]
    sans_acre = _estimation(acre=False, creation=None)["cotisations_sociales"]
    assert sans_acre == pytest.approx(30000 * taux_plein, abs=0.01)

    ancienne = _estimation(acre=True, creation=date(2026, 1, 10))["cotisations_sociales"]
    assert ancienne == pytest.approx(30000 * taux_plein * 0.50, abs=0.01)

    nouvelle = _estimation(acre=True, creation=date(2026, 7, 10))["cotisations_sociales"]
    assert nouvelle == pytest.approx(30000 * taux_plein * 0.75, abs=0.01)

    # L'écart, c'est ce que l'utilisateur aurait oublié de mettre de côté.
    manque = nouvelle - ancienne
    assert manque > 1000, "l'enjeu se compte en milliers d'euros, pas en centimes"


@pytest.mark.parametrize("activite", list(AUTO_ENTREPRENEUR_RATES.keys()))
def test_la_regle_vaut_pour_tous_les_regimes(activite):
    aujourd_hui = date(2026, 8, 6)

    def calcul(creation):
        return estimate_auto_entrepreneur(
            activite=activite, periodicite="mensuelle", acre=True,
            versement_liberatoire=False, incomes=[(aujourd_hui, 10000.0)],
            today=aujourd_hui, date_creation_activite=creation,
        ).detail["cotisations_sociales"]

    assert calcul(date(2026, 7, 5)) == pytest.approx(calcul(date(2026, 6, 5)) * 1.5, rel=1e-6)

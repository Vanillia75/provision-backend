

# ── ACRE : le repli quand la date de creation est inconnue (15/08/2026) ──────
#  Corrige apres verification a la source (decret n° 2026-69 du 06/02/2026,
#  service-public.fr fiche F11677). L'exoneration est passee de 50 % a 25 % pour
#  les micro-entreprises creees a partir du 1er juillet 2026. Or l'application ne
#  demandait NULLE PART la date de creation : la branche « apres la bascule »
#  etait morte et tout le monde etait calcule a 50 %, donc SOUS-PROVISIONNE.

from datetime import date as _date

from tax_engine import ACRE_BASCULE, acre_date_manquante, acre_part_a_payer


def test_creation_avant_la_bascule_garde_la_moitie():
    assert acre_part_a_payer(_date(2026, 3, 15)) == 0.50
    assert acre_part_a_payer(_date(2026, 6, 30)) == 0.50


def test_creation_a_partir_du_1er_juillet_2026_paie_les_trois_quarts():
    assert acre_part_a_payer(ACRE_BASCULE) == 0.75
    assert acre_part_a_payer(_date(2026, 8, 15)) == 0.75


def test_sans_date_on_se_trompe_du_cote_PRUDENT():
    """Le point de la correction. Sous-estimer l'URSSAF est la mauvaise surprise
    que TOTOR existe pour eviter : sans date, on suppose le taux le moins
    favorable, quitte a faire mettre de cote un peu trop."""
    assert acre_part_a_payer(None) == 0.75, (
        "sans date de creation, on doit provisionner 75 % des cotisations, "
        "pas 50 % : trop mettre de cote se rattrape, pas assez ne se rattrape pas"
    )


def test_on_sait_dire_quand_le_calcul_est_au_juge():
    assert acre_date_manquante(True, None) is True
    assert acre_date_manquante(True, _date(2026, 2, 1)) is False
    assert acre_date_manquante(False, None) is False

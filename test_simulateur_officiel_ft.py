# -*- coding: utf-8 -*-
"""
test_simulateur_officiel_ft.py — Le moteur face au SIMULATEUR OFFICIEL de France Travail.

Le 27/07/2026, Camille a trouvé que le simulateur officiel
(https://simucalcul.pole-emploi-services.fr/) est accessible SANS COMPTE. C'est un
banc d'essai illimité : on peut confronter notre calcul à la source officielle sur
autant de cas qu'on veut, avec des chiffres INVENTÉS — donc sans jamais toucher aux
données réelles de personne.

Les 14 cas ci-dessous ont été relevés à la main ce jour-là. Ils couvrent :
  - les DEUX annexes (8 techniciens, 10 artistes) ;
  - de 2 000 à 500 000 € de salaire de référence ;
  - 507 h et 1 200 h ;
  - le plancher d'allocation, la zone sans CSG, la zone d'écrêtement, la zone
    de CSG pleine.

C'est ce backtest qui a permis d'OUVRIR la Loi X aux deux annexes sans plafond de
montant (cf. branche_affichable). Ce fichier est donc le filet de sécurité de cette
décision : si un jour quelqu'un touche aux formules ou aux taux, ces tests le disent.

⚠️ Si un test casse en janvier, ce n'est probablement PAS un bug : le SMIC est
revalorisé, donc le plancher net (62,00 €) et l'allocation minimale bougent.
Rejouer les cas sur le simulateur et mettre à jour ces chiffres AVEC la source.
"""
import pytest

from allocation_engine import calculer_aj, branche_affichable

ANNEXE = {"A8": "annexe8", "A10": "annexe10"}

# (libellé, régime, heures, salaire de référence,
#  allocation initiale, retenue retraite, brute après retraite, net, CSG)
# Tous relevés sur le simulateur officiel le 2026-07-27, date de fin de contrat 30/06/2026.
CAS_OFFICIELS = [
    ("artiste plancher",        "A10",  507,   2000,  44.00,  0.37, 43.63, 43.63, 0.00),
    ("artiste bas",             "A10",  507,  10000,  53.69,  1.83, 51.86, 51.86, 0.00),
    ("artiste sans csg",        "A10",  507,  20000,  64.22,  3.67, 60.55, 60.55, 0.00),
    ("artiste juste sous 62",   "A10",  507,  30000,  None,   None, 61.91, 61.91, 0.00),
    ("artiste ecrete 1",        "A10",  507,  40000,  70.61,  7.34, 63.27, 62.00, 1.27),
    ("artiste ecrete 2",        "A10",  507,  50000,  None,   None, 64.64, 62.00, 2.64),
    ("artiste ecrete 3",        "A10",  507,  60000,  None,   None, 65.99, 62.00, 3.99),
    ("artiste csg pleine",      "A10",  507,  70000,  None,   None, 67.36, 62.93, 4.43),
    ("artiste haut",            "A10",  507, 100000,  89.79, 18.34, 71.45, 66.75, 4.70),
    ("artiste tres haut",       "A10",  507, 500000, 155.77, 91.72, 64.05, 62.00, 2.05),
    ("artiste 1200h",           "A10", 1200,  60000,  82.57,  4.65, 77.92, 72.79, 5.13),
    ("technicien plancher",     "A8",   507,   2000,  38.00,  0.29, 37.71, 37.71, 0.00),
    ("technicien moyen",        "A8",   507,  20000,  61.54,  2.93, 58.61, 58.61, 0.00),
    ("technicien haut",         "A8",   507, 100000,  87.11, 14.67, 72.44, 67.67, 4.77),
]

# On tronque là où France Travail arrondit certains termes intermédiaires : on tombe
# donc de 0 à 2 centimes EN DESSOUS. Jamais au-dessus — c'est la règle qu'on vérifie.
# Les écarts sont ARRONDIS avant comparaison : sans ça, 89,79 - 89,77 vaut
# 0,020000000000010 en virgule flottante et un test juste passerait pour faux.
TOLERANCE = 0.02


def _ecart(officiel, chez_nous):
    """Écart officiel - nous, arrondi au centime (positif = on annonce moins)."""
    return round(officiel - chez_nous, 2)


@pytest.mark.parametrize(
    "libelle,regime,heures,sr,ft_init,ft_retraite,ft_brute,ft_net,ft_csg",
    CAS_OFFICIELS,
    ids=[c[0] for c in CAS_OFFICIELS],
)
def test_colle_au_simulateur_officiel(libelle, regime, heures, sr, ft_init,
                                      ft_retraite, ft_brute, ft_net, ft_csg):
    r = calculer_aj(ANNEXE[regime], sr=float(sr), nht=float(heures))

    if ft_init is not None:
        ecart = _ecart(ft_init, r["aj_brute"])
        assert 0 <= ecart <= TOLERANCE, (
            f"{libelle} : allocation {r['aj_brute']} vs {ft_init} officiels "
            f"(ecart {ecart:+.2f}, on ne doit JAMAIS annoncer plus que France Travail)"
        )

    if ft_retraite is not None:
        assert r["retenue_retraite"] == pytest.approx(ft_retraite, abs=0.005), (
            f"{libelle} : retenue retraite {r['retenue_retraite']} vs {ft_retraite} officiels"
        )

    notre_brute = round(r["aj_brute"] - r["retenue_retraite"], 2)
    ecart_brute = _ecart(ft_brute, notre_brute)
    assert 0 <= ecart_brute <= TOLERANCE, (
        f"{libelle} : brut apres retraite {notre_brute} vs {ft_brute} officiels"
    )

    assert abs(_ecart(ft_csg, r["retenue_csg_crds"])) <= TOLERANCE, (
        f"{libelle} : CSG {r['retenue_csg_crds']} vs {ft_csg} officiels"
    )

    ecart_net = _ecart(ft_net, r["aj_nette"])
    assert 0 <= ecart_net <= TOLERANCE, (
        f"{libelle} : net {r['aj_nette']} vs {ft_net} officiels "
        f"(ecart {ecart_net:+.2f}, on annonce toujours au plus le montant officiel)"
    )


# Deuxième relevé du 27/07 : la montée vers le plafond, puis le plateau.
# C'est ce relevé qui a fait découvrir que notre plafond (174,80 €) était FAUX.
MONTEE_VERS_LE_PLAFOND = [
    ("A10",  150000, 105.77),
    ("A10",  200000, 121.75),
    ("A10",  250000, 137.73),
    ("A10",  300000, 153.71),
    ("A10",  306000, 155.62),
    ("A10",  320000, 155.77),   # plateau
    ("A10",  350000, 155.77),
    ("A10", 1000000, 155.77),
    ("A8",   300000, 151.03),
    ("A8",  1000000, 155.77),   # même plafond pour les techniciens
]


@pytest.mark.parametrize("regime,sr,ft_init", MONTEE_VERS_LE_PLAFOND,
                         ids=[f"{r}-{s}" for r, s, _ in MONTEE_VERS_LE_PLAFOND])
def test_montee_et_plafond(regime, sr, ft_init):
    r = calculer_aj(ANNEXE[regime], sr=float(sr), nht=507.0)
    ecart = _ecart(ft_init, r["aj_brute"])
    assert 0 <= ecart <= TOLERANCE, (
        f"{regime} SR {sr} : allocation {r['aj_brute']} vs {ft_init} officiels (ecart {ecart:+.2f})"
    )


def test_le_plafond_vaut_bien_155_77():
    """
    Le garde-fou du 27/07. On avait 174,80 € (guide France Travail daté de 2024) :
    au-delà d'environ 306 500 € de salaire de référence, on aurait annoncé PLUS que
    France Travail. Exactement ce que la Loi X interdit.
    """
    for annexe in ("annexe8", "annexe10"):
        r = calculer_aj(annexe, sr=1000000.0, nht=507.0)
        assert r["plafond_applique"] is True
        assert r["aj_brute"] == pytest.approx(155.77, abs=0.005), (
            f"{annexe} : plafond {r['aj_brute']}, le simulateur officiel dit 155,77 €"
        )


def test_le_net_ne_descend_jamais_sous_le_plancher():
    """
    La règle percée le 27/07 : la CSG est rabotée pour que le net ne tombe pas sous
    62,00 €. Preuve officielle : trois bruts différents (63,27 / 64,64 / 65,99 €)
    donnent tous 62,00 € net EXACTEMENT.
    """
    for sr in (40000, 50000, 60000):
        r = calculer_aj("annexe10", sr=float(sr), nht=507.0)
        assert r["aj_nette"] == pytest.approx(62.00, abs=0.02), (
            f"SR {sr} : net {r['aj_nette']}, on attend le plancher 62,00 €"
        )
        assert r["retenue_csg_crds"] > 0, "dans cette zone la CSG existe, mais rabotée"


def test_pas_de_csg_quand_on_est_deja_sous_le_plancher():
    """Sous 62 €, France Travail ne prélève RIEN : vérifié à 60,55 € et 61,91 €."""
    for sr in (20000, 30000):
        r = calculer_aj("annexe10", sr=float(sr), nht=507.0)
        assert r["retenue_csg_crds"] == 0.0, (
            f"SR {sr} : CSG {r['retenue_csg_crds']}, on attend 0 (déjà sous le plancher)"
        )


def test_csg_pleine_au_dessus_de_la_zone_ecretee():
    """Assez haut, la CSG reprend son taux normal : 6,7 % sur 98,25 % du brut."""
    r = calculer_aj("annexe10", sr=100000.0, nht=507.0)
    brute = round(r["aj_brute"] - r["retenue_retraite"], 2)
    attendu = round(brute * 0.9825 * 0.067, 2)
    assert r["retenue_csg_crds"] == pytest.approx(attendu, abs=0.01)
    assert r["aj_nette"] > 62.00, "au-dessus du plancher, pas d'écrêtement"


def test_les_deux_annexes_sont_affichables():
    """L'ouverture de la Loi X du 27/07/2026, sur laquelle repose tout le reste."""
    for annexe in ("annexe8", "annexe10"):
        for sr, nht in ((2000, 507), (20000, 507), (100000, 507), (60000, 1200)):
            r = calculer_aj(annexe, sr=float(sr), nht=float(nht))
            affichable, raison = branche_affichable(annexe, r)
            assert affichable is True, f"{annexe} SR {sr} bloqué pour {raison!r}"

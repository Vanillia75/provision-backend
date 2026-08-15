# -*- coding: utf-8 -*-
# ════════════════════════════════════════════════════════════════════════
#  LE PLAFOND MENSUEL DES HEURES RETENUES (trouvé par l'audit du 15/08/2026).
#
#  Le moteur additionnait les heures ligne à ligne, sans aucun plafond mensuel.
#  Un mois à 400 heures comptait 400 heures. Or le guide France Travail est
#  formel : « Le nombre d'heures de travail retenu par mois civil ne peut pas
#  dépasser un plafond : 208 heures pour les ouvriers et techniciens (majoré à
#  250 heures en cas d'employeurs différents sur le mois) ; 28 cachets pour les
#  artistes. »
#
#  L'erreur allait dans le sens INTERDIT par la Loi X : quelqu'un pouvait se
#  croire au-dessus des 507 heures alors que France Travail ne lui en retenait
#  qu'une partie, et découvrir le trou à sa date anniversaire.
# ════════════════════════════════════════════════════════════════════════
from datetime import date

from intermittent_engine import (HEURES_CACHET, PLAFOND_MENSUEL_CACHETS,
                                 PLAFOND_MENSUEL_HEURES,
                                 PLAFOND_MENSUEL_HEURES_MULTI,
                                 _compter_sur_fenetre)


class Act:
    """Une activité, réduite à ce que le moteur regarde."""

    def __init__(self, jour, type_activite, nombre, employeur=None):
        self.date = jour
        self.type_activite = type_activite
        self.nombre = nombre
        self.employeur = employeur
        self.salaire_brut = None


FIN = date(2026, 12, 31)


def total(activites):
    return _compter_sur_fenetre(activites, FIN)[0]


# ── Annexe 8 : les heures ────────────────────────────────────────────────

def test_un_mois_normal_n_est_pas_touche():
    assert total([Act(date(2026, 3, 10), "heures", 150, "TF1")]) == 150.0


def test_un_mois_a_300_heures_est_ramene_a_208():
    """Un seul employeur : le plafond est 208 h. Avant, on comptait 300."""
    assert total([Act(date(2026, 3, 10), "heures", 300, "TF1")]) == PLAFOND_MENSUEL_HEURES


def test_plusieurs_employeurs_dans_le_mois_montent_le_plafond_a_250():
    a = [Act(date(2026, 3, 5), "heures", 200, "TF1"),
         Act(date(2026, 3, 20), "heures", 200, "ARTE")]
    assert total(a) == PLAFOND_MENSUEL_HEURES_MULTI


def test_le_plafond_est_MENSUEL_pas_annuel():
    """Trois mois à 208 h font bien 624 h : on ne rabote pas l'année."""
    a = [Act(date(2026, 1, 10), "heures", 208, "TF1"),
         Act(date(2026, 2, 10), "heures", 208, "TF1"),
         Act(date(2026, 3, 10), "heures", 208, "TF1")]
    assert total(a) == 624.0


# ── Annexe 10 : les cachets ──────────────────────────────────────────────

def test_28_cachets_dans_le_mois_passent_entierement():
    assert total([Act(date(2026, 5, 10), "cachet_isole", 28)]) == PLAFOND_MENSUEL_CACHETS * HEURES_CACHET


def test_40_cachets_dans_le_mois_sont_ramenes_a_28():
    """Avant : 40 x 12 = 480 h comptées. France Travail n'en retient que 28."""
    lu = total([Act(date(2026, 5, 10), "cachet_isole", 40)])
    assert lu == PLAFOND_MENSUEL_CACHETS * HEURES_CACHET
    assert lu == 336.0


def test_cachets_et_heures_ont_des_plafonds_distincts():
    """Un artiste qui fait aussi des heures : les deux plafonds cohabitent,
    ils ne se mélangent pas."""
    a = [Act(date(2026, 5, 10), "cachet_isole", 30),
         Act(date(2026, 5, 20), "heures", 300, "TF1")]
    assert total(a) == PLAFOND_MENSUEL_CACHETS * HEURES_CACHET + PLAFOND_MENSUEL_HEURES


# ── Ce que la personne voit ──────────────────────────────────────────────

def test_la_ligne_rabotee_explique_pourquoi():
    _, detail, _ = _compter_sur_fenetre([Act(date(2026, 3, 10), "heures", 300, "TF1")], FIN)
    assert "Plafond mensuel" in detail[0]["regle"], detail[0]["regle"]
    assert "écartées" in detail[0]["regle"]


def test_le_reste_du_moteur_n_est_pas_touche():
    """Les arrêts assimilés et la formation gardent leurs propres règles."""
    a = [Act(date(2026, 4, 10), "formation", 100),
         Act(date(2026, 4, 12), "maladie_assimilee", 10)]
    assert total(a) > 0

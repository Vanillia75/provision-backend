# -*- coding: utf-8 -*-
"""
test_simulateur_public.py — Le simulateur en accès libre (sans compte).

Posé le 27/07/2026 : tous les concurrents en ont un, c'est une porte d'entrée SEO
majeure. Le nôtre affiche le NET là où les autres s'arrêtent au brut, parce que
notre calcul brut→net est vérifié au centime contre le simulateur OFFICIEL de
France Travail (cf. test_simulateur_officiel_ft.py).

Ce que ces tests protègent :
  - l'accès sans compte et sans email ;
  - les chiffres, qui doivent rester ceux du simulateur officiel ;
  - les heures assimilées, qui comptent pour les 507 h mais JAMAIS pour le montant ;
  - la franchise congés payés, formule officielle et non un raccourci ;
  - la protection anti-abus.
"""
import pytest
from fastapi import HTTPException

import api


class _FausseRequete:
    """Le strict minimum dont l'anti-abus a besoin : une adresse d'appelant."""
    def __init__(self, ip="203.0.113.7"):
        self.headers = {"x-forwarded-for": ip}
        self.client = type("C", (), {"host": ip})()


def _appel(ip="203.0.113.7", **kw):
    params = {"annexe": "annexe10", "heures": 507.0, "salaire_reference": 20000.0}
    params.update(kw)
    return api.simulateur_allocation_public(api.SimulateurPublicRequest(**params), _FausseRequete(ip))


@pytest.fixture(autouse=True)
def _vide_le_compteur():
    """Chaque test repart avec l'anti-abus à zéro."""
    api._SIMU_RL.clear()
    yield
    api._SIMU_RL.clear()


# ── Les chiffres ─────────────────────────────────────────────────────────────

def test_les_chiffres_sont_ceux_du_simulateur_officiel():
    """Annexe 10, 507 h, 20 000 € : France Travail annonce 60,55 € net."""
    r = _appel(ip="198.51.100.1")
    assert r["affichable"] is True
    assert r["aj_initiale"] == pytest.approx(64.22, abs=0.02)
    assert r["retenue_retraite"] == pytest.approx(3.67, abs=0.005)
    assert r["aj_brute"] == pytest.approx(60.55, abs=0.02)
    assert r["retenue_csg_crds"] == 0.0
    assert r["aj_nette"] == pytest.approx(60.55, abs=0.02)


def test_le_technicien_a_droit_a_son_chiffre():
    """Annexe 8 : ouverte le 27/07/2026. France Travail annonce 58,61 € net."""
    r = _appel(ip="198.51.100.2", annexe="annexe8")
    assert r["affichable"] is True
    assert r["aj_nette"] == pytest.approx(58.61, abs=0.02)


def test_le_detail_du_calcul_est_donne():
    """Notre différence : on montre d'où vient le montant, pas juste le résultat."""
    r = _appel(ip="198.51.100.3")
    assert r["partie_a"] > 0 and r["partie_b"] > 0 and r["partie_c"] > 0
    somme = round(r["partie_a"] + r["partie_b"] + r["partie_c"], 2)
    assert somme == pytest.approx(r["aj_initiale"], abs=0.01)


def test_la_csg_ecretee_apparait():
    """Zone d'écrêtement : France Travail prélève 1,27 € et le net tombe à 62,00 €."""
    r = _appel(ip="198.51.100.4", salaire_reference=40000.0)
    assert r["retenue_csg_crds"] == pytest.approx(1.27, abs=0.02)
    assert r["aj_nette"] == pytest.approx(62.00, abs=0.02)


# ── Les 507 heures ───────────────────────────────────────────────────────────

def test_507_heures_atteintes():
    r = _appel(ip="198.51.100.5")
    assert r["eligible"] is True
    assert r["manque_heures"] == 0
    assert r["seuil_507"] == 507


def test_507_heures_manquantes():
    r = _appel(ip="198.51.100.6", heures=400.0)
    assert r["eligible"] is False
    assert r["manque_heures"] == 107


def test_les_heures_assimilees_comptent_pour_les_507_mais_pas_pour_le_montant():
    """Piège officiel : formation et enseignement ouvrent le droit, sans gonfler l'allocation."""
    sans = _appel(ip="198.51.100.7", heures=450.0)
    avec = _appel(ip="198.51.100.8", heures=450.0, heures_formation=100.0)
    assert sans["eligible"] is False
    assert avec["eligible"] is True           # 450 + 100 = 550 ≥ 507
    assert avec["heures_assimilees"] == 100
    assert avec["aj_initiale"] == sans["aj_initiale"]   # le montant, lui, ne bouge pas


def test_enseignement_plafonne_a_70_heures():
    r = _appel(ip="198.51.100.9", heures=450.0, heures_enseignement=200.0)
    assert r["heures_assimilees"] == 70


def test_formation_et_enseignement_plafonnes_a_338_heures():
    r = _appel(ip="198.51.100.10", heures=100.0, heures_enseignement=70.0, heures_formation=500.0)
    assert r["heures_assimilees"] == 338


# ── Franchise congés payés (formule officielle, vérifiée sur le simulateur) ──

@pytest.mark.parametrize("jours,attendu", [
    (60, 6),      # 60 x 2,5 / 24 = 6,25
    (96, 10),     # 96 x 2,5 / 24 = 10,00 EXACTEMENT — le cas qui départage : un
                  # simple « jours / 10 » donnerait 9. France Travail dit 10.
    (100, 10),
    (120, 12),
    (300, 30),    # plafonné à 30 jours (le calcul brut donnerait 31,25)
])
def test_franchise_conges_payes(jours, attendu):
    r = _appel(ip=f"198.51.100.{jours}", jours_travailles=float(jours))
    assert r["franchise_cp_jours"] == attendu


def test_pas_de_franchise_si_on_ne_donne_pas_les_jours():
    r = _appel(ip="198.51.100.11")
    assert "franchise_cp_jours" not in r


# ── Date anniversaire ────────────────────────────────────────────────────────

def test_date_anniversaire():
    r = _appel(ip="198.51.100.12", date_fin_contrat="2026-06-30")
    assert r["date_anniversaire"] == "2027-06-30"


def test_date_invalide_ignoree_sans_planter():
    r = _appel(ip="198.51.100.13", date_fin_contrat="pas une date")
    assert "date_anniversaire" not in r
    assert r["affichable"] is True     # le reste du calcul sort quand même


# ── Garde-fous ───────────────────────────────────────────────────────────────

def test_annexe_invalide_refusee():
    with pytest.raises(HTTPException) as e:
        _appel(ip="198.51.100.14", annexe="annexe42")
    assert e.value.status_code == 400


def test_valeurs_negatives_ramenees_a_zero():
    r = _appel(ip="198.51.100.15", heures=-500.0, salaire_reference=-1000.0)
    assert r["heures_travail"] == 0
    assert r["eligible"] is False


def test_anti_abus_bloque_les_rafales():
    ip = "198.51.100.200"
    for _ in range(api._SIMU_RL_MAX):
        _appel(ip=ip)
    with pytest.raises(HTTPException) as e:
        _appel(ip=ip)
    assert e.value.status_code == 429


def test_deux_visiteurs_ne_se_genent_pas():
    for _ in range(api._SIMU_RL_MAX):
        _appel(ip="198.51.100.201")
    r = _appel(ip="198.51.100.202")     # une autre adresse passe toujours
    assert r["affichable"] is True

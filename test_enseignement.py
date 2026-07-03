"""
test_enseignement.py — Heures d'enseignement DISPENSÉ (artiste/technicien enseignant).

Écrits AVANT le code moteur. Source : guide France Travail p.8-9.

RÈGLE : l'enseignement dispensé compte heure pour heure, avec DEUX plafonds :
  - un sous-plafond propre de 70h (120h si ≥50 ans → HORS V1, pas de date de naissance) ;
  - un plafond PARTAGÉ de 338h avec la formation : formation + enseignement ≤ 338h.
Comme la formation, c'est de l'assimilé (branche estimation par prudence, conditions FT
non vérifiables). Type d'activité : "enseignement".
"""
from datetime import date, timedelta

from intermittent_engine import Activite, calculer, heures_de

AUJOURDHUI = date(2026, 7, 3)


def A(jours_avant, type_activite, nombre):
    return Activite(date=AUJOURDHUI - timedelta(days=jours_avant), type_activite=type_activite, nombre=nombre)


# ── Conversion + sous-plafond 70h ────────────────────────────────────────────
def test_enseignement_heure_pour_heure():
    assert heures_de(Activite(date=AUJOURDHUI, type_activite="enseignement", nombre=50)) == 50.0


def test_enseignement_sous_plafond_70():
    r = calculer([A(30, "enseignement", 100)], aujourdhui=AUJOURDHUI)
    assert r.total_heures == 70.0   # plafonné à 70h


def test_enseignement_sous_les_plafonds():
    r = calculer([A(30, "enseignement", 50)], aujourdhui=AUJOURDHUI)
    assert r.total_heures == 50.0


# ── Plafond PARTAGÉ de 338h avec la formation ────────────────────────────────
def test_formation_plus_enseignement_plafond_partage_338():
    # 300h formation (traitée en premier) + 100h enseignement → 300 + 38 = 338 (partage saturé).
    r = calculer([A(60, "formation", 300), A(30, "enseignement", 100)], aujourdhui=AUJOURDHUI)
    assert r.total_heures == 338.0


def test_enseignement_puis_formation_partage():
    # Ordre inverse : 70h ens (sous-plafond) + 300h formation → 70 + 268 = 338.
    r = calculer([A(60, "enseignement", 70), A(30, "formation", 300)], aujourdhui=AUJOURDHUI)
    assert r.total_heures == 338.0


def test_enseignement_seul_ne_peut_ouvrir_droits():
    r = calculer([A(30, "enseignement", 500)], aujourdhui=AUJOURDHUI)
    assert r.total_heures == 70.0
    assert r.droits_securises is False


# ── Mélange avec du travail réel ─────────────────────────────────────────────
def test_enseignement_avec_cachets():
    # 40 cachets (480h) + 60h enseignement (sous les 2 plafonds) = 540h → droits ouverts.
    r = calculer([A(90, "cachet_isole", 40), A(30, "enseignement", 60)], aujourdhui=AUJOURDHUI)
    assert r.total_heures == 540.0
    assert r.droits_securises is True


def test_enseignement_hors_fenetre_ignore():
    r = calculer([A(400, "enseignement", 50)], aujourdhui=AUJOURDHUI)
    assert r.total_heures == 0.0


# ── Transparence ─────────────────────────────────────────────────────────────
def test_detail_et_trace_enseignement():
    r = calculer([A(30, "enseignement", 40)], aujourdhui=AUJOURDHUI)
    lignes = [l for l in r.detail_lignes if l["type"] == "enseignement"]
    assert len(lignes) == 1 and lignes[0]["heures"] == 40.0
    assert any("enseignement" in t.lower() for t in r.regles_appliquees)
    r2 = calculer([A(30, "cachet_isole", 10)], aujourdhui=AUJOURDHUI)
    assert not any("enseignement" in t.lower() for t in r2.regles_appliquees)

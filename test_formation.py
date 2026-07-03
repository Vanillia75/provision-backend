"""
test_formation.py — Heures de formation SUIVIE dans le moteur intermittent.

RÈGLE (sourcée — étape zéro du chantier, 2026-07) :
  Les heures de formation suivie sont ASSIMILÉES à des heures de travail dans la
  limite des 2/3 du nombre d'heures requis, soit 338h pour un seuil de 507h.
  Conséquence structurelle : la formation seule ne peut JAMAIS ouvrir des droits
  (338 < 507) — il faut au moins 169h de travail effectif. Le plafond encode ça.

  Sources : Unédic, annexes VIII et X au règlement d'assurance chômage
  (art. 3 : heures de formation assimilées dans la limite des 2/3) ; ARTCENA,
  Précis juridique annexes VIII et X. (L'enseignement DISPENSÉ — 70h/120h — est
  une AUTRE règle, hors périmètre V1, volontairement non codée.)

Ces tests sont écrits AVANT le code moteur (discipline du moteur sacré) :
ils décrivent le comportement attendu, le moteur doit s'y plier.
"""
from datetime import date

from intermittent_engine import Activite, calculer, heures_de, simuler_contrat
from regles_intermittent import REGLES, valeur_de

AUJOURDHUI = date(2026, 7, 3)
PLAFOND = 338
SEUIL = 507


def A(jours_avant, type_activite, nombre):
    """Petite fabrique : une activité datée N jours avant AUJOURDHUI."""
    from datetime import timedelta
    return Activite(date=AUJOURDHUI - timedelta(days=jours_avant), type_activite=type_activite, nombre=nombre)


# ── Le référentiel porte la règle, sourcée et utilisable ─────────────────────
def test_regle_referentiel_formation():
    r = REGLES["formationPlafondNouvelleAdmission"]
    assert r["valeur"] == PLAFOND
    assert r["verifie"] is True, "la règle a été sourcée (Unédic annexes VIII/X) — elle doit être vérifiée"
    assert not r.get("frontOnly"), "le moteur l'utilise désormais : elle n'est plus frontOnly"
    assert valeur_de("formationPlafondNouvelleAdmission") == PLAFOND


# ── Conversion unitaire : la formation compte heure pour heure ───────────────
def test_heures_de_formation_heure_pour_heure():
    assert heures_de(Activite(date=AUJOURDHUI, type_activite="formation", nombre=35)) == 35.0
    assert heures_de(Activite(date=AUJOURDHUI, type_activite="formation", nombre=0)) == 0.0


# ── Comptage simple sous le plafond ──────────────────────────────────────────
def test_formation_sous_plafond_compte_telle_quelle():
    r = calculer([A(30, "formation", 100)], aujourdhui=AUJOURDHUI)
    assert r.total_heures == 100.0


# ── Le plafond : 338h maximum, quelle que soit la déclaration ────────────────
def test_formation_seule_plafonnee_a_338():
    r = calculer([A(30, "formation", 400)], aujourdhui=AUJOURDHUI)
    assert r.total_heures == float(PLAFOND)
    assert r.droits_securises is False, "la formation seule ne peut jamais ouvrir des droits"


def test_plafond_partage_entre_plusieurs_formations():
    # 200 + 200 = 400 déclarées → 338 retenues, le plafond est GLOBAL sur la fenêtre.
    r = calculer([A(60, "formation", 200), A(30, "formation", 200)], aujourdhui=AUJOURDHUI)
    assert r.total_heures == float(PLAFOND)


# ── Mélange travail + formation ──────────────────────────────────────────────
def test_melange_cachets_et_formation_sous_plafond():
    # 20 cachets = 240h + 300h de formation (sous plafond) = 540h → droits sécurisés.
    r = calculer([A(90, "cachet_isole", 20), A(30, "formation", 300)], aujourdhui=AUJOURDHUI)
    assert r.total_heures == 540.0
    assert r.droits_securises is True


def test_melange_heures_et_formation_plafonnee():
    # 120h réelles + 400h de formation → 120 + 338 = 458h (le plafond ne touche pas le travail).
    r = calculer([A(90, "heures", 120), A(30, "formation", 400)], aujourdhui=AUJOURDHUI)
    assert r.total_heures == 458.0
    assert r.droits_securises is False


def test_le_travail_ne_consomme_pas_le_plafond_formation():
    # 507h de travail réel + 50h de formation : tout compte (557h), le plafond
    # ne concerne QUE les heures de formation entre elles.
    r = calculer([A(90, "heures", 507), A(30, "formation", 50)], aujourdhui=AUJOURDHUI)
    assert r.total_heures == 557.0
    assert r.droits_securises is True


# ── Fenêtre glissante : une formation trop ancienne ne compte pas ────────────
def test_formation_hors_fenetre_ignoree():
    r = calculer([A(400, "formation", 200), A(30, "formation", 100)], aujourdhui=AUJOURDHUI)
    assert r.total_heures == 100.0


# ── Transparence : le détail dit quand le plafond a joué ─────────────────────
def test_detail_signale_le_plafonnement():
    r = calculer([A(30, "formation", 400)], aujourdhui=AUJOURDHUI)
    lignes_formation = [l for l in r.detail_lignes if l["type"] == "formation"]
    assert len(lignes_formation) == 1
    assert lignes_formation[0]["heures"] == float(PLAFOND)
    assert "plafond" in lignes_formation[0]["regle"].lower()


def test_regles_appliquees_mentionnent_formation_si_presente():
    r = calculer([A(30, "formation", 10)], aujourdhui=AUJOURDHUI)
    assert any("formation" in t.lower() for t in r.regles_appliquees)
    # ... et ne polluent pas la trace quand il n'y a pas de formation :
    r2 = calculer([A(30, "heures", 10)], aujourdhui=AUJOURDHUI)
    assert not any("formation" in t.lower() for t in r2.regles_appliquees)


# ── Simulation : "et si j'ajoute cette formation ?" respecte le plafond ──────
def test_simulation_formation_plafond_deja_atteint():
    existantes = [A(30, "formation", 338), A(90, "heures", 100)]
    sim = simuler_contrat(existantes, A(10, "formation", 50), aujourdhui=AUJOURDHUI)
    assert sim["heures_ajoutees"] == 0.0, "plafond déjà atteint : une formation de plus n'apporte rien"


def test_simulation_formation_apporte_le_reste_du_plafond():
    existantes = [A(30, "formation", 300)]
    sim = simuler_contrat(existantes, A(10, "formation", 100), aujourdhui=AUJOURDHUI)
    assert sim["heures_ajoutees"] == 38.0  # 338 - 300


# ── Projection à la date anniversaire : même plafond, même fenêtre ───────────
def test_projection_applique_aussi_le_plafond():
    from datetime import timedelta
    anniv = AUJOURDHUI + timedelta(days=100)
    r = calculer([A(30, "formation", 400)], aujourdhui=AUJOURDHUI, date_anniversaire=anniv)
    assert r.projection_disponible is True
    assert r.projection_plancher_heures == float(PLAFOND)

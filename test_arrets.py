"""
test_arrets.py — Arrêts assimilés (maternité, adoption, AT/MP, ALD, suspension de
contrat) dans le moteur 507h.

Écrits AVANT le code moteur (discipline du moteur sacré). Réponses attendues
sourcées : cf. MOTEUR_ARRETS_SOURCES.md.

RÈGLE : certains arrêts INDEMNISÉS comptent comme du travail à 5h/jour calendaire,
SANS plafond. Types assimilés (mécanisme A) :
  - arret_maternite  (maternité/adoption, hors contrat)
  - arret_accident   (AT/MP)
  - arret_ald        (maladie longue durée, hors contrat)
  - arret_suspension (arrêt PENDANT un contrat)
La maladie ordinaire hors contrat et la paternité NE sont PAS des types assimilés
(hors périmètre V1) → le moteur, comme pour tout type inconnu, compte 0h.

Loi X / discipline : tant qu'aucun dossier réel (Héloïse) n'a validé la branche,
tout apport d'arrêt marque le résultat `arret_estimation = True`.

`nombre` d'une activité d'arrêt = nombre de jours calendaires (week-ends inclus).
"""
from datetime import date, timedelta

from intermittent_engine import Activite, calculer, heures_de

AUJOURDHUI = date(2026, 7, 3)


def A(jours_avant, type_activite, nombre):
    return Activite(date=AUJOURDHUI - timedelta(days=jours_avant), type_activite=type_activite, nombre=nombre)


# ── Conversion unitaire : 5h par jour d'arrêt ────────────────────────────────
def test_heures_de_maternite_5h_par_jour():
    assert heures_de(Activite(date=AUJOURDHUI, type_activite="arret_maternite", nombre=112)) == 560.0


def test_heures_de_weekends_inclus():
    # 7 jours calendaires = 35h (pas 5 jours ouvrés × 5) : l'utilisateur saisit les jours calendaires.
    assert heures_de(Activite(date=AUJOURDHUI, type_activite="arret_accident", nombre=7)) == 35.0


def test_heures_de_tous_types_assimiles():
    for t in ("arret_maternite", "arret_accident", "arret_ald", "arret_suspension"):
        assert heures_de(Activite(date=AUJOURDHUI, type_activite=t, nombre=10)) == 50.0


def test_heures_de_zero_jour():
    assert heures_de(Activite(date=AUJOURDHUI, type_activite="arret_maternite", nombre=0)) == 0.0


# ── Types NON assimilés (hors périmètre V1) → 0h, jamais de sur-comptage ──────
def test_maladie_ordinaire_et_paternite_ne_comptent_pas():
    # Ces motifs relèvent de la neutralisation (hors V1) : le moteur ne les connaît
    # pas comme types assimilés → 0h (comportement "type inconnu", jamais deviné).
    assert heures_de(Activite(date=AUJOURDHUI, type_activite="arret_maladie_ordinaire", nombre=30)) == 0.0
    assert heures_de(Activite(date=AUJOURDHUI, type_activite="arret_paternite", nombre=25)) == 0.0


# ── Comptage dans le moteur + drapeau estimation ─────────────────────────────
def test_maternite_seule_ouvre_les_droits():
    r = calculer([A(30, "arret_maternite", 112)], aujourdhui=AUJOURDHUI)
    assert r.total_heures == 560.0
    assert r.droits_securises is True
    assert r.arret_estimation is True


def test_accident_du_travail():
    r = calculer([A(20, "arret_accident", 30)], aujourdhui=AUJOURDHUI)
    assert r.total_heures == 150.0
    assert r.arret_estimation is True


def test_suspension_pendant_contrat():
    r = calculer([A(10, "arret_suspension", 10)], aujourdhui=AUJOURDHUI)
    assert r.total_heures == 50.0


def test_ald():
    r = calculer([A(40, "arret_ald", 60)], aujourdhui=AUJOURDHUI)
    assert r.total_heures == 300.0


def test_melange_arret_et_travail():
    # 20 cachets (240h) + 60 jours ALD (300h) = 540h → droits ouverts, mais estimation.
    r = calculer([A(90, "cachet_isole", 20), A(30, "arret_ald", 60)], aujourdhui=AUJOURDHUI)
    assert r.total_heures == 540.0
    assert r.droits_securises is True
    assert r.arret_estimation is True


def test_sans_arret_pas_de_drapeau_estimation():
    r = calculer([A(30, "cachet_isole", 10)], aujourdhui=AUJOURDHUI)
    assert r.arret_estimation is False


def test_arret_hors_fenetre_ignore():
    # Un arrêt daté hors des 12 mois glissants ne compte pas.
    r = calculer([A(400, "arret_maternite", 112)], aujourdhui=AUJOURDHUI)
    assert r.total_heures == 0.0
    assert r.arret_estimation is False


# ── Transparence : le détail et la trace mentionnent l'arrêt ─────────────────
def test_detail_signale_l_arret():
    r = calculer([A(30, "arret_maternite", 112)], aujourdhui=AUJOURDHUI)
    lignes = [l for l in r.detail_lignes if l["type"] == "arret_maternite"]
    assert len(lignes) == 1
    assert lignes[0]["heures"] == 560.0
    assert "arrêt" in lignes[0]["regle"].lower() or "assimil" in lignes[0]["regle"].lower()


def test_regles_appliquees_mentionnent_l_arret_si_present():
    r = calculer([A(30, "arret_accident", 10)], aujourdhui=AUJOURDHUI)
    assert any("arrêt" in t.lower() or "assimil" in t.lower() for t in r.regles_appliquees)
    r2 = calculer([A(30, "cachet_isole", 10)], aujourdhui=AUJOURDHUI)
    assert not any("assimil" in t.lower() for t in r2.regles_appliquees)

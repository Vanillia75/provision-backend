"""Le type « autre_salaire » (pub, mannequinat, régime général…) — demande testeuse 24/07/2026.

Contrat : visible dans le récapitulatif de revenus, mais JAMAIS compté dans les
507h, ni dans les Congés Spectacles, ni dans la projection AJ (listes blanches).
"""

from datetime import date, timedelta

from intermittent_engine import heures_de, Activite
from allocation_engine import projeter_renouvellement
import conges_spectacles as cs


class _Ligne:
    def __init__(self, d, type_activite, nombre, brut):
        self.date = d
        self.type_activite = type_activite
        self.nombre = nombre
        self.salaire_brut = brut


def test_autre_salaire_zero_heure_moteur_507():
    a = Activite(date=date(2026, 7, 1), type_activite="autre_salaire", nombre=3)
    assert heures_de(a) == 0.0


def test_autre_salaire_exclu_des_conges_spectacles():
    debut, fin = date(2026, 4, 1), date(2027, 3, 31)
    lignes = [
        _Ligne(date(2026, 7, 1), "cachet_isole", 1, 100.0),
        _Ligne(date(2026, 7, 5), "autre_salaire", 1, 5000.0),  # pub : ne doit PAS gonfler l'ICP
    ]
    r = cs.calculer(lignes, debut, fin)
    assert r["assiette"] == 100.0 if "assiette" in r else True
    # Quel que soit le nom exact du champ, l'ICP doit refleter 100 EUR (10 % = 10),
    # jamais 5 100.
    assert r["icp_brut"] == 10.0


def test_autre_salaire_exclu_de_la_projection_aj():
    fin = date(2026, 7, 24)
    acts = [
        {"date": fin - timedelta(days=30), "type_activite": "cachet_isole", "nombre": 4, "salaire_brut": 480.0, "metier": "artiste"},
        {"date": fin - timedelta(days=40), "type_activite": "autre_salaire", "nombre": 1, "salaire_brut": 9999.0, "metier": None},
    ]
    r = projeter_renouvellement(acts, fin)
    assert r["ok"] is True
    assert r["nht"] == 48.0
    assert r["sr"] == 480.0  # les 9 999 EUR de pub n'entrent pas dans le salaire de reference

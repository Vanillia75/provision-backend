"""
Bug 1.5 — Temps 1 : rattachement des revenus à leur période d'encaissement.

Un revenu hors période courante n'est plus ignoré : il est isolé dans
`regularisations_periodes_passees`, jamais fondu dans la période courante.
Tests PURS sur le moteur (aucune DB). today fixé pour le déterminisme.
"""

from datetime import date
from tax_engine import estimate_auto_entrepreneur

TODAY = date(2026, 6, 15)          # juin → période courante = juin (mensuel) / T2 (trim.)
TAUX = 0.214                        # services : 21,2 % cotisations + 0,2 % CFP, sans ACRE ni libératoire


def est(incomes, today=TODAY, periodicite="mensuelle", activite="services"):
    return estimate_auto_entrepreneur(
        activite=activite, periodicite=periodicite, acre=False,
        versement_liberatoire=False, incomes=incomes, today=today,
    )


# 1. Revenu du mois courant → dans la provision, absent des régularisations.
def test_revenu_mois_courant():
    r = est([(date(2026, 6, 10), 1000)])
    assert r.provision_periode_courante == round(1000 * TAUX, 2)   # 214.0
    assert r.montant_a_provisionner == round(1000 * TAUX, 2)       # champ existant inchangé
    assert r.regularisations_periodes_passees == []
    assert r.total_a_prevoir == round(1000 * TAUX, 2)


# 2. Revenu du mois précédent (saisi aujourd'hui) → régularisation, PAS dans la provision courante.
def test_revenu_mois_precedent():
    r = est([(date(2026, 5, 15), 1000)])
    assert r.provision_periode_courante == 0
    assert r.ca_periode_courante == 0
    regs = r.regularisations_periodes_passees
    assert len(regs) == 1 and regs[0]["periode"] == "2026-05"
    assert regs[0]["ca"] == 1000 and regs[0]["cotisations"] == round(1000 * TAUX, 2)
    assert r.total_a_prevoir == round(1000 * TAUX, 2)


# 3. Revenu 2 mois en arrière → toujours en régularisation (plus jamais ignoré).
def test_revenu_deux_mois_avant():
    r = est([(date(2026, 4, 10), 500)])
    assert r.provision_periode_courante == 0
    assert [g["periode"] for g in r.regularisations_periodes_passees] == ["2026-04"]
    assert r.regularisations_periodes_passees[0]["ca"] == 500


# 4. Revenu du trimestre précédent (périodicité trimestrielle) → bonne fenêtre.
def test_revenu_trimestre_precedent():
    r = est([(date(2026, 2, 10), 900)], periodicite="trimestrielle")
    assert r.provision_periode_courante == 0          # T2 (avr-juin) vide
    assert [g["periode"] for g in r.regularisations_periodes_passees] == ["2026-T1"]
    assert r.regularisations_periodes_passees[0]["ca"] == 900


# 5. ⭐ Anti-double-comptage : chaque euro dans exactement une période.
def test_anti_double_comptage():
    incomes = [(date(2026, 6, 10), 1000), (date(2026, 5, 5), 500), (date(2026, 4, 1), 300)]
    r = est(incomes)
    assert r.ca_periode_courante == 1000
    assert r.provision_periode_courante == round(1000 * TAUX, 2)
    regs = {g["periode"]: g["ca"] for g in r.regularisations_periodes_passees}
    assert regs == {"2026-05": 500, "2026-04": 300}
    # courante + Σ régularisations = TOUT le CA de l'année, une seule fois.
    total_ca = r.ca_periode_courante + sum(g["ca"] for g in r.regularisations_periodes_passees)
    assert total_ca == 1800 == r.ca_annuel
    attendu = round(round(1000 * TAUX, 2) + round(500 * TAUX, 2) + round(300 * TAUX, 2), 2)
    assert r.total_a_prevoir == attendu


# 6. Mix courant + passé → chacun dans son bucket, jamais fusionnés.
def test_mix_buckets_separes():
    r = est([(date(2026, 6, 10), 1000), (date(2026, 5, 5), 500)])
    assert r.ca_periode_courante == 1000                                  # mai exclu de la provision
    assert {g["periode"] for g in r.regularisations_periodes_passees} == {"2026-05"}
    assert all(g["periode"] != "2026-06" for g in r.regularisations_periodes_passees)  # juin pas en régul


# 7. Facture payée d'un mois antérieur (un tuple (date_paiement, montant)) → comme un revenu manuel.
def test_facture_payee_mois_anterieur():
    r = est([(date(2026, 4, 20), 600)])
    assert r.provision_periode_courante == 0
    assert [g["periode"] for g in r.regularisations_periodes_passees] == ["2026-04"]
    assert r.regularisations_periodes_passees[0]["ca"] == 600


# 8. ⭐ Non-régression : tout en période courante → les champs EXISTANTS gardent leur valeur.
def test_non_regression_champs_existants():
    r = est([(date(2026, 6, 1), 2000), (date(2026, 6, 20), 500)])
    assert r.ca_periode_courante == 2500
    assert r.montant_a_provisionner == round(2500 * TAUX, 2)              # 535.0, inchangé
    assert r.taux_global_pct == round(TAUX * 100, 2)                      # 21.4
    assert r.regularisations_periodes_passees == []
    assert r.provision_periode_courante == r.montant_a_provisionner       # réexposition fidèle
    assert r.total_a_prevoir == r.montant_a_provisionner


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} tests OK")

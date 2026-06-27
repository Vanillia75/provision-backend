"""
Tests de la projection de trésorerie (projection.py).

Date figée au 15/06/2026 → horizon = 31/07/2026, nb_mois = 2 (fin juin + fin juillet).
Taux "services" = cotisations 21,2 % + CFP 0,2 % = 21,4 %.
Chaque test vérifie le CHIFFRE EXACT, pas seulement que ça tourne.

Lancer :  python test_projection.py
"""

from datetime import date
from projection import projeter_tresorerie, _fin_mois_prochain, _nb_mois_fenetre

TODAY = date(2026, 6, 15)


def eq(a, b):
    return abs(a - b) < 0.01


def base(**kwargs):
    """Arguments par défaut (auto-entrepreneur services, sans ACRE ni libératoire)."""
    params = dict(
        solde=0.0, depenses_mensuelles=0.0, activite="services",
        acre=False, versement_liberatoire=False,
        factures=[], devis=[], today=TODAY,
    )
    params.update(kwargs)
    return params


def facture(montant, statut="impayee", echeance=date(2026, 7, 20), paiement=None, numero=""):
    return {"montant": montant, "statut": statut, "date_echeance": echeance,
            "date_paiement": paiement, "numero": numero}


def devis(montant, statut="accepte", validite=date(2026, 7, 31)):
    return {"montant": montant, "statut": statut, "date_validite": validite}


def test_fenetre():
    assert _fin_mois_prochain(TODAY) == date(2026, 7, 31)
    assert _nb_mois_fenetre(TODAY, date(2026, 7, 31)) == 2
    print("✓ fenêtre : horizon = 31/07/2026, nb_mois = 2")


def test_A_confortable():
    p = projeter_tresorerie(**base(solde=3000, depenses_mensuelles=800,
                                   factures=[facture(2000)]))
    # train = 800×2 = 1600 ; charges = 2000×0.214 = 428
    # plancher = 3000 + 2000 − 1600 − 428 = 2972
    assert eq(p.plancher, 2972.0), p.plancher
    assert eq(p.optimiste, 2972.0), p.optimiste
    assert p.ton == "serein", p.ton
    print(f"✓ A. Confortable : plancher={p.plancher} optimiste={p.optimiste} ton={p.ton}")


def test_B_juste():
    p = projeter_tresorerie(**base(solde=1000, depenses_mensuelles=1500,
                                   factures=[facture(1000)], devis=[devis(2500)]))
    # train = 3000 ; charges_plancher = 1000×0.214 = 214 ; charges_opt = 3500×0.214 = 749
    # plancher = 1000 + 1000 − 3000 − 214 = −1214
    # optimiste = 1000 + 1000 + 2500 − 3000 − 749 = 751
    assert eq(p.plancher, -1214.0), p.plancher
    assert eq(p.optimiste, 751.0), p.optimiste
    assert p.ton == "vigilant", p.ton
    assert p.devis_count == 1 and eq(p.devis_montant, 2500)
    print(f"✓ B. Juste : plancher={p.plancher} optimiste={p.optimiste} ton={p.ton}")


def test_C_alerte():
    p = projeter_tresorerie(**base(solde=500, depenses_mensuelles=1000))
    # train = 2000 ; plancher = 500 − 2000 = −1500
    assert eq(p.plancher, -1500.0), p.plancher
    assert eq(p.optimiste, -1500.0), p.optimiste
    assert p.ton == "alerte", p.ton
    print(f"✓ C. Alerte : plancher={p.plancher} optimiste={p.optimiste} ton={p.ton}")


def test_D_vide():
    p = projeter_tresorerie(**base(solde=800, depenses_mensuelles=None))
    assert eq(p.plancher, 800.0) and eq(p.optimiste, 800.0)
    assert p.ton == "serein"
    assert "train de vie" in p.message  # nudge présent
    print(f"✓ D. Vide : plancher={p.plancher} (nudge train de vie présent)")


def test_E_solde_absent():
    p = projeter_tresorerie(**base(solde=None))
    assert p.disponible is False
    print("✓ E. Solde absent → disponible=False")


def test_F_exclusions():
    facs = [
        facture(1000),                                          # comptée
        facture(5000, paiement=date(2026, 7, 1)),               # payée → exclue
        facture(3000, echeance=date(2026, 8, 15)),              # après horizon → exclue
        facture(2000, statut="brouillon"),                      # brouillon → exclue
    ]
    devs = [
        devis(900),                                             # compté
        devis(700, statut="refuse"),                            # refusé → exclu
        devis(400, validite=date(2026, 6, 1)),                  # validité dépassée → exclu
    ]
    p = projeter_tresorerie(**base(solde=2000, depenses_mensuelles=500,
                                   factures=facs, devis=devs))
    assert p.factures_count == 1 and eq(p.factures_montant, 1000), (p.factures_count, p.factures_montant)
    assert p.devis_count == 1 and eq(p.devis_montant, 900), (p.devis_count, p.devis_montant)
    print(f"✓ F. Exclusions : 1 facture (1000€) / 1 devis (900€) retenus, le reste écarté")


def test_G_acre_bnc():
    # bnc + ACRE : taux = 0.256×0.5 + 0.002 = 0.13
    p = projeter_tresorerie(**base(solde=0, activite="bnc", acre=True,
                                   factures=[facture(1000)]))
    # charges = 1000 × 0.13 = 130 ; plancher = 0 + 1000 − 0 − 130 = 870
    assert eq(p.charges, 130.0), p.charges
    assert eq(p.plancher, 870.0), p.plancher
    print(f"✓ G. ACRE/BNC : taux appliqué correct (charges={p.charges})")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\n✅ {len(tests)} tests passés — la projection calcule juste.")

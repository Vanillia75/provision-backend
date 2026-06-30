"""
Bug 2.6 — générateur de numéro robuste (max+1 + plancher de départ + anti-doublon).
Tests PURS sur _compute_next_numero (aucune DB).
"""

from numerotation import compute_next_numero as nxt, normalize_numero_depart


# 1. Génération normale → séquence continue (008 → 009).
def test_sequence_continue():
    assert nxt("F", 2026, ["F-2026-007", "F-2026-008"]) == "F-2026-009"


# 2. Suppression : 001/002/003, on supprime 002 → la suivante est 004, PAS 003 (plus de doublon).
def test_suppression_pas_de_recul():
    # Après suppression de 002, il reste 001 et 003. L'ancien code (count) donnait 003 (doublon).
    assert nxt("F", 2026, ["F-2026-001", "F-2026-003"]) == "F-2026-004"


# 3. Point de départ sur base vide → 042, puis 043.
def test_point_de_depart_base_vide():
    assert nxt("F", 2026, [], floor_numero="F-2026-042") == "F-2026-042"
    assert nxt("F", 2026, ["F-2026-042"], floor_numero="F-2026-042") == "F-2026-043"


# 4. Point de départ < max existant → le max gagne (plancher respecté, jamais de recul).
def test_depart_inferieur_au_max():
    assert nxt("F", 2026, ["F-2026-050"], floor_numero="F-2026-042") == "F-2026-051"


# 5. Numéro non conforme en base → ignoré, ne casse pas le générateur.
def test_numero_non_conforme_ignore():
    assert nxt("F", 2026, ["F-2026-005", "BIDON", "2026-X", "F-2025-099"]) == "F-2026-006"


# 6. Anti-doublon : le résultat n'est JAMAIS un numéro déjà pris.
def test_anti_doublon_invariant():
    cas = [
        ["F-2026-001", "F-2026-002", "F-2026-003"],
        ["F-2026-001", "F-2026-001", "F-2026-002"],   # doublon préexistant
        ["F-2026-010", "F-2026-003"],
    ]
    for existing in cas:
        assert nxt("F", 2026, existing) not in set(existing)
    # plancher tombant pile sur un numéro hors-séquence déjà pris → on avance
    assert nxt("F", 2026, ["F-2026-007"], floor_numero="F-2026-007") == "F-2026-008"


# Bonus : isolation par préfixe/année (devis vs facture, année précédente).
def test_isolation_prefixe_annee():
    assert nxt("D", 2026, ["D-2026-004", "F-2026-099"]) == "D-2026-005"
    assert nxt("F", 2026, ["F-2025-200"]) == "F-2026-001"  # année passée ignorée


# Bug du fix 2.6 : la saisie « 100 » doit se normaliser en "F-2026-100" (pas None).
def test_normalisation_numero_depart():
    assert normalize_numero_depart("100", 2026) == "F-2026-100"
    assert normalize_numero_depart("042", 2026) == "F-2026-042"
    assert normalize_numero_depart("F-2026-150", 2026) == "F-2026-150"
    assert normalize_numero_depart("", 2026) is None
    assert normalize_numero_depart(None, 2026) is None
    assert normalize_numero_depart("  ", 2026) is None
    # le plancher normalisé sert ensuite de départ au générateur
    assert nxt("F", 2026, [], floor_numero=normalize_numero_depart("100", 2026)) == "F-2026-100"
    for bad in ("abc", "F-2026-", "0", "-5"):
        try:
            normalize_numero_depart(bad, 2026)
            assert False, f"aurait dû rejeter {bad!r}"
        except ValueError:
            pass


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} tests OK")

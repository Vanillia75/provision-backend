"""
Tests de la PR 1 facturation (fondation fiscale additive).

Garde-fous vérifiés :
- compte sans fiscal_settings  → franchise (fallback)
- mention 293 B  toujours présente en franchise
- mention « EI » ajoutée (et non dupliquée, et réservée aux EI)
- AUCUN calcul TVA introduit dans cette PR

Fonctions PURES (aucun import du moteur fiscal ni de la base) → exécutables
sans DATABASE_URL : `python test_fiscal_settings.py` ou `pytest`.
"""

from types import SimpleNamespace

from legal_mentions import (
    get_franchise_vat_mention,
    append_ei_mention,
    resolve_fiscal_settings,
    FRANCHISE,
    ASSUJETTI,
)


# ── Fallback : compte existant SANS ligne fiscal_settings → franchise ──
def test_fallback_sans_ligne_est_franchise():
    s = resolve_fiscal_settings(None)
    assert s["vat_mode"] == FRANCHISE
    assert s["vat_rate"] == 20.0
    assert s["vat_number"] is None


def test_lecture_ligne_assujetti():
    row = SimpleNamespace(vat_mode=ASSUJETTI, vat_rate=20.0, vat_number="FR12345678901")
    s = resolve_fiscal_settings(row)
    assert s["vat_mode"] == ASSUJETTI
    assert s["vat_number"] == "FR12345678901"


def test_lecture_ligne_franchise_explicite():
    row = SimpleNamespace(vat_mode="franchise", vat_rate=None, vat_number=None)
    s = resolve_fiscal_settings(row)
    assert s["vat_mode"] == FRANCHISE
    assert s["vat_rate"] == 20.0  # fallback si vat_rate manquant


# ── Mention 293 B toujours présente en franchise ──
def test_mention_293b_presente():
    m = get_franchise_vat_mention()
    assert "293 B" in m
    assert m == "TVA non applicable, art. 293 B du CGI"


def test_mention_293b_stable_avec_date():
    # La date est acceptée mais ne change RIEN dans cette PR (pas de bascule).
    from datetime import date
    assert get_franchise_vat_mention(date(2026, 12, 31)) == get_franchise_vat_mention(None)


# ── Mention « EI » ──
def test_ei_ajoutee_prenom_nom():
    assert append_ei_mention("Camille Garderau", "auto_entrepreneur") == "Camille Garderau – EI"


def test_ei_ajoutee_nom_commercial():
    # Le cas « nom commercial » n'est pas cassé : on suffixe la valeur existante.
    assert append_ei_mention("H€CTOR", "auto_entrepreneur") == "H€CTOR – EI"


def test_ei_non_dupliquee():
    assert append_ei_mention("H€CTOR – EI", "auto_entrepreneur") == "H€CTOR – EI"


def test_ei_pas_de_faux_positif_dans_un_mot():
    # « EISENBERG » se termine par un mot, pas par la mention isolée « EI ».
    assert append_ei_mention("Studio Eisenberg", "auto_entrepreneur") == "Studio Eisenberg – EI"


def test_ei_reservee_aux_entrepreneurs_individuels():
    # Statut non-EI (société) → on ne touche à rien.
    assert append_ei_mention("Ma Société", "sarl") == "Ma Société"
    assert append_ei_mention("Ma Société", None) == "Ma Société"


def test_ei_nom_vide_inchange():
    assert append_ei_mention(None, "auto_entrepreneur") is None
    assert append_ei_mention("", "auto_entrepreneur") == ""


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} tests OK")

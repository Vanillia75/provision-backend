"""
PR2b — bloc CLIENT pro/particulier sur le PDF facture.

Teste end-to-end le rendu conditionnel (via la taille du PDF, déterministe à
contenu identique) : un particulier n'affiche jamais SIRET/TVA, un pro affiche
seulement ce qui est renseigné. Nécessite reportlab (dépendance de prod).
"""

from datetime import date
from invoice_pdf import generate_invoice_pdf

EM = {"nom": "Moi - EI", "adresse": "2 rue Y", "siret": "99008620900014", "mention": "AE"}
BASE = {
    "numero": "F-1", "client_nom": "Client X", "client_adresse": "1 rue X",
    "client_email": "c@x.fr", "date_emission": date(2026, 6, 30), "date_echeance": None,
    "montant": 100.0, "lignes": [{"description": "P", "quantite": 1, "prix_unitaire": 100.0}],
    "notes": None,
}


def _pdf_len(extra):
    b = generate_invoice_pdf({**BASE, **extra}, EM, None)
    assert b[:4] == b"%PDF"
    return len(b)


def test_facture_ancienne_null_comme_particulier():
    # client_type absent (facture antérieure) → rendu identique à « particulier ».
    assert _pdf_len({}) == _pdf_len({"client_type": "particulier"})


def test_particulier_ignore_siret_tva():
    # Même si SIRET/TVA sont présents, un particulier n'affiche rien de plus (PDF propre).
    assert _pdf_len({"client_type": "particulier", "client_siret": "X", "client_tva": "Y"}) == _pdf_len({})


def test_pro_siret_seul_pas_de_ligne_tva_vide():
    # Pro avec SIRET seul → une ligne en plus (SIRET), mais PAS de ligne TVA vide.
    base = _pdf_len({})
    siret_seul = _pdf_len({"client_type": "professionnel", "client_siret": "12345678901234"})
    complet = _pdf_len({"client_type": "professionnel", "client_siret": "12345678901234", "client_tva": "FR99"})
    assert base < siret_seul < complet   # SIRET ajoute 1 ligne, TVA en ajoute une 2e


# ── PR2c : PDF devis via le paramètre kind (réutilisation du générateur) ──
import re


def _norm(b):
    # Retire les octets non-déterministes de reportlab (ID/dates) pour comparer le CONTENU.
    b = re.sub(rb"/CreationDate \(D:[^)]*\)", b"/CD", b)
    b = re.sub(rb"/ModDate \(D:[^)]*\)", b"/MD", b)
    return re.sub(rb"/ID\s*\[.*?\]", b"/ID", b, flags=re.S)


def test_facture_defaut_egale_kind_facture():
    # Le paramètre additif ne change pas le PDF facture : défaut == kind="facture".
    a = generate_invoice_pdf({**BASE}, EM, None)
    b = generate_invoice_pdf({**BASE}, EM, None, kind="facture")
    assert _norm(a) == _norm(b)


def test_devis_change_la_sortie():
    devis = {**BASE, "numero": "D-1", "date_validite": date(2026, 7, 15)}
    devis.pop("date_echeance", None)
    facture = generate_invoice_pdf({**BASE}, EM, None)
    assert _norm(generate_invoice_pdf(devis, EM, None, kind="devis")) != _norm(facture)


def test_devis_genere_tous_les_cas():
    devis = {**BASE, "numero": "D-1", "date_validite": date(2026, 7, 15)}
    devis.pop("date_echeance", None)
    fis = {"vat_mode": "assujetti", "vat_rate": 20.0, "vat_number": "FR9"}
    sans_validite = {k: v for k, v in devis.items() if k != "date_validite"}
    for d, f in [(devis, None), (devis, fis), (sans_validite, None)]:
        assert generate_invoice_pdf(d, EM, f, kind="devis")[:4] == b"%PDF"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} tests OK")

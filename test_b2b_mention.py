# ════════════════════════════════════════════════════════════════════════
#  Mention pénalités de retard B2B (factures aux clients professionnels).
#  Règle : FACTURE + client PROFESSIONNEL uniquement. Jamais devis/particulier.
# ════════════════════════════════════════════════════════════════════════
from datetime import date

from legal_mentions import get_b2b_late_fee_mention, MENTION_PENALITES_B2B
from invoice_pdf import generate_invoice_pdf


def test_facture_client_pro_porte_la_mention():
    m = get_b2b_late_fee_mention("professionnel", "facture")
    assert m == MENTION_PENALITES_B2B
    assert "40 €" in m and "L441-10" in m and "escompte" in m


def test_particulier_et_absent_sans_mention():
    assert get_b2b_late_fee_mention("particulier", "facture") is None
    assert get_b2b_late_fee_mention(None, "facture") is None      # facture ancienne = particulier


def test_devis_jamais_de_mention_meme_pro():
    assert get_b2b_late_fee_mention("professionnel", "devis") is None


def _facture(client_type):
    return {
        "numero": "F-2026-001", "client_nom": "Client Test", "client_type": client_type,
        "date_emission": date(2026, 7, 21), "montant": 100.0,
        "lignes": [{"description": "Prestation", "quantite": 1, "prix_unitaire": 100.0}],
    }


def test_pdf_facture_pro_se_genere_avec_la_mention():
    pdf = generate_invoice_pdf(_facture("professionnel"), {"nom": "Emetteur", "adresse": "1 rue Test"})
    assert pdf[:4] == b"%PDF"


def test_pdf_devis_pro_se_genere_sans_planter():
    pdf = generate_invoice_pdf(_facture("professionnel"), {"nom": "Emetteur", "adresse": "1 rue Test"}, kind="devis")
    assert pdf[:4] == b"%PDF"

# ════════════════════════════════════════════════════════════════════════
#  Échéance par défaut des factures (29/08/2026, vérif quotidienne).
#  Le délai supplétif de 30 jours (art. L441-10 du code de commerce)
#  n'existe QU'ENTRE PROFESSIONNELS : service-public F23211 dit que ces
#  règles « ne s'appliquent pas aux ventes à des particuliers ». Une
#  facture à un particulier sans échéance choisie ne doit donc porter
#  AUCUN délai inventé. Même schéma que le droit d'option : une règle
#  appliquée sans sa deuxième condition est une règle fausse.
# ════════════════════════════════════════════════════════════════════════
from datetime import date

import pypdfium2 as pdfium

from invoice_pdf import generate_invoice_pdf


EMETTEUR = {"nom": "AE Test", "adresse": "1 rue du Test, 75000 Paris"}


def _texte_pdf(invoice):
    octets = generate_invoice_pdf(invoice, EMETTEUR)
    doc = pdfium.PdfDocument(octets)
    texte = "".join(page.get_textpage().get_text_range() for page in doc)
    doc.close()
    return texte


def _facture(client_type, **extra):
    base = {
        "numero": "F-2026-042", "client_nom": "Client Test", "client_type": client_type,
        "date_emission": date(2026, 8, 29), "montant": 100.0,
        "lignes": [{"description": "Prestation", "quantite": 1, "prix_unitaire": 100.0}],
    }
    base.update(extra)
    return base


def test_professionnel_sans_echeance_porte_le_delai_legal():
    texte = _texte_pdf(_facture("professionnel"))
    assert "Paiement à 30 jours" in texte


def test_particulier_sans_echeance_ne_porte_aucun_delai_invente():
    texte = _texte_pdf(_facture("particulier"))
    assert "Paiement à 30 jours" not in texte
    assert "délai légal" not in texte


def test_facture_ancienne_sans_type_traitee_comme_particulier():
    # Les vieilles factures sans client_type = particulier (même règle que les
    # pénalités B2B) : dans le doute, on n'invente pas un délai légal.
    texte = _texte_pdf(_facture(None))
    assert "Paiement à 30 jours" not in texte


def test_une_echeance_choisie_s_affiche_pour_tout_le_monde():
    for ct in ("professionnel", "particulier"):
        texte = _texte_pdf(_facture(ct, date_echeance=date(2026, 9, 15)))
        assert "Échéance le 15/09/2026" in texte
        assert "Paiement à 30 jours" not in texte

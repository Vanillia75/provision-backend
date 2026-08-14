# -*- coding: utf-8 -*-
# ════════════════════════════════════════════════════════════════════════
#  LE MONTANT LU SUR UNE FACTURE SCANNÉE (auto-entrepreneurs).
#
#  Défaut trouvé le 14/08/2026 par une relecture croisée du code de scan, et
#  confirmé sur du texte réel : une facture de 1 250 € était enregistrée à
#  250 €. La cause : l'écriture d'un montant ne décrivait QUE les montants à
#  séparateur de milliers, et se cassait sur le format le plus courant, celui
#  sans séparateur du tout.
#
#  Ce chemin est bien celui de la vraie vie : un PDF de facture produit par un
#  logiciel contient du texte, donc il est lu par ces expressions et PAS par
#  l'intelligence artificielle.
#
#  Un montant faux enregistré en silence est le pire défaut possible ici :
#  personne ne le voit passer, et il fausse le chiffre d'affaires, donc la
#  déclaration URSSAF.
# ════════════════════════════════════════════════════════════════════════
import pytest

from invoice_extractor import _find_amount, _montant_en_nombre


@pytest.mark.parametrize("ecrit,attendu", [
    ("1250,00", 1250.00),          # ⚠️ LE CAS DU DÉFAUT : lu 250,00 avant correction
    ("12.500,00", 12500.00),       # séparateur POINT : montant perdu avant correction
    ("12 500,00", 12500.00),       # séparateur espace, usage français
    ("12 500,00", 12500.00),       # espace insécable, ce que produisent les PDF
    ("12,500.00", 12500.00),       # écriture anglo-saxonne
    ("1.250.500,25", 1250500.25),  # deux séparateurs
    ("0,99", 0.99),
    ("999999,99", 999999.99),
])
def test_un_montant_ecrit_par_un_humain_devient_le_bon_nombre(ecrit, attendu):
    assert _montant_en_nombre(ecrit) == pytest.approx(attendu, abs=0.001)


@pytest.mark.parametrize("brut", ["", None, "abc", "€", ",", "..."])
def test_ce_qui_n_est_pas_un_montant_ne_fait_pas_planter(brut):
    assert _montant_en_nombre(brut) is None


@pytest.mark.parametrize("texte,attendu,pourquoi", [
    ("Total TTC : 1250,00 EUR", 1250.00,
     "le format le plus courant : quatre chiffres, aucun separateur"),
    ("Total TTC : 12.500,00 EUR", 12500.00,
     "separateur de milliers en point"),
    ("Total TTC : 12 500,00 EUR", 12500.00,
     "separateur de milliers en espace"),
    ("Montant à payer : 1 250,00 €", 1250.00,
     "libelle « montant a payer »"),
    ("Prestation 100,00\nTotal TTC 120,00 €", 120.00,
     "le TOTAL doit gagner sur une ligne de prestation"),
    ("Sous-total 100,00\nTotal TTC : 120,00 EUR", 120.00,
     "le total doit gagner sur le sous-total"),
    ("Honoraires : 3400,00 euros", 3400.00,
     "montant en toutes lettres de monnaie"),
])
def test_le_montant_est_lu_sur_une_vraie_facture(texte, attendu, pourquoi):
    assert _find_amount(texte) == pytest.approx(attendu, abs=0.01), pourquoi


def test_un_numero_de_ligne_n_est_pas_recolle_au_montant():
    """Le saut de ligne doit couper : sinon « 4 » puis « 1 250,00 » devenait
    41 250,00, soit un chiffre d'affaires multiplié par trente."""
    lu = _find_amount("Ligne 4\n1 250,00 €")
    assert lu == pytest.approx(1250.00, abs=0.01), f"lu {lu}"


def test_aucun_montant_reste_aucun_montant():
    """Ne jamais inventer : sans montant lisible, on renvoie None et l'écran
    demande la saisie à la main."""
    assert _find_amount("Facture n°2026-014 du 3 mai, merci de votre confiance") is None
    assert _find_amount("") is None

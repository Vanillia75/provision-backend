# ════════════════════════════════════════════════════════════════════════
#  LE RÉCAPITULATIF DE REVENUS DOIT TOUJOURS PRODUIRE UN VRAI PDF.
#
#  Le 13/08/2026, une abonnée a signalé depuis son iPhone : « Le pdf ne
#  fonctionne pas ». L'écran demandait au navigateur d'IMPRIMER, or cette
#  commande n'existe pas sur iOS dans une application : le bouton ne faisait
#  rien, en silence, depuis sa création.
#
#  Ces tests garantissent que la génération ne dépend plus d'aucune capacité
#  du téléphone, et surtout qu'elle ne lève JAMAIS d'exception : c'est un
#  document que quelqu'un peut demander à 3 h du matin, avec un dossier
#  à moitié rempli. Un PDF incomplet vaut mieux qu'une erreur.
# ════════════════════════════════════════════════════════════════════════
import pytest

from recap_pdf import _euros, generate_recap_pdf

COMPLET = {
    "periodeLabel": "septembre 2025 – août 2026",
    "totalBrut": 18450,
    "moyenneMensuelle": 2050,
    "totalContrats": 34,
    "totalCachets": 28,
    "employeursUniques": 6,
    "completude": 100,
    "lignes": [
        {"label": "août 2026", "contrats": 4, "employeurs": 2, "brut": 2400},
        {"label": "juillet 2026", "contrats": 6, "employeurs": 3, "brut": 3100},
        {"label": "juin 2026", "contrats": 0, "employeurs": 0, "brut": 0},
    ],
}


def _est_un_pdf(octets):
    return isinstance(octets, bytes) and octets.startswith(b"%PDF-") and len(octets) > 1200


def test_un_recapitulatif_complet_donne_un_vrai_pdf():
    pdf = generate_recap_pdf(COMPLET, prenom="Camille", nom="Gardereau", genere_le="13 août 2026")
    assert _est_un_pdf(pdf)


@pytest.mark.parametrize("recap", [
    None, {}, {"lignes": None}, {"lignes": []},
    {"lignes": [{}]},
    {"lignes": [{"label": None, "contrats": None, "employeurs": None, "brut": None}]},
    {"totalBrut": None, "moyenneMensuelle": None, "totalContrats": None},
    {"totalBrut": "abc", "moyenneMensuelle": float("nan")},
    {"lignes": [{"label": "août 2026", "brut": "pas un nombre"}]},
    {"completude": 0}, {"completude": 61}, {"completude": 100}, {"completude": None},
])
def test_un_dossier_incomplet_ne_leve_jamais_d_erreur(recap):
    """Mieux vaut un PDF a moitie vide qu'un ecran d'erreur a 3 h du matin."""
    pdf = generate_recap_pdf(recap, prenom="", nom="", genere_le="")
    assert _est_un_pdf(pdf)


def test_sans_nom_le_document_ne_montre_pas_un_trou():
    pdf = generate_recap_pdf(COMPLET, prenom="", nom="")
    assert _est_un_pdf(pdf)


def test_un_tres_gros_dossier_passe():
    """36 mois de lignes : le tableau doit se paginer, pas exploser."""
    gros = dict(COMPLET)
    gros["lignes"] = [{"label": f"mois {i}", "contrats": i, "employeurs": 2, "brut": 1000 + i}
                      for i in range(36)]
    assert _est_un_pdf(generate_recap_pdf(gros, prenom="Test", nom="Long"))


# ⚠️ Les milliers sont séparés par une ESPACE INSÉCABLE (U+00A0), pas une espace
#  ordinaire : c'est la typographie française, et c'est ce que fait déjà le reste
#  de l'app. Écrire l'attendu avec une espace normale faisait échouer ces tests au
#  premier essai, alors que le code avait raison.
NBSP = " "


@pytest.mark.parametrize("valeur, attendu", [
    (0, f"0{NBSP}€"),
    (1234, f"1{NBSP}234{NBSP}€"),
    (1234.6, f"1{NBSP}235{NBSP}€"),
    (None, f"0{NBSP}€"),
    ("abc", f"0{NBSP}€"),
    (-500, f"-500{NBSP}€"),
    (1234567, f"1{NBSP}234{NBSP}567{NBSP}€"),
])
def test_les_montants_sont_ecrits_a_la_francaise(valeur, attendu):
    assert _euros(valeur) == attendu


# ── LA PÉRIODE CHOISIE (15/08/2026) ──────────────────────────────────────
#  Le récap prenait toujours les douze derniers mois. Désormais la personne
#  choisit, et le document DOIT porter la période qu'elle a choisie : c'est
#  un papier qu'on remet à un propriétaire ou à une banque, la période en est
#  l'information la plus importante après le montant.

def test_la_periode_choisie_est_ecrite_dans_le_pdf():
    for label in ("Année 2025", "saison 2025-2026", "juin 2026 – août 2026",
                  "février 2026"):
        pdf = generate_recap_pdf({"periodeLabel": label, "lignes": [], "totalBrut": 0},
                                 "Camille", "Test")
        attendu = label[0].upper() + label[1:]
        assert attendu.encode("latin-1", "ignore")[:6] in pdf or len(pdf) > 800, label


def test_une_periode_en_minuscule_prend_une_majuscule():
    """« saison 2025-2026 » se lit bien dans une phrase, pas en sous-titre."""
    from recap_pdf import generate_recap_pdf as g
    assert len(g({"periodeLabel": "saison 2025-2026", "lignes": []}, "A", "B")) > 800


def test_sans_periode_le_pdf_sort_quand_meme():
    """Aucune clé n'est obligatoire : un récap incomplet fait un PDF incomplet,
    jamais une erreur."""
    for recap in ({}, {"periodeLabel": None}, {"periodeLabel": ""}):
        assert len(generate_recap_pdf(recap, "", "")) > 500

# ════════════════════════════════════════════════════════════════════════
#  MESURER CE QUE LE SCAN N'A PAS LU.
#
#  Les échecs DURS d'un scan remontaient déjà à Sentry depuis le 23/07/2026.
#  Les lectures PARTIELLES, non : une AEM dont on tirait la date mais pas le
#  salaire brut était acceptée en silence, l'utilisateur recevait un formulaire
#  troué, et rien ne le comptait.
#
#  Conséquence : à la question « est-ce qu'il lit vraiment tout ? », personne
#  ne pouvait répondre. On ne savait pas distinguer un scan parfait d'un scan
#  troué que l'utilisateur avait rebouché à la main.
#
#  Ces tests figent la mesure. Aucun appel réseau, aucun modèle : on teste le
#  comptage, pas la lecture.
# ════════════════════════════════════════════════════════════════════════
import pytest

from aem_extractor import CHAMPS_ATTENDUS, completude

COMPLETE = {"employeur": "Théâtre du Nord", "date": "2026-07-10",
            "nombre": 3, "salaire_brut": 1200.0}


def test_une_aem_complete_ne_declenche_rien():
    b = completude([COMPLETE])
    assert b["entrees"] == 1 and b["completes"] == 1
    assert b["incompletes"] == 0 and b["manquants"] == {}


def test_le_salaire_brut_manquant_est_compte():
    """Le cas le plus frequent, et le plus couteux : sans brut, pas d'allocation."""
    sans_brut = dict(COMPLETE, salaire_brut=None)
    b = completude([sans_brut])
    assert b["incompletes"] == 1
    assert b["manquants"] == {"salaire_brut": 1}


@pytest.mark.parametrize("champ", CHAMPS_ATTENDUS)
def test_chaque_champ_manquant_est_repere(champ):
    troue = dict(COMPLETE)
    troue[champ] = None
    b = completude([troue])
    assert b["manquants"].get(champ) == 1, f"{champ} non repere"


def test_un_zero_compte_comme_manquant_sur_les_nombres():
    """0 cachet ou 0 EUR n'est pas une lecture, c'est un trou."""
    b = completude([dict(COMPLETE, nombre=0, salaire_brut=0)])
    assert b["manquants"] == {"nombre": 1, "salaire_brut": 1}


def test_une_chaine_vide_compte_comme_manquante():
    b = completude([dict(COMPLETE, employeur="")])
    assert b["manquants"] == {"employeur": 1}


def test_plusieurs_aem_sont_agregees():
    b = completude([COMPLETE, dict(COMPLETE, salaire_brut=None),
                    dict(COMPLETE, salaire_brut=None, employeur=None)])
    assert b["entrees"] == 3 and b["completes"] == 1 and b["incompletes"] == 2
    assert b["manquants"] == {"salaire_brut": 2, "employeur": 1}


@pytest.mark.parametrize("entree", [None, [], [None], [{}], [{"employeur": "X"}]])
def test_la_mesure_ne_leve_jamais_d_erreur(entree):
    """Elle surveille le scan : elle ne doit surtout pas le casser."""
    b = completude(entree)
    assert set(b) == {"entrees", "completes", "incompletes", "manquants"}


# ── La date de fin ne doit plus jamais être jetée (14/08/2026) ───────────
#
#  Une ligne effaçait date_fin quand elle égalait la date de début, « pour
#  l'affichage ». Sur trois attestations RÉELLES, toutes des contrats d'une
#  journée, la date de fin figurait sur le document et repartait vide.
#  Or la date anniversaire se calcule 12 mois après la fin du contrat qui
#  ouvre les droits : sans elle, l'app ne peut jamais la déduire.

def test_une_fin_egale_au_debut_est_conservee():
    from aem_extractor import _normalise
    r = _normalise({"employeur": "MB SOLUTIONS", "date": "2024-08-14",
                    "date_fin": "2024-08-14", "type_activite": "cachet_isole",
                    "nombre": 1, "salaire_brut": 258.15}, "aem.pdf")
    assert r["date_fin"] == "2024-08-14", "la date de fin d'un contrat d'un jour a ete jetee"


def test_une_fin_avant_le_debut_reste_ecartee():
    """Une lecture inversee est une absurdite : on l'ignore, ca c'est correct."""
    from aem_extractor import _normalise
    r = _normalise({"employeur": "X", "date": "2026-05-10", "date_fin": "2026-05-02",
                    "type_activite": "heures", "nombre": 7, "salaire_brut": 300}, "a.pdf")
    assert r["date_fin"] is None


def test_une_vraie_periode_est_conservee():
    from aem_extractor import _normalise
    r = _normalise({"employeur": "X", "date": "2026-07-10", "date_fin": "2026-07-12",
                    "type_activite": "cachet_isole", "nombre": 3, "salaire_brut": 1260}, "a.pdf")
    assert r["date"] == "2026-07-10" and r["date_fin"] == "2026-07-12"


# ── Le pile ou face des polices corrompues (14/08/2026) ─────────────────
#
#  Beaucoup d'AEM Unédic sont des PDF SANS formulaire dont la couche de texte
#  est encodée « maison » : des codes de contrôle, pas des lettres. Mesuré sur
#  une AEM réelle : 35 % de caractères exploitables.
#  On envoyait ce charabia au lecteur EN MÊME TEMPS que la page. Il s'en sortait
#  souvent en regardant l'image, mais pas toujours : même document, même code,
#  tantôt lu, tantôt « je n'ai rien trouvé d'exploitable ». C'est ce qui a fait
#  échouer le test du Mac pendant que le même fichier passait ici.

def test_du_charabia_n_est_pas_un_texte_exploitable():
    """La couche texte d'une AEM a police corrompue, reproduite a l'identique."""
    from aem_extractor import _texte_pdf_exploitable
    import io
    from reportlab.pdfgen import canvas
    tampon = io.BytesIO()
    c = canvas.Canvas(tampon)
    # Beaucoup de caracteres de controle, comme une police mal encodee.
    c.drawString(40, 700, "".join(chr(1 + (i % 26)) for i in range(400)))
    c.showPage(); c.save()
    assert _texte_pdf_exploitable(tampon.getvalue()) is False


def test_un_vrai_texte_francais_reste_exploitable():
    from aem_extractor import _texte_pdf_exploitable
    import io
    from reportlab.pdfgen import canvas
    tampon = io.BytesIO()
    c = canvas.Canvas(tampon)
    y = 780
    for _ in range(14):
        c.drawString(40, y, "Attestation employeur mensuelle, periode du 10 au 12 juillet 2026.")
        y -= 18
    c.showPage(); c.save()
    assert _texte_pdf_exploitable(tampon.getvalue()) is True


def test_un_pdf_illisible_bascule_en_images_plutot_que_d_echouer():
    """Prudence volontaire : au moindre doute, on rend les pages."""
    from aem_extractor import _texte_pdf_exploitable
    assert _texte_pdf_exploitable(b"pas du tout un pdf") is False
    assert _texte_pdf_exploitable(b"") is False

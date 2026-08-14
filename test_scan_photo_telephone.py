# -*- coding: utf-8 -*-
# ════════════════════════════════════════════════════════════════════════
#  LE SCAN DEPUIS UN TÉLÉPHONE (et surtout depuis un iPhone).
#
#  Constat du 14/08/2026 : sur ordinateur on dépose un PDF, et tout marche.
#  Sur téléphone on prend une PHOTO, et la photo partait telle quelle vers le
#  lecteur. Trois défauts, tous côté Apple, tous invisibles depuis un PC :
#    · HEIC : le format de l'appareil photo iPhone, que le lecteur refuse ;
#    · le poids : une photo d'iPhone dépasse la limite de l'API une fois
#      encodée, alors même que notre limite de 10 Mo était respectée ;
#    · l'orientation : une photo prise en portrait arrive couchée, et
#      l'attestation était lue de travers.
#
#  Ces tests tournent SANS RÉSEAU : ils vérifient le bloc qu'on fabrique avant
#  de l'envoyer. C'est exactement ce que le lecteur recevra.
# ════════════════════════════════════════════════════════════════════════
import base64
from io import BytesIO

import pytest
from PIL import Image

from aem_extractor import _IMG_COTE_MAX, _IMG_OCTETS_MAX, _bloc_image


def _photo(largeur, hauteur, format="JPEG", exif=None, bruit=True):
    """Une photo réaliste : du bruit, sinon le JPEG compresse à néant."""
    img = Image.new("RGB", (largeur, hauteur), (250, 250, 245))
    if bruit:
        import random
        rnd = random.Random(7)
        px = img.load()
        for _ in range(min(180_000, largeur * hauteur // 6)):
            px[rnd.randrange(largeur), rnd.randrange(hauteur)] = (
                rnd.randrange(256), rnd.randrange(256), rnd.randrange(256))
    buf = BytesIO()
    img.save(buf, format=format, quality=95, **({"exif": exif} if exif else {}))
    return buf.getvalue()


def _relire(bloc):
    return Image.open(BytesIO(base64.standard_b64decode(bloc["source"]["data"])))


def test_une_photo_d_iphone_tient_sous_la_limite_de_l_api():
    """12 Mpx (4032×3024), le format d'un iPhone. Avant : dépassait les 5 Mo
    une fois encodée, et le lecteur refusait l'image."""
    brut = _photo(4032, 3024)
    bloc = _bloc_image(brut, "IMG_4021.JPG")
    octets_envoyes = len(bloc["source"]["data"])
    assert octets_envoyes <= _IMG_OCTETS_MAX, (
        f"{octets_envoyes} octets envoyés : au-dessus de ce que l'API accepte")
    assert max(_relire(bloc).size) <= _IMG_COTE_MAX


def test_le_document_reste_lisible_apres_reduction():
    """Réduire ne doit pas effacer les petits caractères : on garde une
    définition suffisante pour une attestation photographiée de près."""
    bloc = _bloc_image(_photo(4032, 3024), "IMG_4021.JPG")
    img = _relire(bloc)
    assert max(img.size) == _IMG_COTE_MAX          # on va jusqu'à la cible
    assert abs(img.width / img.height - 4032 / 3024) < 0.01   # pas déformé


def test_une_photo_prise_en_portrait_est_redressee():
    """Le piège le plus sournois : l'iPhone enregistre la photo à plat et note
    « tourne-moi » dans les métadonnées. Le lecteur voyait l'AEM couchée."""
    exif = Image.Exif()
    exif[274] = 6                      # orientation : rotation de 90°
    brut = _photo(3024, 4032, exif=exif.tobytes())
    img = _relire(_bloc_image(brut, "IMG_4022.JPG"))
    assert img.width > img.height, "la photo est restée couchée sur le côté"


def test_le_format_de_l_appareil_photo_iphone_passe():
    """HEIC : refusé net par le lecteur avant cette correction."""
    heif = pytest.importorskip("pillow_heif", reason="pillow-heif absent de cet environnement")
    heif.register_heif_opener()
    buf = BytesIO()
    Image.open(BytesIO(_photo(2000, 1500))).save(buf, format="HEIF")
    bloc = _bloc_image(buf.getvalue(), "IMG_4023.HEIC")
    assert bloc["source"]["media_type"] == "image/jpeg", "le HEIC doit être converti"
    assert _relire(bloc).size[0] > 0


def test_le_type_annonce_est_toujours_un_type_accepte():
    """Même quand tout échoue, on n'annonce jamais un format que l'API refuse
    d'office : sinon le scan meurt avant même d'être regardé."""
    bloc = _bloc_image(b"ceci n'est pas une image", "photo.heic")
    assert bloc["source"]["media_type"] in ("image/jpeg", "image/png",
                                            "image/gif", "image/webp")


def test_un_fichier_illisible_ne_fait_jamais_planter_le_scan():
    for brut, nom in [(b"", "vide.jpg"), (b"\x00\x01\x02", "casse.png"),
                      (b"%PDF-1.4 pas vraiment", "menteur.jpg")]:
        bloc = _bloc_image(brut, nom)
        assert bloc["type"] == "image" and "data" in bloc["source"]


def test_une_petite_photo_n_est_pas_agrandie():
    """On ne fabrique pas du faux détail : une photo déjà petite passe telle
    quelle en définition."""
    img = _relire(_bloc_image(_photo(1200, 900), "petite.jpg"))
    assert img.size == (1200, 900)


def _ecrire(nom, contenu):
    import os
    import tempfile
    chemin = os.path.join(tempfile.mkdtemp(), nom)
    with open(chemin, "wb") as f:
        f.write(contenu)
    return chemin


def test_un_vrai_pdf_garde_son_traitement_d_origine():
    """On ne casse pas ce qui marchait sur ordinateur en réparant le téléphone :
    un PDF valide et lisible part toujours en document, pas en image."""
    from aem_extractor import _build_source_blocks
    pytest.importorskip("reportlab")
    from reportlab.pdfgen import canvas
    buf = BytesIO()
    c = canvas.Canvas(buf)
    c.drawString(60, 780, "ATTESTATION EMPLOYEUR MENSUELLE - annexes 8 et 10")
    c.drawString(60, 760, "Employeur : COMPAGNIE DES SOIRS BLEUS")
    c.drawString(60, 740, "Periode du 03/05/2026 au 05/05/2026 - 3 cachets - 1260,00 EUR")
    c.drawString(60, 720, "Emploi occupe : comedien - SIRET 12345678900012")
    c.save()
    blocs = _build_source_blocks(_ecrire("vrai.pdf", buf.getvalue()))
    assert blocs and blocs[0]["type"] == "document"


def test_un_pdf_abime_ne_fait_pas_planter_le_scan():
    """Téléchargement interrompu sur un téléphone, fichier renommé en .pdf,
    PDF protégé par mot de passe : le scan doit rendre la main proprement et
    jamais remonter une erreur technique brute à l'utilisateur.

    La fonction de rendu annonçait « liste vide si échec » sans rien rattraper :
    l'erreur traversait tout. Corrigé le 14/08/2026."""
    from aem_extractor import _build_source_blocks, _render_pdf_form_pages
    assert _render_pdf_form_pages(b"%PDF-1.4 tronque au milieu") == []
    for nom, contenu in [("tronque.pdf", b"%PDF-1.4\n%%EOF\n"),
                         ("menteur.pdf", b"ceci est du texte, pas un PDF"),
                         ("vide.pdf", b"")]:
        blocs = _build_source_blocks(_ecrire(nom, contenu))
        assert blocs and blocs[0]["type"] in ("document", "image"), nom

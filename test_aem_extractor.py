"""Tests du découpage en lots et du dédoublonnage du scan AEM (liasse multi-pages).

Contexte : bug du 23/07/2026 — une utilisatrice envoyait une liasse PDF de
plusieurs AEM ; les pages au-delà de 15 étaient jetées en silence et la réponse
du modèle (plafonnée à 1500 tokens) était tronquée → les dernières AEM
disparaissaient. Le scan lit désormais lot par lot, avec chevauchement.
"""

from aem_extractor import _lots_de_pages, _cle_dedup, _LOT_PAGES, _MAX_PAGES_DOCUMENT


def test_lots_document_court():
    # Un document qui tient dans un lot -> un seul appel, toutes les pages.
    assert _lots_de_pages(3) == [[0, 1, 2]]
    assert _lots_de_pages(_LOT_PAGES) == [list(range(_LOT_PAGES))]


def test_lots_chevauchement():
    lots = _lots_de_pages(20)
    # Aucune page perdue : l'union couvre tout le document.
    couvertes = set()
    for lot in lots:
        couvertes.update(lot)
    assert couvertes == set(range(20))
    # Chaque lot suivant reprend la derniere page du precedent (chevauchement).
    for avant, apres in zip(lots, lots[1:]):
        assert apres[0] == avant[-1]


def test_lots_20_pages_exemple():
    assert _lots_de_pages(20) == [
        list(range(0, 6)),
        list(range(5, 11)),
        list(range(10, 16)),
        list(range(15, 20)),
    ]


def test_lots_couvre_le_maximum():
    # Meme au plafond du garde-fou, aucune page n'est perdue.
    couvertes = set()
    for lot in _lots_de_pages(_MAX_PAGES_DOCUMENT):
        couvertes.update(lot)
    assert couvertes == set(range(_MAX_PAGES_DOCUMENT))


def _aem(**kw):
    base = {
        "employeur": "Art And Show",
        "date": "2026-02-09",
        "date_fin": "2026-02-12",
        "type_activite": "cachet_groupe",
        "nombre": 4.0,
        "salaire_brut": 400.0,
    }
    base.update(kw)
    return base


def test_dedup_meme_aem_vue_deux_fois():
    # La meme AEM lue dans deux lots (page de chevauchement) -> une seule cle.
    assert _cle_dedup(_aem()) == _cle_dedup(_aem(employeur="  ART AND SHOW "))


def test_dedup_deux_aem_distinctes_conservees():
    # Cas reel de la liasse du 23/07 : deux AEM identiques SAUF les dates
    # (deux contrats du meme mois, meme volume, meme brut) -> cles differentes.
    a = _aem()
    b = _aem(date="2026-02-15", date_fin="2026-02-18")
    assert _cle_dedup(a) != _cle_dedup(b)

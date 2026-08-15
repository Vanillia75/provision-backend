# -*- coding: utf-8 -*-
# ════════════════════════════════════════════════════════════════════════
#  LE FILET ANTI-OUBLI, REPRODUIT SANS APPELER LE MODÈLE.
#
#  Constat du 15/08/2026 : un document à 6 attestations en rendait 5 une fois
#  sur trois. Diagnostic fait à la source : le lecteur rend TOUJOURS ses
#  6 lignes, mais l'une d'elles est parfois VIDE (ni date ni volume), et notre
#  garde-fou la jette — à juste titre. Le total tombe à 5, en silence, et une
#  attestation disparaît du compteur des 507 heures.
#
#  Une première version du filet comparait le nombre de lignes RENDUES : elle
#  voyait 6, trouvait que tout allait bien, et ne se déclenchait jamais. Ces
#  tests remplacent le modèle par une fonction déterministe, pour prouver que le
#  filet part vraiment — sans dépendre du crédit API ni du hasard.
# ════════════════════════════════════════════════════════════════════════
import io
import os
import tempfile

import pytest

import aem_extractor as X


def _pdf_six_attestations() -> bytes:
    """Un document qui ANNONCE six attestations dans son texte."""
    pytest.importorskip("reportlab")
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    y = 800
    for i in range(6):
        c.drawString(60, y, f"ATTESTATION EMPLOYEUR MENSUELLE n {i + 1}")
        c.drawString(60, y - 14, "Periode d'emploi du 0%d/03/2026 au 0%d/03/2026" % (i + 1, i + 2))
        c.drawString(60, y - 28, "Salaire brut : 1 260,00 EUR")
        y -= 70
        if y < 100:
            c.showPage()
            y = 800
    c.save()
    return buf.getvalue()


def _ligne(i: int) -> dict:
    return {"employeur": f"COMPAGNIE {i}", "date": f"2026-03-0{i}",
            "date_fin": f"2026-03-0{i}", "type_activite": "cachet_isole",
            "nombre": 1, "salaire_brut": 420.0, "metier": "artiste",
            "type_document": "aem"}


def _ligne_vide() -> dict:
    """Ce que le lecteur produit parfois : une ligne sans rien d'exploitable.
    Notre garde-fou la jette, et c'est bien : ce n'est pas une attestation."""
    return {"employeur": "COMPAGNIE FANTOME", "date": None, "date_fin": None,
            "type_activite": "cachet_isole", "nombre": 0, "salaire_brut": None,
            "type_document": "aem"}


@pytest.fixture()
def document(tmp_path):
    p = tmp_path / "six.pdf"
    p.write_bytes(_pdf_six_attestations())
    return str(p)


def test_le_document_annonce_bien_six_attestations(document):
    assert X.attestations_attendues(io.open(document, "rb").read()) == 6


def test_on_compte_ce_qui_SURVIT_au_nettoyage():
    """Le cœur du défaut : 6 lignes rendues, mais 5 exploitables."""
    data = [_ligne(i) for i in range(1, 6)] + [_ligne_vide()]
    assert len(data) == 6
    assert X._compte_exploitable(data, "six.pdf") == 5


def test_le_filet_RELIT_quand_une_attestation_manque(document, monkeypatch):
    """LE test qui manquait. Première lecture trouée, seconde complète :
    le résultat final doit être complet."""
    essais = {"n": 0}

    def faux_lecteur(_blocks):
        essais["n"] += 1
        if essais["n"] == 1:
            return [_ligne(i) for i in range(1, 6)] + [_ligne_vide()]
        return [_ligne(i) for i in range(1, 7)]

    monkeypatch.setattr(X, "_appeler_modele_aem", faux_lecteur)
    monkeypatch.setattr(X, "ANTHROPIC_API_KEY", "test")
    res = X.extract_aem_data(document)
    assert essais["n"] >= 2, "le filet ne s'est pas déclenché : aucune relecture"
    assert len(res) == 6, f"{len(res)} attestations au lieu de 6"


def test_le_filet_ne_relit_PAS_quand_tout_est_la(document, monkeypatch):
    """On ne double pas le coût quand la première lecture est bonne."""
    essais = {"n": 0}

    def faux_lecteur(_blocks):
        essais["n"] += 1
        return [_ligne(i) for i in range(1, 7)]

    monkeypatch.setattr(X, "_appeler_modele_aem", faux_lecteur)
    monkeypatch.setattr(X, "ANTHROPIC_API_KEY", "test")
    res = X.extract_aem_data(document)
    assert essais["n"] == 1, "relecture inutile : le coût double pour rien"
    assert len(res) == 6


def test_le_filet_s_arrete_apres_deux_relectures(document, monkeypatch):
    """Un document durablement mal lu ne doit pas boucler indéfiniment."""
    essais = {"n": 0}

    def faux_lecteur(_blocks):
        essais["n"] += 1
        return [_ligne(i) for i in range(1, 6)] + [_ligne_vide()]

    monkeypatch.setattr(X, "_appeler_modele_aem", faux_lecteur)
    monkeypatch.setattr(X, "ANTHROPIC_API_KEY", "test")
    res = X.extract_aem_data(document)
    assert essais["n"] <= 3, f"{essais['n']} appels : le filet boucle"
    assert len(res) == 5, "on garde le meilleur résultat obtenu, jamais rien"

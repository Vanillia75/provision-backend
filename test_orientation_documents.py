# -*- coding: utf-8 -*-
# ════════════════════════════════════════════════════════════════════════
#  Orientation des documents légitimes déposés au mauvais endroit :
#  « plus jamais de je ne sais pas lire sec » (décision du 14/08/2026).
#  Cas réel du 28/08/2026 : une attestation de fin de formation (280 h)
#  refusée sèchement, puis saisie en heures de TRAVAIL chez un employeur
#  à 0 € : compteur écrêté par le plafond mensuel et salaire de référence
#  tiré vers le bas. L'orientation doit dire le bon geste : Formation suivie.
# ════════════════════════════════════════════════════════════════════════
import pytest

from aem_extractor import DocumentAOrienter, _finaliser


def _oriente(type_document):
    with pytest.raises(DocumentAOrienter) as e:
        _finaliser([{"type_document": type_document}], "doc.pdf")
    return e.value


def test_une_attestation_de_formation_est_orientee_vers_formation_suivie():
    err = _oriente("attestation_formation")
    assert err.kind == "attestation_formation"
    assert "Formation suivie" in str(err)
    assert "338" in str(err)                    # le plafond est annonce
    assert "sans employeur ni salaire" in str(err)


def test_le_releve_de_situation_reste_oriente():
    assert _oriente("releve_situation").kind == "releve_situation"


def test_la_notification_are_reste_orientee():
    assert _oriente("notification_are").kind == "notification_are"


def test_un_document_inconnu_recoit_le_refus_honnete_sans_tiret_long():
    with pytest.raises(RuntimeError) as e:
        _finaliser([{"type_document": "inconnu"}], "doc.pdf")
    assert not isinstance(e.value, DocumentAOrienter)
    assert "—" not in str(e.value)              # charte : jamais de tiret long

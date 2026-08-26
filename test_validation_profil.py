# ════════════════════════════════════════════════════════════════════════
#  Garde-fous de vraisemblance du profil intermittent (25/08/2026).
#  Trouvé par la vérification quotidienne sur de vrais comptes : une date
#  anniversaire en 2030, une en 2005 (une date de naissance), une allocation
#  à 13,53 €/jour. La date anniversaire vit autour d'aujourd'hui ; une AJ
#  brute vit entre ~38 et 181,18 € (plafond Unédic avril 2026), on borne
#  large (20 à 200) pour ne jamais rejeter un vrai cas limite.
# ════════════════════════════════════════════════════════════════════════
from datetime import date, timedelta

import pytest
from fastapi import HTTPException

from api import _valider_date_anniversaire, _valider_montant_journalier


def test_une_date_normale_passe():
    _valider_date_anniversaire(date.today() + timedelta(days=180))
    _valider_date_anniversaire(date.today() - timedelta(days=90))


def test_une_date_de_naissance_est_refusee():
    with pytest.raises(HTTPException) as e:
        _valider_date_anniversaire(date(2005, 10, 20))
    assert e.value.status_code == 422
    assert "carte d'identité" in e.value.detail


def test_une_date_de_science_fiction_est_refusee():
    with pytest.raises(HTTPException) as e:
        _valider_date_anniversaire(date(2030, 1, 10))
    assert e.value.status_code == 422


def test_une_date_passee_recente_reste_acceptee():
    # Un renouvellement passé il y a un an et demi : vieux dossier, mais possible.
    _valider_date_anniversaire(date.today() - timedelta(days=540))


def test_les_montants_plausibles_passent():
    for m in (38.0, 44.0, 61.0, 181.18):
        _valider_montant_journalier(m)


def test_les_montants_impossibles_sont_refuses():
    for m in (13.53, 0.0, 500.0):
        with pytest.raises(HTTPException) as e:
            _valider_montant_journalier(m)
        assert e.value.status_code == 422
        assert "notification ARE" in e.value.detail

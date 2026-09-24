# ════════════════════════════════════════════════════════════════════════
#  L'AIDE DOIT SAVOIR RÉPONDRE « COMBIEN ÇA COÛTE ».
#
#  Le 10/08/2026, un inscrit du jour a posé exactement cette question à
#  l'Aide vivante, depuis le cockpit. La carte ne contenait AUCUN tarif :
#  Totor n'avait donc rien à répondre sur la question la plus décisive
#  qu'un prospect puisse poser, et risquait pire encore, inventer un prix.
#
#  Ces tests gravent les montants réels et l'interdiction d'en inventer.
#  Ils échoueront si quelqu'un change un prix ici sans le répercuter, ou
#  l'inverse : c'est le but.
# ════════════════════════════════════════════════════════════════════════
import re

import pytest

from aide_app import CARTE_APP, prompt_aide


def _plat(texte):
    return re.sub(r"\s+", " ", texte)


@pytest.mark.parametrize("statut", ["auto_entrepreneur", "intermittent"])
def test_les_deux_metiers_connaissent_les_tarifs(statut):
    """Un intermittent comme un auto-entrepreneur peut demander le prix."""
    p = _plat(prompt_aide(statut))
    assert "COMBIEN ÇA COÛTE" in p
    for montant in ["4,99 €", "34,99 €", "2,92 €", "24,99 €", "2,08 €"]:
        assert montant in p, f"{montant} absent du prompt {statut}"


def test_le_gratuit_est_presente_comme_durable_pas_comme_un_essai():
    """« Le gratuit expire » serait un mensonge : les quotas ne sont pas une durée."""
    p = _plat(CARTE_APP)
    assert "GRATUITE et le reste" in p
    assert "PAS une période d'essai qui expire" in p


def test_les_sept_jours_d_essai_sont_bien_limites_aux_stores():
    """L'essai gratuit n'existe QUE sur iPhone et Android, et QUE sur le mensuel."""
    p = _plat(CARTE_APP)
    assert "Sur iPhone et Android" in p
    assert "7 jours d'essai gratuit" in p


def test_le_pionnier_porte_ses_deux_garde_fous():
    """À vie ET limité aux 100 premiers : promettre l'un sans l'autre serait faux."""
    p = _plat(CARTE_APP)
    assert "VERROUILLÉ À VIE" in p
    assert "100 premiers" in p
    # L'offre disparaît d'elle-même : Totor ne doit pas la promettre aveuglément.
    assert "disparaît d'elle-même" in p


@pytest.mark.parametrize("statut", ["auto_entrepreneur", "intermittent"])
def test_inventer_un_prix_est_explicitement_interdit(statut):
    p = _plat(prompt_aide(statut))
    assert "NI UN PRIX" in p
    assert "jamais un montant approché ou déduit" in p
    assert "Un prix inventé est une faute" in p


def test_les_tarifs_de_la_carte_collent_a_ceux_du_site():
    """Garde-fou anti-dérive : 34,99 € par an font bien 2,92 € par mois, 24,99 font 2,08.

    Si quelqu'un change un prix sans recalculer l'équivalent mensuel, ce test tombe.
    """
    assert round(34.99 / 12, 2) == 2.92
    assert round(24.99 / 12, 2) == 2.08
    p = _plat(CARTE_APP)
    assert "34,99 € par an, soit 2,92 € par mois" in p
    assert "24,99 € par an, soit 2,08 € par mois" in p


def test_le_tarif_solidaire_n_est_plus_propose():
    """Le prix public EST devenu le prix solidaire : promettre encore une remise
    « sur demande » enverrait les gens réclamer quelque chose qui n'existe plus."""
    p = _plat(CARTE_APP)
    assert "tarif solidaire N'EXISTE PLUS" in p
    assert "Les fins de mois sont dures" not in p

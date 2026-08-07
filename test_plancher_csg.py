# ════════════════════════════════════════════════════════════════════════
#  LE PLANCHER CSG N'EST PAS UNE CONSTANTE : IL SUIT LE SMIC.
#
#  Découvert le 06/08/2026 sur un relevé France Travail réel (cas réel n°2,
#  annexe 8). Le même dossier, sans qu'aucun droit ne change, affichait :
#     · mai 2026  : 61,00 € net/jour (la CSG rabotée jusqu'au plancher) ;
#     · juin 2026 : 61,88 € net/jour (plus aucune CSG prélevée du tout).
#  La seule chose qui avait bougé entre les deux, c'est le SMIC, revalorisé
#  le 1er juin 2026 (1 823,03 → 1 867,02 €). Le plancher, qui vaut le SMIC
#  brut mensuel divisé par 30 arrondi à l'euro, est donc passé de 61 à 62.
#
#  Avant correction, la valeur 62,00 était écrite en dur SANS DATE : toute
#  estimation d'un mois antérieur à juin 2026 utilisait 62 au lieu de 61.
#  Un écart d'un euro par jour, suffisant pour faire douter une testeuse de
#  l'ensemble de nos chiffres. Ces tests interdisent le retour en arrière.
#
#  Aucun appel réseau : on teste la règle, pas une API.
# ════════════════════════════════════════════════════════════════════════
from datetime import date

import pytest

from allocation_engine import calculer_aj
from regles_intermittent import PLANCHER_NET_CSG_HISTORIQUE, plancher_net_csg


# ── La valeur applicable selon la date ──────────────────────────────────

@pytest.mark.parametrize("jour, attendu", [
    ("2026-08-06", 62.00),   # aujourd'hui
    ("2026-06-01", 62.00),   # premier jour du nouveau SMIC
    ("2026-05-31", 61.00),   # la veille : l'ancien plancher s'applique encore
    ("2026-05-15", 61.00),   # le mois de Delphine
    ("2026-04-30", 61.00),   # avril, où le même dossier donnait déjà 61,00 €
    ("2026-01-01", 61.00),   # premier jour du SMIC de janvier
    ("2025-12-31", 61.00),   # avant nos observations : on garde la plus ancienne
])
def test_le_plancher_depend_de_la_date(jour, attendu):
    assert plancher_net_csg(jour) == attendu


def test_le_plancher_accepte_aussi_un_objet_date():
    assert plancher_net_csg(date(2026, 5, 15)) == 61.00
    assert plancher_net_csg(date(2026, 6, 15)) == 62.00


def test_sans_date_on_prend_aujourdhui():
    """Tous les appels existants doivent continuer à donner le plancher du jour."""
    assert plancher_net_csg() == plancher_net_csg(date.today().isoformat())


def test_lhistorique_est_trie_du_plus_recent_au_plus_ancien():
    """La résolution parcourt la liste dans l'ordre : la casser fausserait tout."""
    dates = [e["depuis"] for e in PLANCHER_NET_CSG_HISTORIQUE]
    assert dates == sorted(dates, reverse=True)


def test_chaque_entree_est_sourcee_et_coherente_avec_le_smic():
    """Le plancher vaut le SMIC brut mensuel divisé par 30, arrondi à l'euro."""
    for e in PLANCHER_NET_CSG_HISTORIQUE:
        assert e["source"], f"{e['depuis']} n'est pas sourcé"
        assert round(e["smic_mensuel"] / 30) == e["valeur"], (
            f"{e['depuis']} : {e['smic_mensuel']} / 30 ne donne pas {e['valeur']}")


# ── Le moteur applique bien le bon plancher ─────────────────────────────

def test_le_moteur_change_de_plancher_avec_la_date():
    """Un dossier identique doit donner un net différent avant et après le 01/06/2026.

    On choisit un salaire de référence qui place l'allocation dans la zone où
    la CSG mord, sinon le plancher ne se voit pas.
    """
    avant = calculer_aj("annexe8", sr=36000, nht=600, le="2026-05-15")
    apres = calculer_aj("annexe8", sr=36000, nht=600, le="2026-06-15")

    assert avant["aj_brute"] == apres["aj_brute"], "seul le NET doit bouger"
    assert avant["plancher_net_csg"] == 61.00
    assert apres["plancher_net_csg"] == 62.00
    assert avant["aj_nette"] < apres["aj_nette"], (
        "avec un plancher plus bas, la CSG mord davantage, donc le net est plus bas")


def test_le_net_ne_descend_jamais_sous_le_plancher_du_moment():
    for jour, plancher in [("2026-05-15", 61.00), ("2026-06-15", 62.00)]:
        for sr, nht in [(32000, 600), (36000, 600), (40000, 600), (46000, 600)]:
            r = calculer_aj("annexe10", sr=sr, nht=nht, le=jour)
            if r["retenue_csg_crds"] > 0:
                assert r["aj_nette"] >= plancher - 0.01, (
                    f"{jour} sr={sr} nht={nht} : net {r['aj_nette']} sous le plancher {plancher}")


def test_une_allocation_deja_sous_le_plancher_ne_paie_aucune_csg():
    """C'est ce qui explique juin 2026 : net remonté tout seul, CSG à zéro."""
    r = calculer_aj("annexe10", sr=20000, nht=600, le="2026-06-15")
    apres_retraite = round(r["aj_brute"] - r["retenue_retraite"], 2)
    if apres_retraite <= 62.00:
        assert r["retenue_csg_crds"] == 0.0


def test_sans_date_le_moteur_donne_le_meme_resultat_quavant():
    """Garde-fou de non-régression : la signature a changé, pas le comportement."""
    from datetime import date as _d
    sans = calculer_aj("annexe8", sr=36000, nht=600)
    avec = calculer_aj("annexe8", sr=36000, nht=600, le=_d.today())
    assert sans["aj_nette"] == avec["aj_nette"]

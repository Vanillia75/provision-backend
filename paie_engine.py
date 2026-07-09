# -*- coding: utf-8 -*-
"""La Paie d'Hector — moteur pur du salaire lissé de l'auto-entrepreneur.

Principe : redonner la stabilité psychologique d'un salaire. On regarde les
6 derniers mois civils COMPLETS de « net réel » (encaissé − provision URSSAF)
et on en tire trois montants :

  - prudent    : la moyenne des 3 mois les plus faibles (tenable même en creux) ;
  - recommandé : la médiane des 6 mois (le salaire durable) ;
  - maximum    : ce que le dernier mois a réellement laissé (ou la médiane si
                 le dernier mois est plus faible — on ne recommande jamais de
                 se verser plus que ce que l'activité vient de produire OU ce
                 qu'elle produit durablement).

Les trois montants sont arrondis à la dizaine d'euros (une paie, pas un décompte)
et ordonnés (prudent ≤ recommandé ≤ maximum). Moins de 3 mois avec des
encaissements → pas de paie proposée (historique insuffisant), Totor le dit.

RÈGLES DE MARQUE : c'est une RECOMMANDATION basée sur ce que l'utilisateur a
saisi. C'est lui qui décide et lui qui fait le virement. Jamais d'affirmation.
"""
from statistics import median


def _arrondi_dizaine(x: float) -> int:
    """Arrondi à la dizaine inférieure : une paie se lit en dizaines d'euros."""
    return max(0, int(x // 10) * 10)


def calculer_paie(nets_mensuels: list) -> dict:
    """nets_mensuels : les nets réels des 6 derniers mois civils complets,
    du plus ancien au plus récent (mois sans activité = 0). Renvoie les trois
    montants ou historique_suffisant=False si moins de 3 mois non nuls."""
    nets = [max(0.0, float(n or 0)) for n in (nets_mensuels or [])][-6:]
    nb_mois_actifs = sum(1 for n in nets if n > 0)
    if nb_mois_actifs < 3:
        return {
            "historique_suffisant": False,
            "nb_mois_actifs": nb_mois_actifs,
            "prudent": None, "recommande": None, "maximum": None,
        }

    recommande = median(nets)
    plus_faibles = sorted(nets)[:3]
    prudent = sum(plus_faibles) / len(plus_faibles)
    dernier = nets[-1]
    maximum = max(dernier, recommande)

    # Ordre garanti : prudent ≤ recommandé ≤ maximum.
    prudent = min(prudent, recommande)

    return {
        "historique_suffisant": True,
        "nb_mois_actifs": nb_mois_actifs,
        "prudent": _arrondi_dizaine(prudent),
        "recommande": _arrondi_dizaine(recommande),
        "maximum": _arrondi_dizaine(maximum),
    }

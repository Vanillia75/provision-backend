"""
Projection de trésorerie auto-entrepreneur — « vais-je m'en sortir le mois prochain ? »

Fonction PURE : aucun accès base. On lui passe le solde, le train de vie et les
listes de factures / devis (déjà extraites en amont), elle renvoie deux scénarios
chiffrés + un message de Totor :

  - PLANCHER  (le sûr)      : solde + factures déjà émises − train de vie − charges
  - OPTIMISTE (le probable) : plancher + devis acceptés (pipeline) − charges associées

S'appuie sur tax_engine pour le taux de charges (cotisations URSSAF + CFP +
versement libératoire éventuel), afin de rester aligné avec le calcul du
« disponible » du dashboard. Ne stocke jamais de chiffre en base : tout se
recalcule à la demande à partir des données existantes.
"""

from datetime import date
from dataclasses import dataclass, field
from typing import Optional, List

from tax_engine import AUTO_ENTREPRENEUR_RATES, ACRE_REDUCTION, _last_day_of_month, _add_months


MOIS_FR = [
    "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
]


def _fin_mois_prochain(today: date) -> date:
    """Dernier jour du mois prochain (l'horizon de la projection)."""
    premier_du_mois_prochain = _add_months(date(today.year, today.month, 1), 1)
    return _last_day_of_month(premier_du_mois_prochain)


def _nb_mois_fenetre(today: date, horizon: date) -> int:
    """Nombre de fins de mois (échéances de train de vie) entre aujourd'hui et l'horizon inclus."""
    return (horizon.year - today.year) * 12 + (horizon.month - today.month) + 1


def _taux_global(activite: str, acre: bool, versement_liberatoire: bool) -> float:
    """Taux de charges appliqué au CA encaissé (miroir du calcul du dashboard)."""
    rates = AUTO_ENTREPRENEUR_RATES.get(activite) or AUTO_ENTREPRENEUR_RATES["services"]
    taux = rates["cotisations"] * (ACRE_REDUCTION if acre else 1.0)
    taux += rates["cfp"]
    if versement_liberatoire:
        taux += rates["liberatoire"]
    return taux


@dataclass
class Projection:
    disponible: bool
    horizon: Optional[date] = None
    horizon_label: Optional[str] = None
    solde_actuel: float = 0.0
    plancher: float = 0.0
    optimiste: float = 0.0
    nb_mois: int = 0
    factures_montant: float = 0.0
    factures_count: int = 0
    devis_montant: float = 0.0
    devis_count: int = 0
    train_de_vie: float = 0.0
    charges: float = 0.0
    ton: str = "serein"           # serein | vigilant | alerte
    message: str = ""
    leviers: List[dict] = field(default_factory=list)


def _message_hector(plancher, optimiste, horizon, impayees, devis_in, depenses_mensuelles):
    """Construit le ton, le message et les leviers concrets — sans jamais faire peur."""
    leviers = []
    # Levier 1 : relancer la plus grosse facture impayée (de l'argent qui dort).
    if impayees:
        plus_grosse = max(impayees, key=lambda f: f["montant"])
        num = (plus_grosse.get("numero") or "").strip()
        ref = f" {num}" if num else ""
        leviers.append({
            "type": "facture_impayee",
            "label": f"Relancer ta facture{ref} ({round(plus_grosse['montant'])} €)",
        })
    # Levier 2 : encaisser le plus gros devis accepté (le pipeline à concrétiser).
    if devis_in:
        plus_gros = max(devis_in, key=lambda d: d["montant"])
        leviers.append({
            "type": "devis",
            "label": f"Encaisser ton devis accepté ({round(plus_gros['montant'])} €)",
        })

    mois = MOIS_FR[horizon.month - 1]

    if plancher >= 0:
        ton = "serein"
        message = f"Tu es tranquille pour {mois} : même au strict minimum, tu finis dans le vert. 🐾"
    elif optimiste >= 0:
        ton = "vigilant"
        if devis_in:
            message = (f"Le strict minimum est un peu juste pour {mois}, "
                       f"mais tes devis acceptés te font passer. Encaisse-les et tu es tranquille. 🐾")
        else:
            message = (f"{mois.capitalize()} s'annonce un peu juste. "
                       f"Une rentrée et tu repasses dans le vert — on regarde ça ensemble. 🐾")
    else:
        ton = "alerte"
        message = (f"{mois.capitalize()} s'annonce serré. On ne panique pas : "
                   f"voici ce qu'on peut faire dès maintenant pour repasser dans le vert. 🐾")

    if not depenses_mensuelles:
        message += " (Dis-moi ton train de vie mensuel pour une projection plus fiable.)"

    return ton, message, leviers


def projeter_tresorerie(*, solde, depenses_mensuelles, activite, acre,
                        versement_liberatoire, factures, devis, today=None) -> Projection:
    """
    factures : liste de dicts {montant, statut, date_echeance, date_paiement, numero?}
    devis    : liste de dicts {montant, statut, date_validite}
    """
    if solde is None:
        return Projection(disponible=False)

    today = today or date.today()
    horizon = _fin_mois_prochain(today)
    nb_mois = _nb_mois_fenetre(today, horizon)
    taux = _taux_global(activite, acre, versement_liberatoire)

    # Entrées certaines : factures émises (envoyée/impayée), non encore payées,
    # dont l'échéance tombe avant l'horizon.
    fac_in = [
        f for f in factures
        if f.get("statut") in ("envoyee", "impayee")
        and not f.get("date_paiement")
        and f.get("date_echeance") is not None
        and f["date_echeance"] <= horizon
    ]
    factures_montant = round(sum(f["montant"] for f in fac_in), 2)

    # Entrées probables : devis acceptés dont la validité n'est pas dépassée.
    dev_in = [
        d for d in devis
        if d.get("statut") == "accepte"
        and (d.get("date_validite") is None or d["date_validite"] >= today)
    ]
    devis_montant = round(sum(d["montant"] for d in dev_in), 2)

    train_de_vie = round((depenses_mensuelles or 0) * nb_mois, 2)
    charges_plancher = round(factures_montant * taux, 2)
    charges_optimiste = round((factures_montant + devis_montant) * taux, 2)

    plancher = round(solde + factures_montant - train_de_vie - charges_plancher, 2)
    optimiste = round(solde + factures_montant + devis_montant - train_de_vie - charges_optimiste, 2)

    impayees = [f for f in fac_in if f.get("statut") == "impayee"]
    ton, message, leviers = _message_hector(
        plancher, optimiste, horizon, impayees, dev_in, depenses_mensuelles
    )

    return Projection(
        disponible=True,
        horizon=horizon,
        horizon_label="fin " + MOIS_FR[horizon.month - 1],
        solde_actuel=round(solde, 2),
        plancher=plancher,
        optimiste=optimiste,
        nb_mois=nb_mois,
        factures_montant=factures_montant,
        factures_count=len(fac_in),
        devis_montant=devis_montant,
        devis_count=len(dev_in),
        train_de_vie=train_de_vie,
        charges=charges_plancher,
        ton=ton,
        message=message,
        leviers=leviers,
    )

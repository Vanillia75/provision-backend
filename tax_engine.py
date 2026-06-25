"""
Moteur de calcul des cotisations / impots selon le statut.

═══════════════════════════════════════════════════════════════════════════════
 JUMEAU de fiscalite.js (frontend). Les valeurs de AUTO_ENTREPRENEUR_RATES
 DOIVENT rester strictement alignées avec FISCALITE.regimes côté front : c'est
 la même réglementation, déclinée dans les deux langages. Si tu modifies un taux
 ici, modifie-le AUSSI dans fiscalite.js (et inversement).

 Le test test_coherence_fiscalite.py vérifie automatiquement que les deux fichiers
 concordent — lance-le après tout changement de taux.

 Taux verifies (sources officielles URSSAF, janvier 2026) pour l'auto-entrepreneuriat.
 SARL / SAS : structure prevue, calcul pas encore implemente (statuts "a venir").
═══════════════════════════════════════════════════════════════════════════════
"""

from datetime import date, timedelta
from dataclasses import dataclass, field
from typing import Optional


# Version du référentiel fiscal (doit correspondre à FISCALITE.version côté front).
FISCALITE_VERSION = "2026.1"
FISCALITE_DATE_VALIDITE = "2026-01-01"

# Taux par régime. MIROIR EXACT de FISCALITE.regimes (fiscalite.js).
# - cotisations : cotisations sociales URSSAF (hors CFP)
# - cfp         : contribution formation professionnelle (prélevée en plus)
# - liberatoire : versement libératoire de l'IR (option)
# - plafond     : plafond de CA annuel du régime micro
# - seuil_tva   : seuil de franchise en base de TVA
AUTO_ENTREPRENEUR_RATES = {
    "vente": {
        "cotisations": 0.123,   # 12,3 %
        "cfp": 0.001,           # 0,1 %
        "liberatoire": 0.01,    # 1 %
        "plafond": 203100,
        "seuil_tva": 85000,
    },
    "services": {
        "cotisations": 0.212,   # 21,2 %
        "cfp": 0.002,           # 0,2 %
        "liberatoire": 0.017,   # 1,7 %
        "plafond": 83600,
        "seuil_tva": 37500,
    },
    "bnc": {
        "cotisations": 0.256,   # 25,6 % (hausse 2026 : 24,6 % → 25,6 % au 01/01/2026)
        "cfp": 0.002,           # 0,2 %
        "liberatoire": 0.022,   # 2,2 %
        "plafond": 83600,
        "seuil_tva": 37500,
    },
}

ACRE_REDUCTION = 0.5  # 50% sur les cotisations sociales la 1ere annee
STATUTS_DISPONIBLES = ["auto_entrepreneur"]
STATUTS_A_VENIR = ["sarl", "sas"]


@dataclass
class PeriodWindow:
    start: date
    end: date
    label: str
    date_limite_declaration: date
    jours_restants: int


@dataclass
class TaxEstimate:
    statut: str
    activite: str
    periode_courante: PeriodWindow
    periode_precedente: PeriodWindow
    ca_periode_courante: float
    ca_periode_precedente: float
    taux_global_pct: float
    montant_a_provisionner: float
    detail: dict
    ca_annuel: float
    plafond: float
    pourcentage_plafond: float


def _last_day_of_month(d: date) -> date:
    if d.month == 12:
        return date(d.year, 12, 31)
    return date(d.year, d.month + 1, 1) - timedelta(days=1)


def _add_months(d: date, n: int) -> date:
    month_index = d.month - 1 + n
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    return date(year, month, 1)


def _period_bounds(periodicite: str, reference_day: date) -> tuple[date, date, str]:
    if periodicite == "mensuelle":
        start = date(reference_day.year, reference_day.month, 1)
        end = _last_day_of_month(reference_day)
        mois_fr = [
            "janvier", "fevrier", "mars", "avril", "mai", "juin",
            "juillet", "aout", "septembre", "octobre", "novembre", "decembre",
        ]
        label = f"{mois_fr[start.month - 1]} {start.year}"
    elif periodicite == "trimestrielle":
        quarter_start_month = ((reference_day.month - 1) // 3) * 3 + 1
        start = date(reference_day.year, quarter_start_month, 1)
        end = _last_day_of_month(_add_months(start, 2))
        quarter_number = (quarter_start_month - 1) // 3 + 1
        label = f"T{quarter_number} {start.year}"
    else:
        raise ValueError(f"Periodicite inconnue : {periodicite}")
    return start, end, label


def _build_period_window(periodicite: str, reference_day: date, today: date) -> PeriodWindow:
    start, end, label = _period_bounds(periodicite, reference_day)
    deadline = _last_day_of_month(_add_months(date(end.year, end.month, 1), 1))
    jours_restants = (deadline - today).days
    return PeriodWindow(
        start=start, end=end, label=label,
        date_limite_declaration=deadline, jours_restants=jours_restants,
    )


def estimate_auto_entrepreneur(
    activite: str,
    periodicite: str,
    acre: bool,
    versement_liberatoire: bool,
    incomes: list,  # liste de tuples (date, montant)
    today: Optional[date] = None,
) -> TaxEstimate:
    if activite not in AUTO_ENTREPRENEUR_RATES:
        raise ValueError(f"Activite inconnue : {activite}")

    today = today or date.today()
    rates = AUTO_ENTREPRENEUR_RATES[activite]

    periode_courante = _build_period_window(periodicite, today, today)

    # Periode precedente : un jour avant le debut de la periode courante
    jour_precedent = periode_courante.start - timedelta(days=1)
    periode_precedente = _build_period_window(periodicite, jour_precedent, today)

    def ca_sur(window: PeriodWindow) -> float:
        return sum(
            amount for (d, amount) in incomes
            if window.start <= d <= window.end
        )

    ca_courante = ca_sur(periode_courante)
    ca_precedente = ca_sur(periode_precedente)

    taux_cotisations = rates["cotisations"] * (ACRE_REDUCTION if acre else 1.0)
    taux_cfp = rates["cfp"]
    taux_liberatoire = rates["liberatoire"] if versement_liberatoire else 0.0
    taux_global = taux_cotisations + taux_cfp + taux_liberatoire

    montant_cotisations = round(ca_courante * taux_cotisations, 2)
    montant_cfp = round(ca_courante * taux_cfp, 2)
    montant_liberatoire = round(ca_courante * taux_liberatoire, 2)
    montant_total = round(montant_cotisations + montant_cfp + montant_liberatoire, 2)

    ca_annuel = sum(amount for (d, amount) in incomes if d.year == today.year)
    pourcentage_plafond = round((ca_annuel / rates["plafond"]) * 100, 1) if rates["plafond"] else 0.0

    return TaxEstimate(
        statut="auto_entrepreneur",
        activite=activite,
        periode_courante=periode_courante,
        periode_precedente=periode_precedente,
        ca_periode_courante=round(ca_courante, 2),
        ca_periode_precedente=round(ca_precedente, 2),
        taux_global_pct=round(taux_global * 100, 2),
        montant_a_provisionner=montant_total,
        detail={
            "cotisations_sociales": montant_cotisations,
            "formation_professionnelle": montant_cfp,
            "versement_liberatoire": montant_liberatoire,
            "acre_applique": acre,
        },
        ca_annuel=round(ca_annuel, 2),
        plafond=rates["plafond"],
        pourcentage_plafond=pourcentage_plafond,
    )


def estimate(statut: str, **kwargs) -> TaxEstimate:
    if statut == "auto_entrepreneur":
        return estimate_auto_entrepreneur(**kwargs)
    elif statut in STATUTS_A_VENIR:
        raise NotImplementedError(f"Le statut '{statut}' n'est pas encore disponible.")
    else:
        raise ValueError(f"Statut inconnu : {statut}")

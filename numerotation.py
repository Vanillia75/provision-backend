"""
Numérotation des factures / devis — générateur robuste.

PUR (aucune DB) → testable directement. Règles :
- Prochain numéro = max(plancher de départ, plus grand compteur existant + 1).
- Basé sur le MAX, pas un comptage de lignes → supprimer une pièce ne fait jamais
  reculer le compteur (corrige le bug de doublon à la suppression).
- Parse prudemment `{prefix}-{year}-NNN` ; ignore tout numéro non conforme (jamais de crash).
- `floor_numero` (ex. "F-2026-042", reprise d'une séquence) = PLANCHER : on ne descend
  jamais en dessous, mais le max existant l'emporte s'il est plus haut.
- Garde-fou anti-doublon : on avance tant que le numéro est déjà pris.
"""

import re


def compute_next_numero(prefix: str, year: int, existing_numeros, floor_numero: str = None) -> str:
    pat = re.compile(rf"^{prefix}-{year}-(\d+)$")
    existing = set(existing_numeros or [])
    max_existing = 0
    for n in existing:
        m = pat.match(n or "")
        if m:
            max_existing = max(max_existing, int(m.group(1)))
    floor = 0
    if floor_numero:
        m = pat.match(floor_numero)
        if m:
            floor = int(m.group(1))
    nxt = max(floor, max_existing + 1)
    numero = f"{prefix}-{year}-{nxt:03d}"
    while numero in existing:
        nxt += 1
        numero = f"{prefix}-{year}-{nxt:03d}"
    return numero


def normalize_numero_depart(raw, year: int):
    """
    Normalise une saisie de plancher en "F-{année}-NNN" (compteur sur 3 chiffres).
    Accepte "42", "042", "F-2026-042". Retourne None si vide. Lève ValueError si invalide.
    """
    raw = (raw or "").strip()
    if not raw:
        return None
    m = re.match(r"^(?:F-(\d{4})-)?0*(\d+)$", raw)
    if not m:
        raise ValueError("Numéro de départ invalide (ex. 42 ou F-2026-042)")
    annee = int(m.group(1)) if m.group(1) else year
    compteur = int(m.group(2))
    if compteur < 1:
        raise ValueError("Le numéro de départ doit être supérieur à 0")
    return f"F-{annee}-{compteur:03d}"

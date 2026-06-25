"""
test_coherence_fiscalite.py — Filet de sécurité anti-divergence.

Vérifie que les taux fiscaux du backend (tax_engine.AUTO_ENTREPRENEUR_RATES)
sont STRICTEMENT identiques à ceux du frontend (fiscalite.js / FISCALITE.regimes).

Les deux fichiers portent la même réglementation dans deux langages : ils doivent
toujours concorder. Ce test lit les valeurs du JS (par expression régulière, sans
exécuter de JavaScript) et les compare à celles du Python.

Usage :
    python3 test_coherence_fiscalite.py
Sortie : code 0 si tout concorde, code 1 (et liste des écarts) sinon.

À lancer après TOUT changement de taux, d'un côté comme de l'autre.
"""

import re
import sys
import os

# Le backend est la référence Python. On l'importe directement.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tax_engine import AUTO_ENTREPRENEUR_RATES, FISCALITE_VERSION

# Correspondance des noms de clés entre le front (JS) et le back (Python).
# back -> front
CLES = {
    "cotisations": "tauxCotisations",
    "cfp": None,            # le front met la CFP dans un bloc séparé (cf. ci-dessous)
    "liberatoire": "tauxVersementLiberatoire",
    "plafond": "plafondCA",
    "seuil_tva": "seuilTVA",
}

REGIMES = ["vente", "services", "bnc"]


def _lire_fiscalite_js(chemin="fiscalite.js"):
    """
    Extrait les valeurs numériques de FISCALITE.regimes et FISCALITE.cfp depuis
    le fichier JS, par expression régulière. Renvoie un dict identique en forme
    à AUTO_ENTREPRENEUR_RATES.
    """
    with open(chemin, encoding="utf-8") as f:
        src = f.read()

    out = {}

    # Pour chaque régime, on isole son bloc { ... } puis on lit les champs.
    for regime in REGIMES:
        # Bloc du régime : depuis "regime: {" jusqu'à l'accolade fermante du bloc.
        m = re.search(regime + r"\s*:\s*\{(.*?)\n\s{4}\}", src, re.DOTALL)
        if not m:
            raise AssertionError(f"Régime '{regime}' introuvable dans {chemin}")
        bloc = m.group(1)

        def champ(nom):
            mm = re.search(nom + r"\s*:\s*([0-9.]+)", bloc)
            if not mm:
                raise AssertionError(f"Champ '{nom}' introuvable pour '{regime}' dans {chemin}")
            return float(mm.group(1))

        out[regime] = {
            "cotisations": champ("tauxCotisations"),
            "liberatoire": champ("tauxVersementLiberatoire"),
            "plafond": champ("plafondCA"),
            "seuil_tva": champ("seuilTVA"),
        }

    # CFP : bloc séparé dans le front (FISCALITE.cfp), par régime.
    mcfp = re.search(r"cfp\s*:\s*\{(.*?)\}", src, re.DOTALL)
    if not mcfp:
        raise AssertionError(f"Bloc 'cfp' introuvable dans {chemin}")
    bloc_cfp = mcfp.group(1)
    for regime in REGIMES:
        mm = re.search(regime + r"\s*:\s*([0-9.]+)", bloc_cfp)
        if not mm:
            raise AssertionError(f"CFP du régime '{regime}' introuvable dans {chemin}")
        out[regime]["cfp"] = float(mm.group(1))

    return out


def comparer():
    front = _lire_fiscalite_js()
    ecarts = []

    for regime in REGIMES:
        back_r = AUTO_ENTREPRENEUR_RATES[regime]
        front_r = front[regime]
        for cle in ("cotisations", "cfp", "liberatoire", "plafond", "seuil_tva"):
            vb = back_r[cle]
            vf = front_r[cle]
            # Comparaison robuste pour les flottants.
            if abs(float(vb) - float(vf)) > 1e-9:
                ecarts.append(f"  [{regime}] {cle} : back={vb} ≠ front={vf}")

    return ecarts


if __name__ == "__main__":
    print(f"Cohérence fiscalité (version backend {FISCALITE_VERSION})…")
    try:
        ecarts = comparer()
    except AssertionError as e:
        print(f"ERREUR de lecture : {e}")
        sys.exit(1)

    if ecarts:
        print("✗ DIVERGENCE détectée entre tax_engine.py et fiscalite.js :")
        print("\n".join(ecarts))
        print("\n→ Aligne les deux fichiers avant de déployer.")
        sys.exit(1)

    print("✓ tax_engine.py et fiscalite.js concordent parfaitement.")
    sys.exit(0)

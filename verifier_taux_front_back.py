# -*- coding: utf-8 -*-
"""Vérifie que les chiffres réglementaires du FRONT et du BACK concordent.

Le back (tax_engine.py, AUTO_ENTREPRENEUR_RATES) et le front (fiscalite.js) sont
deux JUMEAUX maintenus à la main (deux dépôts séparés, deux langages). Ce script
compare les deux, ligne par ligne, et affiche CLAIREMENT tout écart. À lancer
après chaque mise à jour de taux (rituel de janvier).

Usage :
    python verifier_taux_front_back.py
    python verifier_taux_front_back.py "chemin/vers/fiscalite.js"   (si le front est ailleurs)

Sortie : un tableau par régime, « OK » ou « DIVERGE » avec les deux valeurs.
Code de sortie 0 si tout concorde, 1 s'il y a au moins un écart.
"""

import os
import re
import sys

from tax_engine import AUTO_ENTREPRENEUR_RATES  # source BACK

# Correspondance des noms de champ : (nom lisible, clé FRONT dans fiscalite.js, clé BACK)
CHAMPS = [
    ("Cotisations sociales", "tauxCotisations", "cotisations"),
    ("Versement liberatoire", "tauxVersementLiberatoire", "liberatoire"),
    ("Plafond de CA", "plafondCA", "plafond"),
    ("Seuil de TVA", "seuilTVA", "seuil_tva"),
    ("Seuil de TVA majore", "seuilTVAMajore", "seuil_tva_majore"),
]
REGIMES = ["vente", "services", "bnc"]


def chemin_fiscalite() -> str:
    if len(sys.argv) > 1:
        return sys.argv[1]
    env = os.environ.get("FISCALITE_JS_PATH")
    if env:
        return env
    # Par défaut : le dépôt front est à côté du dépôt back.
    ici = os.path.dirname(os.path.abspath(__file__))
    return os.path.normpath(os.path.join(ici, "..", "provision-frontend", "src", "fiscalite.js"))


def lire_valeurs_front(path: str) -> dict:
    """Extrait {regime: {cleFront: float}} depuis fiscalite.js (parse par regex, aucune exécution JS)."""
    with open(path, encoding="utf-8") as f:
        texte = f.read()
    resultat = {}
    for regime in REGIMES:
        # Bloc du régime : `<regime>: { ... }` (les blocs de régime n'ont pas d'accolade imbriquée).
        m = re.search(regime + r"\s*:\s*\{(.*?)\}", texte, re.DOTALL)
        if not m:
            raise SystemExit(f"Régime '{regime}' introuvable dans {path}")
        bloc = m.group(1)
        valeurs = {}
        for _, cle_front, _ in CHAMPS:
            mm = re.search(cle_front + r"\s*:\s*([0-9.]+)", bloc)
            if mm:
                valeurs[cle_front] = float(mm.group(1))
        resultat[regime] = valeurs
    return resultat


def main():
    path = chemin_fiscalite()
    if not os.path.exists(path):
        raise SystemExit(f"Fichier front introuvable : {path}\n"
                         "Passe le chemin en argument : python verifier_taux_front_back.py <chemin fiscalite.js>")
    front = lire_valeurs_front(path)
    print(f"Comparaison FRONT ({os.path.basename(path)}) vs BACK (tax_engine.py)\n")

    ecarts = 0
    for regime in REGIMES:
        print(f"-- {regime.upper()} --")
        for lisible, cle_front, cle_back in CHAMPS:
            vf = front.get(regime, {}).get(cle_front)
            vb = AUTO_ENTREPRENEUR_RATES.get(regime, {}).get(cle_back)
            if vf is None or vb is None:
                print(f"   [?]        {lisible:<24} manquant (front={vf}, back={vb})")
                ecarts += 1
            elif abs(float(vf) - float(vb)) < 1e-9:
                print(f"   [OK]       {lisible:<24} {vf}")
            else:
                print(f"   [DIVERGE]  {lisible:<24} FRONT={vf}  !=  BACK={vb}")
                ecarts += 1
        print()

    if ecarts == 0:
        print("==> Tout concorde : le front et le back sont synchronises.")
        sys.exit(0)
    else:
        print(f"==> {ecarts} ecart(s) detecte(s) ci-dessus. Corrige fiscalite.js ET tax_engine.py pour qu'ils s'accordent.")
        sys.exit(1)


if __name__ == "__main__":
    main()

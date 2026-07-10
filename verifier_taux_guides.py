# -*- coding: utf-8 -*-
"""Verifie que les chiffres AFFICHES DANS LES GUIDES SEO concordent avec fiscalite.js.

Les guides HTML (provision-frontend/public/guides/*.html) contiennent des taux en
dur (cotisations, abattements). Si on met a jour fiscalite.js sans toucher les
guides, ils derivent en silence. Ce script attrape cette derive : il lit les vraies
valeurs dans fiscalite.js, les met au format francais ("12,3 %", "71 %"), et verifie
qu'elles apparaissent bien dans les guides qui les citent.

A lancer dans le rituel de janvier, apres avoir mis a jour fiscalite.js.

Usage :
    python verifier_taux_guides.py
    python verifier_taux_guides.py "chemin/vers/fiscalite.js"

Sortie : OK / MANQUE par guide et par valeur. Code de sortie 0 si tout concorde,
1 s'il manque au moins une valeur (= un guide a derive).
"""

import os
import re
import sys

REGIMES = ["vente", "services", "bnc"]


def chemin_repo_front():
    ici = os.path.dirname(os.path.abspath(__file__))
    return os.path.normpath(os.path.join(ici, "..", "provision-frontend"))


def chemin_fiscalite():
    if len(sys.argv) > 1:
        return sys.argv[1]
    return os.path.join(chemin_repo_front(), "src", "fiscalite.js")


def lire_regimes(path):
    """Extrait {regime: {tauxCotisations, abattementFiscal}} depuis fiscalite.js."""
    with open(path, encoding="utf-8") as f:
        texte = f.read()
    res = {}
    for regime in REGIMES:
        m = re.search(regime + r"\s*:\s*\{(.*?)\n    \}", texte, re.DOTALL)
        bloc = m.group(1) if m else texte
        def val(cle):
            mm = re.search(cle + r"\s*:\s*([0-9.]+)", bloc)
            return float(mm.group(1)) if mm else None
        res[regime] = {
            "cotisations": val("tauxCotisations"),
            "abattement": val("abattementFiscal"),
        }
    return res


def pct_1(dec):
    """0.123 -> '12,3 %' (une decimale, virgule, espace avant %)."""
    return f"{dec * 100:.1f}".replace(".", ",") + " %"


def pct_0(dec):
    """0.71 -> '71 %' (entier)."""
    return f"{dec * 100:.0f} %"


def main():
    fpath = chemin_fiscalite()
    if not os.path.exists(fpath):
        raise SystemExit(f"fiscalite.js introuvable : {fpath}")
    reg = lire_regimes(fpath)
    guides_dir = os.path.join(chemin_repo_front(), "public", "guides")

    coti = {r: pct_1(reg[r]["cotisations"]) for r in REGIMES}
    abat = {r: pct_0(reg[r]["abattement"]) for r in REGIMES}

    # Quel guide doit contenir quelles valeurs (format francais exact).
    ATTENDU = {
        "bic-bnc-choisir-activite-auto-entrepreneur.html": [
            ("cotisations vente", coti["vente"]),
            ("cotisations services", coti["services"]),
            ("cotisations bnc", coti["bnc"]),
            ("abattement vente", abat["vente"]),
            ("abattement services", abat["services"]),
            ("abattement bnc", abat["bnc"]),
        ],
        "declaration-revenus-auto-entrepreneur-impot.html": [
            ("abattement vente", abat["vente"]),
            ("abattement services", abat["services"]),
            ("abattement bnc", abat["bnc"]),
        ],
    }

    print(f"Verification des guides vs fiscalite.js ({os.path.basename(fpath)})\n")
    manques = 0
    for guide, attendus in ATTENDU.items():
        chemin = os.path.join(guides_dir, guide)
        print(f"-- {guide} --")
        if not os.path.exists(chemin):
            print("   [?]       fichier introuvable")
            manques += 1
            print()
            continue
        html = open(chemin, encoding="utf-8").read()
        for label, valeur in attendus:
            if valeur in html:
                print(f"   [OK]      {label:<22} {valeur}")
            else:
                print(f"   [MANQUE]  {label:<22} attendu '{valeur}' (le guide a derive)")
                manques += 1
        print()

    if manques == 0:
        print("==> Tout concorde : les guides affichent les memes chiffres que fiscalite.js.")
        sys.exit(0)
    print(f"==> {manques} valeur(s) manquante(s). Mets a jour le(s) guide(s) pour coller a fiscalite.js.")
    sys.exit(1)


if __name__ == "__main__":
    main()

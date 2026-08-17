# -*- coding: utf-8 -*-
"""
Lecture d'un BULLETIN DE PAIE, pour en déduire le rapport net / brut.

Pourquoi ce fichier existe (15/08/2026, demande de Lucile) :
elle veut prévoir son mois, donc savoir ce qui va ARRIVER sur son compte. TOTOR
connaît le net de son allocation France Travail (backtesté au centime) mais
seulement le BRUT de ses cachets. Additionner un net et un brut donnerait un
total faux, et elle ferait ses comptes dessus.

On pourrait lui demander son ratio. On préfère l'APPRENDRE : elle dépose déjà
ses bulletins dans son classeur, et un bulletin porte le brut et le net à payer
écrits noir sur blanc. Zéro question de plus.

RÈGLE : on ne renvoie un ratio QUE s'il est plausible et cohérent. Dans le doute
on ne renvoie rien, et l'écran continue de n'afficher que le brut. Mieux vaut
pas de chiffre qu'un chiffre inventé.
"""
import os

from aem_extractor import (ANTHROPIC_API_KEY, MODEL, _build_source_blocks,
                           _clean_json)

# Bornes de plausibilité d'un net/brut de salaire du spectacle. En dessous ou
# au-dessus, on considère qu'on a mal lu et on ne renvoie rien.
#  · un net à moins de 60 % du brut n'existe pas dans le régime général ;
#  · un net à plus de 92 % non plus (les cotisations salariales tournent autour
#    de 22 à 25 % pour un cachet, souvent plus avec les caisses du spectacle).
RATIO_MIN = 0.60
RATIO_MAX = 0.92

PROMPT_BULLETIN = """Tu lis un BULLETIN DE PAIE français (fiche de paie), souvent celui d'un intermittent du spectacle.

Trouve DEUX montants, ceux de la PÉRIODE du bulletin :
1. Le SALAIRE BRUT total (souvent « Salaire brut », « Total brut », « Brut »).
2. Le NET À PAYER (souvent « Net à payer », « Net à payer avant impôt sur le revenu », « NET A PAYER AU SALARIE »).

⚠️ PIÈGES À ÉVITER :
- Ne prends PAS le « net imposable » ni le « net social » : ce sont d'autres montants. Il faut le NET À PAYER, celui qui est versé.
- Si le bulletin affiche un net à payer AVANT et APRÈS impôt à la source, prends celui AVANT prélèvement à la source (l'impôt dépend de la personne, pas du salaire).
- Ne prends pas un cumul annuel : il faut les montants DE LA PÉRIODE du bulletin.
- N'additionne rien toi-même, ne calcule rien : recopie ce qui est écrit.

Réponds STRICTEMENT en JSON, sans texte autour, sans Markdown :
{"brut": nombre ou null, "net_a_payer": nombre ou null, "periode": "YYYY-MM" ou null, "est_un_bulletin": true ou false}

Règles :
- "est_un_bulletin" : false si ce document n'est pas une fiche de paie (attestation, contrat, courrier…). Dans ce cas mets tout le reste à null.
- Si un montant est absent ou illisible, mets null. Ne devine JAMAIS.
- Les nombres sans symbole ni espace : 1234.56
- Réponds en JSON pur, rien d'autre."""


def _nombre(v):
    if v is None:
        return None
    try:
        f = float(str(v).replace(" ", "").replace(" ", "").replace(",", "."))
    except (TypeError, ValueError):
        return None
    if f != f or f <= 0 or f > 1_000_000:
        return None
    return round(f, 2)


def lire_bulletin(file_path: str) -> dict:
    """Renvoie {brut, net_a_payer, periode, ratio} — ratio à None si pas sûr.

    Ne lève pas : un bulletin illisible n'est pas une erreur pour l'utilisateur,
    il a juste rangé un document. On renvoie simplement un ratio vide.
    """
    vide = {"brut": None, "net_a_payer": None, "periode": None, "ratio": None}
    if not ANTHROPIC_API_KEY:
        return vide
    try:
        import json

        import requests
        blocks = _build_source_blocks(file_path)
        if not blocks:
            return vide
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json={"model": MODEL, "max_tokens": 300,
                  "messages": [{"role": "user",
                                "content": blocks + [{"type": "text", "text": PROMPT_BULLETIN}]}]},
            timeout=180,
        )
        if r.status_code != 200:
            return vide
        parts = [b.get("text", "") for b in r.json().get("content", []) if b.get("type") == "text"]
        data = json.loads(_clean_json("".join(parts)))
    except Exception:
        return vide

    if not isinstance(data, dict) or data.get("est_un_bulletin") is False:
        return vide

    brut = _nombre(data.get("brut"))
    net = _nombre(data.get("net_a_payer"))
    periode = data.get("periode") if isinstance(data.get("periode"), str) else None

    ratio = None
    if brut and net:
        r_ = net / brut
        # On n'accepte que le plausible. Un ratio hors bornes veut dire qu'on a
        # confondu deux lignes du bulletin : on préfère ne rien retenir.
        if RATIO_MIN <= r_ <= RATIO_MAX:
            ratio = round(r_, 4)

    return {"brut": brut, "net_a_payer": net, "periode": periode, "ratio": ratio}

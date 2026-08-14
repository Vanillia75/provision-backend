# -*- coding: utf-8 -*-
"""
Lecture du RELEVÉ DE SITUATION France Travail d'un intermittent.

Décision de Camille du 14/08/2026 : un relevé scanné doit permettre de VÉRIFIER
le versement (« France Travail t'a payé juste » / « il manque X €, voici
pourquoi »), nourrir la carte « Ton mois », et se croiser avec les AEM.

Ce module ne fait QUE la lecture : il transforme le document en chiffres, que
l'utilisateur VALIDE ensuite à l'écran avant tout enregistrement (même règle
que les AEM : Totor propose, la personne confirme).

⚠️ RÈGLES DE PRUDENCE, héritées du reste du moteur :
  · on ne devine JAMAIS un montant : illisible = null ;
  · les périodes en double (un relevé montre souvent « Situation au X » puis
    « Situation au Y » pour les mêmes mois) sont dédoublonnées en gardant la
    version la plus récente, c'est-à-dire la DERNIÈRE du document ;
  · aucune donnée personnelle n'est extraite : ni nom, ni NIR, ni adresse.
    On ne lit que des périodes, des jours et des montants.
"""

import json
import os
from datetime import datetime

from aem_extractor import (ANTHROPIC_API_KEY, MODEL, _RETRY_PAUSES,
                           _build_source_blocks, _clean_json)

PROMPT_RELEVE = """Tu lis un RELEVÉ DE SITUATION France Travail d'un intermittent du spectacle français (titre « RELEVE DE SITUATION DU … AU … », tableaux « Allocations déjà versées » / « Allocations dues », lignes « Allocation d'Aide au Retour à l'Emploi », « DECOMPTE GENERAL », « REGLEMENT DU … »).

Extrais les PÉRIODES indemnisées et les RÈGLEMENTS (virements). Réponds STRICTEMENT en JSON, sans aucun texte autour, sans balises Markdown :

{
  "type_document": "releve_situation" ou "autre",
  "periodes": [
    {
      "debut": "YYYY-MM-DD",
      "fin": "YYYY-MM-DD",
      "nature": "regularisation", "paiement_provisoire", "paiement_provisoire_regularise" ou "paiement",
      "aj_dues": nombre d'allocations journalières DUES (colonne « Allocations dues ») ou null,
      "brut_du": montant BRUT dû en euros ou null,
      "net_du": montant NET dû en euros ou null,
      "aj_deja_versees": nombre d'allocations journalières déjà versées (colonne de gauche) ou null,
      "net_deja_verse": montant net déjà versé ou null,
      "jours_travail": nombre de « Jours non indemnisés : travail » ou null,
      "jours_franchise_cp": nombre de « Jours non indemnisés : franchise CP » ou null,
      "jours_franchise_salaires": nombre de jours de franchise salaires ou null
    }
  ],
  "reglements": [
    { "date": "YYYY-MM-DD", "montant": montant réglé en euros }
  ]
}

Règles importantes :
- Si le document N'EST PAS un relevé de situation France Travail, renvoie {"type_document": "autre"} et rien d'autre.
- Un relevé montre souvent PLUSIEURS « Situation au … » successives qui répètent les MÊMES périodes avec des chiffres mis à jour : pour chaque période (même début et même fin), ne garde que la version la PLUS RÉCENTE, c'est-à-dire la DERNIÈRE du document.
- « aj_dues » : le NOMBRE d'allocations journalières de la colonne « Allocations dues » (à droite). C'est un nombre de jours, pas un montant.
- Quand une période est marquée « Paiement provisoire régularisé », les colonnes s'appellent « Pmt provisoire à déduire » (gauche) et « Allocations dues » (droite) : mets la droite dans aj_dues/net_du et la gauche dans aj_deja_versees/net_deja_verse.
- « reglements » : chaque bloc « REGLEMENT DU date » avec son montant (« Règlement de X euros par virement »). Un relevé peut en contenir plusieurs.
- Les montants sont en euros, format nombre (1237.60), sans symbole ni espace.
- Si une information est absente ou illisible, mets null. Ne devine JAMAIS.
- N'extrais AUCUNE donnée personnelle : ni nom, ni numéro, ni adresse.
- Réponds en JSON pur, rien d'autre."""


def _date_iso(v):
    if not v:
        return None
    s = str(v).strip()[:10]
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _nombre(v, maxi):
    """Un nombre plausible, ou None. Jamais d'exception, jamais de valeur folle."""
    if v is None:
        return None
    try:
        f = float(str(v).replace(",", ".").replace(" ", "").replace(" ", ""))
    except (TypeError, ValueError):
        return None
    if f != f or f < 0 or f > maxi:
        return None
    return round(f, 2)


def _normaliser_releve(data: dict, filename: str) -> dict:
    periodes = []
    for p in (data.get("periodes") or []):
        if not isinstance(p, dict):
            continue
        debut = _date_iso(p.get("debut"))
        fin = _date_iso(p.get("fin"))
        if not debut and not fin:
            continue
        aj_dues = _nombre(p.get("aj_dues"), 31)
        net_du = _nombre(p.get("net_du"), 20000)
        entree = {
            "debut": debut,
            "fin": fin,
            "nature": str(p.get("nature") or "paiement")[:40],
            "aj_dues": aj_dues,
            "brut_du": _nombre(p.get("brut_du"), 20000),
            "net_du": net_du,
            "aj_deja_versees": _nombre(p.get("aj_deja_versees"), 31),
            "net_deja_verse": _nombre(p.get("net_deja_verse"), 20000),
            "jours_travail": _nombre(p.get("jours_travail"), 31),
            "jours_franchise_cp": _nombre(p.get("jours_franchise_cp"), 31),
            "jours_franchise_salaires": _nombre(p.get("jours_franchise_salaires"), 31),
            # Le taux net par jour, quand il se déduit du document lui-même.
            # C'est LE chiffre qui permet de comparer avec l'AJ de TOTOR.
            "taux_net_jour": round(net_du / aj_dues, 2) if (net_du and aj_dues) else None,
        }
        periodes.append(entree)

    # Dédoublonnage par période : la DERNIÈRE occurrence gagne (situation la plus récente).
    par_periode = {}
    for p in periodes:
        par_periode[(p["debut"], p["fin"])] = p
    periodes = sorted(par_periode.values(), key=lambda p: (p["debut"] or "", p["fin"] or ""))

    reglements = []
    for r in (data.get("reglements") or []):
        if not isinstance(r, dict):
            continue
        d = _date_iso(r.get("date"))
        m = _nombre(r.get("montant"), 50000)
        if d and m:
            reglements.append({"date": d, "montant": m})
    # Même règlement cité deux fois (répété par « Situation au ») : une seule fois.
    vus, uniques = set(), []
    for r in sorted(reglements, key=lambda x: x["date"]):
        cle = (r["date"], r["montant"])
        if cle in vus:
            continue
        vus.add(cle)
        uniques.append(r)

    return {"periodes": periodes, "reglements": uniques, "filename": filename}


def extract_releve_data(file_path: str) -> dict:
    """Lit un relevé de situation → périodes + règlements, à VALIDER à l'écran."""
    if not ANTHROPIC_API_KEY:
        raise RuntimeError("Lecture indisponible : clé API non configurée.")

    import time
    import requests

    blocks = _build_source_blocks(file_path)
    derniere_erreur = None
    for tentative in range(len(_RETRY_PAUSES) + 1):
        if tentative > 0:
            time.sleep(_RETRY_PAUSES[tentative - 1])
        try:
            resp = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": ANTHROPIC_API_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": MODEL,
                    "max_tokens": 3000,
                    "messages": [{"role": "user",
                                  "content": blocks + [{"type": "text", "text": PROMPT_RELEVE}]}],
                },
                timeout=90,
            )
        except requests.RequestException:
            derniere_erreur = RuntimeError("Lecture impossible (connexion). Réessaie dans un instant.")
            continue
        if resp.status_code != 200:
            derniere_erreur = RuntimeError(f"Lecture impossible (code {resp.status_code}).")
            continue
        body = resp.json()
        parts = [b.get("text", "") for b in body.get("content", []) if b.get("type") == "text"]
        try:
            data = json.loads(_clean_json("".join(parts)))
        except json.JSONDecodeError:
            derniere_erreur = RuntimeError("Je n'ai pas réussi à lire ce relevé. Essaie un scan plus net.")
            continue
        if not isinstance(data, dict):
            derniere_erreur = RuntimeError("Lecture impossible : format inattendu.")
            continue

        if data.get("type_document") == "autre":
            raise RuntimeError(
                "Ça ne ressemble pas à un relevé de situation France Travail. "
                "Si c'est une attestation employeur, dépose-la dans « Mes AEM »."
            )
        resultat = _normaliser_releve(data, os.path.basename(file_path))
        if not resultat["periodes"] and not resultat["reglements"]:
            derniere_erreur = RuntimeError(
                "Je n'ai trouvé ni période ni règlement sur ce document. Essaie un scan plus net."
            )
            continue
        return resultat

    raise derniere_erreur

"""
Extraction des données d'une AEM (Attestation Employeur Mensuelle) du spectacle,
via Claude Vision. Beaucoup plus fiable que le regex pour ces documents structurés
et variables d'un employeur à l'autre.

Retourne un dict :
    {
      "employeur":      str | None,   # raison sociale
      "siret":          str | None,
      "date":           "YYYY-MM-DD" | None,  # date de fin de période / dernier jour travaillé
      "type_activite":  "cachet_isole" | "cachet_groupe" | "heures",
      "nombre":         float,         # nb de cachets OU nb d'heures selon type
      "salaire_brut":   float | None,
      "filename":       str,
    }
"""

import os
import json
import base64
import mimetypes
from datetime import datetime

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()  # .strip() : cf api.py (Railway ajoute un \n en fin de valeur)
MODEL = "claude-sonnet-4-6"

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}

# Instruction donnée à Claude. On lui demande UNIQUEMENT du JSON, rien d'autre,
# pour pouvoir le parser directement.
PROMPT = """Tu lis un document qui contient UNE OU PLUSIEURS AEM (Attestation Employeur Mensuelle) d'un intermittent du spectacle français. Un même document (souvent un PDF de plusieurs pages) regroupe fréquemment PLUSIEURS attestations, une par contrat.
Le document peut aussi être une attestation GUSO (Guichet Unique du Spectacle Occasionnel — l'équivalent de l'AEM pour les employeurs occasionnels, fréquent chez les musiciens) : traite-la exactement comme une AEM et extrais les mêmes champs.

Repère CHAQUE attestation distincte (chaque numéro d'attestation différent, chaque période de travail différente = une AEM séparée) et extrais-les TOUTES.

Réponds STRICTEMENT en JSON, sans aucun texte autour, sans balises Markdown. Le format est une LISTE d'objets, même s'il n'y a qu'une seule attestation :

[
  {
    "employeur": "raison sociale de l'employeur (la structure qui emploie, pas le salarié)",
    "siret": "numéro SIRET de l'employeur si présent, sinon null",
    "date": "date de DÉBUT du contrat (date d'embauche) au format YYYY-MM-DD",
    "date_fin": "date de FIN du contrat au format YYYY-MM-DD si elle est indiquée, sinon null",
    "type_activite": "cachet_isole, cachet_groupe ou heures",
    "nombre": nombre de cachets OU nombre d'heures (un nombre),
    "salaire_brut": salaire brut total de la période en euros (un nombre, sans symbole), sinon null
  }
]

Règles importantes :
- Une attestation = un bloc avec son propre numéro d'attestation et sa propre période. S'il y a 2 numéros d'attestation différents, renvoie 2 objets. S'il y en a 3, renvoie 3 objets.
- "date" : c'est TOUJOURS la date de début / d'embauche (le premier jour travaillé). Elle est presque toujours présente.
- "date_fin" : c'est le dernier jour travaillé / date de fin de contrat. Si le contrat est sur un seul jour, date_fin peut être égale à date. Si elle n'est pas indiquée, mets null. Ne confonds JAMAIS début et fin : si tu n'as qu'une date, mets-la dans "date" et mets "date_fin" à null.
- "type_activite" : si l'AEM mentionne des CACHETS, utilise "cachet_isole" (cas le plus courant) ou "cachet_groupe" si explicitement groupés. Si elle est en HEURES réelles (technicien, annexe 8), utilise "heures".
- "nombre" : si ce sont des cachets, mets le NOMBRE DE CACHETS. Si ce sont des heures, mets le NOMBRE D'HEURES. Ne convertis pas toi-même.
- Si une information est absente ou illisible, mets null (sauf "nombre" : mets 0 si introuvable).
- Ne devine jamais un SIRET ou un montant : si tu n'es pas sûr, mets null.
- Réponds en JSON pur (une liste []), rien d'autre."""


PROMPT_ARE = """Tu lis une attestation/notification France Travail (ARE — Allocation de Retour à l'Emploi) d'un intermittent du spectacle français.

Trouve DEUX informations :
1. La DATE ANNIVERSAIRE : la date de prochain réexamen / renouvellement / réadmission des droits. Elle est souvent libellée "date anniversaire", "date de fin de droits", "fin de droits", "prochaine date de réexamen". Format YYYY-MM-DD.
2. Le MONTANT JOURNALIER brut de l'allocation : l'allocation journalière (AJ), parfois "montant journalier brut" ou "allocation journalière brute", en euros.

Réponds STRICTEMENT en JSON, sans aucun texte autour, sans Markdown :
{"date_anniversaire": "YYYY-MM-DD" ou null, "montant_journalier": nombre ou null}

Règles :
- Si une info est absente ou illisible, mets null. Ne devine jamais.
- "montant_journalier" : un nombre (ex : 52.34), sans symbole € ni texte.
- Réponds en JSON pur, rien d'autre."""


def extract_are_data(file_path: str) -> dict:
    """Lit une attestation ARE via Claude Vision → {date_anniversaire, montant_journalier, filename}."""
    if not ANTHROPIC_API_KEY:
        raise RuntimeError("Lecture d'attestation indisponible : clé API non configurée.")

    import requests

    media_type, b64, kind = _encode_file(file_path)
    if kind == "document":
        source_block = {"type": "document", "source": {"type": "base64", "media_type": media_type, "data": b64}}
    else:
        source_block = {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": b64}}

    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": MODEL,
            "max_tokens": 600,
            "messages": [{"role": "user", "content": [source_block, {"type": "text", "text": PROMPT_ARE}]}],
        },
        timeout=60,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Lecture impossible (code {resp.status_code}).")

    body = resp.json()
    parts = [b.get("text", "") for b in body.get("content", []) if b.get("type") == "text"]
    raw = _clean_json("".join(parts))
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        raise RuntimeError("Je n'ai pas réussi à lire cette attestation. Essaie une photo plus nette, ou saisis à la main.")

    if isinstance(data, list):
        data = data[0] if data else {}
    if not isinstance(data, dict):
        raise RuntimeError("Lecture impossible : format inattendu. Saisis à la main.")

    # Normalisation : date stricte YYYY-MM-DD, montant en float.
    da = data.get("date_anniversaire")
    date_anniv = None
    if isinstance(da, str) and da.strip():
        try:
            datetime.strptime(da.strip()[:10], "%Y-%m-%d")
            date_anniv = da.strip()[:10]
        except ValueError:
            date_anniv = None

    mj = data.get("montant_journalier")
    montant = None
    if isinstance(mj, (int, float)):
        montant = float(mj)
    elif isinstance(mj, str):
        s = mj.replace("€", "").replace(",", ".").strip()
        try:
            montant = float(s)
        except ValueError:
            montant = None

    return {"date_anniversaire": date_anniv, "montant_journalier": montant, "filename": os.path.basename(file_path)}


def _encode_file(file_path: str):
    """Retourne (media_type, base64, kind) où kind est 'image' ou 'document' (pdf)."""
    ext = os.path.splitext(file_path)[1].lower()
    with open(file_path, "rb") as f:
        raw = f.read()
    b64 = base64.standard_b64encode(raw).decode("utf-8")
    if ext == ".pdf":
        return "application/pdf", b64, "document"
    media_type = mimetypes.guess_type(file_path)[0] or "image/jpeg"
    return media_type, b64, "image"


def _clean_json(text: str) -> str:
    """Retire d'éventuelles balises Markdown ```json ... ``` autour du JSON."""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("```", 2)[1] if "```" in t[3:] else t[3:]
        if t.startswith("json"):
            t = t[4:]
    return t.strip().strip("`").strip()


def _normalise(data: dict, filename: str) -> dict:
    """Nettoie et borne les valeurs renvoyées par le modèle."""
    type_act = data.get("type_activite") or "cachet_isole"
    if type_act not in ("cachet_isole", "cachet_groupe", "heures"):
        type_act = "cachet_isole"

    # nombre
    try:
        nombre = float(data.get("nombre") or 0)
        if nombre < 0:
            nombre = 0.0
    except (TypeError, ValueError):
        nombre = 0.0

    # salaire
    brut = data.get("salaire_brut")
    try:
        brut = float(brut) if brut is not None else None
        if brut is not None and brut < 0:
            brut = None
    except (TypeError, ValueError):
        brut = None

    # date (début / embauche) — sert de référence pour le calcul
    date_str = data.get("date")
    date_iso = None
    if date_str:
        try:
            date_iso = datetime.strptime(str(date_str)[:10], "%Y-%m-%d").date().isoformat()
        except ValueError:
            date_iso = None

    # date_fin (dernier jour) — purement pour l'affichage "du X au Y"
    date_fin_str = data.get("date_fin")
    date_fin_iso = None
    if date_fin_str:
        try:
            date_fin_iso = datetime.strptime(str(date_fin_str)[:10], "%Y-%m-%d").date().isoformat()
        except ValueError:
            date_fin_iso = None
    # Si la fin est avant le début (lecture inversée), on l'ignore plutôt que d'afficher une absurdité.
    if date_iso and date_fin_iso and date_fin_iso < date_iso:
        date_fin_iso = None
    # Si la fin égale le début (contrat d'un jour), pas besoin de l'afficher comme une période.
    if date_fin_iso and date_iso and date_fin_iso == date_iso:
        date_fin_iso = None

    return {
        "employeur": (data.get("employeur") or None),
        "siret": (data.get("siret") or None),
        "date": date_iso,
        "date_fin": date_fin_iso,
        "type_activite": type_act,
        "nombre": nombre,
        "salaire_brut": brut,
        "filename": filename,
    }


def extract_aem_data(file_path: str) -> dict:
    if not ANTHROPIC_API_KEY:
        raise RuntimeError("Lecture d'AEM indisponible : clé API non configurée.")

    import requests  # déjà présent dans les dépendances backend

    media_type, b64, kind = _encode_file(file_path)

    if kind == "document":
        source_block = {"type": "document", "source": {"type": "base64", "media_type": media_type, "data": b64}}
    else:
        source_block = {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": b64}}

    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": MODEL,
            "max_tokens": 1500,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        source_block,
                        {"type": "text", "text": PROMPT},
                    ],
                }
            ],
        },
        timeout=60,
    )

    if resp.status_code != 200:
        raise RuntimeError(f"Lecture impossible (code {resp.status_code}).")

    body = resp.json()
    # Concatène les blocs texte de la réponse
    parts = [b.get("text", "") for b in body.get("content", []) if b.get("type") == "text"]
    raw = _clean_json("".join(parts))

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        raise RuntimeError("Je n'ai pas réussi à lire cette AEM. Essaie une photo plus nette, ou saisis à la main.")

    fname = os.path.basename(file_path)
    # Le document peut contenir plusieurs AEM → on attend une liste.
    # Compatibilité : si le modèle renvoie un seul objet, on l'enveloppe dans une liste.
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        raise RuntimeError("Lecture impossible : format inattendu. Saisis à la main.")

    resultats = [_normalise(item, fname) for item in data if isinstance(item, dict)]
    # On écarte les entrées totalement vides (ni date, ni nombre exploitable).
    resultats = [r for r in resultats if r.get("date") or (r.get("nombre") or 0) > 0]
    if not resultats:
        raise RuntimeError("Je n'ai rien trouvé d'exploitable sur ce document. Essaie une photo plus nette, ou saisis à la main.")
    return resultats

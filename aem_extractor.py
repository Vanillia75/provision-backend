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

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
MODEL = "claude-sonnet-4-6"

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}

# Instruction donnée à Claude. On lui demande UNIQUEMENT du JSON, rien d'autre,
# pour pouvoir le parser directement.
PROMPT = """Tu lis une AEM (Attestation Employeur Mensuelle) d'un intermittent du spectacle français.

Extrais UNIQUEMENT ces informations et réponds STRICTEMENT en JSON, sans aucun texte autour, sans balises Markdown :

{
  "employeur": "raison sociale de l'employeur (la structure qui emploie, pas le salarié)",
  "siret": "numéro SIRET de l'employeur si présent, sinon null",
  "date": "date de fin de la période d'emploi au format YYYY-MM-DD (ou le dernier jour travaillé)",
  "type_activite": "cachet_isole, cachet_groupe ou heures",
  "nombre": nombre de cachets OU nombre d'heures (un nombre),
  "salaire_brut": salaire brut total de la période en euros (un nombre, sans symbole), sinon null
}

Règles importantes :
- "type_activite" : si l'AEM mentionne des CACHETS, utilise "cachet_isole" (cas le plus courant) ou "cachet_groupe" si explicitement groupés. Si elle est en HEURES réelles (technicien, annexe 8), utilise "heures".
- "nombre" : si ce sont des cachets, mets le NOMBRE DE CACHETS. Si ce sont des heures, mets le NOMBRE D'HEURES. Ne convertis pas toi-même.
- Si une information est absente ou illisible, mets null (sauf "nombre" : mets 0 si introuvable).
- Ne devine jamais un SIRET ou un montant : si tu n'es pas sûr, mets null.
- Réponds en JSON pur, rien d'autre."""


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

    # date
    date_str = data.get("date")
    date_iso = None
    if date_str:
        try:
            date_iso = datetime.strptime(str(date_str)[:10], "%Y-%m-%d").date().isoformat()
        except ValueError:
            date_iso = None

    return {
        "employeur": (data.get("employeur") or None),
        "siret": (data.get("siret") or None),
        "date": date_iso,
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
            "max_tokens": 600,
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

    return _normalise(data, os.path.basename(file_path))

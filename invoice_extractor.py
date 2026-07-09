"""
Extrait montant et date depuis une facture (PDF avec texte, PDF scanne, JPG ou PNG).
"""

import os
import re
from datetime import datetime
from typing import Optional


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".webp"}

AMOUNT_PATTERNS = [
    r"montant\s+(?:prélevé|à payer|net\s+à\s+payer|ttc)\b[^\n€$]{0,50}?(\d{1,3}(?:[\s.]\d{3})*[.,]\d{2})",
    r"total\s+(?:ttc|à payer|général)\b[^\n€$]{0,50}?(\d{1,3}(?:[\s.]\d{3})*[.,]\d{2})",
    r"total\b[^\n€$]{0,50}?(\d{1,3}(?:[\s.]\d{3})*[.,]\d{2})",
    r"([\d\s]+[.,]\d{2})\s*€",
    r"€\s*([\d\s]+[.,]\d{2})",
    r"\$\s*([\d\s]+[.,]\d{2})",
    r"([\d\s]+[.,]\d{2})\s*\$",
]

DATE_PATTERNS = [
    r"(\d{1,2})[\/\-\.](\d{1,2})[\/\-\.](\d{4})",
    r"(\d{4})[\/\-\.](\d{1,2})[\/\-\.](\d{1,2})",
    r"(\d{1,2})\s+(janvier|février|mars|avril|mai|juin|juillet|août|septembre|octobre|novembre|décembre)\s+(\d{4})",
]

MONTHS_FR = {
    "janvier": 1, "février": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6,
    "juillet": 7, "août": 8, "septembre": 9, "octobre": 10, "novembre": 11, "décembre": 12,
}

STOPWORDS_DEBUT = {
    "le", "la", "les", "un", "une", "des", "est", "sont", "aussi", "sur", "dans",
    "pour", "avec", "où", "qui", "que", "dont", "tout", "tous", "toute", "toutes",
    "ce", "cet", "cette", "ces", "il", "elle", "nous", "vous", "ils", "elles",
    "et", "ou", "mais", "donc", "or", "ni", "car", "de", "du", "au", "aux",
}

MOTS_LEGAUX = {
    "tva", "applicable", "article", "conformément", "conditions", "paiement",
    "échéance", "pénalité", "indemnité", "recouvrement", "cgv", "rgpd", "siret",
    "siren", "rcs", "naf", "ape", "tribunal", "compétent", "vigueur", "code",
    "civil", "commercial", "intracommunautaire", "exonération",
}

# Headers de tableau PDF qui ne sont pas des descriptions de prestation
HEADERS_TABLEAU = {
    "qté", "quantite", "quantité", "prix", "unitaire", "prix unitaire",
    "taxe", "montant", "total", "ht", "ttc", "tva", "désignation", "designation",
    "libellé", "libelle", "description", "référence", "reference", "article",
    "remise", "discount", "quantity", "unit price", "amount", "subtotal",
}


def _est_credible(valeur: str) -> bool:
    if not valeur:
        return False
    # Rejeter si ressemble à un header de tableau
    mots_valeur = set(w.lower().strip(".,;:()") for w in valeur.split())
    if len(mots_valeur & HEADERS_TABLEAU) >= 2:
        return False
    # Rejeter si contient des mots de header de tableau connus
    valeur_lower = valeur.lower()
    if any(h in valeur_lower for h in ["qté", "prix unitaire", "taxe", "montant ht", "montant ttc"]):
        return False
    mot_initial = valeur.strip().split(" ")[0].lower().strip(".,;:")
    if mot_initial in STOPWORDS_DEBUT:
        return False
    mots = set(w.lower().strip(".,;:()") for w in valeur.split())
    if mots & MOTS_LEGAUX:
        return False
    if valeur.strip().endswith(".") and len(valeur.split()) > 4:
        return False
    if not any(c.isupper() for c in valeur[:1]):
        return False
    return True


def _nom_fichier_propre(filename: str) -> str:
    """Nettoie un nom de fichier pour en faire une description lisible."""
    nom = re.sub(r"\.(pdf|jpg|jpeg|png|bmp|tiff|webp)$", "", filename, flags=re.IGNORECASE)
    nom = re.sub(r"[_\-]+", " ", nom).strip()
    # Capitaliser
    nom = nom.capitalize()
    return nom if len(nom) > 2 else ""


def extract_invoice_data(file_path: str) -> dict:
    ext = os.path.splitext(file_path)[1].lower()

    # Photos et PDF scannés : lecture par Claude Vision (le même circuit que le scan AEM).
    # ⚠️ L'ancien repli Tesseract est SUPPRIMÉ : le binaire n'existe pas sur Railway,
    # toutes les photos échouaient en prod (bug détecté à la revue du 09/07/2026).
    if ext in IMAGE_EXTENSIONS:
        vision = _extract_via_vision(file_path)
        if vision:
            return vision
        raise RuntimeError("Je n'ai pas réussi à lire cette image. Essaie une photo plus nette, ou saisis à la main.")

    text = _extract_pdf_text(file_path)
    if not text.strip():
        vision = _extract_via_vision(file_path)
        if vision:
            return vision
        text = ""

    amount = _find_amount(text)
    invoice_date = _find_date(text)
    client_brut = _find_first_match(text, CLIENT_PATTERNS)
    description_brut = _find_first_match(text, DESCRIPTION_PATTERNS)
    client = client_brut if _est_credible(client_brut) else None
    description = description_brut if _est_credible(description_brut) else None

    # Fallback description : nom du fichier nettoyé
    if not description:
        description = _nom_fichier_propre(os.path.basename(file_path)) or None

    numero = _find_first_match(text, NUMERO_PATTERNS)
    tva = _find_tva(text)

    return {
        "amount": amount,
        "date": invoice_date,
        "filename": os.path.basename(file_path),
        "client": client,
        "description": description,
        "numero_facture": numero,
        "tva_pct": tva,
    }


def _extract_pdf_text(pdf_path: str) -> str:
    import pdfplumber
    with pdfplumber.open(pdf_path) as pdf:
        return "\n".join(page.extract_text() or "" for page in pdf.pages)


PROMPT_VISION_FACTURE = """Tu lis une facture ou un reçu (photo ou scan). Extrais ces champs en JSON :
{"montant_ttc": nombre ou null, "date": "YYYY-MM-DD" ou null, "client": "..." ou null,
 "description": "..." ou null, "numero_facture": "..." ou null, "tva_pct": nombre ou null}
Règles :
- "montant_ttc" = le TOTAL TTC (ou le montant payé), un nombre sans symbole.
- "description" = l'objet de la facture en quelques mots (jamais un header de tableau).
- Si une info est absente ou illisible, mets null. Ne devine jamais.
- Réponds en JSON pur, rien d'autre."""


def _extract_via_vision(file_path: str) -> Optional[dict]:
    """Lecture par Claude Vision (photos, PDF scannés). None si indisponible/échec."""
    try:
        import json
        import requests
        from aem_extractor import _build_source_blocks, _clean_json, ANTHROPIC_API_KEY, MODEL

        if not ANTHROPIC_API_KEY:
            return None
        blocks = _build_source_blocks(file_path)
        if not blocks:
            return None
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json={"model": MODEL, "max_tokens": 400,
                  "messages": [{"role": "user", "content": blocks + [{"type": "text", "text": PROMPT_VISION_FACTURE}]}]},
            timeout=60,
        )
        if resp.status_code != 200:
            return None
        parts = [b.get("text", "") for b in resp.json().get("content", []) if b.get("type") == "text"]
        data = json.loads(_clean_json("".join(parts)))
        if isinstance(data, list):
            data = data[0] if data else {}
        if not isinstance(data, dict):
            return None

        montant = data.get("montant_ttc")
        try:
            montant = float(montant) if montant is not None else None
        except (TypeError, ValueError):
            montant = None
        dt = None
        d = data.get("date")
        if isinstance(d, str) and d.strip():
            try:
                dt = datetime.strptime(d.strip()[:10], "%Y-%m-%d")
            except ValueError:
                dt = None
        tva = data.get("tva_pct")
        try:
            tva = float(tva) if tva is not None else None
        except (TypeError, ValueError):
            tva = None
        description = data.get("description") or _nom_fichier_propre(os.path.basename(file_path)) or None
        if montant is None and dt is None:
            return None  # rien d'utile lu → on laisse le message d'échec honnête
        return {
            "amount": montant,
            "date": dt,
            "filename": os.path.basename(file_path),
            "client": data.get("client") or None,
            "description": description,
            "numero_facture": data.get("numero_facture") or None,
            "tva_pct": tva,
        }
    except Exception:
        return None


CLIENT_PATTERNS = [
    r"factur[ée]\s*(?:à|a)\s*:?\s*\n*\s*([A-Z][A-Za-zÀ-ÿ0-9&'\.\-\s]{2,60}?)(?:\n|$)",
    r"client\s*:?\s*\n*\s*([A-Z][A-Za-zÀ-ÿ0-9&'\.\-\s]{2,60}?)(?:\n|$)",
    r"destinataire\s*:?\s*\n*\s*([A-Z][A-Za-zÀ-ÿ0-9&'\.\-\s]{2,60}?)(?:\n|$)",
    r"adress[ée]\s*(?:à|a)\s*:?\s*\n*\s*([A-Z][A-Za-zÀ-ÿ0-9&'\.\-\s]{2,60}?)(?:\n|$)",
    r"adresse\s+de\s+facturation\s*:?\s*\n*\s*([A-Z][A-Za-zÀ-ÿ0-9&'\.\-\s]{2,60}?)(?:\n|$)",
    r"à\s+l['']attention\s+de\s*:?\s*\n*\s*([A-Z][A-Za-zÀ-ÿ0-9&'\.\-\s]{2,60}?)(?:\n|$)",
    r"bill(?:ed)?\s+to\s*:?\s*\n*\s*([A-Z][A-Za-zÀ-ÿ0-9&'\.\-\s]{2,60}?)(?:\n|$)",
    r"customer\s*:?\s*\n*\s*([A-Z][A-Za-zÀ-ÿ0-9&'\.\-\s]{2,60}?)(?:\n|$)",
]

DESCRIPTION_PATTERNS = [
    r"objet\s*:?\s*\n*\s*([A-Za-zÀ-ÿ0-9&'\.\-,\s]{4,80}?)(?:\n|$)",
    r"prestation\s*:?\s*\n*\s*([A-Za-zÀ-ÿ0-9&'\.\-,\s]{4,80}?)(?:\n|$)",
    r"libell[ée]\s*:?\s*\n*\s*([A-Za-zÀ-ÿ0-9&'\.\-,\s]{4,80}?)(?:\n|$)",
    r"intitul[ée]\s*:?\s*\n*\s*([A-Za-zÀ-ÿ0-9&'\.\-,\s]{4,80}?)(?:\n|$)",
    r"service\s*:?\s*\n*\s*([A-Za-zÀ-ÿ0-9&'\.\-,\s]{4,80}?)(?:\n|$)",
    # "désignation" et "description" retirés car trop souvent headers de tableau
]

NUMERO_PATTERNS = [
    r"facture\s*n[°o]?\s*:?\s*([A-Za-z0-9\-\/]{2,20})",
    r"n[°o]\s*(?:de\s*)?facture\s*:?\s*([A-Za-z0-9\-\/]{2,20})",
    r"invoice\s*(?:number|#|n[°o])?\s*:?\s*([A-Za-z0-9\-\/]{2,20})",
]

TVA_PATTERN = r"tva\s*:?\s*(\d{1,2})\s*%"


def _find_first_match(text: str, patterns: list) -> Optional[str]:
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            value = match.group(1).strip(" :\n\t-")
            if value and len(value) >= 2:
                return value
    return None


def _find_tva(text: str) -> Optional[float]:
    match = re.search(TVA_PATTERN, text.lower())
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            return None
    return None


def _find_amount(text: str) -> Optional[float]:
    text_lower = text.lower()
    for pattern in AMOUNT_PATTERNS:
        matches = re.findall(pattern, text_lower)
        for m in matches:
            clean = m.replace(" ", "").replace(",", ".")
            try:
                val = float(clean)
                if val > 0:
                    return val
            except ValueError:
                continue
    return None


def _find_date(text: str) -> Optional[datetime]:
    for pattern in DATE_PATTERNS[:2]:
        match = re.search(pattern, text)
        if match:
            groups = match.groups()
            try:
                if len(groups[0]) == 4:
                    return datetime(int(groups[0]), int(groups[1]), int(groups[2]))
                else:
                    return datetime(int(groups[2]), int(groups[1]), int(groups[0]))
            except (ValueError, IndexError):
                continue

    match = re.search(DATE_PATTERNS[2], text.lower())
    if match:
        day, month_name, year = match.groups()
        month = MONTHS_FR.get(month_name)
        if month:
            try:
                return datetime(int(year), month, int(day))
            except ValueError:
                pass
    return None

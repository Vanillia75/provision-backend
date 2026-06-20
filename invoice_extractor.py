"""
Extrait montant et date depuis une facture (PDF avec texte, PDF scanne, JPG ou PNG).
Reprend la logique validee et testee sur le projet Qontrol.
"""

import os
import re
from datetime import datetime
from typing import Optional


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".webp"}

AMOUNT_PATTERNS = [
    r"total\b[^\n€$]{0,50}?(\d{1,3}(?:[\s.]\d{3})*[.,]\d{2})",
    r"montant\s+(?:prélevé|à payer|ttc)\b[^\n€$]{0,50}?(\d{1,3}(?:[\s.]\d{3})*[.,]\d{2})",
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

# Mots qui, s'ils apparaissent dans le texte capture, trahissent un fragment de phrase
# (texte legal, CGV, suite de paragraphe) plutot qu'une vraie donnee (nom client, objet)
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


def _est_credible(valeur: str) -> bool:
    """Filtre de confiance : mieux vaut ne rien extraire qu'extraire un fragment de phrase."""
    if not valeur:
        return False
    mot_initial = valeur.strip().split(" ")[0].lower().strip(".,;:")
    if mot_initial in STOPWORDS_DEBUT:
        return False
    mots = set(w.lower().strip(".,;:()") for w in valeur.split())
    if mots & MOTS_LEGAUX:
        return False
    # Une vraie donnee (nom client, objet court) ne se termine quasiment jamais par un point isole
    # type fin de phrase ("...individualisable.") sauf abreviation courante
    if valeur.strip().endswith(".") and len(valeur.split()) > 4:
        return False
    # Doit contenir au moins une majuscule en debut de mot (nom propre, debut d'objet structure)
    if not any(c.isupper() for c in valeur[:1]):
        return False
    return True


def extract_invoice_data(file_path: str) -> dict:
    ext = os.path.splitext(file_path)[1].lower()

    if ext in IMAGE_EXTENSIONS:
        text = _extract_text_from_image(file_path)
    else:
        text = _extract_pdf_text(file_path)
        if not text.strip():
            text = _extract_text_via_ocr_pdf(file_path)

    amount = _find_amount(text)
    invoice_date = _find_date(text)
    client_brut = _find_first_match(text, CLIENT_PATTERNS)
    description_brut = _find_first_match(text, DESCRIPTION_PATTERNS)
    client = client_brut if _est_credible(client_brut) else None
    description = description_brut if _est_credible(description_brut) else None
    numero = _find_first_match(text, NUMERO_PATTERNS)
    tva = _find_tva(text)

    return {
        "amount": amount,
        "date": invoice_date,
        "filename": os.path.basename(file_path),
        "client": client,  # best-effort, peut etre None
        "description": description,  # best-effort, peut etre None
        "numero_facture": numero,  # best-effort, peut etre None
        "tva_pct": tva,  # best-effort, peut etre None (ne pas supposer 0%)
    }


def _extract_pdf_text(pdf_path: str) -> str:
    import pdfplumber
    with pdfplumber.open(pdf_path) as pdf:
        return "\n".join(page.extract_text() or "" for page in pdf.pages)


def _ocr_image(image) -> str:
    import pytesseract
    try:
        return pytesseract.image_to_string(image, lang="fra+eng")
    except Exception:
        return pytesseract.image_to_string(image)


def _extract_text_from_image(image_path: str) -> str:
    from PIL import Image
    return _ocr_image(Image.open(image_path))


def _extract_text_via_ocr_pdf(pdf_path: str) -> str:
    from pdf2image import convert_from_path
    pages = convert_from_path(pdf_path)
    return "\n".join(_ocr_image(p) for p in pages)


CLIENT_PATTERNS = [
    r"factur[ée]\s*(?:à|a)\s*:?\s*\n?\s*([A-Z][A-Za-zÀ-ÿ0-9&'\.\-\s]{2,60}?)(?:\n|$)",
    r"client\s*:?\s*\n?\s*([A-Z][A-Za-zÀ-ÿ0-9&'\.\-\s]{2,60}?)(?:\n|$)",
    r"destinataire\s*:?\s*\n?\s*([A-Z][A-Za-zÀ-ÿ0-9&'\.\-\s]{2,60}?)(?:\n|$)",
]

DESCRIPTION_PATTERNS = [
    r"objet\s*:?\s*\n?\s*([A-Za-zÀ-ÿ0-9&'\.\-,\s]{4,80}?)(?:\n|$)",
    r"d[ée]signation\s*:?\s*\n?\s*([A-Za-zÀ-ÿ0-9&'\.\-,\s]{4,80}?)(?:\n|$)",
    r"prestation\s*:?\s*\n?\s*([A-Za-zÀ-ÿ0-9&'\.\-,\s]{4,80}?)(?:\n|$)",
]

NUMERO_PATTERNS = [
    r"facture\s*n[°o]?\s*:?\s*([A-Za-z0-9\-\/]{2,20})",
    r"n[°o]\s*(?:de\s*)?facture\s*:?\s*([A-Za-z0-9\-\/]{2,20})",
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
    best_amount = None
    for pattern in AMOUNT_PATTERNS:
        matches = re.findall(pattern, text_lower)
        for m in matches:
            clean = m.replace(" ", "").replace(",", ".")
            try:
                val = float(clean)
                if val > 0 and (best_amount is None or val > best_amount):
                    best_amount = val
            except ValueError:
                continue
        if best_amount:
            break
    return best_amount


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

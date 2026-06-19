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

    return {
        "amount": amount,
        "date": invoice_date,
        "filename": os.path.basename(file_path),
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

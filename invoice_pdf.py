"""
Generation du PDF d'une facture client, avec mentions legales (emetteur,
SIRET, numerotation, lignes, totaux). Utilise reportlab (Platypus) pour
une mise en page propre en tableaux.
"""

import io
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

INK = colors.HexColor("#0A2540")
GREY = colors.HexColor("#6B7A8D")
LIGHT = colors.HexColor("#F7F9F5")
LINE = colors.HexColor("#DDE5EE")
LINE_LIGHT = colors.HexColor("#EEF2F7")


def generate_invoice_pdf(invoice: dict, emitter: dict) -> bytes:
    """
    invoice : dict produit par _invoice_to_dict (numero, client_nom, client_email,
        client_adresse, date_emission, date_echeance, montant, lignes, notes)
    emitter : dict avec les cles "nom", "adresse", "siret", "mention" (mention
        juridique optionnelle, ex: dispense d'immatriculation)
    Renvoie les octets bruts du PDF genere.
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        topMargin=20 * mm, bottomMargin=20 * mm, leftMargin=20 * mm, rightMargin=20 * mm,
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("TitleH", parent=styles["Title"], textColor=INK, fontSize=20, spaceAfter=2, alignment=0)
    label_style = ParagraphStyle("Label", parent=styles["Normal"], textColor=GREY, fontSize=8, leading=11)
    normal_style = ParagraphStyle("NormalDark", parent=styles["Normal"], textColor=INK, fontSize=10, leading=15)
    small_style = ParagraphStyle("Small", parent=styles["Normal"], textColor=GREY, fontSize=8, leading=11)

    story = []

    story.append(Paragraph(f"Facture {invoice.get('numero', '')}", title_style))
    story.append(Spacer(1, 6 * mm))

    emitter_html = f"<b>{emitter.get('nom') or '—'}</b><br/>{emitter.get('adresse') or ''}"
    if emitter.get("siret"):
        emitter_html += f"<br/>SIRET : {emitter['siret']}"
    if emitter.get("mention"):
        emitter_html += f"<br/>{emitter['mention']}"

    client_html = f"<b>{invoice.get('client_nom', '')}</b><br/>{invoice.get('client_adresse') or ''}"
    if invoice.get("client_email"):
        client_html += f"<br/>{invoice['client_email']}"

    info_table = Table(
        [
            [Paragraph("ÉMETTEUR", label_style), Paragraph("CLIENT", label_style)],
            [Paragraph(emitter_html, normal_style), Paragraph(client_html, normal_style)],
        ],
        colWidths=[85 * mm, 85 * mm],
    )
    info_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 4),
    ]))
    story.append(info_table)
    story.append(Spacer(1, 8 * mm))

    date_emission = invoice.get("date_emission")
    meta = f"Émise le {date_emission.strftime('%d/%m/%Y')}" if date_emission else ""
    date_echeance = invoice.get("date_echeance")
    if date_echeance:
        meta += f" — Échéance le {date_echeance.strftime('%d/%m/%Y')}"
    story.append(Paragraph(meta, label_style))
    story.append(Spacer(1, 6 * mm))

    data = [["Description", "Qté", "Prix unitaire", "Total"]]
    for l in (invoice.get("lignes") or []):
        desc = l.get("description", "") if isinstance(l, dict) else getattr(l, "description", "")
        qte = (l.get("quantite", 0) if isinstance(l, dict) else getattr(l, "quantite", 0)) or 0
        pu = (l.get("prix_unitaire", 0) if isinstance(l, dict) else getattr(l, "prix_unitaire", 0)) or 0
        total = qte * pu
        data.append([desc, f"{qte:g}", f"{pu:.2f} €", f"{total:.2f} €"])

    lignes_table = Table(data, colWidths=[85 * mm, 20 * mm, 35 * mm, 30 * mm])
    lignes_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), LIGHT),
        ("TEXTCOLOR", (0, 0), (-1, 0), GREY),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ALIGN", (1, 0), (1, -1), "CENTER"),
        ("ALIGN", (2, 0), (-1, -1), "RIGHT"),
        ("LINEBELOW", (0, 0), (-1, 0), 0.5, LINE),
        ("LINEBELOW", (0, 1), (-1, -1), 0.5, LINE_LIGHT),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(lignes_table)
    story.append(Spacer(1, 4 * mm))

    montant = invoice.get("montant", 0) or 0
    totals_table = Table(
        [
            ["Total HT", f"{montant:.2f} €"],
            ["TVA non applicable — art. 293 B du CGI", "0,00 €"],
            ["Total TTC", f"{montant:.2f} €"],
        ],
        colWidths=[140 * mm, 30 * mm],
    )
    totals_table.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("TEXTCOLOR", (0, 1), (-1, 1), GREY),
        ("FONTSIZE", (0, 1), (-1, 1), 8),
        ("FONTNAME", (0, 2), (-1, 2), "Helvetica-Bold"),
        ("FONTSIZE", (0, 2), (-1, 2), 11),
        ("LINEABOVE", (0, 2), (-1, 2), 0.75, INK),
        ("TOPPADDING", (0, 2), (-1, 2), 6),
    ]))
    story.append(totals_table)

    if invoice.get("notes"):
        story.append(Spacer(1, 8 * mm))
        story.append(Paragraph(f"<b>Notes :</b> {invoice['notes']}", small_style))

    story.append(Spacer(1, 14 * mm))
    story.append(Paragraph("Document généré par H€CTOR — hector-app.fr", small_style))

    doc.build(story)
    buffer.seek(0)
    return buffer.read()

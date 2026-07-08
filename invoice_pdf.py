"""
Generation du PDF d'une facture client, avec mentions legales (emetteur,
SIRET, numerotation, lignes, totaux). Utilise reportlab (Platypus) pour
une mise en page propre en tableaux.
"""

import io
import html
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

from legal_mentions import compute_invoice_totals, format_vat_rate

INK = colors.HexColor("#0A2540")
GREY = colors.HexColor("#6B7A8D")
LIGHT = colors.HexColor("#F7F9F5")
LINE = colors.HexColor("#DDE5EE")
LINE_LIGHT = colors.HexColor("#EEF2F7")


def generate_invoice_pdf(invoice: dict, emitter: dict, fiscal: dict = None, kind: str = "facture") -> bytes:
    """
    invoice : dict produit par _invoice_to_dict / _quote_to_dict (numero, client_*,
        date_emission, date_echeance OU date_validite, montant, lignes, notes)
    emitter : dict avec les cles "nom", "adresse", "siret", "mention" (mention
        juridique optionnelle, ex: dispense d'immatriculation)
    fiscal  : dict des parametres TVA (vat_mode, vat_rate, vat_number). None => franchise.
        Sert UNIQUEMENT a l'affichage des totaux ; `invoice["montant"]` reste le HT.
    kind    : "facture" (defaut) ou "devis". Ne change QUE le titre et la ligne de dates ;
        tout le reste (emetteur, client, totaux, mentions) est identique. Le defaut garantit
        que le PDF facture reste inchange.
    Renvoie les octets bruts du PDF genere.
    """
    is_devis = (kind == "devis")
    totals = compute_invoice_totals(invoice.get("montant", 0) or 0, fiscal, invoice.get("date_emission"))
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

    # Échappement : ces champs viennent de l'utilisateur et reportlab interprète
    # les balises (<b>, <br/>...). On échappe pour éviter toute injection de balise.
    e = lambda v: html.escape(str(v)) if v is not None else ""

    story.append(Paragraph(f"{'Devis' if is_devis else 'Facture'} {e(invoice.get('numero', ''))}", title_style))
    story.append(Spacer(1, 6 * mm))

    emitter_html = f"<b>{e(emitter.get('nom')) or '—'}</b><br/>{e(emitter.get('adresse'))}"
    if emitter.get("siret"):
        emitter_html += f"<br/>SIRET : {e(emitter['siret'])}"
    if totals.get("vat_number"):
        emitter_html += f"<br/>N° TVA : {e(totals['vat_number'])}"
    if emitter.get("mention"):
        emitter_html += f"<br/>{e(emitter['mention'])}"

    client_html = f"<b>{e(invoice.get('client_nom', ''))}</b><br/>{e(invoice.get('client_adresse'))}"
    if invoice.get("client_email"):
        client_html += f"<br/>{e(invoice['client_email'])}"
    # SIRET / n° TVA du client : uniquement pour un client professionnel, et seulement
    # si renseignés (un particulier ou une facture ancienne n'affiche rien). NULL → particulier.
    if invoice.get("client_type") == "professionnel":
        if invoice.get("client_siret"):
            client_html += f"<br/>SIRET : {e(invoice['client_siret'])}"
        if invoice.get("client_tva"):
            client_html += f"<br/>N° TVA : {e(invoice['client_tva'])}"

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
    if is_devis:
        meta = f"Émis le {date_emission.strftime('%d/%m/%Y')}" if date_emission else ""
        date_validite = invoice.get("date_validite")
        if date_validite:
            meta += f" — Valable jusqu'au {date_validite.strftime('%d/%m/%Y')}"
    else:
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
        data.append([Paragraph(e(desc), normal_style), f"{qte:g}", f"{pu:.2f} €", f"{total:.2f} €"])

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

    # Ligne du milieu : en franchise, mention 293 B + 0,00 € (discrète) ;
    # en assujetti, vraie ligne « TVA (X %) : montant ».
    if totals["mode"] == "assujetti":
        mid_label = f"TVA ({format_vat_rate(totals['rate'])} %)"
        mid_value = f"{totals['tva']:.2f} €"
    else:
        mid_label = totals["mention"]
        mid_value = "0,00 €"

    totals_table = Table(
        [
            ["Total HT", f"{totals['ht']:.2f} €"],
            [mid_label, mid_value],
            ["Total TTC", f"{totals['ttc']:.2f} €"],
        ],
        colWidths=[140 * mm, 30 * mm],
    )
    totals_style = [
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("FONTNAME", (0, 2), (-1, 2), "Helvetica-Bold"),
        ("FONTSIZE", (0, 2), (-1, 2), 11),
        ("LINEABOVE", (0, 2), (-1, 2), 0.75, INK),
        ("TOPPADDING", (0, 2), (-1, 2), 6),
    ]
    if totals["mode"] != "assujetti":
        # La mention 293 B reste discrète (grise, plus petite).
        totals_style += [
            ("TEXTCOLOR", (0, 1), (-1, 1), GREY),
            ("FONTSIZE", (0, 1), (-1, 1), 8),
        ]
    totals_table.setStyle(TableStyle(totals_style))
    story.append(totals_table)

    if invoice.get("notes"):
        story.append(Spacer(1, 8 * mm))
        story.append(Paragraph(f"<b>Notes :</b> {e(invoice['notes'])}", small_style))

    story.append(Spacer(1, 14 * mm))
    story.append(Paragraph("Document généré par TOTOR · montotor.fr", small_style))

    doc.build(story)
    buffer.seek(0)
    return buffer.read()

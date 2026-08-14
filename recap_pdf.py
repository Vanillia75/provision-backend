# -*- coding: utf-8 -*-
"""
Récapitulatif de revenus de l'intermittent, en VRAI fichier PDF.

⚠️ POURQUOI CE FICHIER EXISTE (13/08/2026).
Le récapitulatif était jusqu'ici « imprimé » depuis le navigateur, via
window.print(). Or cette commande N'EXISTE PAS sur iPhone dans une application
(limite d'Apple, documentée sur leurs forums développeurs). Pire, le code
testait si la fenêtre s'était ouverte : sur iPhone elle « s'ouvre » mais reste
inutilisable, donc le programme repartait satisfait et le bouton ne faisait
RIEN, sans le moindre message.

Signalé par une abonnée depuis son iPhone : « Le pdf ne fonctionne pas ». Le
bouton n'avait en réalité jamais marché sur iOS depuis sa création.

On produit donc un vrai PDF sur le serveur, avec reportlab, exactement comme
pour les factures (invoice_pdf.py). Un fichier PDF s'ouvre partout, sur tous
les téléphones, sans dépendre d'une capacité du navigateur.

⚠️ CE MODULE NE CALCULE RIEN. Il met en page des chiffres déjà calculés par
l'application. La source de vérité du récapitulatif reste unique, côté front :
la dupliquer ici serait le meilleur moyen de la faire diverger.
"""

import io

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (Paragraph, SimpleDocTemplate, Spacer, Table,
                                TableStyle)

INK = colors.HexColor("#0A2540")
ACCENT = colors.HexColor("#378ADD")
GREY = colors.HexColor("#6B7A8D")
LIGHT = colors.HexColor("#F4F7FB")
LINE = colors.HexColor("#DDE5EE")


def _nombre(n):
    """Toute valeur -> un nombre sur. Ne leve JAMAIS d'exception.

    ⚠️ Attrape par les tests des le premier passage : un « brut » recu sous forme
    de texte faisait planter la comparaison « > 0 », et donc TOUTE la generation
    du PDF. Le document doit sortir meme quand une donnee est bizarre.
    """
    try:
        v = float(n)
    except (TypeError, ValueError):
        return 0.0
    if v != v or v in (float("inf"), float("-inf")):   # v != v : NaN
        return 0.0
    return v


def _euros(n):
    """1234.5 -> « 1 235 € ». Espace insecable, comme partout dans l'app."""
    return f"{int(round(_nombre(n))):,}".replace(",", " ") + " €"


def generate_recap_pdf(recap: dict, prenom: str = "", nom: str = "",
                       genere_le: str = "") -> bytes:
    """recap : le dict produit par l'application (lignes, totaux, période...).

    Aucune clé n'est obligatoire : un récapitulatif incomplet doit produire un
    PDF incomplet, jamais une erreur. C'est un document que quelqu'un peut
    demander à 3 h du matin depuis son téléphone.
    """
    recap = recap or {}
    lignes = recap.get("lignes") or []
    # ⚠️ Pas de tiret de remplacement quand le nom est absent : la ligne devenait
    #  « — — intermittent du spectacle » (double tiret vu par le Mac le 14/08/2026).
    #  Sans nom, on écrit simplement la qualité, ce qui se lit très bien.
    nom_complet = " ".join(x for x in [(prenom or "").strip(), (nom or "").strip()] if x)

    tampon = io.BytesIO()
    doc = SimpleDocTemplate(
        tampon, pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=16 * mm, bottomMargin=16 * mm,
        title=("Recapitulatif de revenus - " + nom_complet) if nom_complet else "Recapitulatif de revenus",
        author="TOTOR",
    )
    base = getSampleStyleSheet()
    # ⚠️ leading ET spaceAfter explicites : sans eux, les jambages du titre
    #  mordaient sur le sous-titre (chevauchement vu par le Mac le 14/08/2026 sur
    #  les 6 PDF). Un titre de 16 pt a besoin d'environ 20 pt d'interligne.
    st_titre = ParagraphStyle("t", parent=base["Normal"], fontName="Helvetica-Bold",
                              fontSize=16, leading=20, textColor=INK, spaceAfter=7)
    st_sous = ParagraphStyle("s", parent=base["Normal"], fontName="Helvetica",
                             fontSize=9.5, textColor=GREY, spaceAfter=14)
    st_norm = ParagraphStyle("n", parent=base["Normal"], fontName="Helvetica",
                             fontSize=9.5, textColor=INK, leading=14)
    st_note = ParagraphStyle("no", parent=base["Normal"], fontName="Helvetica",
                             fontSize=7.5, textColor=GREY, leading=11)

    flux = []

    # ── En-tête ──
    entete = Table([[
        Paragraph('<font color="#0A2540"><b>T</b></font>'
                  '<font color="#378ADD"><b>O</b></font>'
                  '<font color="#0A2540"><b>T</b></font>'
                  '<font color="#378ADD"><b>O</b></font>'
                  '<font color="#0A2540"><b>R</b></font>',
                  ParagraphStyle("b", parent=base["Normal"], fontName="Helvetica-Bold", fontSize=17)),
        Paragraph(f"Édité le {genere_le or '—'}", ParagraphStyle(
            "d", parent=base["Normal"], fontName="Helvetica", fontSize=8.5,
            textColor=GREY, alignment=2)),
    ]], colWidths=[90 * mm, 84 * mm])
    entete.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, 0), 1.4, INK),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 9),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    flux += [entete, Spacer(1, 12)]

    flux.append(Paragraph("Récapitulatif de revenus", st_titre))
    flux.append(Paragraph(recap.get("periodeLabel") or "Sur les 12 derniers mois", st_sous))

    # ── Qui ──
    ligne_qui = (f"<b>{nom_complet}</b> — intermittent du spectacle"
                 if nom_complet else "Intermittent du spectacle")
    qui = Table([[Paragraph(ligne_qui, st_norm)]],
                colWidths=[174 * mm])
    qui.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), LIGHT),
        ("TOPPADDING", (0, 0), (-1, -1), 9), ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
        ("LEFTPADDING", (0, 0), (-1, -1), 12), ("RIGHTPADDING", (0, 0), (-1, -1), 12),
    ]))
    flux += [qui, Spacer(1, 14)]

    # ── Les quatre chiffres ──
    st_v = ParagraphStyle("v", parent=base["Normal"], fontName="Helvetica-Bold",
                          fontSize=15, textColor=INK, alignment=1)
    st_l = ParagraphStyle("l", parent=base["Normal"], fontName="Helvetica",
                          fontSize=7.5, textColor=GREY, alignment=1, leading=10)
    cases = [
        (_euros(recap.get("totalBrut")), "Total brut déclaré"),
        (_euros(recap.get("moyenneMensuelle")), "Moyenne par mois travaillé"),
        (str(recap.get("totalContrats") or 0), "Contrats"),
        (str(recap.get("employeursUniques") or 0), "Employeurs"),
    ]
    tab_stats = Table([[Paragraph(v, st_v) for v, _ in cases],
                       [Paragraph(l, st_l) for _, l in cases]],
                      colWidths=[43.5 * mm] * 4)
    tab_stats.setStyle(TableStyle([
        ("BOX", (0, 0), (0, 1), 0.7, LINE), ("BOX", (1, 0), (1, 1), 0.7, LINE),
        ("BOX", (2, 0), (2, 1), 0.7, LINE), ("BOX", (3, 0), (3, 1), 0.7, LINE),
        ("TOPPADDING", (0, 0), (-1, 0), 11), ("BOTTOMPADDING", (0, 1), (-1, 1), 11),
    ]))
    flux += [tab_stats, Spacer(1, 16)]

    # ── Le détail mois par mois ──
    donnees = [["Mois", "Contrats", "Employeurs", "Brut"]]
    for l in lignes:
        donnees.append([
            str(l.get("label") or "—"),
            str(l.get("contrats") or 0),
            str(l.get("employeurs") or 0),
            _euros(l.get("brut")) if _nombre(l.get("brut")) > 0 else "—",
        ])
    donnees.append(["Total", str(recap.get("totalContrats") or 0),
                    str(recap.get("employeursUniques") or 0),
                    _euros(recap.get("totalBrut"))])

    tab = Table(donnees, colWidths=[74 * mm, 30 * mm, 34 * mm, 36 * mm], repeatRows=1)
    tab.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), INK),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ALIGN", (1, 0), (2, -1), "CENTER"),
        ("ALIGN", (3, 0), (3, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("LINEBELOW", (0, 1), (-1, -2), 0.5, LINE),
        ("LINEABOVE", (0, -1), (-1, -1), 1.1, INK),
        ("BACKGROUND", (0, -1), (-1, -1), LIGHT),
    ]))
    flux += [tab, Spacer(1, 10)]

    # ── Avertissement de complétude, seulement s'il est utile ──
    completude = recap.get("completude")
    if isinstance(completude, (int, float)) and 0 <= completude < 100:
        flux.append(Paragraph(
            f"Le salaire brut n'est renseigné que sur {int(completude)} % des contrats "
            "de la période : les totaux ci-dessus sont donc un minimum.", st_note))
        flux.append(Spacer(1, 6))

    # ── Mentions, mot pour mot celles de l'écran ──
    flux.append(Paragraph(
        "Ce document est un récapitulatif personnel établi à partir des données saisies par "
        "l'utilisateur dans l'application TOTOR. Il ne constitue pas une attestation officielle "
        "de France Travail, d'un employeur ou de tout autre organisme, et n'a pas de valeur "
        "juridique ou fiscale. Pour un document officiel, l'utilisateur doit s'adresser aux "
        "organismes compétents.", st_note))

    doc.build(flux)
    return tampon.getvalue()

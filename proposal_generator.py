#!/usr/bin/env python3
"""
ReDry Proposal PDF Generator - Parameterized version
Accepts a config dict and produces the branded proposal PDF.
"""

from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib.colors import HexColor, white, black
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    PageBreak, Image, HRFlowable, KeepTogether
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT, TA_JUSTIFY
from reportlab.pdfgen import canvas
from reportlab.platypus.doctemplate import PageTemplate, BaseDocTemplate, Frame
from datetime import datetime, timedelta
import os
import io

# ── Brand Colors ──
NAVY = HexColor("#1B2A4A")
ORANGE = HexColor("#E8943A")
DARK_GRAY = HexColor("#333333")
MED_GRAY = HexColor("#666666")
LIGHT_GRAY = HexColor("#F5F5F5")
BORDER_GRAY = HexColor("#CCCCCC")
WHITE = white

# ── Page Setup ──
PAGE_W, PAGE_H = letter
MARGIN_L = 0.75 * inch
MARGIN_R = 0.75 * inch
MARGIN_T = 0.75 * inch
MARGIN_B = 0.75 * inch

# ── Styles ──
styles = getSampleStyleSheet()

style_title = ParagraphStyle(
    'ProposalTitle', parent=styles['Normal'],
    fontName='Helvetica-Bold', fontSize=22, leading=26,
    textColor=NAVY, spaceAfter=4
)

style_subtitle = ParagraphStyle(
    'ProposalSubtitle', parent=styles['Normal'],
    fontName='Helvetica', fontSize=11, leading=14,
    textColor=MED_GRAY, spaceAfter=2
)

style_section_head = ParagraphStyle(
    'SectionHead', parent=styles['Normal'],
    fontName='Helvetica-Bold', fontSize=13, leading=16,
    textColor=NAVY, spaceBefore=16, spaceAfter=8
)

style_body = ParagraphStyle(
    'Body', parent=styles['Normal'],
    fontName='Helvetica', fontSize=10, leading=14,
    textColor=DARK_GRAY, alignment=TA_JUSTIFY, spaceAfter=6
)

style_small = ParagraphStyle(
    'Small', parent=styles['Normal'],
    fontName='Helvetica', fontSize=8.5, leading=11,
    textColor=MED_GRAY, spaceAfter=4
)

style_table_header = ParagraphStyle(
    'TableHeader', parent=styles['Normal'],
    fontName='Helvetica-Bold', fontSize=9.5, leading=12,
    textColor=WHITE
)

style_table_cell = ParagraphStyle(
    'TableCell', parent=styles['Normal'],
    fontName='Helvetica', fontSize=9.5, leading=12,
    textColor=DARK_GRAY
)

style_table_cell_right = ParagraphStyle(
    'TableCellRight', parent=style_table_cell,
    alignment=TA_RIGHT
)

style_table_cell_bold = ParagraphStyle(
    'TableCellBold', parent=style_table_cell,
    fontName='Helvetica-Bold'
)

style_table_cell_bold_right = ParagraphStyle(
    'TableCellBoldRight', parent=style_table_cell_bold,
    alignment=TA_RIGHT
)

style_label = ParagraphStyle(
    'Label', parent=styles['Normal'],
    fontName='Helvetica', fontSize=9, leading=12,
    textColor=MED_GRAY, spaceAfter=2
)


def fmt_currency(val):
    return "${:,.2f}".format(val)


def orange_rule():
    return HRFlowable(
        width="100%", thickness=2, color=ORANGE,
        spaceAfter=10, spaceBefore=2
    )


def thin_rule():
    return HRFlowable(
        width="100%", thickness=0.5, color=BORDER_GRAY,
        spaceAfter=8, spaceBefore=8
    )


def num_to_word(n):
    words = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five",
             6: "six", 7: "seven", 8: "eight", 9: "nine", 10: "ten"}
    return words.get(n, str(n))


def generate_proposal_pdf(config, logo_path=None, vent_map_path=None):
    """
    Generate a ReDry proposal PDF.
    
    config keys:
        clientCompany, clientContact, clientTitle, clientPhone, clientEmail,
        projectName, projectAddress, projectCity, projectState, projectZip,
        projectSection, wetSF, ratePSF, scanCost, numScans, scanInterval,
        totalVents, proposalDate, validDays
    
    Returns: bytes of the PDF file
    """
    # Parse config
    client_company = config.get("clientCompany", "")
    client_contact = config.get("clientContact", "")
    client_title = config.get("clientTitle", "")
    client_phone = config.get("clientPhone", "")
    client_email = config.get("clientEmail", "")
    
    project_name = config.get("projectName", "Project")
    project_address = config.get("projectAddress", "")
    project_city = config.get("projectCity", "")
    project_state = config.get("projectState", "")
    project_zip = config.get("projectZip", "")
    project_section = config.get("projectSection", "")
    
    wet_sf = int(float(config.get("wetSF", 0)))
    rate_psf = float(config.get("ratePSF", 2.00))
    scan_cost = float(config.get("scanCost", 4500))
    num_scans = int(float(config.get("numScans", 4)))
    scan_interval = config.get("scanInterval", "3")
    total_vents = config.get("totalVents", "")
    
    proposal_date_str = config.get("proposalDate", datetime.now().strftime("%Y-%m-%d"))
    valid_days = int(config.get("validDays", 30))
    
    # Compute values
    full_address_parts = [project_address]
    city_state_zip = ", ".join(filter(None, [project_city, project_state]))
    if project_zip:
        city_state_zip += " " + project_zip if city_state_zip else project_zip
    if city_state_zip:
        full_address_parts.append(city_state_zip)
    full_address = ", ".join(full_address_parts)
    
    vent_system_total = wet_sf * rate_psf
    scan_total = scan_cost * num_scans
    project_total = vent_system_total + scan_total
    deposit = vent_system_total * 0.50
    balance = vent_system_total - deposit
    
    proposal_date = datetime.strptime(proposal_date_str, "%Y-%m-%d")
    proposal_date_display = proposal_date.strftime("%B %d, %Y").replace(" 0", " ")
    valid_through_date = proposal_date + timedelta(days=valid_days)
    valid_through = valid_through_date.strftime("%B %d, %Y").replace(" 0", " ")
    
    mm = proposal_date.strftime("%m")
    dd = proposal_date.strftime("%d")
    proposal_num = f"P-{proposal_date.year}-{mm}{dd}"
    
    # Client TO block
    to_lines = []
    if client_company:
        to_lines.append(client_company)
    if client_contact:
        to_lines.append(client_contact)
    if client_title:
        to_lines.append(client_title)
    if client_phone:
        to_lines.append(client_phone)
    if client_email:
        to_lines.append(client_email)
    to_text = "<br/>".join(to_lines) if to_lines else "[Client / General Contractor]"
    
    # Vent count text
    vent_count_text = ""
    if total_vents:
        vent_count_text = f", calling for an estimated <b>{total_vents} vents</b>"
    
    # Number word
    num_scans_word = num_to_word(num_scans)
    
    # ── Build PDF ──
    buf = io.BytesIO()
    
    class ProposalDocTemplate(BaseDocTemplate):
        def __init__(self, filename, **kwargs):
            super().__init__(filename, **kwargs)
            frame = Frame(
                MARGIN_L, MARGIN_B,
                PAGE_W - MARGIN_L - MARGIN_R,
                PAGE_H - MARGIN_T - MARGIN_B,
                id='normal'
            )
            template = PageTemplate(id='main', frames=frame, onPage=self._draw_page)
            self.addPageTemplates([template])

        def _draw_page(self, canvas_obj, doc):
            canvas_obj.saveState()
            canvas_obj.setStrokeColor(ORANGE)
            canvas_obj.setLineWidth(3)
            canvas_obj.line(0, PAGE_H - 4, PAGE_W, PAGE_H - 4)

            if logo_path and os.path.exists(logo_path):
                from PIL import Image as PILImage
                img = PILImage.open(logo_path)
                img_w, img_h = img.size
                aspect = img_h / img_w
                footer_logo_w = 0.7 * inch
                footer_logo_h = footer_logo_w * aspect
                canvas_obj.drawImage(
                    logo_path,
                    MARGIN_L, 0.28 * inch,
                    width=footer_logo_w, height=footer_logo_h,
                    mask='auto', preserveAspectRatio=True
                )

            canvas_obj.setFont("Helvetica", 7.5)
            canvas_obj.setFillColor(MED_GRAY)
            canvas_obj.drawCentredString(
                PAGE_W / 2, 0.4 * inch,
                "ReDry, LLC  |  re-dry.com  |  info@re-dry.com  |  Confidential and Proprietary"
            )
            canvas_obj.drawRightString(
                PAGE_W - MARGIN_R, 0.4 * inch,
                f"Page {doc.page}"
            )
            canvas_obj.restoreState()

    doc = ProposalDocTemplate(
        buf,
        pagesize=letter,
        leftMargin=MARGIN_L,
        rightMargin=MARGIN_R,
        topMargin=MARGIN_T,
        bottomMargin=MARGIN_B,
        title=f"ReDry Proposal - {project_name}",
        author="ReDry, LLC"
    )

    story = []
    usable_width = PAGE_W - MARGIN_L - MARGIN_R

    # ── HEADER ──
    if logo_path and os.path.exists(logo_path):
        from PIL import Image as PILImage
        img = PILImage.open(logo_path)
        img_w, img_h = img.size
        aspect = img_h / img_w
        logo_img = Image(logo_path, width=2.4 * inch, height=2.4 * inch * aspect)
        
        header_data = [
            [
                logo_img,
                Paragraph(f"Proposal No: {proposal_num}<br/>Date: {proposal_date_display}<br/>Valid Through: {valid_through}",
                          ParagraphStyle('HeaderRight', parent=style_small, alignment=TA_RIGHT, fontSize=9, leading=13, textColor=MED_GRAY))
            ]
        ]
        header_table = Table(header_data, colWidths=[usable_width * 0.6, usable_width * 0.4])
        header_table.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('LEFTPADDING', (0, 0), (-1, -1), 0),
            ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ]))
        story.append(header_table)
        story.append(Spacer(1, 6))
    
    story.append(Paragraph("PROPOSAL", style_title))
    story.append(orange_rule())
    story.append(Spacer(1, 4))

    # ── FROM / TO ──
    from_to_data = [
        [
            Paragraph("FROM", style_label),
            Paragraph("TO", style_label),
            Paragraph("PROJECT", style_label),
        ],
        [
            Paragraph("ReDry, LLC<br/>Adam Capps, Founder<br/>865.771.3848<br/>adam@re-dry.com<br/>re-dry.com",
                       ParagraphStyle('FromVal', parent=style_body, fontSize=9.5, leading=13, spaceAfter=0)),
            Paragraph(to_text,
                       ParagraphStyle('ToVal', parent=style_body, fontSize=9.5, leading=13, spaceAfter=0)),
            Paragraph(f"<b>{project_name}</b><br/>{full_address}<br/>{project_section}<br/>Vent System Lease,<br/>Commissioning, and Monitoring",
                       ParagraphStyle('ProjVal', parent=style_body, fontSize=9.5, leading=13, spaceAfter=0)),
        ]
    ]
    from_to_table = Table(from_to_data, colWidths=[usable_width * 0.33, usable_width * 0.33, usable_width * 0.34])
    from_to_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 8),
        ('TOPPADDING', (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))
    story.append(from_to_table)
    story.append(Spacer(1, 8))
    story.append(thin_rule())

    # ── 1. PROJECT OVERVIEW ──
    story.append(Paragraph("1. PROJECT OVERVIEW", style_section_head))
    story.append(Paragraph(
        f"ReDry, LLC is the manufacturer and lessor of the ReDry 2-Way Vent System, a proprietary solar-powered drying "
        f"system designed to remove trapped moisture from commercial roof insulation without membrane removal or tear-off. "
        f"This proposal covers the lease of the ReDry Vent System and associated performance monitoring services for the "
        f"{project_section} of {project_name}, located at {full_address}.",
        style_body
    ))
    story.append(Paragraph(
        f"A Roof MRI moisture survey identified approximately <b>{wet_sf:,} square feet</b> of wet insulation "
        f"within the project area. ReDry has engineered a vent Placement Map specific to this section based on the "
        f"survey data{vent_count_text}. The Placement Map defines the exact quantity and positioning of all vents and is provided to the "
        f"roofing contractor prior to installation.",
        style_body
    ))
    story.append(Paragraph(
        "The roofing contractor is responsible for installing the 2-Way Vents per the ReDry Installation Specification. "
        "Following installation, ReDry will attach its proprietary ReDry Vent heads to the installed 2-Way Vents, confirm "
        "proper placement, and conduct periodic moisture scans to monitor drying progress and verify system performance.",
        style_body
    ))

    # ── 2. SCOPE OF WORK ──
    story.append(Paragraph("2. SCOPE OF WORK", style_section_head))
    story.append(Paragraph("<b>2.1  ReDry Vent System Furnishing and Commissioning</b>", style_body))
    scope_items_a = [
        "ReDry will furnish all ReDry 2-Way Vents and ReDry Vent heads per the engineered Placement Map.",
        "ReDry will provide the Installation Specification (SPEC-VENT-2026-01, Rev. A) and Placement Map to the roofing contractor.",
        "The <b>roofing contractor</b> is solely responsible for installing and bonding the 2-Way Vents to the roof membrane per the Installation Specification. ReDry is not responsible for the attachment of the 2-Way Vents to the roof surface.",
        "Following 2-Way Vent installation, ReDry will attach the proprietary ReDry Vent heads to each installed 2-Way Vent and confirm that all vents are properly positioned over the cored holes per the Placement Map.",
        "ReDry will complete photo documentation per specification requirements for warranty activation.",
    ]
    for item in scope_items_a:
        story.append(Paragraph(f"&nbsp;&nbsp;&nbsp;&nbsp;\u2022&nbsp;&nbsp;{item}", style_body))
    story.append(Spacer(1, 4))

    story.append(PageBreak())
    story.append(Paragraph("<b>2.2  Moisture Monitoring Program</b>", style_body))
    scope_items_b = [
        f"ReDry will perform <b>{num_scans} ({num_scans_word}) moisture scans</b> at approximately <b>{scan_interval}-month intervals</b> following installation.",
        "Each scan includes a full moisture survey of the treated area using Roof MRI technology and a written report documenting moisture levels and drying progress.",
        "Scan reports will be delivered to the client within a reasonable timeframe following each survey.",
        f"A minimum of {num_scans} scans is required under this agreement regardless of drying timeline.",
    ]
    for item in scope_items_b:
        story.append(Paragraph(f"&nbsp;&nbsp;&nbsp;&nbsp;\u2022&nbsp;&nbsp;{item}", style_body))
    story.append(Spacer(1, 4))

    # 2.3 Vent Retrieval
    story.append(Paragraph("<b>2.3  Vent Retrieval and Performance Criteria</b>", style_body))
    story.append(Paragraph(
        "The ReDry Vents remain the property of ReDry, LLC throughout the lease period. ReDry will retrieve "
        "the vent heads once the insulation in the area served by each vent is confirmed to have reached an "
        "acceptable moisture reading as measured by the Roof MRI PHD (Precise Hydrology Detection) scale. "
        "Drying performance is evaluated using the following criteria:",
        style_body
    ))
    story.append(Spacer(1, 4))

    # PHD Scale table
    phd_data = [
        [Paragraph("PHD Setting", style_table_header),
         Paragraph("Dry", style_table_header),
         Paragraph("Damp", style_table_header),
         Paragraph("Saturated", style_table_header)],
        [Paragraph("Level 3", style_table_cell_bold),
         Paragraph("0 – 35", ParagraphStyle('GreenCell', parent=style_table_cell, textColor=HexColor("#228B22"))),
         Paragraph("35 – 70", ParagraphStyle('OrangeCell', parent=style_table_cell, textColor=ORANGE)),
         Paragraph("70 – 99", ParagraphStyle('RedCell', parent=style_table_cell, textColor=HexColor("#CC0000")))],
        [Paragraph("Level 2", style_table_cell_bold),
         Paragraph("0 – 15", ParagraphStyle('GreenCell2', parent=style_table_cell, textColor=HexColor("#228B22"))),
         Paragraph("15 – 40", ParagraphStyle('OrangeCell2', parent=style_table_cell, textColor=ORANGE)),
         Paragraph("40 – 99", ParagraphStyle('RedCell2', parent=style_table_cell, textColor=HexColor("#CC0000")))],
        [Paragraph("Level 1", style_table_cell_bold),
         Paragraph("0", ParagraphStyle('GreenCell3', parent=style_table_cell, textColor=HexColor("#228B22"))),
         Paragraph("1 – 20", ParagraphStyle('OrangeCell3', parent=style_table_cell, textColor=ORANGE)),
         Paragraph("21 – 99", ParagraphStyle('RedCell3', parent=style_table_cell, textColor=HexColor("#CC0000")))],
    ]
    phd_table = Table(phd_data, colWidths=[usable_width * 0.25] * 4)
    phd_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), NAVY),
        ('TEXTCOLOR', (0, 0), (-1, 0), WHITE),
        ('ALIGN', (1, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 9.5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('GRID', (0, 0), (-1, -1), 0.5, BORDER_GRAY),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [LIGHT_GRAY, WHITE]),
    ]))
    story.append(phd_table)
    story.append(Spacer(1, 6))

    story.append(Paragraph(
        'Vents serving areas that have reached the "Dry" threshold on the applicable PHD setting will be '
        'retrieved by ReDry at the next scheduled site visit. Vents serving areas that remain in the "Damp" '
        'or "Saturated" range will remain in place and continue operating until acceptable readings are achieved.',
        style_body
    ))

    # 2.4 Exclusions
    story.append(Paragraph("<b>2.4  Exclusions</b>", style_body))
    exclusions = [
        "Installation and adhesive bonding of the 2-Way Vents to the roof membrane (by roofing contractor).",
        "Coring of the roof membrane and insulation (by roofing contractor per ReDry Installation Specification).",
        "Roof membrane repair, replacement, or coating (by others).",
        "Structural deck repair or modification.",
        "Access provisions, barricading, or traffic control (by others unless otherwise agreed).",
        f"Any work beyond the {project_section} boundary identified on the Placement Map.",
    ]
    for item in exclusions:
        story.append(Paragraph(f"&nbsp;&nbsp;&nbsp;&nbsp;\u2022&nbsp;&nbsp;{item}", style_body))

    # ── 3. PRICING ──
    story.append(Paragraph("3. PRICING", style_section_head))
    pricing_data = [
        [Paragraph("Description", style_table_header),
         Paragraph("Quantity", style_table_header),
         Paragraph("Unit Rate", style_table_header),
         Paragraph("Total", style_table_header)],
        [Paragraph("ReDry 2-Way Vent System<br/>Lease and Commissioning", style_table_cell),
         Paragraph(f"{wet_sf:,} SF", style_table_cell),
         Paragraph(f"{fmt_currency(rate_psf)} / SF", style_table_cell_right),
         Paragraph(fmt_currency(vent_system_total), style_table_cell_bold_right)],
        [Paragraph("Moisture Monitoring Scan", style_table_cell),
         Paragraph(f"{num_scans} scans", style_table_cell),
         Paragraph(f"{fmt_currency(scan_cost)} / scan", style_table_cell_right),
         Paragraph(fmt_currency(scan_total), style_table_cell_bold_right)],
    ]
    pricing_table = Table(pricing_data, colWidths=[usable_width * 0.40, usable_width * 0.18, usable_width * 0.22, usable_width * 0.20])
    pricing_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), NAVY),
        ('TEXTCOLOR', (0, 0), (-1, 0), WHITE),
        ('ALIGN', (2, 1), (-1, -1), 'RIGHT'),
        ('GRID', (0, 0), (-1, -1), 0.5, BORDER_GRAY),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [LIGHT_GRAY, WHITE]),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
        ('RIGHTPADDING', (0, 0), (-1, -1), 8),
    ]))
    story.append(pricing_table)

    # Total row
    total_data = [
        [Paragraph("", style_table_cell),
         Paragraph("", style_table_cell),
         Paragraph("CONTRACT TOTAL", style_table_cell_bold_right),
         Paragraph(fmt_currency(project_total), style_table_cell_bold_right)]
    ]
    total_table = Table(total_data, colWidths=[usable_width * 0.40, usable_width * 0.18, usable_width * 0.22, usable_width * 0.20])
    total_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), NAVY),
        ('TEXTCOLOR', (0, 0), (-1, -1), WHITE),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
        ('RIGHTPADDING', (0, 0), (-1, -1), 8),
    ]))
    story.append(total_table)
    story.append(Spacer(1, 6))

    # ── 4. PAYMENT TERMS ──
    story.append(PageBreak())
    story.append(Paragraph("4. PAYMENT TERMS", style_section_head))
    story.append(Paragraph("<b>4.1  Vent System Lease and Commissioning</b>", style_body))
    story.append(Paragraph(
        f"A deposit of <b>fifty percent (50%)</b> of the Vent System Lease and Commissioning total ({fmt_currency(deposit)}) "
        f"is due upon execution of this agreement. The remaining <b>fifty percent (50%)</b> ({fmt_currency(balance)}) "
        f"is due within <b>thirty (30) days</b> of completed installation.",
        style_body
    ))
    story.append(Paragraph("<b>4.2  Moisture Monitoring Scans</b>", style_body))
    story.append(Paragraph(
        f"Each moisture scan ({fmt_currency(scan_cost)} per scan) shall be invoiced individually upon delivery of the scan "
        f"report. Payment is due within <b>fifteen (15) days</b> of receipt of the report. "
        f"All {num_scans} scans are guaranteed under this agreement and are non-cancelable.",
        style_body
    ))

    # Payment schedule table
    pay_data = [
        [Paragraph("Payment", style_table_header),
         Paragraph("Amount", style_table_header),
         Paragraph("Due", style_table_header)],
        [Paragraph("Deposit (50% of Vent System Lease)", style_table_cell),
         Paragraph(fmt_currency(deposit), style_table_cell_bold_right),
         Paragraph("Upon contract execution", style_table_cell)],
        [Paragraph("Balance (50% of Vent System Lease)", style_table_cell),
         Paragraph(fmt_currency(balance), style_table_cell_bold_right),
         Paragraph("Net 30 from installation completion", style_table_cell)],
    ]
    for i in range(1, num_scans + 1):
        pay_data.append([
            Paragraph(f"Moisture Scan {i} of {num_scans}", style_table_cell),
            Paragraph(fmt_currency(scan_cost), style_table_cell_bold_right),
            Paragraph("Net 15 from report delivery", style_table_cell),
        ])
    pay_table = Table(pay_data, colWidths=[usable_width * 0.45, usable_width * 0.20, usable_width * 0.35])
    pay_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), NAVY),
        ('TEXTCOLOR', (0, 0), (-1, 0), WHITE),
        ('GRID', (0, 0), (-1, -1), 0.5, BORDER_GRAY),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [LIGHT_GRAY, WHITE]),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
        ('RIGHTPADDING', (0, 0), (-1, -1), 8),
    ]))
    story.append(pay_table)
    story.append(Spacer(1, 6))

    # ── 5. GENERAL CONDITIONS ──
    story.append(Paragraph("5. GENERAL CONDITIONS", style_section_head))
    conditions = [
        ("5.1  Relationship of Parties",
         "ReDry is the manufacturer and lessor of the ReDry Vent System and is not a roofing contractor or subcontractor. "
         "ReDry's role is limited to furnishing the vent system, engineering the Placement Map, attaching the ReDry Vent heads, "
         "confirming vent placement, and providing ongoing moisture monitoring services."),
        ("5.2  Roofing Contractor Responsibilities",
         "The roofing contractor engaged by the client or general contractor is solely responsible for installing and bonding the 2-Way "
         "Vents to the roof membrane in accordance with the ReDry Installation Specification (SPEC-VENT-2026-01, Rev. A). This includes "
         "all coring, adhesive application, membrane flash-in, and related roofing work. ReDry assumes no liability for the quality or "
         "workmanship of the roofing contractor's installation."),
        ("5.3  Installation Specification",
         "All 2-Way Vent installation work shall be performed by the roofing contractor in accordance with ReDry Vent System "
         "Installation Specification SPEC-VENT-2026-01 (Rev. A). A copy of the specification will be provided to the roofing "
         "contractor and is incorporated herein by reference."),
        ("5.4  Placement Map",
         "The ReDry Placement Map is a controlled engineering document generated from project-specific moisture survey data. "
         "The Placement Map shall not be modified by the roofing contractor or any other party without prior written authorization from ReDry."),
        ("5.5  Warranty",
         "System warranty activation requires complete photo documentation, a passed QC inspection per the installation specification, "
         "and installation by a qualified roofing contractor. Warranty terms and coverage details are provided under separate cover upon request."),
        ("5.6  Equipment Ownership and Retrieval",
         "All ReDry Vent heads furnished under this agreement remain the sole property of ReDry, LLC throughout the lease period. "
         "The client shall not remove, relocate, or tamper with the ReDry Vents without prior written authorization. "
         'ReDry will retrieve the vent heads once the served area reaches an acceptable "Dry" reading on the PHD scale as described in Section 2.3. '
         "Upon retrieval, the roofing contractor or client is responsible for sealing the remaining 2-Way Vent penetrations per standard roofing practice."),
        ("5.7  Access and Coordination",
         "The client or general contractor shall provide safe, unobstructed access to the roof area during ReDry's commissioning visit "
         "and each scheduled moisture scan. Scheduling will be coordinated with the client to minimize disruption to building operations."),
        ("5.8  Weather Delays",
         "Installation of 2-Way Vents by the roofing contractor requires dry conditions per adhesive manufacturer specifications. "
         "ReDry's commissioning visit will be scheduled following completion of the contractor's installation. In the event of weather "
         "delays, the project schedule will be adjusted accordingly at no additional cost."),
        ("5.9  Proposal Validity",
         f"This proposal is valid for thirty (30) days from the date of issue ({valid_through}). "
         "Pricing is subject to revision after that date."),
    ]
    for title, text in conditions:
        story.append(Paragraph(f"<b>{title}.</b>&nbsp;&nbsp;{text}", style_body))

    # ── 6. ACCEPTANCE ──
    story.append(Paragraph("6. ACCEPTANCE", style_section_head))
    story.append(Paragraph(
        "By signing below, the authorized representative of the client accepts this proposal and the terms described herein. "
        "This document constitutes a binding agreement between the parties upon execution.",
        style_body
    ))
    story.append(Spacer(1, 12))

    # Signature block
    sig_line = ParagraphStyle('SigLine', parent=style_body, fontSize=9, leading=12, textColor=MED_GRAY, spaceAfter=0)
    sig_data = [
        [Paragraph("<b>ReDry, LLC</b>", style_body), Paragraph("", style_body), Paragraph(f"<b>{client_company or 'Client'}</b>", style_body)],
        [Paragraph("_" * 45, sig_line), Paragraph("", sig_line), Paragraph("_" * 45, sig_line)],
        [Paragraph("Signature", sig_line), Paragraph("", sig_line), Paragraph("Signature", sig_line)],
        [Spacer(1, 6), Spacer(1, 6), Spacer(1, 6)],
        [Paragraph("_" * 45, sig_line), Paragraph("", sig_line), Paragraph("_" * 45, sig_line)],
        [Paragraph("Printed Name / Title", sig_line), Paragraph("", sig_line), Paragraph("Printed Name / Title", sig_line)],
        [Spacer(1, 6), Spacer(1, 6), Spacer(1, 6)],
        [Paragraph("_" * 45, sig_line), Paragraph("", sig_line), Paragraph("_" * 45, sig_line)],
        [Paragraph("Date", sig_line), Paragraph("", sig_line), Paragraph("Date", sig_line)],
    ]
    sig_table = Table(sig_data, colWidths=[usable_width * 0.45, usable_width * 0.10, usable_width * 0.45])
    sig_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'BOTTOM'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
    ]))
    story.append(sig_table)

    # ── PAGE: VENT MAP EXHIBIT ──
    story.append(PageBreak())
    story.append(Paragraph("EXHIBIT A: VENT PLACEMENT MAP", style_title))
    story.append(orange_rule())
    story.append(Paragraph(
        f"{project_name} | {full_address} | {project_section}",
        style_subtitle
    ))
    story.append(Spacer(1, 6))
    story.append(Paragraph(
        f"Wet insulation area: {wet_sf:,} SF. Vent quantity and placement per ReDry engineering. "
        "This map is a controlled document and shall not be modified without written authorization from ReDry.",
        style_body
    ))
    story.append(Spacer(1, 8))

    if vent_map_path and os.path.exists(vent_map_path):
        from PIL import Image as PILImage
        img = PILImage.open(vent_map_path)
        img_w, img_h = img.size
        aspect = img_h / img_w
        display_w = usable_width * 0.9
        display_h = display_w * aspect
        max_h = 5.5 * inch
        if display_h > max_h:
            display_h = max_h
            display_w = display_h / aspect
        vent_map_img = Image(vent_map_path, width=display_w, height=display_h)
        story.append(vent_map_img)
        story.append(Spacer(1, 10))

    story.append(Paragraph(
        "Heat map color key: Green = dry, Yellow = moderate moisture, Orange = elevated moisture, Red = saturated. "
        "Vent icons indicate engineered placement locations.",
        style_small
    ))

    # Build
    doc.build(story)
    buf.seek(0)
    return buf.read()


if __name__ == "__main__":
    # Test with sample data
    config = {
        "clientCompany": "L.D. Tebben Company",
        "clientContact": "Justin Boren",
        "clientTitle": "Senior Technical Estimator",
        "clientPhone": "(512) 663-9226",
        "clientEmail": "jboren@ldtebben.com",
        "projectName": "Crockett High School",
        "projectAddress": "5601 Menchaca Rd",
        "projectCity": "Austin",
        "projectState": "TX",
        "projectZip": "78745",
        "projectSection": "North Section",
        "wetSF": "11600",
        "ratePSF": "2.00",
        "scanCost": "4500",
        "numScans": "4",
        "scanInterval": "3",
        "totalVents": "30",
        "proposalDate": "2026-02-20",
        "validDays": "30",
    }
    
    pdf_bytes = generate_proposal_pdf(
        config,
        logo_path="/home/claude/redry_logo.jpg",
        vent_map_path="/home/claude/vent_map.png"
    )
    
    output_path = "/home/claude/test_proposal_output.pdf"
    with open(output_path, "wb") as f:
        f.write(pdf_bytes)
    print(f"Test PDF generated: {output_path} ({len(pdf_bytes)} bytes)")

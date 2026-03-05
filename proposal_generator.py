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
    waive_scans = config.get("waiveScans", False)
    
    # Tax rate
    tax_rate_val = 0
    try:
        tax_rate_val = float(config.get("taxRateOverride", "") or config.get("taxRate", "") or 0)
    except (ValueError, TypeError):
        pass
    
    # Payment option visibility
    show_option_0 = config.get("showOption0", False)  # Pay in Full
    show_option_1 = config.get("showOption1", True)    # 50/50
    show_option_2 = config.get("showOption2", False)  # Easy Start
    
    # Proposal link for online acceptance
    proposal_id = config.get("_proposalId", "")
    
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
    tax_amount = round(vent_system_total * tax_rate_val, 2)
    vent_subtotal = round(vent_system_total + tax_amount, 2)
    scan_total = 0 if waive_scans else round(scan_cost * num_scans, 2)
    
    # Payment options are based on vent subtotal (vent lease + tax), NOT including scans
    # This matches the web view's calcOptions logic
    # Option 0: Pay in Full (3% discount on vent base, then tax)
    pf_vent_discounted = round(vent_system_total * 0.97, 2)
    pf_tax = round(pf_vent_discounted * tax_rate_val, 2)
    pf_total = round(pf_vent_discounted + pf_tax, 2)
    pf_savings = round(vent_subtotal - pf_total, 2)
    
    # Option 1: Standard 50/50 (no adjustment)
    std_total = vent_subtotal
    std_deposit = round(std_total / 2, 2)
    std_balance = round(std_total - std_deposit, 2)
    
    # Option 2: Easy Start (3% convenience fee on vent base, then tax)
    ez_vent_adjusted = round(vent_system_total * 1.03, 2)
    ez_tax = round(ez_vent_adjusted * tax_rate_val, 2)
    ez_total = round(ez_vent_adjusted + ez_tax, 2)
    ez_deposit = round(ez_total * 0.10, 2)
    ez_install = round(ez_total * 0.40, 2)
    ez_final = round(ez_total - ez_deposit - ez_install, 2)
    
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

    # ── 3. PRICING & PAYMENT OPTIONS ──
    story.append(PageBreak())
    story.append(Paragraph("3. PRICING & PAYMENT OPTIONS", style_section_head))

    # ── Project cost summary (compact) ──
    story.append(Paragraph(
        f"Roof MRI identified <b>{wet_sf:,} SF</b> of wet insulation in the {project_section} of "
        f"{project_name}. Vent system lease: <b>{fmt_currency(rate_psf)}/SF</b>."
        + (f" Rental tax: {tax_rate_val*100:.2f}%." if tax_rate_val > 0 else ""),
        style_body
    ))
    story.append(Spacer(1, 4))

    # Compact cost breakdown - single table
    cost_rows = [
        [Paragraph("ReDry 2-Way Vent System Lease", style_table_cell),
         Paragraph(f"{wet_sf:,} SF × {fmt_currency(rate_psf)}", style_table_cell_right),
         Paragraph(fmt_currency(vent_system_total), style_table_cell_bold_right)],
    ]
    if tax_rate_val > 0:
        cost_rows.append([
            Paragraph(f"Rental Tax ({tax_rate_val*100:.2f}%)", style_table_cell),
            Paragraph("", style_table_cell_right),
            Paragraph(fmt_currency(tax_amount), style_table_cell_bold_right)])

    cost_rows.append([
        Paragraph("VENT SYSTEM TOTAL", ParagraphStyle('TotalLabel', parent=style_table_cell_bold, textColor=WHITE)),
        Paragraph("", style_table_cell_right),
        Paragraph(fmt_currency(vent_subtotal), ParagraphStyle('TotalAmt', parent=style_table_cell_bold_right, textColor=WHITE))])

    cost_table = Table(cost_rows, colWidths=[usable_width * 0.48, usable_width * 0.27, usable_width * 0.25])
    cost_table.setStyle(TableStyle([
        ('GRID', (0, 0), (-1, -2), 0.5, BORDER_GRAY),
        ('BACKGROUND', (0, -1), (-1, -1), NAVY),
        ('TEXTCOLOR', (0, -1), (-1, -1), WHITE),
        ('ROWBACKGROUNDS', (0, 0), (-1, -2), [LIGHT_GRAY, WHITE]),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
        ('RIGHTPADDING', (0, 0), (-1, -1), 8),
    ]))
    story.append(cost_table)
    story.append(Spacer(1, 4))

    # Scan note (one line)
    if waive_scans:
        story.append(Paragraph(
            f"\u2713 <b>Moisture Monitoring Included:</b> {num_scans} scans at {scan_interval}-month intervals "
            f"at no additional charge ({fmt_currency(scan_cost * num_scans)} value).",
            ParagraphStyle('ScanNote', parent=style_body, fontSize=9, textColor=HexColor("#228B22"))
        ))
    else:
        story.append(Paragraph(
            f"<b>Moisture Monitoring:</b> {num_scans} scans at {scan_interval}-month intervals, "
            f"invoiced separately at {fmt_currency(scan_cost)}/scan. Net 15 from report delivery.",
            ParagraphStyle('ScanNote2', parent=style_body, fontSize=9)
        ))
    story.append(Spacer(1, 10))

    # ── Payment Options Grid ──
    story.append(Paragraph("CHOOSE YOUR PAYMENT OPTION", ParagraphStyle('PayHead', parent=style_section_head, fontSize=11, spaceBefore=0, spaceAfter=4)))

    # Styles for grid cells
    opt_head = ParagraphStyle('OH', fontName='Helvetica-Bold', fontSize=10, leading=13, textColor=WHITE, alignment=TA_CENTER)
    opt_price = ParagraphStyle('OP', fontName='Helvetica-Bold', fontSize=16, leading=20, textColor=NAVY, alignment=TA_CENTER)
    opt_desc = ParagraphStyle('OD', fontName='Helvetica', fontSize=8, leading=10, textColor=MED_GRAY, alignment=TA_CENTER)
    opt_label = ParagraphStyle('OL', fontName='Helvetica', fontSize=8.5, leading=11, textColor=DARK_GRAY)
    opt_amt = ParagraphStyle('OA', fontName='Helvetica-Bold', fontSize=8.5, leading=11, textColor=DARK_GRAY, alignment=TA_RIGHT)
    opt_when = ParagraphStyle('OW', fontName='Helvetica', fontSize=7.5, leading=10, textColor=MED_GRAY)

    # Build columns for visible options
    visible = []
    if show_option_0:
        visible.append({
            "name": "Pay in Full",
            "total": pf_total,
            "tag": f"Save {fmt_currency(pf_savings)} (3% discount)",
            "tag_color": HexColor("#228B22"),
            "payments": [
                ("Full Payment", pf_total, "Due upon contract execution"),
            ]
        })
    if show_option_1:
        visible.append({
            "name": "50/50",
            "total": std_total,
            "tag": "Standard terms",
            "tag_color": MED_GRAY,
            "payments": [
                ("Deposit (50%)", std_deposit, "Due upon contract execution"),
                ("Balance (50%)", std_balance, "Due at vent installation"),
            ]
        })
    if show_option_2:
        visible.append({
            "name": "Let\u2019s Get Going!",
            "total": ez_total,
            "tag": "Lowest deposit \u2022 3% convenience fee",
            "tag_color": MED_GRAY,
            "payments": [
                ("Deposit (10%)", ez_deposit, "Due upon contract execution"),
                ("Install Pmt (40%)", ez_install, "Due when ready for install"),
                ("Final Pmt (50%)", ez_final, "Due at vent installation"),
            ]
        })

    if len(visible) == 0:
        visible.append({"name": "50/50", "total": std_total, "tag": "Standard terms",
                         "tag_color": MED_GRAY, "payments": [
                             ("Deposit (50%)", std_deposit, "Due upon contract execution"),
                             ("Balance (50%)", std_balance, "Due at vent installation")]})

    n_opts = len(visible)
    # Calculate column widths
    col_w = usable_width / n_opts

    # Build the grid as a single table with merged-feel rows
    # Row 0: Option names (navy header)
    row_header = [Paragraph(v["name"], opt_head) for v in visible]
    # Row 1: Total price
    row_price = [Paragraph(fmt_currency(v["total"]), opt_price) for v in visible]
    # Row 2: Tag line
    row_tag = [Paragraph(v["tag"], ParagraphStyle('OTag', parent=opt_desc, textColor=v["tag_color"])) for v in visible]

    # Row 3+: Payment schedule rows - need to normalize to max number of payments
    max_pmts = max(len(v["payments"]) for v in visible)

    schedule_rows = []
    for p_idx in range(max_pmts):
        row_lbl = []
        row_amt_val = []
        row_due = []
        for v in visible:
            if p_idx < len(v["payments"]):
                lbl, amt, due = v["payments"][p_idx]
                row_lbl.append(Paragraph(lbl, opt_label))
                row_amt_val.append(Paragraph(fmt_currency(amt), opt_amt))
                row_due.append(Paragraph(due, opt_when))
            else:
                row_lbl.append(Paragraph("", opt_label))
                row_amt_val.append(Paragraph("", opt_amt))
                row_due.append(Paragraph("", opt_when))
        schedule_rows.append(row_lbl)
        schedule_rows.append(row_amt_val)
        schedule_rows.append(row_due)

    all_rows = [row_header, row_price, row_tag] + schedule_rows
    col_widths = [col_w] * n_opts

    grid_table = Table(all_rows, colWidths=col_widths)

    grid_styles = [
        # Header row
        ('BACKGROUND', (0, 0), (-1, 0), NAVY),
        ('TEXTCOLOR', (0, 0), (-1, 0), WHITE),
        ('TOPPADDING', (0, 0), (-1, 0), 10),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 10),
        # Price row
        ('BACKGROUND', (0, 1), (-1, 1), LIGHT_GRAY),
        ('TOPPADDING', (0, 1), (-1, 1), 10),
        ('BOTTOMPADDING', (0, 1), (-1, 1), 4),
        # Tag row
        ('BACKGROUND', (0, 2), (-1, 2), LIGHT_GRAY),
        ('TOPPADDING', (0, 2), (-1, 2), 0),
        ('BOTTOMPADDING', (0, 2), (-1, 2), 8),
        # All cells
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
        ('RIGHTPADDING', (0, 0), (-1, -1), 8),
        # Vertical dividers between options
        ('LINEAFTER', (0, 0), (-2, -1), 1, BORDER_GRAY),
        # Box around entire grid
        ('BOX', (0, 0), (-1, -1), 1.5, NAVY),
        # Line below tag row
        ('LINEBELOW', (0, 2), (-1, 2), 1, BORDER_GRAY),
    ]

    # Style schedule rows: label, amount, due triplets
    for p_idx in range(max_pmts):
        base = 3 + (p_idx * 3)
        # Label row
        grid_styles.append(('TOPPADDING', (0, base), (-1, base), 8))
        grid_styles.append(('BOTTOMPADDING', (0, base), (-1, base), 1))
        # Amount row
        grid_styles.append(('TOPPADDING', (0, base+1), (-1, base+1), 0))
        grid_styles.append(('BOTTOMPADDING', (0, base+1), (-1, base+1), 1))
        # Due row
        grid_styles.append(('TOPPADDING', (0, base+2), (-1, base+2), 0))
        grid_styles.append(('BOTTOMPADDING', (0, base+2), (-1, base+2), 6))
        # Separator between payment groups (except last)
        if p_idx < max_pmts - 1:
            grid_styles.append(('LINEBELOW', (0, base+2), (-1, base+2), 0.5, BORDER_GRAY))

    grid_table.setStyle(TableStyle(grid_styles))
    story.append(grid_table)
    story.append(Spacer(1, 8))

    story.append(Paragraph(
        "Select your preferred option when accepting the proposal online. All payments are processed securely via Stripe.",
        ParagraphStyle('PayFooter', parent=style_small, fontSize=8, alignment=TA_CENTER)
    ))

    # ── 4. GENERAL CONDITIONS ──
    story.append(Paragraph("4. GENERAL CONDITIONS", style_section_head))
    conditions = [
        ("4.1  Relationship of Parties",
         "ReDry is the manufacturer and lessor of the ReDry Vent System and is not a roofing contractor or subcontractor. "
         "ReDry's role is limited to furnishing the vent system, engineering the Placement Map, attaching the ReDry Vent heads, "
         "confirming vent placement, and providing ongoing moisture monitoring services."),
        ("4.2  Roofing Contractor Responsibilities",
         "The roofing contractor engaged by the client or general contractor is solely responsible for installing and bonding the 2-Way "
         "Vents to the roof membrane in accordance with the ReDry Installation Specification (SPEC-VENT-2026-01, Rev. A). This includes "
         "all coring, adhesive application, membrane flash-in, and related roofing work. ReDry assumes no liability for the quality or "
         "workmanship of the roofing contractor's installation."),
        ("4.3  Installation Specification",
         "All 2-Way Vent installation work shall be performed by the roofing contractor in accordance with ReDry Vent System "
         "Installation Specification SPEC-VENT-2026-01 (Rev. A). A copy of the specification will be provided to the roofing "
         "contractor and is incorporated herein by reference."),
        ("4.4  Placement Map",
         "The ReDry Placement Map is a controlled engineering document generated from project-specific moisture survey data. "
         "The Placement Map shall not be modified by the roofing contractor or any other party without prior written authorization from ReDry."),
        ("4.5  Warranty",
         "System warranty activation requires complete photo documentation, a passed QC inspection per the installation specification, "
         "and installation by a qualified roofing contractor. Warranty terms and coverage details are provided under separate cover upon request."),
        ("4.6  Equipment Ownership and Retrieval",
         "All ReDry Vent heads furnished under this agreement remain the sole property of ReDry, LLC throughout the lease period. "
         "The client shall not remove, relocate, or tamper with the ReDry Vents without prior written authorization. "
         'ReDry will retrieve the vent heads once the served area reaches an acceptable "Dry" reading on the PHD scale as described in Section 2.3. '
         "Upon retrieval, the roofing contractor or client is responsible for sealing the remaining 2-Way Vent penetrations per standard roofing practice."),
        ("4.7  Access and Coordination",
         "The client or general contractor shall provide safe, unobstructed access to the roof area during ReDry's commissioning visit "
         "and each scheduled moisture scan. Scheduling will be coordinated with the client to minimize disruption to building operations."),
        ("4.8  Weather Delays",
         "Installation of 2-Way Vents by the roofing contractor requires dry conditions per adhesive manufacturer specifications. "
         "ReDry's commissioning visit will be scheduled following completion of the contractor's installation. In the event of weather "
         "delays, the project schedule will be adjusted accordingly at no additional cost."),
        ("4.9  Proposal Validity",
         f"This proposal is valid for thirty (30) days from the date of issue ({valid_through}). "
         "Pricing is subject to revision after that date."),
    ]
    for title, text in conditions:
        story.append(Paragraph(f"<b>{title}.</b>&nbsp;&nbsp;{text}", style_body))

    # ── 5. ACCEPTANCE ──
    story.append(Paragraph("5. ACCEPTANCE", style_section_head))
    story.append(Paragraph(
        "To accept this proposal, please visit the secure proposal link below. You will be able to review "
        "the full proposal, select your preferred payment option, provide your electronic signature, and "
        "submit your initial payment online.",
        style_body
    ))
    story.append(Spacer(1, 4))
    story.append(Paragraph(
        "Your electronic signature will include your name, date, IP address, and browser information for "
        "verification purposes. Upon signing, both parties will receive a countersigned copy of this agreement.",
        style_body
    ))
    story.append(Spacer(1, 8))

    # Online acceptance box
    if proposal_id:
        proposal_url = f"https://redry-proposal-app.onrender.com/proposal/{proposal_id}"
        
        # Clean CTA with orange button
        cta_small = ParagraphStyle('CTASmall', parent=style_small, alignment=TA_CENTER, fontSize=9, spaceAfter=0, textColor=MED_GRAY)
        btn_text = ParagraphStyle('BtnText', fontName='Helvetica-Bold', fontSize=14, leading=18,
                                   textColor=WHITE, alignment=TA_CENTER, spaceAfter=0)
        
        story.append(Spacer(1, 4))
        story.append(Paragraph("To accept this proposal, review your options and sign electronically:", cta_small))
        story.append(Spacer(1, 8))
        
        # Orange button
        btn_data = [[Paragraph(f'<a href="{proposal_url}" color="#FFFFFF">ACCEPT THIS PROPOSAL</a>', btn_text)]]
        btn_table = Table(btn_data, colWidths=[usable_width * 0.55])
        btn_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), ORANGE),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('TOPPADDING', (0, 0), (-1, -1), 14),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 14),
            ('ROUNDEDCORNERS', [8, 8, 8, 8]),
        ]))
        # Center the button with an outer table
        outer = Table([[btn_table]], colWidths=[usable_width])
        outer.setStyle(TableStyle([('ALIGN', (0, 0), (-1, -1), 'CENTER')]))
        story.append(outer)
        
        story.append(Spacer(1, 8))
        story.append(Paragraph(f"This proposal is valid through {valid_through}.", cta_small))
    else:
        story.append(Paragraph(
            "A secure online link will be provided for proposal acceptance and payment.",
            style_body
        ))

    story.append(Spacer(1, 12))
    story.append(Paragraph(
        "If you have any questions about this proposal, please contact Adam Capps at "
        "adam@re-dry.com or 865.771.3848. We look forward to working with you.",
        style_body
    ))

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


def generate_client_pdf(config, logo_path=None, vent_map_path=None):
    """
    Generate a client-facing ReDry PDF that does NOT show vent system cost.
    Focuses on building confidence in the ReDry system: how it works,
    scope of work, performance criteria, and project details.

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
    num_scans = int(float(config.get("numScans", 4)))
    scan_interval = config.get("scanInterval", "3")
    total_vents = config.get("totalVents", "")

    proposal_id = config.get("_proposalId", "")

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

    class ClientDocTemplate(BaseDocTemplate):
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

    doc = ClientDocTemplate(
        buf,
        pagesize=letter,
        leftMargin=MARGIN_L,
        rightMargin=MARGIN_R,
        topMargin=MARGIN_T,
        bottomMargin=MARGIN_B,
        title=f"ReDry Project Overview - {project_name}",
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

    story.append(Paragraph("PROJECT OVERVIEW", style_title))
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

    # ── 1. THE REDRY SOLUTION ──
    story.append(Paragraph("1. THE REDRY SOLUTION", style_section_head))
    story.append(Paragraph(
        "ReDry, LLC is the manufacturer and lessor of the ReDry 2-Way Vent System, a proprietary solar-powered drying "
        "system designed to remove trapped moisture from commercial roof insulation <b>without membrane removal or tear-off</b>. "
        "The ReDry system preserves the existing roof assembly, eliminates the disruption of a full tear-off, and extends "
        "the functional life of the roof.",
        style_body
    ))
    story.append(Paragraph(
        f"A Roof MRI moisture survey identified approximately <b>{wet_sf:,} square feet</b> of wet insulation "
        f"within the {project_section} of {project_name}, located at {full_address}. "
        f"ReDry has engineered a vent Placement Map specific to this section based on the "
        f"survey data{vent_count_text}.",
        style_body
    ))
    story.append(Spacer(1, 6))

    # ── WHY REDRY – Confidence-building section ──
    story.append(Paragraph("2. WHY REDRY", style_section_head))

    # Benefits table
    benefit_head = ParagraphStyle('BH', fontName='Helvetica-Bold', fontSize=10, leading=13, textColor=NAVY)
    benefit_body = ParagraphStyle('BB', fontName='Helvetica', fontSize=9.5, leading=13, textColor=DARK_GRAY, spaceAfter=4)

    benefits = [
        ("No Tear-Off Required",
         "The ReDry system dries wet insulation in place, eliminating the need for costly and disruptive roof tear-offs. "
         "Your building operations continue uninterrupted while the system works."),
        ("Solar-Powered, Maintenance-Free",
         "Once installed, the ReDry 2-Way Vent System operates entirely on solar energy with no electrical connections, "
         "moving parts, or ongoing maintenance required."),
        ("Proven Drying Technology",
         "The patented 2-Way Vent design actively exchanges moisture-laden air from the insulation layer with dry ambient "
         "air, accelerating the natural drying process and delivering measurable results."),
        ("Data-Driven Performance Monitoring",
         f"ReDry conducts {num_scans} moisture scans at {scan_interval}-month intervals using Roof MRI technology. "
         f"Each scan produces a detailed report documenting moisture levels and drying progress, so you can see the "
         f"results for yourself."),
        ("Engineered for Your Roof",
         "Every Placement Map is custom-engineered based on your project's specific moisture survey data. Vent quantity "
         "and positioning are optimized to maximize drying performance across the affected area."),
        ("Equipment Remains ReDry's Property",
         "The vent heads are leased, not purchased. ReDry retrieves them once the insulation reaches an acceptable dry "
         "reading, leaving no permanent penetrations or equipment on your roof."),
    ]

    for title, desc in benefits:
        benefit_block = [
            [Paragraph(f"\u2713  {title}", benefit_head)],
            [Paragraph(desc, benefit_body)],
        ]
        bt = Table(benefit_block, colWidths=[usable_width - 12])
        bt.setStyle(TableStyle([
            ('LEFTPADDING', (0, 0), (-1, -1), 12),
            ('RIGHTPADDING', (0, 0), (-1, -1), 8),
            ('TOPPADDING', (0, 0), (0, 0), 8),
            ('TOPPADDING', (0, 1), (0, 1), 2),
            ('BOTTOMPADDING', (0, 1), (0, 1), 6),
            ('BACKGROUND', (0, 0), (-1, -1), LIGHT_GRAY),
        ]))
        story.append(bt)
        story.append(Spacer(1, 4))

    # ── 3. HOW IT WORKS ──
    story.append(PageBreak())
    story.append(Paragraph("3. HOW IT WORKS", style_section_head))

    steps = [
        ("Step 1: Moisture Survey",
         "A comprehensive Roof MRI moisture scan identifies and maps all areas of wet insulation within the project area. "
         "This data forms the foundation for the engineered Placement Map."),
        ("Step 2: Placement Map Engineering",
         "ReDry engineers a custom Placement Map that defines the exact quantity and position of every vent, optimized "
         "for maximum drying performance based on the survey data."),
        ("Step 3: 2-Way Vent Installation",
         "The roofing contractor installs the 2-Way Vents per the ReDry Installation Specification. Vents are cored "
         "through the membrane and insulation to access the wet layer beneath."),
        ("Step 4: ReDry Commissioning",
         "ReDry attaches the proprietary vent heads to each installed 2-Way Vent, confirms proper placement against "
         "the Placement Map, and completes photo documentation for warranty activation."),
        ("Step 5: Performance Monitoring",
         f"ReDry returns at approximately {scan_interval}-month intervals to conduct full moisture scans of the "
         f"treated area. Written reports document drying progress and verify system performance."),
        ("Step 6: Vent Retrieval",
         'Once the insulation reaches an acceptable "Dry" reading on the Roof MRI PHD scale, ReDry retrieves the '
         "vent heads. The roofing contractor seals the remaining 2-Way Vent penetrations per standard practice."),
    ]

    step_num_style = ParagraphStyle('StepNum', fontName='Helvetica-Bold', fontSize=10, leading=13, textColor=WHITE, alignment=TA_CENTER)
    step_title_style = ParagraphStyle('StepTitle', fontName='Helvetica-Bold', fontSize=10, leading=13, textColor=NAVY)
    step_body_style = ParagraphStyle('StepBody', fontName='Helvetica', fontSize=9.5, leading=13, textColor=DARK_GRAY, spaceAfter=2)

    for i, (title, desc) in enumerate(steps, 1):
        # Number badge + title + description
        badge_data = [[Paragraph(str(i), step_num_style)]]
        badge = Table(badge_data, colWidths=[0.3 * inch], rowHeights=[0.3 * inch])
        badge.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), ORANGE),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('TOPPADDING', (0, 0), (-1, -1), 2),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
            ('ROUNDEDCORNERS', [4, 4, 4, 4]),
        ]))

        step_data = [[badge, Paragraph(title.split(": ", 1)[1] if ": " in title else title, step_title_style)],
                      ["", Paragraph(desc, step_body_style)]]
        step_table = Table(step_data, colWidths=[0.45 * inch, usable_width - 0.45 * inch])
        step_table.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING', (0, 0), (-1, -1), 0),
            ('RIGHTPADDING', (0, 0), (-1, -1), 4),
            ('TOPPADDING', (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, -1), (-1, -1), 8),
            ('SPAN', (0, 0), (0, 0)),
        ]))
        story.append(step_table)

    # ── 4. PERFORMANCE CRITERIA ──
    story.append(Spacer(1, 4))
    story.append(Paragraph("4. PERFORMANCE CRITERIA", style_section_head))
    story.append(Paragraph(
        "Drying performance is evaluated using the Roof MRI PHD (Precise Hydrology Detection) scale. "
        "The ReDry Vents remain in place and continue operating until the insulation in each vent's service "
        'area reaches an acceptable "Dry" threshold:',
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
         Paragraph("0 \u2013 35", ParagraphStyle('GreenCellC', parent=style_table_cell, textColor=HexColor("#228B22"))),
         Paragraph("35 \u2013 70", ParagraphStyle('OrangeCellC', parent=style_table_cell, textColor=ORANGE)),
         Paragraph("70 \u2013 99", ParagraphStyle('RedCellC', parent=style_table_cell, textColor=HexColor("#CC0000")))],
        [Paragraph("Level 2", style_table_cell_bold),
         Paragraph("0 \u2013 15", ParagraphStyle('GreenCell2C', parent=style_table_cell, textColor=HexColor("#228B22"))),
         Paragraph("15 \u2013 40", ParagraphStyle('OrangeCell2C', parent=style_table_cell, textColor=ORANGE)),
         Paragraph("40 \u2013 99", ParagraphStyle('RedCell2C', parent=style_table_cell, textColor=HexColor("#CC0000")))],
        [Paragraph("Level 1", style_table_cell_bold),
         Paragraph("0", ParagraphStyle('GreenCell3C', parent=style_table_cell, textColor=HexColor("#228B22"))),
         Paragraph("1 \u2013 20", ParagraphStyle('OrangeCell3C', parent=style_table_cell, textColor=ORANGE)),
         Paragraph("21 \u2013 99", ParagraphStyle('RedCell3C', parent=style_table_cell, textColor=HexColor("#CC0000")))],
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
    story.append(Spacer(1, 8))

    story.append(Paragraph(
        'Vents serving areas that have reached the "Dry" threshold on the applicable PHD setting will be '
        'retrieved by ReDry at the next scheduled site visit. Vents serving areas that remain in the "Damp" '
        'or "Saturated" range will remain in place and continue operating until acceptable readings are achieved.',
        style_body
    ))

    # ── 5. PROJECT SCOPE SUMMARY ──
    story.append(Spacer(1, 4))
    story.append(Paragraph("5. PROJECT SCOPE SUMMARY", style_section_head))

    # Summary table - no cost information
    summary_rows = [
        [Paragraph("Item", style_table_header), Paragraph("Detail", style_table_header)],
        [Paragraph("Project", style_table_cell_bold), Paragraph(f"{project_name} \u2013 {project_section}", style_table_cell)],
        [Paragraph("Location", style_table_cell_bold), Paragraph(full_address, style_table_cell)],
        [Paragraph("Affected Area", style_table_cell_bold), Paragraph(f"{wet_sf:,} square feet of wet insulation", style_table_cell)],
    ]
    if total_vents:
        summary_rows.append(
            [Paragraph("Estimated Vents", style_table_cell_bold), Paragraph(f"{total_vents} vents per Placement Map", style_table_cell)]
        )
    summary_rows.append(
        [Paragraph("Monitoring Program", style_table_cell_bold),
         Paragraph(f"{num_scans} moisture scans at {scan_interval}-month intervals", style_table_cell)]
    )
    summary_rows.append(
        [Paragraph("Monitoring Duration", style_table_cell_bold),
         Paragraph(f"Approximately {int(num_scans) * int(scan_interval)} months", style_table_cell)]
    )

    summary_table = Table(summary_rows, colWidths=[usable_width * 0.30, usable_width * 0.70])
    summary_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), NAVY),
        ('TEXTCOLOR', (0, 0), (-1, 0), WHITE),
        ('GRID', (0, 0), (-1, -1), 0.5, BORDER_GRAY),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [LIGHT_GRAY, WHITE]),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
        ('RIGHTPADDING', (0, 0), (-1, -1), 8),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
    ]))
    story.append(summary_table)
    story.append(Spacer(1, 10))

    # ReDry includes list
    story.append(Paragraph("<b>What ReDry Provides:</b>", style_body))
    includes = [
        "All ReDry 2-Way Vents and proprietary vent heads per the engineered Placement Map.",
        "Installation Specification and Placement Map for the roofing contractor.",
        "On-site commissioning: vent head attachment, placement verification, and photo documentation.",
        f"{num_scans} Roof MRI moisture scans with detailed written reports.",
        "Vent retrieval once drying targets are achieved.",
    ]
    for item in includes:
        story.append(Paragraph(f"&nbsp;&nbsp;&nbsp;&nbsp;\u2022&nbsp;&nbsp;{item}", style_body))
    story.append(Spacer(1, 6))

    story.append(Paragraph("<b>Roofing Contractor Responsibilities:</b>", style_body))
    contractor_items = [
        "Installation and adhesive bonding of the 2-Way Vents to the roof membrane per the ReDry Installation Specification.",
        "Coring of the roof membrane and insulation per specification.",
        "Sealing of 2-Way Vent penetrations after vent head retrieval.",
    ]
    for item in contractor_items:
        story.append(Paragraph(f"&nbsp;&nbsp;&nbsp;&nbsp;\u2022&nbsp;&nbsp;{item}", style_body))

    # ── 6. NEXT STEPS ──
    story.append(Spacer(1, 4))
    story.append(Paragraph("6. NEXT STEPS", style_section_head))
    story.append(Paragraph(
        "To move forward with the ReDry solution for your project, review the full proposal at the link below. "
        "You will be able to see all available options, provide your electronic signature, and submit your initial payment online.",
        style_body
    ))
    story.append(Spacer(1, 4))

    # Online acceptance box
    if proposal_id:
        proposal_url = f"https://redry-proposal-app.onrender.com/proposal/{proposal_id}"

        cta_small = ParagraphStyle('CTASmall', parent=style_small, alignment=TA_CENTER, fontSize=9, spaceAfter=0, textColor=MED_GRAY)
        btn_text = ParagraphStyle('BtnText', fontName='Helvetica-Bold', fontSize=14, leading=18,
                                   textColor=WHITE, alignment=TA_CENTER, spaceAfter=0)

        story.append(Spacer(1, 4))
        story.append(Paragraph("View the full proposal, select your payment option, and accept online:", cta_small))
        story.append(Spacer(1, 8))

        # Orange button
        btn_data = [[Paragraph(f'<a href="{proposal_url}" color="#FFFFFF">VIEW FULL PROPOSAL</a>', btn_text)]]
        btn_table = Table(btn_data, colWidths=[usable_width * 0.55])
        btn_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), ORANGE),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('TOPPADDING', (0, 0), (-1, -1), 14),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 14),
            ('ROUNDEDCORNERS', [8, 8, 8, 8]),
        ]))
        outer = Table([[btn_table]], colWidths=[usable_width])
        outer.setStyle(TableStyle([('ALIGN', (0, 0), (-1, -1), 'CENTER')]))
        story.append(outer)

        story.append(Spacer(1, 8))
        story.append(Paragraph(f"This proposal is valid through {valid_through}.", cta_small))
    else:
        story.append(Paragraph(
            "A secure online link will be provided for proposal review and acceptance.",
            style_body
        ))

    story.append(Spacer(1, 12))
    story.append(Paragraph(
        "If you have any questions about this project or the ReDry system, please contact Adam Capps at "
        "adam@re-dry.com or 865.771.3848. We look forward to working with you.",
        style_body
    ))

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
        "taxRate": "0.0925",
        "taxRateOverride": "",
        "waiveScans": False,
        "showOption0": True,
        "showOption1": True,
        "showOption2": True,
        "_proposalId": "test-abc123",
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

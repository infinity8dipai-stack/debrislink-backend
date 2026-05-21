"""
DebrisLink — EPR Compliance Certificate PDF Engine
---------------------------------------------------
Generates a corporate-grade PDF certificate for a completed Trip.

Layout (A4, portrait):
  ┌─────────────────────────────────────────────────┐
  │ [LOGO]              EPR COMPLIANCE CERTIFICATE  │
  │                     Issued under E(C&D)WM Rules │
  │ ───────────────────────────────────────────────│
  │                                                 │
  │   ░░░ VERIFIED ECO-DISPOSAL  (watermark) ░░░   │
  │                                                 │
  │   Certificate No: DLK-YYYYMMDD-<id>             │
  │   Issued To: <Builder Co.>                      │
  │                                                 │
  │   ┌─────────────────────────────────────────┐  │
  │   │ Summary of Disposal                     │  │
  │   │  Trip ID / Site / Lorry / Weight /      │  │
  │   │  Recycling Plant / Disposal Timestamp   │  │
  │   └─────────────────────────────────────────┘  │
  │                                                 │
  │              [ QR PLACEHOLDER ]                 │
  │              scan to verify hash                │
  │                                                 │
  │   Hash: <sha256-hex>                            │
  │   Signed: DebrisLink Systems Pvt. Ltd.          │
  └─────────────────────────────────────────────────┘
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.platypus import Table, TableStyle


# ---------------------------------------------------------------------------
# Visual constants — tweak these for re-branding without touching layout code.
# ---------------------------------------------------------------------------
BRAND_NAVY = colors.HexColor("#0B2545")
BRAND_GREEN = colors.HexColor("#13A858")
BRAND_GREY = colors.HexColor("#5C6770")
WATERMARK_GREY = colors.Color(0.85, 0.88, 0.85, alpha=0.35)

PAGE_W, PAGE_H = A4


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def generate_certificate(trip, output_dir: Path) -> Path:
    """
    Render the EPR Compliance Certificate PDF for `trip`.

    Parameters
    ----------
    trip : database.models.Trip
        Must be in COMPLETED state, with related `builder` and `truck`
        relationships eagerly available and `certificate_hash` populated.
    output_dir : pathlib.Path
        Folder to write the PDF into. Created if missing.

    Returns
    -------
    pathlib.Path
        Absolute path of the rendered PDF.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    filename = f"DLK-{trip.id:06d}-{trip.certificate_hash[:8]}.pdf"
    out_path = output_dir / filename

    c = canvas.Canvas(str(out_path), pagesize=A4)
    c.setTitle(f"EPR Compliance Certificate — Trip {trip.id}")
    c.setAuthor("DebrisLink Systems Pvt. Ltd.")

    _draw_watermark(c)
    _draw_header(c)
    _draw_certificate_meta(c, trip)
    _draw_summary_table(c, trip)
    _draw_qr_placeholder(c, trip.certificate_hash)
    _draw_footer(c, trip.certificate_hash)

    c.showPage()
    c.save()
    return out_path


# ---------------------------------------------------------------------------
# Layout helpers
# ---------------------------------------------------------------------------
def _draw_watermark(c: canvas.Canvas) -> None:
    """Diagonal 'VERIFIED ECO-DISPOSAL' band across the full page background."""
    c.saveState()
    c.translate(PAGE_W / 2, PAGE_H / 2)
    c.rotate(35)
    c.setFillColor(WATERMARK_GREY)
    c.setFont("Helvetica-Bold", 64)
    c.drawCentredString(0, 0, "VERIFIED ECO-DISPOSAL")
    c.setFont("Helvetica", 16)
    c.drawCentredString(0, -40, "DebrisLink • Authorized Recycler Network")
    c.restoreState()


def _draw_header(c: canvas.Canvas) -> None:
    """Logo placeholder + title block."""
    # --- Logo placeholder (boxed area, left side) ---
    logo_x, logo_y = 20 * mm, PAGE_H - 35 * mm
    logo_w, logo_h = 25 * mm, 20 * mm
    c.setStrokeColor(BRAND_NAVY)
    c.setLineWidth(1.0)
    c.rect(logo_x, logo_y, logo_w, logo_h, stroke=1, fill=0)
    c.setFillColor(BRAND_NAVY)
    c.setFont("Helvetica-Bold", 9)
    c.drawCentredString(logo_x + logo_w / 2, logo_y + logo_h / 2 + 2, "DebrisLink")
    c.setFont("Helvetica-Oblique", 7)
    c.drawCentredString(logo_x + logo_w / 2, logo_y + logo_h / 2 - 8, "[ LOGO ]")

    # --- Title block (right of logo) ---
    title_x = 55 * mm
    c.setFillColor(BRAND_NAVY)
    c.setFont("Helvetica-Bold", 18)
    c.drawString(title_x, PAGE_H - 22 * mm, "EPR COMPLIANCE CERTIFICATE")
    c.setFillColor(BRAND_GREY)
    c.setFont("Helvetica", 9)
    c.drawString(
        title_x,
        PAGE_H - 28 * mm,
        "Issued under the Construction & Demolition Waste Management Rules, 2016",
    )
    c.drawString(
        title_x,
        PAGE_H - 32 * mm,
        "Ministry of Environment, Forest and Climate Change — Government of India",
    )

    # Divider line
    c.setStrokeColor(BRAND_GREEN)
    c.setLineWidth(1.5)
    c.line(20 * mm, PAGE_H - 40 * mm, PAGE_W - 20 * mm, PAGE_H - 40 * mm)


def _draw_certificate_meta(c: canvas.Canvas, trip) -> None:
    """Certificate number + 'Issued To' block."""
    builder = trip.builder
    disposal = trip.disposal_timestamp or datetime.now(timezone.utc)
    cert_no = f"DLK-{disposal.strftime('%Y%m%d')}-{trip.id:06d}"

    y = PAGE_H - 55 * mm
    c.setFillColor(BRAND_NAVY)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(20 * mm, y, "Certificate No:")
    c.setFillColor(colors.black)
    c.setFont("Helvetica", 11)
    c.drawString(55 * mm, y, cert_no)

    c.setFillColor(BRAND_NAVY)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(20 * mm, y - 7 * mm, "Issued To:")
    c.setFillColor(colors.black)
    c.setFont("Helvetica", 11)
    c.drawString(55 * mm, y - 7 * mm, builder.company_name)

    c.setFillColor(BRAND_GREY)
    c.setFont("Helvetica", 9)
    c.drawString(55 * mm, y - 12 * mm, builder.site_address[:90])


def _draw_summary_table(c: canvas.Canvas, trip) -> None:
    """Two-column summary of all material disposal facts."""
    builder = trip.builder
    truck = trip.truck

    pickup = (
        trip.pickup_timestamp.strftime("%d %b %Y, %H:%M UTC")
        if trip.pickup_timestamp
        else "—"
    )
    disposal = (
        trip.disposal_timestamp.strftime("%d %b %Y, %H:%M UTC")
        if trip.disposal_timestamp
        else "—"
    )

    rows = [
        ["FIELD", "VALUE"],
        ["Trip ID", f"#{trip.id}"],
        ["Builder / Site", builder.company_name],
        ["Builder Contact", f"{builder.contact_number} • {builder.email}"],
        ["Lorry Reg. No.", truck.lorry_registration_number if truck else "—"],
        ["Driver", truck.driver_name if truck else "—"],
        ["Weight Disposed", f"{trip.weight_tons:.2f} metric tonnes"],
        ["Recycling Plant", trip.recycling_plant_name or "—"],
        ["Pickup Timestamp", pickup],
        ["Disposal Timestamp", disposal],
    ]

    table = Table(rows, colWidths=[55 * mm, 115 * mm])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), BRAND_NAVY),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, 0), 10),
                ("FONTNAME", (0, 1), (0, -1), "Helvetica-Bold"),
                ("FONTSIZE", (0, 1), (-1, -1), 10),
                ("TEXTCOLOR", (0, 1), (0, -1), BRAND_NAVY),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.whitesmoke, colors.white]),
                ("GRID", (0, 0), (-1, -1), 0.4, BRAND_GREY),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    # Render the platypus table onto the canvas at fixed coords.
    table.wrapOn(c, 170 * mm, 100 * mm)
    table.drawOn(c, 20 * mm, PAGE_H - 145 * mm)


def _draw_qr_placeholder(c: canvas.Canvas, cert_hash: str) -> None:
    """
    Mocked QR-code block. In production this would be replaced with a real
    QR encoding the verification URL (e.g. https://debrislink.in/v/<hash>).
    The visual is a styled square with the truncated hash inside.
    """
    qr_size = 32 * mm
    qr_x = PAGE_W - 20 * mm - qr_size
    qr_y = 35 * mm

    # Outer frame
    c.setStrokeColor(BRAND_NAVY)
    c.setLineWidth(1.0)
    c.rect(qr_x, qr_y, qr_size, qr_size, stroke=1, fill=0)

    # Pseudo-QR pattern: 3 corner anchors + diagonal hatching
    anchor = 7 * mm
    c.setFillColor(BRAND_NAVY)
    for ax, ay in [
        (qr_x + 2, qr_y + qr_size - anchor - 2),                      # top-left
        (qr_x + qr_size - anchor - 2, qr_y + qr_size - anchor - 2),   # top-right
        (qr_x + 2, qr_y + 2),                                         # bottom-left
    ]:
        c.rect(ax, ay, anchor, anchor, stroke=0, fill=1)
        c.setFillColor(colors.white)
        c.rect(ax + 1.5, ay + 1.5, anchor - 3, anchor - 3, stroke=0, fill=1)
        c.setFillColor(BRAND_NAVY)
        c.rect(ax + 3, ay + 3, anchor - 6, anchor - 6, stroke=0, fill=1)

    # Caption
    c.setFillColor(BRAND_GREY)
    c.setFont("Helvetica-Oblique", 7)
    c.drawCentredString(qr_x + qr_size / 2, qr_y - 4 * mm, "Scan to verify authenticity")
    c.setFont("Helvetica", 6)
    c.drawCentredString(qr_x + qr_size / 2, qr_y - 7 * mm, cert_hash[:32] + "…")


def _draw_footer(c: canvas.Canvas, cert_hash: str) -> None:
    """Signature block + full hash + issued date."""
    issued_at = datetime.now(timezone.utc).strftime("%d %b %Y, %H:%M UTC")

    c.setStrokeColor(BRAND_GREY)
    c.setLineWidth(0.4)
    c.line(20 * mm, 28 * mm, PAGE_W - 20 * mm, 28 * mm)

    c.setFillColor(BRAND_NAVY)
    c.setFont("Helvetica-Bold", 9)
    c.drawString(20 * mm, 22 * mm, "Authorized Signatory")
    c.setFillColor(BRAND_GREY)
    c.setFont("Helvetica", 8)
    c.drawString(20 * mm, 18 * mm, "DebrisLink Systems Pvt. Ltd.")
    c.drawString(20 * mm, 14 * mm, f"Issued on: {issued_at}")

    c.setFont("Courier", 6)
    c.setFillColor(BRAND_GREY)
    c.drawString(20 * mm, 8 * mm, f"VERIFICATION HASH (SHA-256): {cert_hash}")

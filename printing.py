"""RestroBill — printing.

Two very different print jobs, kept deliberately separate:

* Customer bill — a PDF via reportlab, in A4 or 80mm thermal width.
  Thermal uses a narrow page (226pt ≈ 80mm) with a compact font so it
  fits real receipt printers; A4 is the full-size invoice layout.
* Kitchen Order Ticket (KOT) — plain HTML with a print stylesheet, opened
  in the browser. Kitchens usually print from a browser/shared printer,
  and HTML needs no PDF library, no driver setup, and prints identically
  from any machine on the network. The UI saves the file and opens it;
  the cook hits Ctrl+P (or the ticket auto-prints via onload print()).

All functions here only *write files* — opening the browser / sending to
a printer is the UI's job, so this module stays testable headlessly.
"""

import html
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

BASE = Path(__file__).resolve().parent
BILLS_DIR = BASE / "bills"
KOT_DIR = BASE / "kot"
THERMAL_WIDTH = 80 * mm  # 80mm receipt paper


def _money(value):
    return f"Rs. {value:,.2f}"


def bill_pdf(order, lines, totals, restaurant, path, size="A4"):
    """Write the customer bill PDF. ``size`` is 'A4' or 'thermal'."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if size == "thermal":
        # Thermal paper is a continuous roll, so the page grows with the
        # bill instead of clipping long orders at a fixed height.
        rows = len(lines) + sum(1 for item in lines if item["note"])
        width, height = THERMAL_WIDTH, 240 + rows * 14 + 40
    else:
        width, height = A4
    c = canvas.Canvas(str(path), pagesize=(width, height))
    y = height - 36
    margin = 14 if size == "thermal" else 40

    def line(text, size_pt=10, bold=False, gap=14):
        nonlocal y
        c.setFont("Helvetica-Bold" if bold else "Helvetica", size_pt)
        c.drawString(margin, y, text)
        y -= gap

    def rule():
        nonlocal y
        c.line(margin, y + 4, width - margin, y + 4)
        y -= 10

    line(restaurant, 14 if size == "thermal" else 18, bold=True, gap=18)
    line(f"Bill {order['bill_no']}  |  Table {order['table_name']}"
         if "table_name" in order.keys() else f"Bill {order['bill_no']}",
         9, gap=12)
    line(f"Payment: {order['payment']}", 9, gap=16)
    rule()
    if size == "thermal":
        line(f"{'ITEM':<22}{'QTY':>4}{'AMT':>10}", 9, bold=True)
    else:
        line(f"{'ITEM':<40}{'QTY':>6}{'RATE':>12}{'AMOUNT':>14}", 10, bold=True)
    rule()
    for item in lines:
        amount = item["qty"] * item["price"]
        if size == "thermal":
            name = item["name"][:22]
            line(f"{name:<22}{item['qty']:>4}{amount:>10.2f}", 9, gap=12)
        else:
            name = item["name"][:40]
            line(f"{name:<40}{item['qty']:>6}{item['price']:>12.2f}"
                 f"{amount:>14.2f}", 10, gap=14)
        if item["note"]:
            line(f"  ({item['note']})", 8, gap=12)
    rule()
    label_w = 22 if size == "thermal" else 60
    for label, value in [
            ("Subtotal", totals["subtotal"]),
            ("Discount", -totals["discount"]),
            (f"GST ({order['gst_pct']}%)", totals["gst"])]:
        text = f"{label}: {_money(value)}"
        c.setFont("Helvetica", 10)
        c.drawRightString(width - margin, y, text)
        y -= 14
    c.setFont("Helvetica-Bold", 12)
    c.drawRightString(width - margin, y, f"TOTAL: {_money(totals['total'])}")
    y -= 24
    c.setFont("Helvetica", 9)
    c.drawCentredString(width / 2, y, "Thank you — please visit again!")
    c.showPage()
    c.save()
    return str(path)


def kot_html(order, table_name, lines, restaurant, path):
    """Write the Kitchen Order Ticket as printable HTML.

    Only the lines passed in are rendered — the caller (db.kot_items)
    decides whether that is just the new lines or a full reprint, so a
    reprint is an explicit choice, never an accident.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = "".join(
        f"<tr><td>{line['qty']} &times;</td>"
        f"<td>{html.escape(line['name'])}</td>"
        f"<td>{html.escape(line['note'])}</td></tr>"
        for line in lines)
    doc = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>KOT — Table {html.escape(table_name)}</title>
<style>
  body {{ font-family: sans-serif; max-width: 480px; margin: 24px auto; }}
  h1 {{ font-size: 28px; margin-bottom: 4px; }}
  .meta {{ color: #555; margin-bottom: 16px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 20px; }}
  td {{ border-bottom: 1px solid #ccc; padding: 10px 4px; }}
  td:first-child {{ width: 64px; font-weight: bold; }}
  @media print {{ body {{ margin: 0; }} }}
</style></head>
<body onload="window.print()">
<h1>KOT — Table {html.escape(table_name)}</h1>
<div class="meta">{html.escape(restaurant)} &middot; Order #{order['id']}
&middot; {html.escape(order['opened_at'])}</div>
<table>{rows}</table>
</body></html>"""
    path.write_text(doc, encoding="utf-8")
    return str(path)


def day_summary_pdf(summary, day, restaurant, path):
    """One-page A4 summary: totals, table-wise and item-wise sales."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    width, height = A4
    c = canvas.Canvas(str(path), pagesize=A4)
    y = height - 50
    c.setFont("Helvetica-Bold", 16)
    c.drawString(40, y, f"{restaurant} — Day Summary {day}")
    y -= 28
    c.setFont("Helvetica", 11)
    t = summary["totals"]
    for text in (f"Bills: {t['bills']}",
                 f"Revenue: {_money(t['revenue'])}",
                 f"Discounts given: {_money(t['discount'])}",
                 f"GST collected: {_money(t['gst'])}"):
        c.drawString(40, y, text)
        y -= 16
    y -= 10
    c.setFont("Helvetica-Bold", 12)
    c.drawString(40, y, "Table-wise sales")
    y -= 18
    c.setFont("Helvetica", 11)
    for row in summary["table_wise"]:
        c.drawString(40, y, f"{row['table_name']}: {row['bills']} bills —"
                            f" {_money(row['revenue'])}")
        y -= 15
    y -= 10
    c.setFont("Helvetica-Bold", 12)
    c.drawString(40, y, "Item-wise sales (top first)")
    y -= 18
    c.setFont("Helvetica", 11)
    for row in summary["item_wise"]:
        c.drawString(40, y, f"{row['name']}: {row['qty']} sold —"
                            f" {_money(row['revenue'])}")
        y -= 15
    c.showPage()
    c.save()
    return str(path)

"""RestroBill headless tests — run:  python test_restrobill.py

Covers the money-critical paths (totals, discount caps, merge, split,
billing) plus KOT flagging, day summary, PDF/HTML generation and the
backup round-trip. The UI is smoke-tested separately under Xvfb; these
checks run anywhere with only the standard library + reportlab.
"""

import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import backup as backup_mod
import db
import printing

PASS = 0


def check(name, condition):
    global PASS
    assert condition, f"FAILED: {name}"
    PASS += 1
    print(f"  ok: {name}")


def main():
    tmp = Path(tempfile.mkdtemp())
    db_path = tmp / "test.db"
    db.init_db(db_path)
    conn = db.connect(db_path)

    # --- seeding -------------------------------------------------------
    tables = db.list_tables(conn)
    check("10 tables seeded", len(tables) == 10)
    check("12 starter menu items", len(db.list_menu(conn)) == 12)
    check("default GST is 5", db.get_setting(conn, "gst_pct") == "5")

    # --- menu CRUD -----------------------------------------------------
    mid = db.add_menu_item(conn, "Test Thali", "Main Course", 150, veg=1)
    check("menu add", db.get_menu_item(conn, mid)["price"] == 150)
    db.update_menu_item(conn, mid, price=175)
    check("menu price edit", db.get_menu_item(conn, mid)["price"] == 175)
    try:
        db.add_menu_item(conn, "  ", "Main Course", 10)
        raise AssertionError("blank name should raise")
    except ValueError:
        check("blank menu name refused", True)
    try:
        db.add_menu_item(conn, "Bad", "Main Course", -5)
        raise AssertionError("negative price should raise")
    except ValueError:
        check("negative price refused", True)

    # --- order + add-item merge semantics ------------------------------
    t1, t2 = tables[0]["id"], tables[1]["id"]
    order = db.open_order(conn, t1)
    check("open order created", order["status"] == "open")
    check("open order reused", db.open_order(conn, t1)["id"] == order["id"])
    soup = db.list_menu(conn, query="Tomato Soup")[0]
    db.add_item(conn, order["id"], soup["id"], qty=2)
    db.add_item(conn, order["id"], soup["id"], qty=1)
    lines = db.order_lines(conn, order["id"])
    check("unprinted same-item merges into one line",
          len(lines) == 1 and lines[0]["qty"] == 3)
    # KOT: only unprinted lines, then flagged.
    kot = db.kot_items(conn, order["id"])
    check("KOT picks up unprinted lines", len(kot) == 1)
    db.mark_kot_printed(conn, order["id"], [kot[0]["id"]])
    check("KOT empty after marking printed",
          db.kot_items(conn, order["id"]) == [])
    # Adding more of a printed item creates a FRESH line (never enlarges
    # the line the kitchen already has).
    db.add_item(conn, order["id"], soup["id"], qty=2)
    lines = db.order_lines(conn, order["id"])
    check("printed line not enlarged; fresh line created", len(lines) == 2)
    check("fresh line is unprinted",
          db.kot_items(conn, order["id"])[0]["qty"] == 2)

    # --- totals: discount percent, flat cap, GST on discounted amount --
    totals = db.order_totals(conn, order["id"])
    # 5 soups x 99 = 495, no discount, GST 5% = 24.75
    check("subtotal 495", totals["subtotal"] == 495)
    check("GST 5% on subtotal", totals["gst"] == 24.75)
    check("total 519.75", totals["total"] == 519.75)
    db.set_discount(conn, order["id"], "percent", 10)
    totals = db.order_totals(conn, order["id"])
    check("10% discount = 49.50", totals["discount"] == 49.50)
    # 445.50 x 5% = 22.275 in decimal, but binary float stores it just
    # below the half, so round() gives 22.27 — see the README trade-offs.
    check("GST on discounted amount", totals["gst"] == 22.27)
    check("discounted total", totals["total"] == 467.77)
    db.set_discount(conn, order["id"], "flat", 99999)
    totals = db.order_totals(conn, order["id"])
    check("flat discount capped at subtotal",
          totals["discount"] == 495 and totals["total"] == 0.0)
    db.set_discount(conn, order["id"], "none", 0)
    try:
        db.set_discount(conn, order["id"], "percent", 150)
        raise AssertionError("over-100% discount should raise")
    except ValueError:
        check("over-100% discount refused", True)

    # --- merge tables ---------------------------------------------------
    order2 = db.open_order(conn, t2)
    biryani = db.list_menu(conn, query="Chicken Biryani")[0]
    db.add_item(conn, order2["id"], biryani["id"], qty=1)
    db.merge_tables(conn, t2, t1)
    check("source order marked merged",
          db.get_order(conn, order2["id"])["status"] == "merged")
    check("source table free again",
          db.get_open_order(conn, t2) is None)
    names = [ln["name"] for ln in db.order_lines(conn, order["id"])]
    check("merged line arrived on target", "Chicken Biryani" in names)
    try:
        db.merge_tables(conn, t1, t1)
        raise AssertionError("same-table merge should raise")
    except ValueError:
        check("same-table merge refused", True)
    try:
        db.merge_tables(conn, t2, t1)
        raise AssertionError("empty-source merge should raise")
    except ValueError:
        check("empty-source merge refused", True)

    # --- split ----------------------------------------------------------
    t3 = tables[2]["id"]
    soup_lines = [ln for ln in db.order_lines(conn, order["id"])
                  if ln["name"] == "Tomato Soup"]
    target_id = db.split_items(conn, order["id"],
                               [(soup_lines[0]["id"], 1)], t3)
    src_qty = sum(ln["qty"] for ln in db.order_lines(conn, order["id"])
                  if ln["name"] == "Tomato Soup")
    tgt_qty = sum(ln["qty"] for ln in db.order_lines(conn, target_id)
                  if ln["name"] == "Tomato Soup")
    check("split moved 1 soup to target", tgt_qty == 1 and src_qty == 4)
    try:
        db.split_items(conn, order["id"], [(soup_lines[0]["id"], 99)], t3)
        raise AssertionError("over-split should raise")
    except ValueError:
        check("over-split refused", True)

    # --- billing + numbering -------------------------------------------
    bill_no, totals = db.close_bill(conn, order["id"], "UPI")
    check("first bill number RB-0001", bill_no == "RB-0001")
    check("billed order closed",
          db.get_order(conn, order["id"])["status"] == "closed")
    check("table freed after billing",
          db.get_open_order(conn, t1) is None)
    bill_no2, _ = db.close_bill(conn, target_id, "Cash")
    check("second bill number RB-0002", bill_no2 == "RB-0002")
    empty = db.open_order(conn, t1)
    try:
        db.close_bill(conn, empty["id"])
        raise AssertionError("empty bill should raise")
    except ValueError:
        check("empty order cannot be billed", True)
    db.cancel_order(conn, empty["id"])
    check("cancelled order frees table",
          db.get_open_order(conn, t1) is None)

    # --- delete guard ----------------------------------------------------
    try:
        db.delete_menu_item(conn, soup["id"])
        raise AssertionError("used item delete should raise")
    except ValueError:
        check("used menu item delete refused (deactivate instead)", True)
    db.delete_menu_item(conn, mid)
    check("unused menu item deletes", db.get_menu_item(conn, mid) is None)

    # --- day summary ------------------------------------------------------
    from datetime import date
    summary = db.day_summary(conn, date.today().isoformat())
    check("day summary counts 2 bills", summary["totals"]["bills"] == 2)
    check("day summary revenue matches bills",
          summary["totals"]["revenue"] == round(
              sum(b["total"] for b in summary["bills"]), 2))
    check("top dishes present", len(summary["top_dishes"]) >= 1)
    check("table-wise present", len(summary["table_wise"]) >= 1)

    # --- printing ---------------------------------------------------------
    closed = db.get_order(conn, order["id"])
    row = dict(closed)
    row["table_name"] = "T1"
    lines = db.order_lines(conn, order["id"])
    totals = {"subtotal": closed["subtotal"],
              "discount": closed["discount_amt"], "gst": closed["gst_amt"],
              "total": closed["total"]}
    for size in ("A4", "thermal"):
        pdf = printing.bill_pdf(row, lines, totals, "Test Restaurant",
                                tmp / f"bill-{size}.pdf", size=size)
        check(f"{size} bill PDF valid",
              Path(pdf).read_bytes()[:4] == b"%PDF")
    kot_path = printing.kot_html(closed, "T1", lines, "Test Restaurant",
                                 tmp / "kot.html")
    kot_text = Path(kot_path).read_text()
    check("KOT HTML contains dish name", "Tomato Soup" in kot_text)
    check("KOT HTML auto-prints", "window.print()" in kot_text)
    spdf = printing.day_summary_pdf(summary, date.today().isoformat(),
                                    "Test Restaurant", tmp / "summary.pdf")
    check("day summary PDF valid",
          Path(spdf).read_bytes()[:4] == b"%PDF")

    # --- backup round-trip ------------------------------------------------
    backup_mod.BASE = tmp
    backup_mod.DB_FILE = tmp / "restrobill.db"
    backup_mod.BILLS_DIR = tmp / "bills"
    backup_mod.KOT_DIR = tmp / "kot"
    backup_mod.BACKUP_DIR = tmp / "backups"
    import shutil
    shutil.copy2(db_path, tmp / "restrobill.db")
    zip_path = backup_mod.create_backup()
    check("backup zip created", Path(zip_path).exists())
    with zipfile.ZipFile(zip_path) as zf:
        check("backup contains db", "restrobill.db" in zf.namelist())
    safety = backup_mod.restore_backup(zip_path)
    check("restore returns safety copy", safety is not None
          and Path(safety).exists())

    print(f"\nAll {PASS} checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

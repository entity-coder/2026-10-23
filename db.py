"""RestroBill — SQLite data layer for restaurant table billing.

The model mirrors how a small restaurant actually works:

* ``menu_items`` — the menu card: name, category, price, veg/non-veg tag.
  Items are never hard-deleted once they appear on an order (history must
  stay intact); they are deactivated instead and disappear from ordering.
* ``restaurant_tables`` — T1..T10 by default, more can be added. A table is
  "occupied" exactly when it has an open order; there is no separate flag
  to drift out of sync.
* ``orders`` — one open order per table. It holds the discount, the GST
  rate snapshot, and — once billed — the bill number and final totals.
* ``order_items`` — one line per menu item per order, with the price
  frozen at order time (later menu price edits never rewrite a live bill)
  and a ``kot_printed`` flag so the kitchen ticket only fires new lines.

Money is stored as REAL rupees rounded to 2 decimals. Unlike pharmacy
MRP billing (GST-inclusive), restaurant GST is added ON TOP of the
discounted subtotal: total = (subtotal - discount) + GST on the remainder.
Discount is either a percent of the subtotal or a flat rupee amount
(capped at the subtotal, so a bill can never go negative).

No third-party dependency: only the standard library. The UI layer
(``ui.py``) and the print layer (``printing.py``) both talk to this
module, never to sqlite3 directly.
"""

import sqlite3
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "restrobill.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS restaurant_tables (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,      -- e.g. T1, T2, ...
    status TEXT NOT NULL DEFAULT 'free'  -- kept for display; truth is open orders
);

CREATE TABLE IF NOT EXISTS menu_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'Main Course',
    price REAL NOT NULL CHECK (price >= 0),
    veg INTEGER NOT NULL DEFAULT 1, -- 1 = veg, 0 = non-veg
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    table_id INTEGER NOT NULL REFERENCES restaurant_tables(id),
    status TEXT NOT NULL DEFAULT 'open',  -- open / closed / merged / cancelled
    opened_at TEXT NOT NULL,              -- ISO datetime
    closed_at TEXT,                       -- ISO datetime, set on billing
    discount_type TEXT NOT NULL DEFAULT 'none',  -- none / percent / flat
    discount_value REAL NOT NULL DEFAULT 0,
    gst_pct REAL NOT NULL DEFAULT 5,
    bill_no TEXT UNIQUE,                  -- RB-0001, ... (set on billing)
    payment TEXT NOT NULL DEFAULT 'Cash', -- Cash / UPI / Card
    subtotal REAL, discount_amt REAL, gst_amt REAL, total REAL
);

CREATE TABLE IF NOT EXISTS order_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    menu_item_id INTEGER NOT NULL REFERENCES menu_items(id),
    name TEXT NOT NULL,             -- snapshot at order time
    price REAL NOT NULL,            -- snapshot at order time
    qty INTEGER NOT NULL CHECK (qty > 0),
    note TEXT NOT NULL DEFAULT '',  -- e.g. "less spicy", "no onion"
    kot_printed INTEGER NOT NULL DEFAULT 0
);
"""

# A small starter menu so a new install is usable immediately. Every item
# is editable/deactivatable from the Menu screen; this is demo data, not
# a fixed catalogue.
STARTER_MENU = [
    ("Tomato Soup", "Starters", 99, 1),
    ("Paneer Tikka", "Starters", 199, 1),
    ("Chicken 65", "Starters", 229, 0),
    ("Paneer Butter Masala", "Main Course", 189, 1),
    ("Chicken Curry", "Main Course", 249, 0),
    ("Veg Biryani", "Main Course", 169, 1),
    ("Chicken Biryani", "Main Course", 219, 0),
    ("Butter Naan", "Breads", 40, 1),
    ("Tandoori Roti", "Breads", 25, 1),
    ("Masala Chaas", "Beverages", 49, 1),
    ("Cold Coffee", "Beverages", 99, 1),
    ("Gulab Jamun (2 pc)", "Desserts", 79, 1),
]

CATEGORIES = ["Starters", "Main Course", "Breads", "Beverages", "Desserts"]


def connect(path=DB_PATH):
    """Open a connection with foreign keys on and Row access."""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(path=DB_PATH):
    """Create schema and seed tables + starter menu on a fresh database."""
    conn = connect(path)
    try:
        conn.executescript(SCHEMA)
        if conn.execute("SELECT COUNT(*) FROM restaurant_tables").fetchone()[0] == 0:
            for n in range(1, 11):
                conn.execute("INSERT INTO restaurant_tables (name) VALUES (?)",
                             (f"T{n}",))
        if conn.execute("SELECT COUNT(*) FROM menu_items").fetchone()[0] == 0:
            for name, cat, price, veg in STARTER_MENU:
                conn.execute(
                    "INSERT INTO menu_items (name, category, price, veg)"
                    " VALUES (?, ?, ?, ?)", (name, cat, price, veg))
        conn.execute("INSERT OR IGNORE INTO settings (key, value)"
                     " VALUES ('restaurant_name', 'My Restaurant')")
        conn.execute("INSERT OR IGNORE INTO settings (key, value)"
                     " VALUES ('gst_pct', '5')")
        conn.commit()
    finally:
        conn.close()


# ----------------------------------------------------------------- settings

def get_setting(conn, key, default=""):
    row = conn.execute("SELECT value FROM settings WHERE key = ?",
                       (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn, key, value):
    conn.execute("INSERT INTO settings (key, value) VALUES (?, ?)"
                 " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                 (key, str(value)))
    conn.commit()


# -------------------------------------------------------------------- menu

def add_menu_item(conn, name, category, price, veg=1):
    """Insert a menu item. Raises ValueError on a blank name/negative price."""
    name = name.strip()
    if not name:
        raise ValueError("Item name cannot be blank.")
    if float(price) < 0:
        raise ValueError("Price cannot be negative.")
    cur = conn.execute(
        "INSERT INTO menu_items (name, category, price, veg)"
        " VALUES (?, ?, ?, ?)",
        (name, category.strip() or "Main Course", round(float(price), 2),
         1 if veg else 0))
    conn.commit()
    return cur.lastrowid


def update_menu_item(conn, item_id, **fields):
    """Update only the whitelisted columns in ``fields``."""
    allowed = {"name", "category", "price", "veg", "active"}
    sets, values = [], []
    for key, value in fields.items():
        if key in allowed:
            sets.append(f"{key} = ?")
            values.append(value)
    if not sets:
        return
    values.append(item_id)
    conn.execute(f"UPDATE menu_items SET {', '.join(sets)} WHERE id = ?",
                 values)
    conn.commit()


def delete_menu_item(conn, item_id):
    """Refused if the item appears on any order (history must stay intact);
    deactivate it instead. Unused items delete cleanly."""
    used = conn.execute("SELECT 1 FROM order_items WHERE menu_item_id = ?"
                        " LIMIT 1", (item_id,)).fetchone()
    if used:
        raise ValueError("Item is on past orders — deactivate it instead of"
                         " deleting, so old bills stay readable.")
    conn.execute("DELETE FROM menu_items WHERE id = ?", (item_id,))
    conn.commit()


def list_menu(conn, category=None, veg=None, query="", active_only=True):
    """Menu listing with optional category / veg / text filters."""
    sql = "SELECT * FROM menu_items WHERE 1 = 1"
    args = []
    if active_only:
        sql += " AND active = 1"
    if category and category != "All":
        sql += " AND category = ?"
        args.append(category)
    if veg is not None:
        sql += " AND veg = ?"
        args.append(1 if veg else 0)
    if query.strip():
        sql += " AND name LIKE ?"
        args.append(f"%{query.strip()}%")
    sql += " ORDER BY category, name COLLATE NOCASE"
    return conn.execute(sql, args).fetchall()


def get_menu_item(conn, item_id):
    return conn.execute("SELECT * FROM menu_items WHERE id = ?",
                        (item_id,)).fetchone()


# ------------------------------------------------------------------- tables

def list_tables(conn):
    """All tables with their open order (if any) attached as ``order_id``."""
    return conn.execute(
        "SELECT t.*, (SELECT o.id FROM orders o WHERE o.table_id = t.id"
        " AND o.status = 'open' LIMIT 1) AS order_id FROM restaurant_tables t"
        " ORDER BY t.id").fetchall()


def get_table(conn, table_id):
    return conn.execute("SELECT * FROM restaurant_tables WHERE id = ?",
                        (table_id,)).fetchone()


def add_table(conn, name):
    """Add a table (name must be unique, e.g. T11 or 'Family-1')."""
    name = name.strip()
    if not name:
        raise ValueError("Table name cannot be blank.")
    try:
        cur = conn.execute("INSERT INTO restaurant_tables (name) VALUES (?)",
                           (name,))
    except sqlite3.IntegrityError:
        raise ValueError(f"Table '{name}' already exists.")
    conn.commit()
    return cur.lastrowid


# ------------------------------------------------------------------- orders

def _now():
    return datetime.now().isoformat(timespec="seconds")


def get_open_order(conn, table_id):
    return conn.execute(
        "SELECT * FROM orders WHERE table_id = ? AND status = 'open'"
        " LIMIT 1", (table_id,)).fetchone()


def open_order(conn, table_id, gst_pct=None):
    """Return the table's open order, creating one if needed."""
    existing = get_open_order(conn, table_id)
    if existing:
        return existing
    if gst_pct is None:
        gst_pct = float(get_setting(conn, "gst_pct", "5"))
    cur = conn.execute(
        "INSERT INTO orders (table_id, opened_at, gst_pct)"
        " VALUES (?, ?, ?)", (table_id, _now(), float(gst_pct)))
    conn.commit()
    return conn.execute("SELECT * FROM orders WHERE id = ?",
                        (cur.lastrowid,)).fetchone()


def get_order(conn, order_id):
    return conn.execute("SELECT * FROM orders WHERE id = ?",
                        (order_id,)).fetchone()


def list_open_orders(conn):
    return conn.execute(
        "SELECT o.*, t.name AS table_name FROM orders o"
        " JOIN restaurant_tables t ON t.id = o.table_id"
        " WHERE o.status = 'open' ORDER BY o.opened_at").fetchall()


def order_lines(conn, order_id):
    return conn.execute(
        "SELECT * FROM order_items WHERE order_id = ? ORDER BY id",
        (order_id,)).fetchall()


def add_item(conn, order_id, menu_item_id, qty=1, note=""):
    """Add a menu item to an open order.

    If an *unprinted* line for the same item already exists, the quantity
    is bumped on that line instead of adding a duplicate — so the next
    KOT prints the item once with the full quantity, and the kitchen is
    never fired twice for the same dish. A line already sent to the
    kitchen (kot_printed = 1) is never silently enlarged: a fresh line is
    created so the next KOT carries exactly the new quantity.
    """
    order = get_order(conn, order_id)
    if order is None or order["status"] != "open":
        raise ValueError("Can only add items to an open order.")
    item = get_menu_item(conn, menu_item_id)
    if item is None or not item["active"]:
        raise ValueError("Menu item not found or deactivated.")
    qty = int(qty)
    if qty <= 0:
        raise ValueError("Quantity must be positive.")
    existing = conn.execute(
        "SELECT * FROM order_items WHERE order_id = ? AND menu_item_id = ?"
        " AND kot_printed = 0 LIMIT 1", (order_id, menu_item_id)).fetchone()
    if existing:
        conn.execute("UPDATE order_items SET qty = qty + ? WHERE id = ?",
                     (qty, existing["id"]))
    else:
        conn.execute(
            "INSERT INTO order_items (order_id, menu_item_id, name, price,"
            " qty, note) VALUES (?, ?, ?, ?, ?, ?)",
            (order_id, menu_item_id, item["name"], item["price"], qty,
             note.strip()))
    conn.commit()


def update_item_qty(conn, line_id, qty):
    """Set a line's quantity; qty <= 0 removes the line."""
    if int(qty) <= 0:
        conn.execute("DELETE FROM order_items WHERE id = ?", (line_id,))
    else:
        conn.execute("UPDATE order_items SET qty = ? WHERE id = ?",
                     (int(qty), line_id))
    conn.commit()


def remove_item(conn, line_id):
    conn.execute("DELETE FROM order_items WHERE id = ?", (line_id,))
    conn.commit()


def order_totals(conn, order_id):
    """Return subtotal / discount / GST / total for an order.

    GST is computed on the discounted subtotal. Discount is capped at the
    subtotal so the total can never go negative.
    """
    order = get_order(conn, order_id)
    lines = order_lines(conn, order_id)
    subtotal = round(sum(l["qty"] * l["price"] for l in lines), 2)
    if order["discount_type"] == "percent":
        discount = round(subtotal * float(order["discount_value"]) / 100, 2)
    elif order["discount_type"] == "flat":
        discount = round(min(float(order["discount_value"]), subtotal), 2)
    else:
        discount = 0.0
    taxable = round(subtotal - discount, 2)
    gst = round(taxable * float(order["gst_pct"]) / 100, 2)
    return {"subtotal": subtotal, "discount": discount, "taxable": taxable,
            "gst": gst, "total": round(taxable + gst, 2),
            "lines": len(lines),
            "qty": sum(l["qty"] for l in lines)}


def set_discount(conn, order_id, discount_type, discount_value):
    """discount_type: 'none' / 'percent' / 'flat'."""
    if discount_type not in ("none", "percent", "flat"):
        raise ValueError("Unknown discount type.")
    value = float(discount_value)
    if value < 0 or (discount_type == "percent" and value > 100):
        raise ValueError("Discount out of range.")
    conn.execute("UPDATE orders SET discount_type = ?, discount_value = ?"
                 " WHERE id = ?", (discount_type, value, order_id))
    conn.commit()


def set_gst(conn, order_id, gst_pct):
    if float(gst_pct) < 0:
        raise ValueError("GST cannot be negative.")
    conn.execute("UPDATE orders SET gst_pct = ? WHERE id = ?",
                 (float(gst_pct), order_id))
    conn.commit()


# ---------------------------------------------------------------------- KOT

def kot_items(conn, order_id, only_unprinted=True):
    """Lines for the kitchen ticket. By default only lines not yet printed,
    so re-printing a KOT never duplicates a dish in the kitchen."""
    sql = "SELECT * FROM order_items WHERE order_id = ?"
    if only_unprinted:
        sql += " AND kot_printed = 0"
    return conn.execute(sql + " ORDER BY id", (order_id,)).fetchall()


def mark_kot_printed(conn, order_id, line_ids):
    """Flag the given lines as sent to the kitchen."""
    for line_id in line_ids:
        conn.execute("UPDATE order_items SET kot_printed = 1"
                     " WHERE id = ? AND order_id = ?", (line_id, order_id))
    conn.commit()


# ------------------------------------------------------------- merge/split

def merge_tables(conn, source_table_id, target_table_id):
    """Move the source table's open order into the target table's order.

    Lines merge into an existing *unprinted* target line for the same
    item (same rule as add_item); already-printed lines move across
    untouched so the kitchen ticket history stays truthful. The source
    order is marked 'merged' and its table frees up. Returns the target
    order id. Raises ValueError if the source has no open order or the
    tables are the same.
    """
    if source_table_id == target_table_id:
        raise ValueError("Source and target tables must be different.")
    source = get_open_order(conn, source_table_id)
    if source is None:
        raise ValueError("Source table has no running order to merge.")
    target = open_order(conn, target_table_id)
    for line in order_lines(conn, source["id"]):
        if not line["kot_printed"]:
            twin = conn.execute(
                "SELECT * FROM order_items WHERE order_id = ?"
                " AND menu_item_id = ? AND kot_printed = 0 LIMIT 1",
                (target["id"], line["menu_item_id"])).fetchone()
            if twin:
                conn.execute("UPDATE order_items SET qty = qty + ?"
                             " WHERE id = ?", (line["qty"], twin["id"]))
                conn.execute("DELETE FROM order_items WHERE id = ?",
                             (line["id"],))
                continue
        conn.execute("UPDATE order_items SET order_id = ? WHERE id = ?",
                     (target["id"], line["id"]))
    conn.execute("UPDATE orders SET status = 'merged' WHERE id = ?",
                 (source["id"],))
    conn.commit()
    return target["id"]


def split_items(conn, order_id, moves, target_table_id):
    """Move quantities of lines to another table's open order.

    ``moves``: list of (order_item_id, qty). Moving a line's full quantity
    moves the line itself (keeping its kot_printed flag); a partial move
    creates a fresh unprinted line on the target. Partial moves out of an
    already-printed line keep the moved portion unprinted, so the kitchen
    gets a ticket for the new table — the honest limitation is the reverse:
    a *fully* moved printed line keeps kot_printed = 1, so staff must tell
    the kitchen the table changed (the README says so plainly).
    Returns the target order id.
    """
    order = get_order(conn, order_id)
    if order is None or order["status"] != "open":
        raise ValueError("Can only split an open order.")
    target = open_order(conn, target_table_id)
    if target["id"] == order_id:
        raise ValueError("Split target must be a different table.")
    for line_id, qty in moves:
        line = conn.execute("SELECT * FROM order_items WHERE id = ?"
                            " AND order_id = ?", (line_id, order_id)).fetchone()
        if line is None:
            raise ValueError("Order line not found.")
        qty = int(qty)
        if qty <= 0 or qty > line["qty"]:
            raise ValueError(f"Cannot move {qty} of '{line['name']}'"
                             f" (have {line['qty']}).")
        if qty == line["qty"]:
            conn.execute("UPDATE order_items SET order_id = ? WHERE id = ?",
                         (target["id"], line_id))
        else:
            conn.execute("UPDATE order_items SET qty = qty - ? WHERE id = ?",
                         (qty, line_id))
            conn.execute(
                "INSERT INTO order_items (order_id, menu_item_id, name,"
                " price, qty, note, kot_printed) VALUES (?, ?, ?, ?, ?, ?, 0)",
                (target["id"], line["menu_item_id"], line["name"],
                 line["price"], qty, line["note"]))
    conn.commit()
    return target["id"]


def cancel_order(conn, order_id):
    """Void an open order (and its lines) — the table frees up."""
    order = get_order(conn, order_id)
    if order is None or order["status"] != "open":
        raise ValueError("Only an open order can be cancelled.")
    conn.execute("DELETE FROM order_items WHERE order_id = ?", (order_id,))
    conn.execute("UPDATE orders SET status = 'cancelled' WHERE id = ?",
                 (order_id,))
    conn.commit()


# ------------------------------------------------------------------- billing

def next_bill_no(conn):
    row = conn.execute("SELECT bill_no FROM orders WHERE bill_no IS NOT NULL"
                       " ORDER BY id DESC LIMIT 1").fetchone()
    n = int(row["bill_no"].split("-")[1]) + 1 if row else 1
    return f"RB-{n:04d}"


def close_bill(conn, order_id, payment="Cash"):
    """Bill an open order: freeze totals, assign a bill number, free the
    table. Returns (bill_no, totals dict). Empty orders cannot be billed.
    """
    order = get_order(conn, order_id)
    if order is None or order["status"] != "open":
        raise ValueError("Only an open order can be billed.")
    totals = order_totals(conn, order_id)
    if totals["lines"] == 0:
        raise ValueError("Cannot bill an empty order — add items first.")
    bill_no = next_bill_no(conn)
    conn.execute(
        "UPDATE orders SET status = 'closed', closed_at = ?, bill_no = ?,"
        " payment = ?, subtotal = ?, discount_amt = ?, gst_amt = ?,"
        " total = ? WHERE id = ?",
        (_now(), bill_no, payment, totals["subtotal"], totals["discount"],
         totals["gst"], totals["total"], order_id))
    conn.commit()
    return bill_no, totals


def recent_bills(conn, limit=20):
    return conn.execute(
        "SELECT o.*, t.name AS table_name FROM orders o"
        " JOIN restaurant_tables t ON t.id = o.table_id"
        " WHERE o.status = 'closed' ORDER BY o.id DESC LIMIT ?",
        (limit,)).fetchall()


# -------------------------------------------------------------- day summary

def day_summary(conn, day):
    """Sales for one ISO date: totals + table-wise + item-wise + top dishes.

    ``day`` matches the *billing* date (closed_at), not the order-opening
    date — an order opened late at night and billed after midnight counts
    on the day the money came in.
    """
    bills = conn.execute(
        "SELECT o.*, t.name AS table_name FROM orders o"
        " JOIN restaurant_tables t ON t.id = o.table_id"
        " WHERE o.status = 'closed' AND substr(o.closed_at, 1, 10) = ?"
        " ORDER BY o.id", (day,)).fetchall()
    bill_ids = [b["id"] for b in bills]
    table_wise, item_wise = [], []
    if bill_ids:
        ph = ",".join("?" * len(bill_ids))
        table_wise = conn.execute(
            f"SELECT t.name AS table_name, COUNT(*) AS bills,"
            f" ROUND(SUM(o.total), 2) AS revenue FROM orders o"
            f" JOIN restaurant_tables t ON t.id = o.table_id"
            f" WHERE o.id IN ({ph}) GROUP BY t.name ORDER BY revenue DESC",
            bill_ids).fetchall()
        item_wise = conn.execute(
            f"SELECT name, SUM(qty) AS qty,"
            f" ROUND(SUM(qty * price), 2) AS revenue FROM order_items"
            f" WHERE order_id IN ({ph}) GROUP BY name ORDER BY qty DESC",
            bill_ids).fetchall()
    totals = {
        "bills": len(bills),
        "revenue": round(sum(b["total"] for b in bills), 2),
        "discount": round(sum(b["discount_amt"] or 0 for b in bills), 2),
        "gst": round(sum(b["gst_amt"] or 0 for b in bills), 2),
    }
    return {"bills": bills, "totals": totals, "table_wise": table_wise,
            "item_wise": item_wise, "top_dishes": item_wise[:5]}


def dashboard_stats(conn, today):
    """One dict for the dashboard strip."""
    summary = day_summary(conn, today)
    open_orders = list_open_orders(conn)
    running = sum(order_totals(conn, o["id"])["subtotal"] for o in open_orders)
    top = summary["top_dishes"][0]["name"] if summary["top_dishes"] else "—"
    return {
        "today_bills": summary["totals"]["bills"],
        "today_revenue": summary["totals"]["revenue"],
        "open_tables": len(open_orders),
        "running_subtotal": round(running, 2),
        "top_dish": top,
    }

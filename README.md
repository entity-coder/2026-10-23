# RestroBill — Restaurant Table Billing

A desktop billing app for small restaurants, cafes, and food stalls:
table-wise running orders, Kitchen Order Tickets (KOT), merge/split
tables, discounts + GST, printable customer bills, and a day summary.

Built with Python + CustomTkinter + SQLite. Fully offline — no account,
no server, no internet needed after installation.

## Features

- **Menu management** — items with category, price, and veg/non-veg tag.
  A 12-item starter menu is seeded on first run; everything is editable.
  Items that appear on any order can't be deleted (old bills must stay
  readable) — hide them with the Show/Hide toggle instead.
- **Table view** — T1–T10 by default (add more in Settings). A table is
  "occupied" exactly when it has a running order, so the board can never
  disagree with the data. Click a card to open its order.
- **Live ordering** — add items with category / veg / search filters and
  an optional note per item ("less spicy"). Quantities change with +/−.
- **KOT printing** — "Print KOT" sends only the *new* lines to the
  kitchen as a printable HTML ticket (opened in the browser, auto-prints
  with Ctrl+P / onload print). Reprinting never duplicates a dish:
  adding more of an already-sent dish creates a fresh line, so the next
  KOT carries exactly the new quantity.
- **Billing** — percent or flat discount (flat is capped at the
  subtotal, so a bill can never go negative), GST added on the
  discounted subtotal (default 5%, editable per order and in Settings),
  Cash/UPI/Card. Bills are numbered RB-0001, RB-0002, … and saved as
  PDFs in `bills/` — A4 or 80mm thermal (thermal pages grow with the
  bill, since receipt paper is a continuous roll).
- **Merge / split tables** — merge moves one table's whole order into
  another's and frees the source table; split moves chosen quantities
  of chosen lines to another table.
- **Day summary** — bills, revenue, discounts, and GST for any date,
  plus table-wise sales, item-wise sales, and top dishes. Exportable
  as a PDF. A bill counts on the day it was *billed*, not the day the
  order was opened.
- **Dashboard** — today's bills/revenue, running tables, running
  subtotal, and today's top dish.
- **Backup & restore** — timestamped `.zip` of the database + bills +
  KOTs. Restore first makes a safety copy, so a bad restore can never
  destroy live data.
- **Windows .exe** — run `build_exe.bat` once on the restaurant PC
  (PyInstaller); the `.exe` lands in `dist\RestroBill\`. Your data
  (`restrobill.db`, `bills/`, `kot/`, `backups/`) lives next to the
  `.exe` and is never touched by a rebuild.

## How to run

```bash
pip install -r requirements.txt
python main.py
```

Run the tests (headless, no window needed):

```bash
python test_restrobill.py
```

## Files

| File | What it does |
| --- | --- |
| `main.py` | Entry point: creates the DB, launches the UI |
| `db.py` | All SQLite logic: menu, tables, orders, KOT flags, merge/split, billing, day summary |
| `ui.py` | CustomTkinter screens (Dashboard, Tables, Order, Menu, Billing, Day Summary, Backup, Settings) |
| `printing.py` | Customer bill PDFs (A4 + 80mm thermal), KOT HTML, day-summary PDF |
| `backup.py` | Timestamped `.zip` backup / restore with safety copy |
| `test_restrobill.py` | 49 headless checks for the data + printing layers |

## No AI angle — deliberately

Like the other shop apps in this series (RepairDesk, PharmaBill), this
one has no AI feature on purpose: billing, tax, and kitchen tickets
must be deterministic and auditable. A hallucinated price, tax rate, or
KOT line is worse than no AI at all. The honest AI extension would be
a *suggestion* layer that never touches money — e.g. "customers who
ordered X also ordered Y" from past bills — clearly labelled as a
suggestion the staff can ignore.

## Interview notes

**What is this project?**
RestroBill is an offline restaurant POS for a single counter: one
 staffer runs tables, the kitchen gets tickets, and the day ends with a
sales summary. It is the restaurant entry in a series of sellable
offline-first desktop apps for small Indian businesses.

**Why Python + CustomTkinter + SQLite?**
The target user is a small restaurant owner on a Windows PC, not a
developer. CustomTkinter gives a modern-looking desktop UI from one
pip install with no build step; SQLite needs no server process, no
login, and no maintenance — the whole business lives in one
`restrobill.db` file that backup/restore can zip. A web app would need
hosting and would die without internet; this one keeps working during
an outage, which is exactly when a restaurant is busiest.

**Why is a table "occupied" only via its open order?**
A separate `status` flag on the table would eventually disagree with
reality (a crash between billing and flag-clearing, a forgotten update
in one code path). Deriving occupancy from "does an open order exist"
makes the wrong state unrepresentable — there is one source of truth.
(The `status` column still exists in the schema for display, but no
logic reads it; that leftover is documented here rather than hidden.)

**Why is the KOT HTML but the bill a PDF?**
The two print jobs have different audiences. A bill is a financial
document: it must look identical everywhere and survive as a file, so
PDF (reportlab) is right. A KOT goes to a kitchen, often a shared or
network printer, where a browser print is the lowest-friction path and
HTML needs no PDF viewer or driver setup. The KOT HTML auto-prints on
load, so the flow is one click: Print KOT → browser → printer.

**Why do KOT lines never merge into printed lines?**
If adding more of a dish silently enlarged a line the kitchen already
received, the kitchen would cook the old quantity and the extra plates
would never be fired. RestroBill instead creates a fresh unprinted
line, so every KOT contains exactly the quantities the kitchen has not
seen yet. Same rule inside merge: only unprinted lines merge into an
existing unprinted target line.

**Why is GST added on top here, when PharmaBill backs it out?**
Because the pricing model is different: pharmacy goods are sold at
MRP, which already includes GST, so tax must be backed out of the
price. Restaurant menus quote pre-tax prices and tax is added at the
bill — adding GST on the discounted subtotal matches the printed bill
customers expect. Copying PharmaBill's formula here would have been a
real bug, not code reuse.

**Main trade-offs / limitations**
- **Float money rounding.** Totals use Python `round()` on floats, the
  same as the sibling shop apps. One known consequence: 445.50 × 5% is
  22.275 in decimal, but binary float stores it just below the half,
  so it rounds to 22.27, not 22.28 — a 1-paise difference from a
  hand calculation in that edge case. For a shop app this is
  acceptable and documented; a high-volume system should switch money
  to integer paise or `Decimal` throughout.
- **Split of an already-printed line.** A fully moved printed line
  keeps its `kot_printed` flag on the new table, so the kitchen is not
  re-fired — staff must tell the kitchen the table changed. Partial
  splits do create a fresh unprinted line, so the kitchen does get a
  ticket for the moved portion.
- **Single machine.** SQLite means one counter, one database. Two
  counters would need a shared database (e.g. Postgres) and conflict
  rules for two staff editing the same table — deliberately out of
  scope for a first version aimed at small single-counter shops.
- **No printer auto-send.** Bills are saved as PDFs and KOTs as HTML
  files; the app never talks to a printer driver directly. That keeps
  it working on any Windows printer setup, at the cost of one extra
  click to print.
- **Not compiled on the author's machine.** This repo ships source +
  `build_exe.bat`; the Windows `.exe` is built on the restaurant PC
  itself. The data and print layers are covered by 49 automated
  checks, and every screen was smoke-tested under Xvfb.

**How would I extend it?**
In order: (1) integer-paise money to kill the rounding edge case;
(2) table reservation / waiting-list screen; (3) the suggestion-only
AI upsell noted above, fed by past bills and never touching totals;
(4) a second-counter mode with a shared database; (5) UPI QR on the
bill PDF so customers can pay by scanning the printed bill.

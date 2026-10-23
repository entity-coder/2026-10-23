"""RestroBill — CustomTkinter UI.

One window, left sidebar navigation, one screen at a time:

* Dashboard — today's sales, running orders, top dish, shortcuts.
* Tables — one card per table (free / occupied, items, subtotal).
  Clicking a card opens that table's running order.
* Order — add items live (category + search + veg filter), change
  quantities, print the KOT (new lines only), bill, merge, split, void.
* Menu — add / edit / deactivate menu items.
* Billing — pick a running order, apply discount + GST, generate the
  customer bill PDF (A4 or 80mm thermal) and close the order.
* Day Summary — table-wise and item-wise sales for any date, top dishes.
* Backup — timestamped .zip backup / restore with a safety copy.
* Settings — restaurant name, default GST %, add tables.

The UI never touches sqlite3 directly — every action goes through db.py
— and never prints anything itself: bills are PDFs written by
printing.py (the path is shown so staff can open/print them), and KOTs
are HTML files opened in the browser for printing.
"""

import tkinter as tk
import webbrowser
from datetime import date
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

import backup as backup_mod
import db
import printing

BASE = Path(__file__).resolve().parent


class RestroBillApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("RestroBill — Restaurant Billing")
        self.geometry("1100x700")
        self.conn = db.connect()
        self.current_order_id = None
        self.editing_item_id = None

        self.sidebar = ctk.CTkFrame(self, width=170, corner_radius=0)
        self.sidebar.pack(side="left", fill="y")
        ctk.CTkLabel(self.sidebar, text="RestroBill",
                     font=ctk.CTkFont(size=20, weight="bold")).pack(pady=16)
        self.nav_buttons = {}
        for name in ("Dashboard", "Tables", "Menu", "Billing",
                     "Day Summary", "Backup", "Settings"):
            btn = ctk.CTkButton(self.sidebar, text=name,
                                command=lambda n=name: self.show_screen(n))
            btn.pack(fill="x", padx=10, pady=4)
            self.nav_buttons[name] = btn

        self.content = ctk.CTkScrollableFrame(self)
        self.content.pack(side="right", fill="both", expand=True)
        self.show_screen("Dashboard")

    # ------------------------------------------------------------ helpers

    def clear(self):
        for widget in self.content.winfo_children():
            widget.destroy()

    def heading(self, text):
        ctk.CTkLabel(self.content, text=text,
                     font=ctk.CTkFont(size=20, weight="bold")).pack(
                         anchor="w", padx=16, pady=(12, 8))

    def show_screen(self, name):
        self.clear()
        {"Dashboard": self.screen_dashboard,
         "Tables": self.screen_tables,
         "Menu": self.screen_menu,
         "Billing": self.screen_billing,
         "Day Summary": self.screen_summary,
         "Backup": self.screen_backup,
         "Settings": self.screen_settings}[name]()

    def _err(self, exc):
        messagebox.showerror("RestroBill", str(exc))

    def _info(self, text):
        messagebox.showinfo("RestroBill", text)

    # ---------------------------------------------------------- dashboard

    def screen_dashboard(self):
        self.heading("Dashboard")
        stats = db.dashboard_stats(self.conn, date.today().isoformat())
        cards = [
            ("Today's bills", str(stats["today_bills"])),
            ("Today's revenue", f"Rs. {stats['today_revenue']:,.2f}"),
            ("Tables running", str(stats["open_tables"])),
            ("Running subtotal", f"Rs. {stats['running_subtotal']:,.2f}"),
            ("Top dish today", stats["top_dish"]),
        ]
        row = ctk.CTkFrame(self.content)
        row.pack(fill="x", padx=16, pady=8)
        for label, value in cards:
            box = ctk.CTkFrame(row)
            box.pack(side="left", fill="x", expand=True, padx=4, pady=8)
            ctk.CTkLabel(box, text=label, font=ctk.CTkFont(size=12)).pack()
            ctk.CTkLabel(box, text=value,
                         font=ctk.CTkFont(size=15, weight="bold")).pack()
        ctk.CTkButton(self.content, text="Open Tables view",
                      command=lambda: self.show_screen("Tables")).pack(
                          anchor="w", padx=16, pady=4)
        ctk.CTkButton(self.content, text="New menu item",
                      command=lambda: self.show_screen("Menu")).pack(
                          anchor="w", padx=16, pady=4)
        ctk.CTkLabel(self.content, text="Running orders",
                     font=ctk.CTkFont(size=15, weight="bold")).pack(
                         anchor="w", padx=16, pady=(16, 4))
        for order in db.list_open_orders(self.conn):
            totals = db.order_totals(self.conn, order["id"])
            ctk.CTkButton(
                self.content,
                text=f"{order['table_name']}: {totals['qty']} items —"
                     f" Rs. {totals['subtotal']:,.2f}",
                command=lambda oid=order["id"]: self.screen_order(oid)).pack(
                    anchor="w", padx=16, pady=2)

    # ------------------------------------------------------------- tables

    def screen_tables(self):
        self.heading("Tables — click a card to open its order")
        grid = ctk.CTkFrame(self.content)
        grid.pack(fill="both", expand=True, padx=16, pady=8)
        for i, table in enumerate(db.list_tables(self.conn)):
            order = db.get_open_order(self.conn, table["id"])
            if order:
                totals = db.order_totals(self.conn, order["id"])
                text = (f"{table['name']}\nOCCUPIED\n"
                        f"{totals['qty']} items\n"
                        f"Rs. {totals['subtotal']:,.2f}")
                color = "#b45309"
            else:
                text = f"{table['name']}\nFREE"
                color = "#15803d"
            btn = ctk.CTkButton(grid, text=text, fg_color=color,
                                height=90, width=140,
                                command=lambda tid=table["id"]:
                                self._open_table(tid))
            btn.grid(row=i // 5, column=i % 5, padx=6, pady=6)

    def _open_table(self, table_id):
        try:
            order = db.open_order(self.conn, table_id)
            self.screen_order(order["id"])
        except Exception as exc:  # noqa: BLE001 — show any failure to staff
            self._err(exc)

    # -------------------------------------------------------------- order

    def screen_order(self, order_id):
        self.clear()
        self.current_order_id = order_id
        order = db.get_order(self.conn, order_id)
        table = db.get_table(self.conn, order["table_id"])
        self.heading(f"Table {table['name']} — running order")
        totals = db.order_totals(self.conn, order_id)

        lines_box = ctk.CTkFrame(self.content)
        lines_box.pack(fill="x", padx=16, pady=8)
        lines = db.order_lines(self.conn, order_id)
        if not lines:
            ctk.CTkLabel(lines_box, text="No items yet — add from the menu"
                         " below.").pack(padx=8, pady=8)
        for item in lines:
            row = ctk.CTkFrame(lines_box)
            row.pack(fill="x", padx=8, pady=2)
            tag = "🟢" if self._line_is_veg(item) else "🔴"
            kot = " · KOT sent" if item["kot_printed"] else " · not on KOT"
            ctk.CTkLabel(row, text=f"{tag} {item['name']} —"
                         f" Rs. {item['price']:,.2f} × {item['qty']} ="
                         f" Rs. {item['qty'] * item['price']:,.2f}{kot}",
                         width=520, anchor="w").pack(side="left")
            ctk.CTkButton(row, text="−", width=32,
                          command=lambda lid=item["id"], q=item["qty"] - 1:
                          self._change_qty(lid, q)).pack(side="left", padx=2)
            ctk.CTkButton(row, text="+", width=32,
                          command=lambda lid=item["id"], q=item["qty"] + 1:
                          self._change_qty(lid, q)).pack(side="left", padx=2)
            ctk.CTkButton(row, text="Remove", width=70,
                          command=lambda lid=item["id"]:
                          self._remove_line(lid)).pack(side="left", padx=2)
        ctk.CTkLabel(self.content,
                     text=f"Subtotal: Rs. {totals['subtotal']:,.2f}  |  "
                     f"Items: {totals['qty']}",
                     font=ctk.CTkFont(size=14, weight="bold")).pack(
                         anchor="w", padx=16, pady=8)

        actions = ctk.CTkFrame(self.content)
        actions.pack(fill="x", padx=16, pady=4)
        ctk.CTkButton(actions, text="Print KOT (new items)",
                      command=lambda: self._print_kot(order_id, False)).pack(
                          side="left", padx=4)
        ctk.CTkButton(actions, text="Reprint full KOT",
                      command=lambda: self._print_kot(order_id, True)).pack(
                          side="left", padx=4)
        ctk.CTkButton(actions, text="Bill this table",
                      command=lambda: self._goto_billing(order_id)).pack(
                          side="left", padx=4)
        ctk.CTkButton(actions, text="Merge table…",
                      command=lambda: self.screen_merge(order_id)).pack(
                          side="left", padx=4)
        ctk.CTkButton(actions, text="Split items…",
                      command=lambda: self.screen_split(order_id)).pack(
                          side="left", padx=4)
        ctk.CTkButton(actions, text="Void order",
                      fg_color="#b91c1c",
                      command=lambda: self._void_order(order_id)).pack(
                          side="left", padx=4)
        ctk.CTkButton(actions, text="← Tables",
                      command=lambda: self.show_screen("Tables")).pack(
                          side="left", padx=4)

        # --- add items -------------------------------------------------
        ctk.CTkLabel(self.content, text="Add items",
                     font=ctk.CTkFont(size=15, weight="bold")).pack(
                         anchor="w", padx=16, pady=(16, 4))
        filters = ctk.CTkFrame(self.content)
        filters.pack(fill="x", padx=16)
        self.cat_var = tk.StringVar(value="All")
        self.veg_var = tk.StringVar(value="All")
        self.search_var = tk.StringVar()
        ctk.CTkOptionMenu(filters, variable=self.cat_var,
                          values=["All"] + db.CATEGORIES,
                          command=lambda _: self._refresh_add_items()
                          ).pack(side="left", padx=4)
        ctk.CTkOptionMenu(filters, variable=self.veg_var,
                          values=["All", "Veg", "Non-veg"],
                          command=lambda _: self._refresh_add_items()
                          ).pack(side="left", padx=4)
        search = ctk.CTkEntry(filters, textvariable=self.search_var,
                              placeholder_text="Search menu…", width=220)
        search.pack(side="left", padx=4)
        search.bind("<KeyRelease>", lambda _: self._refresh_add_items())
        self.note_var = tk.StringVar()
        ctk.CTkEntry(self.content, textvariable=self.note_var,
                     placeholder_text="Note for next added item"
                     " (e.g. less spicy) — optional",
                     width=420).pack(anchor="w", padx=16, pady=6)
        self.add_box = ctk.CTkFrame(self.content)
        self.add_box.pack(fill="x", padx=16, pady=4)
        self._refresh_add_items()

    def _line_is_veg(self, line):
        item = db.get_menu_item(self.conn, line["menu_item_id"])
        return bool(item and item["veg"])

    def _refresh_add_items(self):
        if not hasattr(self, "add_box"):
            return
        for widget in self.add_box.winfo_children():
            widget.destroy()
        veg = {"All": None, "Veg": True, "Non-veg": False}[self.veg_var.get()]
        items = db.list_menu(self.conn, category=self.cat_var.get(), veg=veg,
                             query=self.search_var.get())
        for item in items:
            tag = "🟢" if item["veg"] else "🔴"
            ctk.CTkButton(
                self.add_box,
                text=f"{tag} {item['name']} — Rs. {item['price']:,.2f}",
                anchor="w",
                command=lambda mid=item["id"]: self._add_to_order(mid)
                ).pack(fill="x", padx=8, pady=2)

    def _add_to_order(self, menu_item_id):
        try:
            db.add_item(self.conn, self.current_order_id, menu_item_id,
                        note=self.note_var.get())
            self.note_var.set("")
            self.screen_order(self.current_order_id)
        except Exception as exc:  # noqa: BLE001
            self._err(exc)

    def _change_qty(self, line_id, qty):
        try:
            db.update_item_qty(self.conn, line_id, qty)
            self.screen_order(self.current_order_id)
        except Exception as exc:  # noqa: BLE001
            self._err(exc)

    def _remove_line(self, line_id):
        try:
            db.remove_item(self.conn, line_id)
            self.screen_order(self.current_order_id)
        except Exception as exc:  # noqa: BLE001
            self._err(exc)

    def _print_kot(self, order_id, full):
        try:
            lines = db.kot_items(self.conn, order_id,
                                 only_unprinted=not full)
            if not lines:
                self._info("Nothing new for the kitchen — all items are"
                           " already on a KOT. Use 'Reprint full KOT' to"
                           " print everything again.")
                return
            order = db.get_order(self.conn, order_id)
            table = db.get_table(self.conn, order["table_id"])
            restaurant = db.get_setting(self.conn, "restaurant_name",
                                        "My Restaurant")
            path = printing.kot_html(
                order, table["name"], lines, restaurant,
                printing.KOT_DIR / f"KOT-{table['name']}-"
                f"{order_id}-{len(lines)}items.html")
            db.mark_kot_printed(self.conn, order_id,
                                [line["id"] for line in lines])
            webbrowser.open(f"file://{path}")
            self._info(f"KOT opened in the browser for printing:\n{path}")
            self.screen_order(order_id)
        except Exception as exc:  # noqa: BLE001
            self._err(exc)

    def _goto_billing(self, order_id):
        self.show_screen("Billing")
        # Preselect this order in the billing dropdown, if present.
        if hasattr(self, "bill_order_var"):
            for order in db.list_open_orders(self.conn):
                if order["id"] == order_id:
                    self.bill_order_var.set(
                        f"{order['table_name']} (order #{order_id})")
                    self._refresh_bill_totals()
                    break

    def _void_order(self, order_id):
        if not messagebox.askyesno("Void order",
                                   "Void this running order? All its"
                                   " items will be discarded."):
            return
        try:
            db.cancel_order(self.conn, order_id)
            self.show_screen("Tables")
        except Exception as exc:  # noqa: BLE001
            self._err(exc)

    # --------------------------------------------------------- merge/split

    def screen_merge(self, order_id):
        self.clear()
        order = db.get_order(self.conn, order_id)
        table = db.get_table(self.conn, order["table_id"])
        self.heading(f"Merge Table {table['name']} into another table")
        ctk.CTkLabel(self.content, text="All items move to the target"
                     " table's order (created if needed). The source table"
                     " then frees up.").pack(anchor="w", padx=16)
        targets = [t for t in db.list_tables(self.conn)
                   if t["id"] != table["id"]]
        self.merge_var = tk.StringVar(
            value=targets[0]["name"] if targets else "")
        ctk.CTkOptionMenu(self.content, variable=self.merge_var,
                          values=[t["name"] for t in targets]).pack(
                              anchor="w", padx=16, pady=12)

        def do_merge():
            try:
                target = next(t for t in targets
                              if t["name"] == self.merge_var.get())
                db.merge_tables(self.conn, table["id"], target["id"])
                self._info(f"Table {table['name']} merged into"
                           f" {target['name']}.")
                self.show_screen("Tables")
            except Exception as exc:  # noqa: BLE001
                self._err(exc)

        ctk.CTkButton(self.content, text="Merge",
                      command=do_merge).pack(anchor="w", padx=16)
        ctk.CTkButton(self.content, text="← Back to order",
                      command=lambda: self.screen_order(order_id)).pack(
                          anchor="w", padx=16, pady=8)

    def screen_split(self, order_id):
        self.clear()
        order = db.get_order(self.conn, order_id)
        table = db.get_table(self.conn, order["table_id"])
        self.heading(f"Split items from Table {table['name']}")
        ctk.CTkLabel(self.content, text="Enter how many of each line to"
                     " move, pick the target table, then Move. Moving a"
                     " line's full quantity moves the line itself; a"
                     " partial move creates a fresh line on the target"
                     " (and a fresh KOT entry).").pack(anchor="w", padx=16)
        targets = [t for t in db.list_tables(self.conn)
                   if t["id"] != table["id"]]
        self.split_target_var = tk.StringVar(
            value=targets[0]["name"] if targets else "")
        ctk.CTkOptionMenu(self.content, variable=self.split_target_var,
                          values=[t["name"] for t in targets]).pack(
                              anchor="w", padx=16, pady=8)
        self.split_entries = {}
        for item in db.order_lines(self.conn, order_id):
            row = ctk.CTkFrame(self.content)
            row.pack(fill="x", padx=16, pady=2)
            ctk.CTkLabel(row, text=f"{item['name']} (have {item['qty']})",
                         width=380, anchor="w").pack(side="left")
            entry = ctk.CTkEntry(row, width=60, placeholder_text="0")
            entry.pack(side="left")
            self.split_entries[item["id"]] = entry

        def do_split():
            try:
                moves = []
                for line_id, entry in self.split_entries.items():
                    raw = entry.get().strip()
                    if raw:
                        moves.append((line_id, int(raw)))
                if not moves:
                    self._info("Enter a quantity for at least one line.")
                    return
                target = next(t for t in targets
                              if t["name"] == self.split_target_var.get())
                db.split_items(self.conn, order_id, moves, target["id"])
                self._info(f"Moved items to {target['name']}.")
                self.screen_order(order_id)
            except Exception as exc:  # noqa: BLE001
                self._err(exc)

        ctk.CTkButton(self.content, text="Move selected",
                      command=do_split).pack(anchor="w", padx=16, pady=8)
        ctk.CTkButton(self.content, text="← Back to order",
                      command=lambda: self.screen_order(order_id)).pack(
                          anchor="w", padx=16)

    # ---------------------------------------------------------------- menu

    def screen_menu(self):
        self.heading("Menu management")
        form = ctk.CTkFrame(self.content)
        form.pack(fill="x", padx=16, pady=8)
        self.m_name = ctk.CTkEntry(form, placeholder_text="Item name",
                                   width=240)
        self.m_name.pack(side="left", padx=4, pady=8)
        self.m_cat = tk.StringVar(value="Main Course")
        ctk.CTkOptionMenu(form, variable=self.m_cat,
                          values=db.CATEGORIES).pack(side="left", padx=4)
        self.m_price = ctk.CTkEntry(form, placeholder_text="Price",
                                    width=100)
        self.m_price.pack(side="left", padx=4)
        self.m_veg = tk.BooleanVar(value=True)
        ctk.CTkCheckBox(form, text="Veg", variable=self.m_veg).pack(
            side="left", padx=4)
        ctk.CTkButton(form, text="Add / Update",
                      command=self._save_menu_item).pack(side="left", padx=4)
        ctk.CTkButton(form, text="Clear form",
                      command=self._clear_menu_form).pack(side="left", padx=4)

        self.menu_filter = tk.StringVar(value="All")
        ctk.CTkOptionMenu(self.content, variable=self.menu_filter,
                          values=["All"] + db.CATEGORIES,
                          command=lambda _: self._refresh_menu_list()
                          ).pack(anchor="w", padx=16, pady=4)
        self.menu_box = ctk.CTkFrame(self.content)
        self.menu_box.pack(fill="x", padx=16, pady=4)
        self._refresh_menu_list()

    def _clear_menu_form(self):
        self.editing_item_id = None
        self.m_name.delete(0, "end")
        self.m_price.delete(0, "end")
        self.m_cat.set("Main Course")
        self.m_veg.set(True)

    def _save_menu_item(self):
        try:
            if self.editing_item_id:
                db.update_menu_item(
                    self.conn, self.editing_item_id,
                    name=self.m_name.get(), category=self.m_cat.get(),
                    price=float(self.m_price.get()), veg=self.m_veg.get())
            else:
                db.add_menu_item(self.conn, self.m_name.get(),
                                 self.m_cat.get(),
                                 float(self.m_price.get()),
                                 self.m_veg.get())
            self._clear_menu_form()
            self._refresh_menu_list()
        except Exception as exc:  # noqa: BLE001
            self._err(exc)

    def _refresh_menu_list(self):
        for widget in self.menu_box.winfo_children():
            widget.destroy()
        items = db.list_menu(self.conn, category=self.menu_filter.get(),
                             active_only=False)
        for item in items:
            row = ctk.CTkFrame(self.menu_box)
            row.pack(fill="x", padx=8, pady=2)
            tag = "🟢" if item["veg"] else "🔴"
            state = "" if item["active"] else " (hidden)"
            ctk.CTkLabel(row, text=f"{tag} {item['name']} —"
                         f" {item['category']} — Rs. {item['price']:,.2f}"
                         f"{state}", width=480, anchor="w").pack(side="left")
            ctk.CTkButton(row, text="Edit", width=60,
                          command=lambda it=item: self._edit_menu_item(it)
                          ).pack(side="left", padx=2)
            toggle = "Hide" if item["active"] else "Show"
            ctk.CTkButton(row, text=toggle, width=60,
                          command=lambda iid=item["id"], a=item["active"]:
                          self._toggle_menu_item(iid, a)).pack(side="left",
                                                               padx=2)
            ctk.CTkButton(row, text="Delete", width=60,
                          fg_color="#b91c1c",
                          command=lambda iid=item["id"]:
                          self._delete_menu_item(iid)).pack(side="left",
                                                            padx=2)

    def _edit_menu_item(self, item):
        self.editing_item_id = item["id"]
        self.m_name.delete(0, "end")
        self.m_name.insert(0, item["name"])
        self.m_cat.set(item["category"])
        self.m_price.delete(0, "end")
        self.m_price.insert(0, str(item["price"]))
        self.m_veg.set(bool(item["veg"]))

    def _toggle_menu_item(self, item_id, currently_active):
        db.update_menu_item(self.conn, item_id,
                            active=0 if currently_active else 1)
        self._refresh_menu_list()

    def _delete_menu_item(self, item_id):
        try:
            db.delete_menu_item(self.conn, item_id)
            self._refresh_menu_list()
        except Exception as exc:  # noqa: BLE001
            self._err(exc)

    # -------------------------------------------------------------- billing

    def screen_billing(self):
        self.heading("Billing — close a running order")
        orders = db.list_open_orders(self.conn)
        if not orders:
            ctk.CTkLabel(self.content, text="No running orders right now."
                         " Open a table from the Tables screen first."
                         ).pack(anchor="w", padx=16)
            return
        labels = [f"{o['table_name']} (order #{o['id']})" for o in orders]
        self.bill_order_var = tk.StringVar(value=labels[0])
        self.bill_order_map = {label: o["id"]
                               for label, o in zip(labels, orders)}
        ctk.CTkOptionMenu(self.content, variable=self.bill_order_var,
                          values=labels,
                          command=lambda _: self._refresh_bill_totals()
                          ).pack(anchor="w", padx=16, pady=8)
        form = ctk.CTkFrame(self.content)
        form.pack(fill="x", padx=16, pady=8)
        self.bill_disc_type = tk.StringVar(value="none")
        ctk.CTkOptionMenu(form, variable=self.bill_disc_type,
                          values=["none", "percent", "flat"],
                          command=lambda _: self._apply_bill_settings()
                          ).pack(side="left", padx=4)
        self.bill_disc_val = ctk.CTkEntry(form, placeholder_text="Discount"
                                          " value", width=110)
        self.bill_disc_val.pack(side="left", padx=4)
        self.bill_gst = ctk.CTkEntry(form, placeholder_text="GST %",
                                    width=80)
        self.bill_gst.pack(side="left", padx=4)
        ctk.CTkButton(form, text="Apply",
                      command=self._apply_bill_settings).pack(side="left",
                                                             padx=4)
        self.bill_pay = tk.StringVar(value="Cash")
        ctk.CTkOptionMenu(self.content, variable=self.bill_pay,
                          values=["Cash", "UPI", "Card"]).pack(anchor="w",
                                                              padx=16)
        self.bill_size = tk.StringVar(value="A4")
        ctk.CTkOptionMenu(self.content, variable=self.bill_size,
                          values=["A4", "thermal"]).pack(anchor="w", padx=16,
                                                        pady=8)
        self.bill_totals_label = ctk.CTkLabel(
            self.content, text="", font=ctk.CTkFont(size=14, weight="bold"))
        self.bill_totals_label.pack(anchor="w", padx=16, pady=8)
        ctk.CTkButton(self.content, text="Generate bill & close order",
                      command=self._generate_bill).pack(anchor="w", padx=16)
        self.bill_path_label = ctk.CTkLabel(self.content, text="",
                                            wraplength=700)
        self.bill_path_label.pack(anchor="w", padx=16, pady=8)
        self._refresh_bill_totals()

    def _selected_bill_order(self):
        return self.bill_order_map[self.bill_order_var.get()]

    def _apply_bill_settings(self):
        try:
            order_id = self._selected_bill_order()
            raw = self.bill_disc_val.get().strip()
            db.set_discount(self.conn, order_id, self.bill_disc_type.get(),
                            float(raw) if raw else 0)
            gst_raw = self.bill_gst.get().strip()
            if gst_raw:
                db.set_gst(self.conn, order_id, float(gst_raw))
            self._refresh_bill_totals()
        except Exception as exc:  # noqa: BLE001
            self._err(exc)

    def _refresh_bill_totals(self):
        try:
            order_id = self._selected_bill_order()
            order = db.get_order(self.conn, order_id)
            if self.bill_gst.get().strip() == "":
                self.bill_gst.delete(0, "end")
                self.bill_gst.insert(0, str(order["gst_pct"]))
            if self.bill_disc_val.get().strip() == "":
                self.bill_disc_val.delete(0, "end")
                self.bill_disc_val.insert(
                    0, str(order["discount_value"])
                    if order["discount_type"] != "none" else "")
                self.bill_disc_type.set(order["discount_type"])
            totals = db.order_totals(self.conn, order_id)
            self.bill_totals_label.configure(
                text=f"Subtotal Rs. {totals['subtotal']:,.2f}  −  Discount"
                f" Rs. {totals['discount']:,.2f}  +  GST"
                f" Rs. {totals['gst']:,.2f}  =  TOTAL"
                f" Rs. {totals['total']:,.2f}")
        except Exception:  # noqa: BLE001 — billing screen may be stale
            pass

    def _generate_bill(self):
        try:
            order_id = self._selected_bill_order()
            self._apply_bill_settings()
            order = db.get_order(self.conn, order_id)
            table = db.get_table(self.conn, order["table_id"])
            lines = db.order_lines(self.conn, order_id)
            bill_no, totals = db.close_bill(self.conn, order_id,
                                            self.bill_pay.get())
            closed = db.get_order(self.conn, order_id)
            row = dict(closed)
            row["table_name"] = table["name"]
            restaurant = db.get_setting(self.conn, "restaurant_name",
                                        "My Restaurant")
            path = printing.bill_pdf(
                row, lines, totals, restaurant,
                printing.BILLS_DIR / f"{bill_no}.pdf",
                size=self.bill_size.get())
            self.bill_path_label.configure(
                text=f"Bill {bill_no} saved — TOTAL"
                f" Rs. {totals['total']:,.2f}\n{path}\n"
                f"Table {table['name']} is now free.")
            self._info(f"Bill {bill_no} generated.\nTotal:"
                       f" Rs. {totals['total']:,.2f}\n{path}")
        except Exception as exc:  # noqa: BLE001
            self._err(exc)

    # ---------------------------------------------------------- day summary

    def screen_summary(self):
        self.heading("Day Summary")
        row = ctk.CTkFrame(self.content)
        row.pack(fill="x", padx=16, pady=8)
        self.sum_date = ctk.CTkEntry(row, width=140)
        self.sum_date.insert(0, date.today().isoformat())
        self.sum_date.pack(side="left", padx=4)
        ctk.CTkButton(row, text="Show",
                      command=self._refresh_summary).pack(side="left")
        ctk.CTkButton(row, text="Export PDF",
                      command=self._export_summary).pack(side="left", padx=4)
        self.sum_box = ctk.CTkFrame(self.content)
        self.sum_box.pack(fill="x", padx=16, pady=4)
        self._refresh_summary()

    def _refresh_summary(self):
        for widget in self.sum_box.winfo_children():
            widget.destroy()
        day = self.sum_date.get().strip()
        try:
            summary = db.day_summary(self.conn, day)
        except Exception as exc:  # noqa: BLE001
            self._err(exc)
            return
        t = summary["totals"]
        ctk.CTkLabel(self.sum_box,
                     text=f"Bills: {t['bills']}  |  Revenue:"
                     f" Rs. {t['revenue']:,.2f}  |  Discounts:"
                     f" Rs. {t['discount']:,.2f}  |  GST:"
                     f" Rs. {t['gst']:,.2f}",
                     font=ctk.CTkFont(size=14, weight="bold")).pack(
                         anchor="w", padx=8, pady=8)
        ctk.CTkLabel(self.sum_box, text="Table-wise sales",
                     font=ctk.CTkFont(size=13, weight="bold")).pack(
                         anchor="w", padx=8)
        for r in summary["table_wise"]:
            ctk.CTkLabel(self.sum_box, text=f"{r['table_name']}:"
                         f" {r['bills']} bills — Rs. {r['revenue']:,.2f}"
                         ).pack(anchor="w", padx=16)
        ctk.CTkLabel(self.sum_box, text="Item-wise sales (top first)",
                     font=ctk.CTkFont(size=13, weight="bold")).pack(
                         anchor="w", padx=8, pady=(12, 0))
        for r in summary["item_wise"]:
            ctk.CTkLabel(self.sum_box, text=f"{r['name']}: {r['qty']} sold"
                         f" — Rs. {r['revenue']:,.2f}").pack(anchor="w",
                                                            padx=16)
        if summary["top_dishes"]:
            ctk.CTkLabel(self.sum_box, text="Top dishes: " + ", ".join(
                r["name"] for r in summary["top_dishes"])).pack(
                    anchor="w", padx=8, pady=(12, 8))

    def _export_summary(self):
        try:
            day = self.sum_date.get().strip()
            summary = db.day_summary(self.conn, day)
            restaurant = db.get_setting(self.conn, "restaurant_name",
                                        "My Restaurant")
            path = printing.day_summary_pdf(
                summary, day, restaurant,
                printing.BILLS_DIR / f"day-summary-{day}.pdf")
            self._info(f"Day summary saved:\n{path}")
        except Exception as exc:  # noqa: BLE001
            self._err(exc)

    # --------------------------------------------------------------- backup

    def screen_backup(self):
        self.heading("Backup & restore")
        ctk.CTkButton(self.content, text="Create backup",
                      command=self._do_backup).pack(anchor="w", padx=16,
                                                   pady=4)
        ctk.CTkButton(self.content, text="Restore from backup…",
                      command=self._do_restore).pack(anchor="w", padx=16,
                                                    pady=4)
        self.backup_label = ctk.CTkLabel(self.content, text="",
                                         wraplength=700)
        self.backup_label.pack(anchor="w", padx=16, pady=8)

    def _do_backup(self):
        try:
            path = backup_mod.create_backup()
            self.backup_label.configure(text=f"Backup saved:\n{path}")
        except Exception as exc:  # noqa: BLE001
            self._err(exc)

    def _do_restore(self):
        path = filedialog.askopenfilename(
            title="Choose a RestroBill backup",
            filetypes=[("Zip files", "*.zip")])
        if not path:
            return
        try:
            safety = backup_mod.restore_backup(path)
            self.backup_label.configure(
                text=f"Restored from {path}.\nSafety copy: {safety}\n"
                "Restart RestroBill to use the restored data.")
        except Exception as exc:  # noqa: BLE001
            self._err(exc)

    # ------------------------------------------------------------- settings

    def screen_settings(self):
        self.heading("Settings")
        form = ctk.CTkFrame(self.content)
        form.pack(fill="x", padx=16, pady=8)
        ctk.CTkLabel(form, text="Restaurant name").pack(anchor="w", padx=8)
        self.set_name = ctk.CTkEntry(form, width=300)
        self.set_name.insert(0, db.get_setting(self.conn, "restaurant_name",
                                              "My Restaurant"))
        self.set_name.pack(anchor="w", padx=8, pady=4)
        ctk.CTkLabel(form, text="Default GST % for new orders").pack(
            anchor="w", padx=8)
        self.set_gst = ctk.CTkEntry(form, width=100)
        self.set_gst.insert(0, db.get_setting(self.conn, "gst_pct", "5"))
        self.set_gst.pack(anchor="w", padx=8, pady=4)
        ctk.CTkButton(form, text="Save settings",
                      command=self._save_settings).pack(anchor="w", padx=8,
                                                       pady=8)
        ctk.CTkLabel(self.content, text="Add a table").pack(anchor="w",
                                                           padx=16)
        self.new_table = ctk.CTkEntry(self.content,
                                      placeholder_text="e.g. T11",
                                      width=160)
        self.new_table.pack(anchor="w", padx=16, pady=4)
        ctk.CTkButton(self.content, text="Add table",
                      command=self._add_table).pack(anchor="w", padx=16)

    def _save_settings(self):
        try:
            db.set_setting(self.conn, "restaurant_name",
                           self.set_name.get())
            db.set_setting(self.conn, "gst_pct", float(self.set_gst.get()))
            self._info("Settings saved.")
        except Exception as exc:  # noqa: BLE001
            self._err(exc)

    def _add_table(self):
        try:
            db.add_table(self.conn, self.new_table.get())
            self.new_table.delete(0, "end")
            self._info("Table added.")
        except Exception as exc:  # noqa: BLE001
            self._err(exc)

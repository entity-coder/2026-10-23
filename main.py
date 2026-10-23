"""RestroBill — restaurant table billing (entry point).

Run:  python main.py
Requires: CustomTkinter, reportlab (see requirements.txt).
Data lives in restrobill.db next to this file; bills go to bills/,
kitchen tickets to kot/, backups to backups/.
"""

import customtkinter as ctk

import db
from ui import RestroBillApp

if __name__ == "__main__":
    db.init_db()
    ctk.set_appearance_mode("System")
    ctk.set_default_color_theme("blue")
    app = RestroBillApp()
    app.mainloop()

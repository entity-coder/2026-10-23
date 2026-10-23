"""RestroBill — backup & restore.

A backup is a timestamped .zip of the SQLite database plus the bills and
KOT folders. Restore first makes a safety copy of the current database
(``restrobill.db.bak-<timestamp>``) so a bad restore can never destroy the
live data — the same pattern RepairDesk/DairyManager/PharmaBill use.
"""

import shutil
import zipfile
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
DB_FILE = BASE / "restrobill.db"
BILLS_DIR = BASE / "bills"
KOT_DIR = BASE / "kot"
BACKUP_DIR = BASE / "backups"


def _stamp():
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def create_backup():
    """Zip db + bills + KOT files. Returns the backup file path."""
    BACKUP_DIR.mkdir(exist_ok=True)
    path = BACKUP_DIR / f"restrobill-backup-{_stamp()}.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        if DB_FILE.exists():
            zf.write(DB_FILE, DB_FILE.name)
        for folder, prefix in ((BILLS_DIR, "bills"), (KOT_DIR, "kot")):
            if folder.exists():
                for f in folder.iterdir():
                    if f.is_file():
                        zf.write(f, f"{prefix}/{f.name}")
    return str(path)


def restore_backup(zip_path):
    """Restore a backup made by create_backup. Returns the safety-copy path.

    Raises ValueError if the zip does not contain restrobill.db.
    """
    with zipfile.ZipFile(zip_path) as zf:
        if "restrobill.db" not in zf.namelist():
            raise ValueError("Not a RestroBill backup (no restrobill.db).")
        safety = BASE / f"restrobill.db.bak-{_stamp()}"
        if DB_FILE.exists():
            shutil.copy2(DB_FILE, safety)
        else:
            safety = None
        zf.extract("restrobill.db", BASE)
        for name in zf.namelist():
            if (name.startswith("bills/") or name.startswith("kot/")) \
                    and not name.endswith("/"):
                (BASE / Path(name).parent).mkdir(parents=True, exist_ok=True)
                zf.extract(name, BASE)
    return str(safety) if safety else None

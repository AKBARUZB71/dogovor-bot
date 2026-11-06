"""Zaxira nusxa olish va tiklash.

Zaxira (bot ishlab turganda ham xavfsiz — SQLite online backup API):
    python scripts/backup.py backup --db data/dogovor.db --out backups --keep 30

Tiklash (AVVAL botni to'xtating!):
    python scripts/backup.py restore backups/dogovor_20261106_120000.db --db data/dogovor.db
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path


def integrity_ok(path: Path) -> bool:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        conn.close()


def last_number(path: Path) -> int:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return conn.execute("SELECT last_number FROM counter WHERE id = 1").fetchone()[0]
    finally:
        conn.close()


def backup(db: Path, out_dir: Path, keep: int) -> Path:
    if not db.exists():
        sys.exit(f"Baza topilmadi: {db}")
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"dogovor_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
    src = sqlite3.connect(str(db))
    dst = sqlite3.connect(str(target))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    if not integrity_ok(target):
        target.unlink(missing_ok=True)
        sys.exit("Zaxira nusxa integrity_check'dan o'tmadi!")
    backups = sorted(out_dir.glob("dogovor_*.db"))
    for old in backups[:-keep] if keep > 0 else []:
        old.unlink()
    print(f"Zaxira: {target} (oxirgi raqam: {last_number(target)})")
    return target


def restore(source: Path, db: Path) -> None:
    if not source.exists():
        sys.exit(f"Zaxira fayl topilmadi: {source}")
    if not integrity_ok(source):
        sys.exit("Zaxira fayl buzilgan (integrity_check), tiklanmadi.")
    db.parent.mkdir(parents=True, exist_ok=True)
    if db.exists():
        safety = db.with_name(f"{db.stem}_before_restore_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db")
        shutil.copy2(db, safety)
        print(f"Joriy baza saqlab qo'yildi: {safety}")
    for suffix in ("-wal", "-shm"):
        Path(str(db) + suffix).unlink(missing_ok=True)
    shutil.copy2(source, db)
    print(f"Tiklandi: {db} (oxirgi raqam: {last_number(db)})")


def main() -> None:
    p = argparse.ArgumentParser(description="Dogovor Drop bazasi zaxirasi")
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("backup")
    b.add_argument("--db", type=Path, default=Path("data/dogovor.db"))
    b.add_argument("--out", type=Path, default=Path("backups"))
    b.add_argument("--keep", type=int, default=30)
    r = sub.add_parser("restore")
    r.add_argument("source", type=Path)
    r.add_argument("--db", type=Path, default=Path("data/dogovor.db"))
    args = p.parse_args()
    if args.cmd == "backup":
        backup(args.db, args.out, args.keep)
    else:
        restore(args.source, args.db)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""One-off: create the SQLite schema at the configured DB_PATH."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db
from app.config import load_settings


def main() -> None:
    settings = load_settings()
    conn = db.get_connection(settings.db_path)
    db.init_db(conn)
    print(f"Initialized schema at {settings.db_path}")
    conn.close()


if __name__ == "__main__":
    main()

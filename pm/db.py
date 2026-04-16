import os
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent
CATALOG_DB_PATH = Path(
    os.getenv(
        "PROJECT_MIRU_CATALOG_DB_PATH", str(PROJECT_ROOT / "data" / "card_catalog.db")
    )
)

def connect_sqlite(db_path):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn

def connect_catalog():
    return connect_sqlite(CATALOG_DB_PATH)

import sqlite3
from contextlib import contextmanager

DB_PATH = "pipeline.db"


def init_db():
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS companies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                domain TEXT,
                event_name TEXT NOT NULL,
                sponsor_url TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(name, event_name)
            );
        """)


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def upsert_company(name: str, domain: str, event_name: str, sponsor_url: str) -> bool:
    """Returns True if newly inserted, False if already existed."""
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO companies (name, domain, event_name, sponsor_url)
            VALUES (?, ?, ?, ?)
            """,
            (name, domain, event_name, sponsor_url),
        )
        return cur.rowcount > 0


def get_companies(event_name: str) -> list[dict]:
    """All companies scraped for an event, ordered by when they were first seen."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM companies WHERE event_name = ? ORDER BY id ASC",
            (event_name,),
        ).fetchall()
        return [dict(r) for r in rows]


def count_companies(event_name: str) -> int:
    """Total distinct companies ever seen for an event (across all daily runs)."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM companies WHERE event_name = ?",
            (event_name,),
        ).fetchone()
        return row["n"]

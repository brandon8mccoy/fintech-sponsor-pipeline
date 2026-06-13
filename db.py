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
                passed_icp INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(name, event_name)
            );

            CREATE TABLE IF NOT EXISTS contacts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_name TEXT NOT NULL,
                event_name TEXT NOT NULL,
                first_name TEXT,
                last_name TEXT,
                title TEXT,
                email TEXT,
                linkedin_url TEXT,
                apollo_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(email, event_name)
            );

            CREATE TABLE IF NOT EXISTS scrape_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_name TEXT NOT NULL,
                sponsor_url TEXT NOT NULL,
                status TEXT,
                run_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(event_name, run_at)
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


def mark_icp_passed(name: str, event_name: str):
    with get_conn() as conn:
        conn.execute(
            "UPDATE companies SET passed_icp = 1 WHERE name = ? AND event_name = ?",
            (name, event_name),
        )


def upsert_contact(contact: dict) -> bool:
    """Returns True if the contact was newly inserted, False if this email was
    already stored for this event ((email, event_name) is UNIQUE, so the same
    person can still appear on a different event's tab)."""
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO contacts
                (company_name, event_name, first_name, last_name, title, email, linkedin_url, apollo_id)
            VALUES
                (:company_name, :event_name, :first_name, :last_name, :title, :email, :linkedin_url, :apollo_id)
            """,
            contact,
        )
        return cur.rowcount > 0


def count_companies(event_name: str) -> int:
    """Total distinct companies ever seen for an event (across all daily runs)."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM companies WHERE event_name = ?",
            (event_name,),
        ).fetchone()
        return row["n"]


def get_icp_companies(event_name: str) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM companies WHERE event_name = ? AND passed_icp = 1",
            (event_name,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_contacts_for_event(event_name: str) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM contacts WHERE event_name = ?",
            (event_name,),
        ).fetchall()
        return [dict(r) for r in rows]

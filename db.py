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
                tier INTEGER,
                contacts_attempted INTEGER DEFAULT 0,
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
        _ensure_company_columns(conn)


def _ensure_company_columns(conn):
    """Self-healing migration: add columns introduced after an existing
    pipeline.db was first created, so older DBs don't crash on queries that
    reference them. SQLite has no `ADD COLUMN IF NOT EXISTS`, so check first."""
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(companies)")}
    if "tier" not in existing:
        conn.execute("ALTER TABLE companies ADD COLUMN tier INTEGER")
    if "contacts_attempted" not in existing:
        conn.execute("ALTER TABLE companies ADD COLUMN contacts_attempted INTEGER DEFAULT 0")


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


def mark_icp_passed(name: str, event_name: str, tier: int = 2):
    """Marks a company as an ICP match and stores its tier so contact-pull
    ordering survives across runs (the in-memory tier is otherwise lost)."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE companies SET passed_icp = 1, tier = ? WHERE name = ? AND event_name = ?",
            (tier, name, event_name),
        )


def mark_contacts_attempted(name: str, event_name: str):
    """Records that we ran a contact lookup for this company, so we don't
    re-query the provider for it on every subsequent run (even if it yielded
    no contacts). Keeps Hunter usage bounded."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE companies SET contacts_attempted = 1 WHERE name = ? AND event_name = ?",
            (name, event_name),
        )


def get_companies_needing_contacts(event_name: str) -> list[dict]:
    """ICP-passed companies for an event we haven't run a contact lookup for yet,
    highest tier first. Drives the daily backfill toward the lifetime cap."""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT * FROM companies
            WHERE event_name = ? AND passed_icp = 1 AND contacts_attempted = 0
            ORDER BY COALESCE(tier, 2) ASC, id ASC
            """,
            (event_name,),
        ).fetchall()
        return [dict(r) for r in rows]


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

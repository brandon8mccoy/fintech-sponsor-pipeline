import sqlite3
from contextlib import contextmanager
from typing import Optional

DB_PATH = "pipeline.db"


def init_db():
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS speakers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                title TEXT,
                company TEXT,
                domain TEXT,
                linkedin_url TEXT,
                event_name TEXT NOT NULL,
                speaker_url TEXT,
                passed_icp INTEGER DEFAULT 0,
                tier INTEGER,
                email_attempted INTEGER DEFAULT 0,
                email TEXT,
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


def upsert_speaker(
    name: str,
    title: str,
    company: str,
    domain: str,
    linkedin_url: str,
    event_name: str,
    speaker_url: str,
) -> bool:
    """Returns True if newly inserted, False if already existed."""
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO speakers
                (name, title, company, domain, linkedin_url, event_name, speaker_url)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (name, title, company, domain, linkedin_url, event_name, speaker_url),
        )
        return cur.rowcount > 0


def mark_icp_passed(name: str, event_name: str, tier: int = 2):
    with get_conn() as conn:
        conn.execute(
            "UPDATE speakers SET passed_icp = 1, tier = ? WHERE name = ? AND event_name = ?",
            (tier, name, event_name),
        )


def mark_email_attempted(name: str, event_name: str, email: str = ""):
    """Records that we ran a Hunter email lookup for this speaker."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE speakers SET email_attempted = 1, email = ? WHERE name = ? AND event_name = ?",
            (email, name, event_name),
        )


def get_speakers_needing_email(event_name: str) -> list[dict]:
    """ICP-passed speakers we haven't looked up yet, Tier 1 first."""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT * FROM speakers
            WHERE event_name = ? AND passed_icp = 1 AND email_attempted = 0
            ORDER BY COALESCE(tier, 2) ASC, id ASC
            """,
            (event_name,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_speakers(event_name: str) -> list[dict]:
    """All ICP-passed speakers for an event (for sheet output)."""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT * FROM speakers
            WHERE event_name = ? AND passed_icp = 1
            ORDER BY COALESCE(tier, 2) ASC, id ASC
            """,
            (event_name,),
        ).fetchall()
        return [dict(r) for r in rows]


def count_speakers(event_name: str) -> int:
    """Total speakers ever seen for an event (all, not just ICP-passed)."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM speakers WHERE event_name = ?",
            (event_name,),
        ).fetchone()
        return row["n"]


def count_icp_speakers(event_name: str) -> int:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM speakers WHERE event_name = ? AND passed_icp = 1",
            (event_name,),
        ).fetchone()
        return row["n"]


def count_emails_found(event_name: str) -> int:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM speakers WHERE event_name = ? AND email != '' AND email IS NOT NULL",
            (event_name,),
        ).fetchone()
        return row["n"]

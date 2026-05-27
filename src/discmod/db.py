import sqlite3
from datetime import datetime, timezone
from pathlib import Path


SCHEMA = """
CREATE TABLE IF NOT EXISTS proposals (
    message_id     INTEGER PRIMARY KEY,
    channel_id     INTEGER NOT NULL,
    thread_id      INTEGER,
    mod_url        TEXT NOT NULL,
    slug           TEXT NOT NULL,
    project_id     TEXT NOT NULL,
    proposer_id    INTEGER NOT NULL,
    proposer_name  TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'pending',
    resolved_version TEXT,
    ai_summary     TEXT,
    error          TEXT,
    created_at     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    decided_at     TIMESTAMP
);

CREATE TABLE IF NOT EXISTS approvals (
    message_id     INTEGER NOT NULL,
    user_id        INTEGER NOT NULL,
    user_name      TEXT NOT NULL,
    created_at     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (message_id, user_id),
    FOREIGN KEY (message_id) REFERENCES proposals(message_id)
);

CREATE INDEX IF NOT EXISTS idx_proposals_status ON proposals(status);
CREATE INDEX IF NOT EXISTS idx_proposals_slug ON proposals(slug);
"""


def open_db(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def insert_proposal(
    conn: sqlite3.Connection,
    *,
    message_id: int,
    channel_id: int,
    mod_url: str,
    slug: str,
    project_id: str,
    proposer_id: int,
    proposer_name: str,
    ai_summary: str = "",
) -> None:
    conn.execute(
        """
        INSERT INTO proposals
            (message_id, channel_id, mod_url, slug, project_id, proposer_id, proposer_name, ai_summary)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (message_id, channel_id, mod_url, slug, project_id, proposer_id, proposer_name, ai_summary),
    )
    conn.commit()


def get_proposal(conn: sqlite3.Connection, message_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM proposals WHERE message_id = ?", (message_id,)
    ).fetchone()


def get_pending_proposals(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM proposals WHERE status = 'pending' ORDER BY created_at DESC"
    ).fetchall()


def update_proposal_status(
    conn: sqlite3.Connection,
    message_id: int,
    status: str,
    resolved_version: str | None = None,
    error: str | None = None,
) -> int:
    now = datetime.now(timezone.utc).isoformat()
    result = conn.execute(
        """
        UPDATE proposals
        SET status = ?, resolved_version = COALESCE(?, resolved_version),
            error = COALESCE(?, error), decided_at = ?
        WHERE message_id = ?
        """,
        (status, resolved_version, error, now, message_id),
    )
    conn.commit()
    return result.rowcount


def transition_to_merging(conn: sqlite3.Connection, message_id: int) -> bool:
    """Atomically transition pending -> merging. Returns True if successful."""
    result = conn.execute(
        "UPDATE proposals SET status='merging' WHERE message_id=? AND status='pending'",
        (message_id,),
    )
    conn.commit()
    return result.rowcount == 1


def update_thread_id(conn: sqlite3.Connection, message_id: int, thread_id: int) -> None:
    conn.execute(
        "UPDATE proposals SET thread_id = ? WHERE message_id = ?",
        (thread_id, message_id),
    )
    conn.commit()


def insert_approval(
    conn: sqlite3.Connection,
    message_id: int,
    user_id: int,
    user_name: str,
) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO approvals (message_id, user_id, user_name)
        VALUES (?, ?, ?)
        """,
        (message_id, user_id, user_name),
    )
    conn.commit()


def count_approvals(conn: sqlite3.Connection, message_id: int, exclude_user_id: int) -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM approvals WHERE message_id=? AND user_id != ?",
        (message_id, exclude_user_id),
    ).fetchone()
    return row[0]


def fail_merging_proposals(conn: sqlite3.Connection) -> list[int]:
    """Mark all 'merging' proposals as failed (used on bot restart)."""
    rows = conn.execute(
        "SELECT message_id FROM proposals WHERE status='merging'"
    ).fetchall()
    ids = [r["message_id"] for r in rows]
    if ids:
        conn.executemany(
            """
            UPDATE proposals SET status='failed',
                error='bot restarted mid-merge — manual review required'
            WHERE message_id=?
            """,
            [(mid,) for mid in ids],
        )
        conn.commit()
    return ids

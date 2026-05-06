"""Add corrections_json column to segments table.

Persists the per-word audit trail produced by vocab_correction (Phase 2)
and llm_vocab_correction (Phase 3) so users can undo individual auto-
corrections, the transcript viewer can render an ✏️ indicator on
corrected words, and re-correct on a finished transcript can append
new entries to the existing trail.

Idempotent ALTER TABLE — checks for column presence first since SQLite
versions older than 3.35 don't support ADD COLUMN IF NOT EXISTS.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)


def _columns(cursor: sqlite3.Cursor, table: str) -> set[str]:
    cursor.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in cursor.fetchall()}


def migrate(db_path: Path) -> None:
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='segments'"
        )
        if cursor.fetchone() is None:
            logger.info("segments table not yet present — skipping corrections_json migration")
            conn.close()
            return

        existing = _columns(cursor, "segments")
        if "corrections_json" not in existing:
            cursor.execute("ALTER TABLE segments ADD COLUMN corrections_json TEXT")
            logger.info("Added corrections_json column to segments")

        conn.commit()
        conn.close()
        logger.info("Segment corrections_json migration completed")
    except sqlite3.Error as e:
        logger.error("Database error during corrections_json migration: %s", e)
        raise


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        migrate(Path(sys.argv[1]))
    else:
        migrate(Path("verbatim.db"))

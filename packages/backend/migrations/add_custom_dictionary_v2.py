"""Extend custom_dictionary table for production-grade domain vocabulary.

Adds three columns to the v1 table:

- sounds_like   TEXT    — comma-separated alternate spellings used as additional
                          phonetic match keys during post-correction. Modelled on
                          Speechmatics' free-form `sounds_like` field (e.g.
                          "nyohki, nyokey" for "gnocchi"). Optional; if NULL we
                          fall back to phonetic codes derived from the term.
- priority      INTEGER  — end-of-prompt ordering weight. Whisper attends more
                          to tokens at the end of initial_prompt, so high-priority
                          terms must land last. Higher value = later in prompt.
                          Default 0; "high" = ~10, "highest" = ~100.
- usage_count   INTEGER  — count of times the term has been emitted in finished
                          transcripts. Used as a secondary sort key (frequently-
                          used terms float later in the prompt) and as the seed
                          for the eventual auto-learn feature (Descript-style:
                          promote a term after N corrections).

Idempotent: ALTER TABLE ADD COLUMN with IF NOT EXISTS semantics emulated via
PRAGMA-checked-then-altered pattern, since SQLite doesn't support ADD COLUMN
IF NOT EXISTS until 3.35 and we target older runtimes.
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
    """Add sounds_like, priority, usage_count columns if absent."""
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='custom_dictionary'"
        )
        if cursor.fetchone() is None:
            logger.info("custom_dictionary table not yet present — skipping v2 migration")
            conn.close()
            return

        existing = _columns(cursor, "custom_dictionary")

        if "sounds_like" not in existing:
            cursor.execute("ALTER TABLE custom_dictionary ADD COLUMN sounds_like TEXT")
            logger.info("Added sounds_like column")

        if "priority" not in existing:
            cursor.execute(
                "ALTER TABLE custom_dictionary ADD COLUMN priority INTEGER NOT NULL DEFAULT 0"
            )
            logger.info("Added priority column")

        if "usage_count" not in existing:
            cursor.execute(
                "ALTER TABLE custom_dictionary ADD COLUMN usage_count INTEGER NOT NULL DEFAULT 0"
            )
            logger.info("Added usage_count column")

        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_custom_dict_priority "
            "ON custom_dictionary(priority DESC, usage_count DESC)"
        )

        conn.commit()
        conn.close()
        logger.info("Custom dictionary v2 migration completed")
    except sqlite3.Error as e:
        logger.error("Database error during custom dictionary v2 migration: %s", e)
        raise


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        migrate(Path(sys.argv[1]))
    else:
        migrate(Path("verbatim.db"))

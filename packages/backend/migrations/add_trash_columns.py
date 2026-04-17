"""Add deleted_at column to recordings, documents, and projects tables.

Also backfills deleted_at for existing is_archived=True rows.
"""

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


def _add_column_if_missing(
    cursor: sqlite3.Cursor,
    table: str,
    column: str,
    column_def: str,
) -> bool:
    """Add a column to a table if it doesn't already exist."""
    cursor.execute(f"PRAGMA table_info({table})")
    columns = [col[1] for col in cursor.fetchall()]
    if column not in columns:
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_def}")
        logger.info("Added %s column to %s", column, table)
        return True
    return False


def migrate(db_path: Path) -> None:
    """Add deleted_at column and backfill existing archived items."""
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        now = datetime.now(timezone.utc).isoformat()

        for table in ("recordings", "documents", "projects"):
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            )
            if not cursor.fetchone():
                continue

            added = _add_column_if_missing(
                cursor, table, "deleted_at", "DATETIME DEFAULT NULL"
            )

            # Backfill: existing archived items get deleted_at set so they
            # appear in the trash with a timestamp and are subject to auto-purge.
            if added:
                cursor.execute(
                    f"UPDATE {table} SET deleted_at = ? WHERE is_archived = 1 AND deleted_at IS NULL",
                    (now,),
                )
                count = cursor.rowcount
                if count:
                    logger.info(
                        "Backfilled deleted_at on %d archived %s rows", count, table
                    )

        conn.commit()
        conn.close()
        logger.info("Trash columns migration completed")

    except sqlite3.Error as e:
        logger.error("Database error during trash columns migration: %s", e)
        raise
    except Exception as e:
        logger.error("Unexpected error during trash columns migration: %s", e)
        raise


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        migrate(Path(sys.argv[1]))
    else:
        migrate(Path("verbatim.db"))

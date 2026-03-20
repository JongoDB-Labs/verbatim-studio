"""Add is_archived column to recordings and documents tables."""

import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)


def migrate(db_path: Path) -> None:
    """Add is_archived column to recordings and documents tables."""
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Check recordings table
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='recordings'")
        if cursor.fetchone():
            cursor.execute("PRAGMA table_info(recordings)")
            recording_columns = [col[1] for col in cursor.fetchall()]
            if "is_archived" not in recording_columns:
                cursor.execute(
                    "ALTER TABLE recordings ADD COLUMN is_archived BOOLEAN DEFAULT 0 NOT NULL"
                )
                logger.info("Added is_archived column to recordings")

        # Check documents table
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='documents'")
        if cursor.fetchone():
            cursor.execute("PRAGMA table_info(documents)")
            document_columns = [col[1] for col in cursor.fetchall()]
            if "is_archived" not in document_columns:
                cursor.execute(
                    "ALTER TABLE documents ADD COLUMN is_archived BOOLEAN DEFAULT 0 NOT NULL"
                )
                logger.info("Added is_archived column to documents")

        conn.commit()
        conn.close()
        logger.info("Archive columns migration completed")

    except sqlite3.Error as e:
        logger.error(f"Database error during archive columns migration: {e}")
        raise
    except Exception as e:
        logger.error(f"Unexpected error during archive columns migration: {e}")
        raise


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        migrate(Path(sys.argv[1]))
    else:
        migrate(Path("verbatim.db"))

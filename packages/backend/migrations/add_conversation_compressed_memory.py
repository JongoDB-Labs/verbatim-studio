"""Add compressed_memory column to conversations table."""

import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)


def migrate(db_path: Path) -> None:
    """Add compressed_memory TEXT column to conversations."""
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Check if conversations table exists
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='conversations'"
        )
        if not cursor.fetchone():
            logger.info("conversations table does not exist yet - skipping migration")
            conn.close()
            return

        # Check if column already exists
        cursor.execute("PRAGMA table_info(conversations)")
        columns = [col[1] for col in cursor.fetchall()]

        if "compressed_memory" not in columns:
            cursor.execute(
                "ALTER TABLE conversations ADD COLUMN compressed_memory TEXT DEFAULT NULL"
            )
            conn.commit()
            logger.info("Added compressed_memory column to conversations")
        else:
            logger.debug("compressed_memory column already exists")

        conn.close()
        logger.info("Conversation compressed_memory migration complete")

    except sqlite3.Error as e:
        logger.error(f"Database error during conversation compressed_memory migration: {e}")
        raise
    except Exception as e:
        logger.error(f"Unexpected error during conversation compressed_memory migration: {e}")
        raise


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    db_path = Path(__file__).parent.parent / "verbatim.db"
    migrate(db_path)

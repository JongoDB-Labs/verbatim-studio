"""Add custom_dictionary table for domain-specific transcription terms.

Terms stored here are joined into Whisper's initial_prompt parameter
to bias the model toward specific words and phrases.
"""

import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)


def migrate(db_path: Path) -> None:
    """Create the custom_dictionary table if it does not exist."""
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS custom_dictionary (
                id TEXT PRIMARY KEY,
                term TEXT NOT NULL,
                category TEXT NOT NULL DEFAULT 'general',
                project_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
            )
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_custom_dict_project
            ON custom_dictionary(project_id)
        """)

        conn.commit()
        conn.close()
        logger.info("Custom dictionary migration completed")

    except sqlite3.Error as e:
        logger.error("Database error during custom dictionary migration: %s", e)
        raise
    except Exception as e:
        logger.error("Unexpected error during custom dictionary migration: %s", e)
        raise


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        migrate(Path(sys.argv[1]))
    else:
        migrate(Path("verbatim.db"))

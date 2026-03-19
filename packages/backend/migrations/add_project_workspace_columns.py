"""Add workspace columns to projects and project_id to conversations."""

import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)


def migrate(db_path: Path) -> None:
    """Add is_archived, sort_order, icon, color to projects; project_id to conversations."""
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Check if projects table exists
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='projects'")
        if not cursor.fetchone():
            logger.info("projects table does not exist — skipping")
            conn.close()
            return

        # Add new columns to projects (idempotent)
        cursor.execute("PRAGMA table_info(projects)")
        project_columns = [col[1] for col in cursor.fetchall()]

        if "is_archived" not in project_columns:
            cursor.execute("ALTER TABLE projects ADD COLUMN is_archived BOOLEAN DEFAULT 0 NOT NULL")
            logger.info("Added is_archived column to projects")

        if "sort_order" not in project_columns:
            cursor.execute("ALTER TABLE projects ADD COLUMN sort_order INTEGER DEFAULT 0 NOT NULL")
            logger.info("Added sort_order column to projects")

        if "icon" not in project_columns:
            cursor.execute("ALTER TABLE projects ADD COLUMN icon VARCHAR(50)")
            logger.info("Added icon column to projects")

        if "color" not in project_columns:
            cursor.execute("ALTER TABLE projects ADD COLUMN color VARCHAR(7)")
            logger.info("Added color column to projects")

        # Add project_id FK to conversations table
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='conversations'")
        if cursor.fetchone():
            cursor.execute("PRAGMA table_info(conversations)")
            conv_columns = [col[1] for col in cursor.fetchall()]

            if "project_id" not in conv_columns:
                cursor.execute(
                    "ALTER TABLE conversations ADD COLUMN project_id VARCHAR(36) "
                    "REFERENCES projects(id) ON DELETE SET NULL"
                )
                logger.info("Added project_id column to conversations")

        conn.commit()
        conn.close()
        logger.info("Project workspace migration completed")

    except sqlite3.Error as e:
        logger.error(f"Database error during project workspace migration: {e}")
        raise

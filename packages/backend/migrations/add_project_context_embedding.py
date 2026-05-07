"""Per-project context-vector cache for vocab retrieval.

The bundled-corpus retrieval embeds the project's textual context
(description, recording titles, prior-transcript summaries) into a
single 768-dim Nomic vector and uses it as the query for the
semantic-retrieval leg. Computing that embedding fresh on every
session would re-pay the embedder load + ~50 ms encode cost; caching
keyed by a hash of the inputs lets us reuse it.

The cache is invalidated by:
- Project description / title changes
- New document upload to the project
- New transcript completed in the project (changes the
  recent-summary input)
- Manual user edit of any vocab term scoped to this project

Migration is idempotent; column adds use the SQLite-3.35-equivalent
"check then add" pattern that the rest of our migrations use.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)


def migrate(db_path: Path) -> None:
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS project_context_embedding (
                project_id   TEXT PRIMARY KEY,
                embedding    BLOB NOT NULL,
                context_hash TEXT NOT NULL,
                last_built_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.commit()
        conn.close()
        logger.info("project_context_embedding migration completed")
    except sqlite3.Error as e:
        logger.error("Database error during project_context_embedding migration: %s", e)
        raise


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        migrate(Path(sys.argv[1]))
    else:
        migrate(Path("verbatim.db"))

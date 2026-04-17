"""Custom dictionary service for domain-specific transcription accuracy.

Manages user-defined terms that are passed to Whisper's initial_prompt
parameter, biasing the model toward specific words and phrases (e.g.
technical jargon, proper nouns, medical terms).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


@dataclass
class CustomDictionaryEntry:
    """A single dictionary entry."""

    term: str
    category: str = "general"  # tech, medical, legal, names, general
    project_id: str | None = None  # None = global, otherwise project-specific
    id: str | None = None
    created_at: str | None = None


def build_initial_prompt(
    entries: list[CustomDictionaryEntry],
    project_id: str | None = None,
    max_chars: int = 800,
) -> str | None:
    """Build a Whisper initial_prompt from dictionary entries.

    Args:
        entries: All dictionary entries (will be filtered).
        project_id: If set, include project-specific entries for this project
                     in addition to global entries.
        max_chars: Maximum character length for the returned prompt.

    Returns:
        Comma-separated string of terms, or None if no entries match.
    """
    # Filter: include global (project_id=None) + matching project entries
    filtered: list[CustomDictionaryEntry] = []
    for entry in entries:
        if entry.project_id is None:
            # Global entry — always included
            filtered.append(entry)
        elif project_id is not None and entry.project_id == project_id:
            # Project-specific entry that matches
            filtered.append(entry)

    if not filtered:
        return None

    # Deduplicate (case-insensitive, preserve first occurrence order)
    seen: set[str] = set()
    unique_terms: list[str] = []
    for entry in filtered:
        key = entry.term.lower()
        if key not in seen:
            seen.add(key)
            unique_terms.append(entry.term)

    if not unique_terms:
        return None

    # Build comma-separated prompt, truncating at max_chars on a comma boundary
    result = ""
    for i, term in enumerate(unique_terms):
        if i == 0:
            candidate = term
        else:
            candidate = result + ", " + term

        if len(candidate) > max_chars:
            break
        result = candidate

    return result if result else None


async def load_dictionary_entries(
    db: AsyncSession | None = None,
    project_id: str | None = None,
) -> list[CustomDictionaryEntry]:
    """Load dictionary entries from the database.

    Args:
        db: An async SQLAlchemy session.  When called from the job thread
            (which doesn't have a request-scoped session), pass ``None``
            and the function will create its own session.
        project_id: Optional project_id to additionally load
                     project-specific entries.

    Returns:
        List of CustomDictionaryEntry objects.
    """
    if db is None:
        from persistence.database import get_session_factory
        async with get_session_factory()() as session:
            return await _query_entries(session, project_id)
    return await _query_entries(db, project_id)


async def _query_entries(
    session: AsyncSession,
    project_id: str | None = None,
) -> list[CustomDictionaryEntry]:
    """Query custom_dictionary table, returning entries."""
    # Check the table exists first (it might not if migration hasn't run yet)
    try:
        if project_id:
            result = await session.execute(
                text(
                    "SELECT id, term, category, project_id, created_at "
                    "FROM custom_dictionary "
                    "WHERE project_id IS NULL OR project_id = :pid "
                    "ORDER BY created_at"
                ),
                {"pid": project_id},
            )
        else:
            result = await session.execute(
                text(
                    "SELECT id, term, category, project_id, created_at "
                    "FROM custom_dictionary "
                    "WHERE project_id IS NULL "
                    "ORDER BY created_at"
                )
            )

        rows = result.fetchall()
        return [
            CustomDictionaryEntry(
                id=row[0],
                term=row[1],
                category=row[2],
                project_id=row[3],
                created_at=row[4],
            )
            for row in rows
        ]
    except Exception as e:
        logger.warning("Could not query custom_dictionary table: %s", e)
        return []

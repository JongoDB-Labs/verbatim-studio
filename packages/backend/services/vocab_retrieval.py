"""Context-aware retrieval over the bundled + user vocabulary corpus.

This is the layer that replaces the v0.64.x `load_dictionary_entries`
call. Instead of returning every user-curated term, it returns the
top-100 terms most relevant to the recording's project context, drawn
from a ~620k-term bundled corpus PLUS the user's own additions.

# Two-stage retrieval

Stage 1 — Build a project context vector:
    Embed the project description, project + recording titles, and AI
    summaries of recent transcripts in the project. The vector is
    cached in `project_context_embedding` keyed by project_id. Only
    invalidated when its inputs change (description edit, new doc
    upload, recording title diff).

Stage 2 — Hybrid query against `vocab_bundled` + `vocab_user`:
    UNION of:
      - TOP 200 by FTS5 BM25 over project_text (keyword recall)
      - TOP 200 by sqlite-vec cosine to project_context_embedding
        (semantic recall)
      - ALL user_additions for this project (hard-floor inclusion)
    Re-ranked by:
      α·BM25 + β·cosine + γ·popularity + δ·is_user + ε·usage_count
    LIMIT 100.

# What feeds off this

- `services/jobs.py:handle_transcription` — calls retrieve_for_project()
  before transcription, hands the result to build_initial_prompt.
- `services/voice_agent.py:WhisperSTTAdapter.with_dictionary` — same.
- `services/vocab_correction.py:correct_segments` — uses retrieved
  candidates as the phonetic-match pool (was: full user dictionary).
- `services/llm_vocab_correction.py:llm_correct_segments` — same.

# Status

Stub implementation pending corpus database. Currently returns the
v0.64.x user dictionary unchanged; the bundled side will come online
when `assets/vocab_bundled.db` ships.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# How many candidates to surface for prompt biasing + post-correction.
# 100 is empirically enough to fill the 224-token Whisper prompt with
# the 60-80 most-relevant terms after token-budget trimming.
DEFAULT_RETRIEVAL_LIMIT = 100

# Hybrid ranking coefficients. Tunable in future per-project settings;
# for now these are the starting values from the research.
RANK_BM25 = 0.30
RANK_COSINE = 0.45
RANK_POPULARITY = 0.10
RANK_IS_USER = 0.10
RANK_USAGE = 0.05


@dataclass
class RetrievedTerm:
    """A single term retrieved from the bundled or user corpus.

    Compatible-shape with the v0.64.x CustomDictionaryEntry so existing
    callers can swap input source without changes to their code.
    """

    id: str
    term: str
    canonical_form: str
    category: str
    sounds_like: list[str]
    metaphone_primary: str
    metaphone_secondary: str
    popularity_score: float
    is_user_addition: bool
    usage_count: int = 0
    score: float = 0.0  # combined ranking score for diagnostics


def _bundled_db_path() -> Path | None:
    """Locate the bundled corpus DB. Returns None if not yet shipped.

    Search order:
      1. user-data dir (post-migration copy) — `~/Library/Application
         Support/@verbatim/electron/vocab_bundled.db` on macOS, equivalent
         path on Windows
      2. Resources dir (bundled with app) — `<resources>/vocab_bundled.db`
      3. Repo asset (development) — `<repo>/assets/vocab_bundled.db`
    """
    from core.config import settings

    user_data = Path(settings.DATA_DIR) / "vocab_bundled.db" if settings.DATA_DIR else None
    if user_data and user_data.exists():
        return user_data

    # Resources path varies by platform — derived from sys.executable.
    import sys
    py_exe = Path(sys.executable)
    if sys.platform == "win32":
        resources = py_exe.parent.parent
    else:
        resources = py_exe.parent.parent.parent
    bundled = resources / "vocab_bundled.db"
    if bundled.exists():
        return bundled

    # Development checkout fallback.
    repo_asset = Path(__file__).resolve().parents[3] / "assets" / "vocab_bundled.db"
    if repo_asset.exists():
        return repo_asset

    return None


async def retrieve_for_project(
    db: AsyncSession,
    *,
    project_id: str | None,
    recording_title: str | None = None,
    limit: int = DEFAULT_RETRIEVAL_LIMIT,
) -> list[RetrievedTerm]:
    """Top-K context-aware retrieval against bundled + user corpus.

    Falls back to "user table only" when the bundled corpus isn't
    available yet (e.g., dev environment without the asset, fresh
    install before vocab_bundled.db is migrated). The shape returned
    is identical in both cases so callers don't branch.

    Args:
        db: SQLAlchemy async session for the user-side queries.
        project_id: When set, project-scoped user terms get hard-floor
                    inclusion and the project's context embedding drives
                    the semantic-retrieval leg.
        recording_title: Recording title, blended into transient context
                         vector for the duration of this retrieval (not
                         cached).
        limit: How many terms to return. Default 100.
    """
    bundled_path = _bundled_db_path()

    if bundled_path is None:
        # Bundled corpus not yet shipping. Return user-only — this lets
        # the new code path turn on incrementally without breaking the
        # existing v0.64.x user-dictionary experience.
        logger.debug(
            "vocab_retrieval: bundled corpus unavailable, falling back to "
            "user-only dictionary load"
        )
        return await _load_user_only(db, project_id=project_id, limit=limit)

    # Real implementation: hybrid retrieval over bundled + user.
    # Build placeholder for the full implementation that lands when the
    # corpus DB ships. For now, defer to user-only too — the
    # architecture is in place but needs the corpus to do its work.
    logger.info(
        "vocab_retrieval: bundled corpus found at %s, but hybrid retrieval "
        "not yet implemented — using user-only path",
        bundled_path,
    )
    return await _load_user_only(db, project_id=project_id, limit=limit)


async def _load_user_only(
    db: AsyncSession,
    *,
    project_id: str | None,
    limit: int,
) -> list[RetrievedTerm]:
    """Load user-side dictionary entries. Compatibility path for the
    pre-bundled-corpus state.

    Reads from the existing `custom_dictionary` table populated in
    v0.64.x. Once `vocab_user` migration lands, this query swaps to
    that table.
    """
    from sqlalchemy import text

    if project_id:
        query = (
            "SELECT id, term, category, sounds_like, priority, usage_count "
            "FROM custom_dictionary "
            "WHERE project_id IS NULL OR project_id = :pid "
            "ORDER BY priority DESC, usage_count DESC "
            "LIMIT :lim"
        )
        params: dict[str, object] = {"pid": project_id, "lim": limit}
    else:
        query = (
            "SELECT id, term, category, sounds_like, priority, usage_count "
            "FROM custom_dictionary "
            "WHERE project_id IS NULL "
            "ORDER BY priority DESC, usage_count DESC "
            "LIMIT :lim"
        )
        params = {"lim": limit}

    try:
        result = await db.execute(text(query), params)
        rows = result.fetchall()
    except Exception as e:
        logger.warning("vocab_retrieval user-only query failed: %s", e)
        return []

    out: list[RetrievedTerm] = []
    for row in rows:
        sounds_like_raw = row[3] or ""
        sounds_like = [s.strip() for s in sounds_like_raw.split(",") if s.strip()]
        out.append(RetrievedTerm(
            id=row[0],
            term=row[1],
            canonical_form=row[1],
            category=row[2] or "general",
            sounds_like=sounds_like,
            metaphone_primary="",
            metaphone_secondary="",
            popularity_score=float(row[4] or 0) / 50.0,  # priority 0/10/50 → 0/0.2/1.0
            is_user_addition=True,
            usage_count=row[5] or 0,
        ))
    return out


def to_legacy_entries(retrieved: list[RetrievedTerm]) -> list:
    """Bridge to v0.64.x CustomDictionaryEntry shape.

    Existing call sites in jobs.py, voice_agent.py, vocab_correction.py
    iterate `CustomDictionaryEntry` objects with attributes:
      .term, .category, .project_id, .sounds_like, .priority,
      .usage_count, .id

    Wrapping retrieved terms in that shape keeps the rest of the pipeline
    untouched while we incrementally migrate.
    """
    from services.custom_dictionary import CustomDictionaryEntry

    return [
        CustomDictionaryEntry(
            id=r.id,
            term=r.canonical_form,
            category=r.category,
            project_id=None,  # bundled has no project; user terms — see below
            sounds_like=r.sounds_like,
            priority=int(r.popularity_score * 50),
            usage_count=r.usage_count,
        )
        for r in retrieved
    ]

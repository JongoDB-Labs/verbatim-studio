"""Context-aware retrieval over the bundled + user vocabulary corpus.

This is the runtime layer that turns the bundled SQLite corpus into
the top-100 most-relevant terms for a given recording's project
context. Replaces v0.64.x's `load_dictionary_entries` which returned
the full user-curated table.

# Two-stage retrieval

Stage 1 — Build a project-context vector:
  Embed the project description + recording title + recent transcript
  AI-summaries via the Nomic embedder (already used for semantic
  search). Cache the resulting 768-dim vector in
  `project_context_embedding` keyed by project_id, invalidated by a
  hash of the input text. Cache hit ≈ 50 µs; cache miss ≈ 30-50 ms
  embedder + DB write.

Stage 2 — Hybrid query against `vocab_bundled` + `vocab_user`:
  - TOP 200 by FTS5 BM25 over the project's keyword text (lexical
    recall — catches "kubernetes" → kubectl/etcd/kubelet)
  - TOP 200 by sqlite-vec cosine to the project context vector
    (semantic recall — catches "Marine Corps administration" →
    MARCORSEPMAN/ADSEP/MARADMIN even without the literal token)
  - All user_additions (hard-floor inclusion)
  - All bundled with popularity_score above a threshold (broad
    safety net for very common terms like FBI, IRS, NATO)

  Re-rank by:
    α·BM25 + β·cosine + γ·popularity + δ·is_user + ε·usage_count

# Graceful degradation

The runtime works in three modes:
  1. Full hybrid — bundled DB present + sqlite-vec extension loads +
     embedder available. ~30-80 ms retrieval.
  2. BM25-only — bundled DB present, no vec extension or no
     embeddings. Drops semantic leg; lexical retrieval still useful.
     ~10 ms retrieval.
  3. User-only — no bundled DB. Falls through to v0.64.x behaviour.
     Same shape as before, no behaviour change for existing installs
     before the corpus ships.

# What feeds off this

- services/jobs.py:handle_transcription — calls retrieve_for_project
  before transcription, hands the result to build_initial_prompt.
- services/voice_agent.py:WhisperSTTAdapter.with_dictionary — same.
- services/vocab_correction.py:correct_segments — uses retrieved
  candidates as the phonetic-match pool (was: full user dictionary).
- services/llm_vocab_correction.py:llm_correct_segments — same.
"""

from __future__ import annotations

import hashlib
import logging
import re
import sqlite3
import struct
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# How many candidates to surface for prompt biasing + post-correction.
# 100 is empirically enough to fill the 224-token Whisper prompt with
# the 60-80 most-relevant terms after token-budget trimming.
DEFAULT_RETRIEVAL_LIMIT = 100

# Per-leg candidate pool sizes BEFORE the final rerank → top-K.
BM25_POOL = 200
COSINE_POOL = 200

# Hybrid ranking coefficients. Tunable in future per-project settings;
# starting values from the bundled-vocab redesign plan (2026-05-07).
RANK_BM25 = 0.30
RANK_COSINE = 0.45
RANK_POPULARITY = 0.10
RANK_IS_USER = 0.10
RANK_USAGE = 0.05

# Popularity threshold for the "broad safety net" pass — bundled terms
# with score above this are always considered candidates regardless of
# their semantic match (so household names like FBI / NASA / Q4 are
# never pruned out of an unfamiliar project).
POPULARITY_FLOOR = 0.85


# ── Data shapes ─────────────────────────────────────────────────────


@dataclass
class RetrievedTerm:
    """A single term retrieved from the bundled or user corpus.

    Compatible-shape with the v0.64.x CustomDictionaryEntry so existing
    callers can swap input source without code changes.
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
    score: float = 0.0  # combined ranking score, for diagnostics


# ── Bundled DB locator ──────────────────────────────────────────────


_bundled_conn: sqlite3.Connection | None = None
_bundled_conn_path: Path | None = None
_bundled_has_vec: bool = False


def _bundled_db_path() -> Path | None:
    """Locate the bundled corpus DB.

    Search order:
      1. user-data dir (post-migration copy)
      2. Resources dir (bundled with app)
      3. Repo asset (development checkout)
    """
    from core.config import settings

    user_data = (
        Path(settings.DATA_DIR) / "vocab_bundled.db" if settings.DATA_DIR else None
    )
    if user_data and user_data.exists():
        return user_data

    import sys
    py_exe = Path(sys.executable)
    if sys.platform == "win32":
        resources = py_exe.parent.parent
    else:
        resources = py_exe.parent.parent.parent
    bundled = resources / "vocab_bundled.db"
    if bundled.exists():
        return bundled

    repo_asset = Path(__file__).resolve().parents[3] / "assets" / "vocab_bundled.db"
    if repo_asset.exists():
        return repo_asset

    return None


def _open_bundled_conn() -> tuple[sqlite3.Connection | None, bool]:
    """Open (or reuse) the bundled DB connection.

    Returns (connection, has_vec). The has_vec flag indicates whether
    sqlite-vec loaded successfully — the runtime falls back to BM25-only
    when it didn't.
    """
    global _bundled_conn, _bundled_conn_path, _bundled_has_vec

    path = _bundled_db_path()
    if path is None:
        return None, False

    if _bundled_conn is not None and _bundled_conn_path == path:
        return _bundled_conn, _bundled_has_vec

    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
    conn.row_factory = sqlite3.Row

    has_vec = False
    try:
        import sqlite_vec
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        # Verify the vec virtual table exists in the bundled DB.
        try:
            conn.execute("SELECT 1 FROM vocab_bundled_vec LIMIT 1").fetchone()
            has_vec = True
        except sqlite3.OperationalError:
            logger.info(
                "vocab_retrieval: bundled DB has no vec table — using BM25-only"
            )
    except Exception as e:
        logger.info("vocab_retrieval: sqlite-vec unavailable (%s) — BM25-only", e)

    _bundled_conn = conn
    _bundled_conn_path = path
    _bundled_has_vec = has_vec
    return conn, has_vec


# ── Project context embedding cache ─────────────────────────────────


def _hash_inputs(parts: list[str]) -> str:
    """Stable hash of the strings that produced a project context vector.
    Used to detect when the cache is stale."""
    h = hashlib.blake2b(digest_size=16)
    for p in parts:
        h.update(p.encode("utf-8", errors="replace"))
        h.update(b"\0")
    return h.hexdigest()


_QUERY_TEXT_RE = re.compile(r"\s+")


def _normalize_text(s: str) -> str:
    return _QUERY_TEXT_RE.sub(" ", (s or "").strip())


async def _gather_project_context(
    db: AsyncSession,
    project_id: str | None,
    recording_title: str | None,
) -> tuple[str, list[str]]:
    """Concatenate the strings that drive the project's context vector.

    Returns (concatenated_text, parts) so the caller can cache by hash.
    """
    parts: list[str] = []

    # Recording title is always a useful signal — it's frequently the
    # only context for first-recording-in-a-project sessions.
    if recording_title:
        parts.append(_normalize_text(recording_title))

    if project_id:
        try:
            row = await db.execute(
                text(
                    "SELECT name, description FROM projects WHERE id = :pid"
                ),
                {"pid": project_id},
            )
            r = row.fetchone()
            if r:
                if r[0]:
                    parts.append(_normalize_text(r[0]))
                if r[1]:
                    # Project description is the heaviest signal — repeat
                    # to up-weight in the embedding (Nomic doesn't accept
                    # per-token weights, repetition is the workaround).
                    desc = _normalize_text(r[1])
                    parts.extend([desc, desc])
        except Exception as e:
            logger.debug("vocab_retrieval: project lookup failed: %s", e)

        # Recent transcript ai_summary blobs from the same project.
        try:
            rows = await db.execute(
                text(
                    "SELECT t.ai_summary FROM transcripts t "
                    "JOIN recordings r ON t.recording_id = r.id "
                    "WHERE r.project_id = :pid AND t.ai_summary IS NOT NULL "
                    "ORDER BY t.created_at DESC LIMIT 3"
                ),
                {"pid": project_id},
            )
            for (summary_blob,) in rows.fetchall():
                if isinstance(summary_blob, dict):
                    summary_text = " ".join(
                        v for v in summary_blob.values() if isinstance(v, str)
                    )
                else:
                    summary_text = str(summary_blob or "")
                summary_text = _normalize_text(summary_text)[:500]
                if summary_text:
                    parts.append(summary_text)
        except Exception as e:
            logger.debug("vocab_retrieval: summaries lookup failed: %s", e)

    text_combined = " ".join(parts)
    return text_combined, parts


async def _get_or_build_context_vector(
    db: AsyncSession,
    project_id: str | None,
    recording_title: str | None,
) -> tuple[list[float] | None, str]:
    """Return the project's context vector, building (and caching) on miss.

    Returns (vector, plaintext_for_bm25). When the embedder is
    unavailable, vector is None but the plaintext is still returned for
    BM25-only fallback.
    """
    plain, parts = await _gather_project_context(db, project_id, recording_title)
    if not plain:
        return None, ""

    inputs_hash = _hash_inputs(parts)

    if project_id:
        try:
            cached = await db.execute(
                text(
                    "SELECT embedding, context_hash FROM project_context_embedding "
                    "WHERE project_id = :pid"
                ),
                {"pid": project_id},
            )
            row = cached.fetchone()
            if row and row[1] == inputs_hash and row[0]:
                count = len(row[0]) // 4
                return list(struct.unpack(f"<{count}f", row[0])), plain
        except Exception:
            # Cache table may be missing in dev DBs that haven't run
            # the migration; treat as miss.
            pass

    try:
        from services.embedding import get_embedder
        embedder = get_embedder()
        vector = embedder.embed_query_sync(plain)
    except Exception as e:
        logger.info("vocab_retrieval: embedder unavailable (%s)", e)
        return None, plain

    if project_id and vector is not None:
        try:
            packed = struct.pack(f"<{len(vector)}f", *vector)
            await db.execute(
                text(
                    "INSERT OR REPLACE INTO project_context_embedding "
                    "(project_id, embedding, context_hash) "
                    "VALUES (:pid, :emb, :hash)"
                ),
                {"pid": project_id, "emb": packed, "hash": inputs_hash},
            )
            await db.commit()
        except Exception as e:
            logger.debug("vocab_retrieval: cache write failed: %s", e)

    return vector, plain


# ── Core retrieval ──────────────────────────────────────────────────


def _bm25_query(
    conn: sqlite3.Connection, query_text: str, limit: int = BM25_POOL,
) -> list[sqlite3.Row]:
    """Top-N BM25 matches against vocab_bundled_fts.

    FTS5 needs a search expression — we strip non-alphanumeric chars
    and OR the surviving tokens. Empty queries return nothing.
    """
    tokens = re.findall(r"[A-Za-z0-9]+", query_text.lower())
    tokens = [t for t in tokens if len(t) >= 2 and len(t) <= 60]
    if not tokens:
        return []
    expr = " OR ".join(tokens[:60])  # FTS5 has parser limits on huge OR chains
    try:
        cur = conn.execute(
            "SELECT b.id, b.term, b.canonical_form, b.category, b.sounds_like, "
            "       b.metaphone_primary, b.metaphone_secondary, "
            "       b.popularity_score, "
            "       bm25(vocab_bundled_fts) AS bm25_score "
            "FROM vocab_bundled_fts f "
            "JOIN vocab_bundled b ON b.id = f.rowid "
            "WHERE vocab_bundled_fts MATCH ? "
            "ORDER BY bm25_score LIMIT ?",
            (expr, limit),
        )
        return cur.fetchall()
    except sqlite3.Error as e:
        logger.debug("vocab_retrieval: BM25 query failed: %s", e)
        return []


def _vec_query(
    conn: sqlite3.Connection, vector: list[float], limit: int = COSINE_POOL,
) -> list[sqlite3.Row]:
    """Top-N cosine matches against vocab_bundled_vec via sqlite-vec."""
    if not vector:
        return []
    packed = struct.pack(f"<{len(vector)}f", *vector)
    try:
        cur = conn.execute(
            "SELECT b.id, b.term, b.canonical_form, b.category, b.sounds_like, "
            "       b.metaphone_primary, b.metaphone_secondary, "
            "       b.popularity_score, "
            "       v.distance AS vec_distance "
            "FROM vocab_bundled_vec v "
            "JOIN vocab_bundled b ON b.id = v.term_id "
            "WHERE v.embedding MATCH ? AND k = ? "
            "ORDER BY v.distance",
            (packed, limit),
        )
        return cur.fetchall()
    except sqlite3.Error as e:
        logger.debug("vocab_retrieval: vec query failed: %s", e)
        return []


def _popularity_floor_query(
    conn: sqlite3.Connection, threshold: float, limit: int = 50,
) -> list[sqlite3.Row]:
    """Always-included top-popularity bundled terms."""
    try:
        cur = conn.execute(
            "SELECT id, term, canonical_form, category, sounds_like, "
            "       metaphone_primary, metaphone_secondary, "
            "       popularity_score "
            "FROM vocab_bundled WHERE popularity_score >= ? "
            "ORDER BY popularity_score DESC LIMIT ?",
            (threshold, limit),
        )
        return cur.fetchall()
    except sqlite3.Error as e:
        logger.debug("vocab_retrieval: popularity floor query failed: %s", e)
        return []


def _row_to_retrieved(
    row: sqlite3.Row,
    *,
    is_user: bool = False,
    bm25: float = 0.0,
    cosine: float = 0.0,
    usage_count: int = 0,
    score: float = 0.0,
) -> RetrievedTerm:
    sounds_like_raw = row["sounds_like"] or ""
    sounds_like = [s.strip() for s in sounds_like_raw.split(",") if s.strip()]
    return RetrievedTerm(
        id=str(row["id"]),
        term=row["term"],
        canonical_form=row["canonical_form"],
        category=row["category"],
        sounds_like=sounds_like,
        metaphone_primary=row["metaphone_primary"] or "",
        metaphone_secondary=row["metaphone_secondary"] or "",
        popularity_score=row["popularity_score"] or 0.0,
        is_user_addition=is_user,
        usage_count=usage_count,
        score=score,
    )


def _normalize_bm25(scores: list[float]) -> list[float]:
    """FTS5 BM25 scores are negative (more-negative = better). Map to
    [0, 1] where 1 is best."""
    if not scores:
        return []
    worst = max(scores)  # least negative
    best = min(scores)  # most negative
    if worst == best:
        return [1.0] * len(scores)
    spread = worst - best
    return [(worst - s) / spread for s in scores]


def _normalize_cosine_distance(distances: list[float]) -> list[float]:
    """sqlite-vec returns L2 distance by default. Smaller is better.
    Map to [0, 1] where 1 is best (closest)."""
    if not distances:
        return []
    worst = max(distances)
    best = min(distances)
    if worst == best:
        return [1.0] * len(distances)
    spread = worst - best
    return [(worst - d) / spread for d in distances]


# ── Public entry point ──────────────────────────────────────────────


async def retrieve_for_project(
    db: AsyncSession,
    *,
    project_id: str | None,
    recording_title: str | None = None,
    limit: int = DEFAULT_RETRIEVAL_LIMIT,
) -> list[RetrievedTerm]:
    """Top-K context-aware retrieval against bundled + user corpus.

    Returns terms ordered by hybrid relevance score. Drops gracefully
    through three operational modes (full hybrid → BM25-only → user-only)
    without behavior changes for existing installs.
    """
    conn, has_vec = _open_bundled_conn()
    if conn is None:
        # Mode 3: no bundled corpus — fall through to v0.64.x user-only.
        return await _load_user_only(db, project_id=project_id, limit=limit)

    vector, plain = await _get_or_build_context_vector(db, project_id, recording_title)

    # Stage 2 — gather candidates from each leg.
    bm25_rows = _bm25_query(conn, plain, limit=BM25_POOL) if plain else []
    cosine_rows = _vec_query(conn, vector or [], limit=COSINE_POOL) if (has_vec and vector) else []
    popular_rows = _popularity_floor_query(conn, POPULARITY_FLOOR)
    user_rows = await _load_user_rows(db, project_id=project_id)

    # Normalize per-leg scores into [0, 1].
    bm25_norm = dict(zip(
        [r["id"] for r in bm25_rows],
        _normalize_bm25([r["bm25_score"] for r in bm25_rows]),
    ))
    cosine_norm = dict(zip(
        [r["id"] for r in cosine_rows],
        _normalize_cosine_distance([r["vec_distance"] for r in cosine_rows]),
    ))

    # Merge candidates by ID.
    by_id: dict[str, sqlite3.Row | dict] = {}
    for r in bm25_rows:
        by_id[str(r["id"])] = r
    for r in cosine_rows:
        by_id.setdefault(str(r["id"]), r)
    for r in popular_rows:
        by_id.setdefault(str(r["id"]), r)

    # Score and emit.
    out: list[RetrievedTerm] = []
    seen_keys: set[str] = set()

    # User additions are pinned — emit them first with their own score.
    for ur in user_rows:
        key = ur["id"]
        if key in seen_keys:
            continue
        seen_keys.add(key)
        out.append(_user_row_to_retrieved(ur, score=RANK_IS_USER + RANK_USAGE * (ur["usage_count"] or 0) / 100.0))

    # Bundled candidates ranked by combined score.
    bundled_scored: list[tuple[float, RetrievedTerm]] = []
    for term_id, row in by_id.items():
        bm25_s = bm25_norm.get(int(term_id), 0.0)
        cosine_s = cosine_norm.get(int(term_id), 0.0)
        pop = float(row["popularity_score"] or 0.0)
        combined = (
            RANK_BM25 * bm25_s
            + RANK_COSINE * cosine_s
            + RANK_POPULARITY * pop
        )
        bundled_scored.append((
            combined,
            _row_to_retrieved(
                row, is_user=False, bm25=bm25_s, cosine=cosine_s, score=combined,
            ),
        ))

    bundled_scored.sort(key=lambda p: p[0], reverse=True)

    for _, rt in bundled_scored:
        if len(out) >= limit:
            break
        if rt.id in seen_keys:
            continue
        seen_keys.add(rt.id)
        out.append(rt)

    logger.info(
        "vocab_retrieval: project=%s mode=%s bm25=%d vec=%d popular=%d user=%d → returned=%d",
        project_id or "global",
        "hybrid" if (has_vec and vector) else ("bm25-only" if plain else "fallback"),
        len(bm25_rows), len(cosine_rows), len(popular_rows), len(user_rows),
        len(out),
    )
    return out[:limit]


# ── User-side loaders ───────────────────────────────────────────────


async def _load_user_rows(
    db: AsyncSession,
    *,
    project_id: str | None,
) -> list[dict]:
    """Load all user-side dictionary entries scoped to this project.

    Reads from the v0.64.x `custom_dictionary` table (will become
    `vocab_user` in Phase D). Returns plain dicts so the legacy DB
    schema (no metaphone columns) maps cleanly.
    """
    if project_id:
        sql = (
            "SELECT id, term, category, sounds_like, priority, usage_count "
            "FROM custom_dictionary "
            "WHERE project_id IS NULL OR project_id = :pid "
            "ORDER BY priority DESC, usage_count DESC"
        )
        params: dict = {"pid": project_id}
    else:
        sql = (
            "SELECT id, term, category, sounds_like, priority, usage_count "
            "FROM custom_dictionary "
            "WHERE project_id IS NULL "
            "ORDER BY priority DESC, usage_count DESC"
        )
        params = {}
    try:
        result = await db.execute(text(sql), params)
        return [
            {
                "id": r[0],
                "term": r[1],
                "category": r[2] or "general",
                "sounds_like": r[3] or "",
                "priority": r[4] or 0,
                "usage_count": r[5] or 0,
            }
            for r in result.fetchall()
        ]
    except Exception as e:
        logger.warning("vocab_retrieval user load failed: %s", e)
        return []


def _user_row_to_retrieved(row: dict, *, score: float = 0.0) -> RetrievedTerm:
    sounds_like = [s.strip() for s in (row["sounds_like"] or "").split(",") if s.strip()]
    return RetrievedTerm(
        id=row["id"],
        term=row["term"],
        canonical_form=row["term"],
        category=row["category"],
        sounds_like=sounds_like,
        metaphone_primary="",
        metaphone_secondary="",
        popularity_score=float(row.get("priority", 0)) / 50.0,
        is_user_addition=True,
        usage_count=row.get("usage_count", 0),
        score=score,
    )


async def _load_user_only(
    db: AsyncSession,
    *,
    project_id: str | None,
    limit: int,
) -> list[RetrievedTerm]:
    """Compatibility path — bundled corpus unavailable, return user-only."""
    rows = await _load_user_rows(db, project_id=project_id)
    return [_user_row_to_retrieved(r) for r in rows[:limit]]


def to_legacy_entries(retrieved: list[RetrievedTerm]) -> list:
    """Bridge to v0.64.x CustomDictionaryEntry shape so jobs.py,
    voice_agent.py, vocab_correction.py, and llm_vocab_correction.py
    keep working unchanged."""
    from services.custom_dictionary import CustomDictionaryEntry

    return [
        CustomDictionaryEntry(
            id=r.id,
            term=r.canonical_form,
            category=r.category,
            project_id=None,
            sounds_like=r.sounds_like,
            priority=int(r.popularity_score * 50),
            usage_count=r.usage_count,
        )
        for r in retrieved
    ]

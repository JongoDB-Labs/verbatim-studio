"""Custom dictionary REST API.

CRUD for the per-user custom vocabulary that biases Whisper transcription
toward domain-specific terms (acronyms, proper nouns, brand names).

Surface mirrors the cross-vendor norm (Otter, Speechmatics, Deepgram):
- Term + optional category
- Optional `sounds_like` for free-form phonetic respellings (Speechmatics-style)
- Optional `priority` (0-100). Higher = lands later in the Whisper prompt
  where encoder attention is strongest. We expose this rather than a
  continuous boost knob because (a) Deepgram and Azure both warn that
  arbitrary boost knobs cause false-positive insertions, and (b) the user
  can already say "this term matters more" — that's exactly what priority
  encodes, with a bounded range.
- Optional `project_id` — global vs. project-scoped, like Otter's two-list model.

Bulk import accepts CSV with headers (term, sounds_like, category, priority).
"""

from __future__ import annotations

import csv
import io
import logging
import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File, status
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from persistence import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dictionary", tags=["dictionary"])


# Category vocabulary — frontend renders these as a dropdown. "general" is
# the catch-all default. Keep the list short; long taxonomies hurt UX more
# than they help. "auto_learned" is reserved for terms the auto-learn
# subsystem promoted from user corrections (Wispr Flow's sparkle marker
# pattern); the UI renders it with a 🔮 indicator and lets the user
# re-categorize.
ALLOWED_CATEGORIES = {
    "general", "tech", "medical", "legal", "names", "business",
    "auto_learned",
}

# Bounded priority range. Three semantic stops are enough — see plan:
#   0  = normal
#   10 = important (lands near end of prompt)
#   50 = critical (always lands last when budget is tight)
# Frontend exposes this as a 3-button toggle, not a free slider, to match
# AssemblyAI's "deliberately coarse" pattern.
ALLOWED_PRIORITIES = {0, 10, 50}


# ── Request / response shapes ────────────────────────────────────────


class DictionaryEntryResponse(BaseModel):
    id: str
    term: str
    category: str
    project_id: str | None
    sounds_like: list[str]
    priority: int
    usage_count: int
    created_at: datetime | None

    class Config:
        from_attributes = True


class DictionaryEntryCreate(BaseModel):
    term: str = Field(..., min_length=1, max_length=200)
    category: str = "general"
    project_id: str | None = None
    sounds_like: list[str] = Field(default_factory=list)
    priority: int = 0

    @field_validator("category")
    @classmethod
    def _category_in_allowed(cls, v: str) -> str:
        if v not in ALLOWED_CATEGORIES:
            raise ValueError(
                f"category must be one of {sorted(ALLOWED_CATEGORIES)}"
            )
        return v

    @field_validator("priority")
    @classmethod
    def _priority_in_allowed(cls, v: int) -> int:
        if v not in ALLOWED_PRIORITIES:
            raise ValueError(
                f"priority must be one of {sorted(ALLOWED_PRIORITIES)} (0=normal, 10=important, 50=critical)"
            )
        return v

    @field_validator("sounds_like")
    @classmethod
    def _sounds_like_clean(cls, v: list[str]) -> list[str]:
        cleaned = [s.strip() for s in v if s and s.strip()]
        if len(cleaned) > 10:
            raise ValueError("sounds_like accepts at most 10 alternates per term")
        return cleaned


class DictionaryEntryUpdate(BaseModel):
    term: str | None = Field(default=None, min_length=1, max_length=200)
    category: str | None = None
    sounds_like: list[str] | None = None
    priority: int | None = None

    @field_validator("category")
    @classmethod
    def _category(cls, v: str | None) -> str | None:
        if v is not None and v not in ALLOWED_CATEGORIES:
            raise ValueError(f"category must be one of {sorted(ALLOWED_CATEGORIES)}")
        return v

    @field_validator("priority")
    @classmethod
    def _priority(cls, v: int | None) -> int | None:
        if v is not None and v not in ALLOWED_PRIORITIES:
            raise ValueError(f"priority must be one of {sorted(ALLOWED_PRIORITIES)}")
        return v


class DictionaryListResponse(BaseModel):
    entries: list[DictionaryEntryResponse]
    total: int


class ImportResponse(BaseModel):
    imported: int
    skipped: int
    errors: list[str]


# ── Helpers ─────────────────────────────────────────────────────────


def _row_to_response(row) -> DictionaryEntryResponse:
    sounds_like_raw = row[5] if len(row) > 5 else None
    sounds_like = (
        [s.strip() for s in sounds_like_raw.split(",") if s.strip()]
        if sounds_like_raw else []
    )
    return DictionaryEntryResponse(
        id=row[0],
        term=row[1],
        category=row[2] or "general",
        project_id=row[3],
        created_at=row[4],
        sounds_like=sounds_like,
        priority=(row[6] if len(row) > 6 else 0) or 0,
        usage_count=(row[7] if len(row) > 7 else 0) or 0,
    )


def _serialize_sounds_like(sounds_like: list[str]) -> str | None:
    cleaned = [s.strip() for s in sounds_like if s and s.strip()]
    if not cleaned:
        return None
    return ",".join(cleaned)


# ── Endpoints ───────────────────────────────────────────────────────


@router.get("", response_model=DictionaryListResponse)
async def list_entries(
    project_id: str | None = Query(default=None, description="If set, also include entries scoped to this project"),
    category: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> DictionaryListResponse:
    """List dictionary entries.

    Without `project_id`, returns global entries only.
    With `project_id`, returns global entries + project-scoped entries (matches
    how the entries are loaded for transcription).
    """
    if project_id:
        query = (
            "SELECT id, term, category, project_id, created_at, "
            "sounds_like, priority, usage_count "
            "FROM custom_dictionary "
            "WHERE (project_id IS NULL OR project_id = :pid)"
        )
        params: dict[str, object] = {"pid": project_id}
    else:
        query = (
            "SELECT id, term, category, project_id, created_at, "
            "sounds_like, priority, usage_count "
            "FROM custom_dictionary "
            "WHERE project_id IS NULL"
        )
        params = {}

    if category:
        if category not in ALLOWED_CATEGORIES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unknown category: {category}",
            )
        query += " AND category = :cat"
        params["cat"] = category

    query += " ORDER BY priority DESC, usage_count DESC, term"

    result = await db.execute(text(query), params)
    rows = result.fetchall()
    entries = [_row_to_response(r) for r in rows]
    return DictionaryListResponse(entries=entries, total=len(entries))


@router.post("", response_model=DictionaryEntryResponse, status_code=status.HTTP_201_CREATED)
async def create_entry(
    body: DictionaryEntryCreate,
    db: AsyncSession = Depends(get_db),
) -> DictionaryEntryResponse:
    """Create a dictionary entry. Term uniqueness is case-insensitive within scope."""
    entry_id = str(uuid.uuid4())

    # Reject duplicates (case-insensitive) within the same scope. Better to
    # 409 than to silently create a parallel entry that competes for the
    # same prompt budget.
    scope_clause = "project_id = :pid" if body.project_id else "project_id IS NULL"
    params = {"term_lower": body.term.strip().lower()}
    if body.project_id:
        params["pid"] = body.project_id
    dup = await db.execute(
        text(
            f"SELECT id FROM custom_dictionary "
            f"WHERE LOWER(term) = :term_lower AND {scope_clause}"
        ),
        params,
    )
    if dup.fetchone():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Dictionary already contains '{body.term}' in this scope",
        )

    await db.execute(
        text(
            "INSERT INTO custom_dictionary "
            "(id, term, category, project_id, sounds_like, priority, usage_count) "
            "VALUES (:id, :term, :category, :project_id, :sounds_like, :priority, 0)"
        ),
        {
            "id": entry_id,
            "term": body.term.strip(),
            "category": body.category,
            "project_id": body.project_id,
            "sounds_like": _serialize_sounds_like(body.sounds_like),
            "priority": body.priority,
        },
    )
    await db.commit()

    result = await db.execute(
        text(
            "SELECT id, term, category, project_id, created_at, "
            "sounds_like, priority, usage_count "
            "FROM custom_dictionary WHERE id = :id"
        ),
        {"id": entry_id},
    )
    row = result.fetchone()
    return _row_to_response(row)


@router.put("/{entry_id}", response_model=DictionaryEntryResponse)
async def update_entry(
    entry_id: str,
    body: DictionaryEntryUpdate,
    db: AsyncSession = Depends(get_db),
) -> DictionaryEntryResponse:
    """Update a dictionary entry. Only fields present in the request are touched."""
    result = await db.execute(
        text("SELECT id FROM custom_dictionary WHERE id = :id"),
        {"id": entry_id},
    )
    if result.fetchone() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Entry not found")

    set_clauses: list[str] = []
    params: dict[str, object] = {"id": entry_id}
    if body.term is not None:
        set_clauses.append("term = :term")
        params["term"] = body.term.strip()
    if body.category is not None:
        set_clauses.append("category = :category")
        params["category"] = body.category
    if body.sounds_like is not None:
        set_clauses.append("sounds_like = :sounds_like")
        params["sounds_like"] = _serialize_sounds_like(body.sounds_like)
    if body.priority is not None:
        set_clauses.append("priority = :priority")
        params["priority"] = body.priority

    if set_clauses:
        await db.execute(
            text(f"UPDATE custom_dictionary SET {', '.join(set_clauses)} WHERE id = :id"),
            params,
        )
        await db.commit()

    result = await db.execute(
        text(
            "SELECT id, term, category, project_id, created_at, "
            "sounds_like, priority, usage_count "
            "FROM custom_dictionary WHERE id = :id"
        ),
        {"id": entry_id},
    )
    return _row_to_response(result.fetchone())


@router.delete(
    "/{entry_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def delete_entry(
    entry_id: str,
    db: AsyncSession = Depends(get_db),
) -> Response:
    result = await db.execute(
        text("DELETE FROM custom_dictionary WHERE id = :id"),
        {"id": entry_id},
    )
    await db.commit()
    if result.rowcount == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Entry not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


class ClearResponse(BaseModel):
    deleted: int


@router.delete("", response_model=ClearResponse)
async def clear_entries(
    project_id: str | None = Query(default=None, description="If set, clears only entries scoped to this project. Otherwise clears global entries."),
    db: AsyncSession = Depends(get_db),
) -> ClearResponse:
    """Bulk-delete entries within a scope.

    With project_id: deletes only that project's entries.
    Without:        deletes only global entries (project_id IS NULL).

    Deliberately does NOT delete everything across all scopes — a single
    DELETE call would be too destructive to bind to a "Clear all" button.
    """
    if project_id:
        result = await db.execute(
            text("DELETE FROM custom_dictionary WHERE project_id = :pid"),
            {"pid": project_id},
        )
    else:
        result = await db.execute(
            text("DELETE FROM custom_dictionary WHERE project_id IS NULL")
        )
    await db.commit()
    return ClearResponse(deleted=result.rowcount or 0)


@router.post("/import", response_model=ImportResponse)
async def import_csv(
    file: UploadFile = File(...),
    project_id: Annotated[str | None, Query()] = None,
    db: AsyncSession = Depends(get_db),
) -> ImportResponse:
    """CSV bulk import.

    Expected columns (case-insensitive headers):
      term         — required
      sounds_like  — optional, comma-separated alternates within the cell
                     (use ; or | to separate; commas conflict with CSV)
      category     — optional, defaults to 'general'
      priority     — optional integer in {0,10,50}, defaults to 0

    Skips rows that already exist (case-insensitive match within scope).
    """
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="File must be a .csv")

    content = (await file.read()).decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(content))
    if reader.fieldnames is None:
        raise HTTPException(status_code=400, detail="CSV is empty or has no header row")

    headers = {h.lower().strip(): h for h in reader.fieldnames}
    if "term" not in headers:
        raise HTTPException(status_code=400, detail="CSV must have a 'term' column")

    existing_terms = await db.execute(
        text(
            "SELECT LOWER(term) FROM custom_dictionary "
            "WHERE (project_id IS NULL AND :pid IS NULL) "
            "   OR (:pid IS NOT NULL AND project_id = :pid)"
        ),
        {"pid": project_id},
    )
    existing = {r[0] for r in existing_terms.fetchall()}

    imported = 0
    skipped = 0
    errors: list[str] = []

    for row_num, row in enumerate(reader, start=2):
        term = (row.get(headers["term"]) or "").strip()
        if not term:
            skipped += 1
            continue
        if term.lower() in existing:
            skipped += 1
            continue

        sounds_like_raw = (
            row.get(headers["sounds_like"]) if "sounds_like" in headers else ""
        ) or ""
        # Accept ; or | as separators inside the cell — CSV's comma conflicts.
        sounds_like = [
            s.strip() for s in sounds_like_raw.replace("|", ";").split(";") if s.strip()
        ]

        category = (row.get(headers["category"]) if "category" in headers else "general") or "general"
        category = category.strip().lower()
        if category not in ALLOWED_CATEGORIES:
            errors.append(f"row {row_num}: invalid category '{category}'")
            skipped += 1
            continue

        priority_str = (row.get(headers["priority"]) if "priority" in headers else "0") or "0"
        try:
            priority = int(str(priority_str).strip())
        except ValueError:
            priority = 0
        if priority not in ALLOWED_PRIORITIES:
            errors.append(f"row {row_num}: priority {priority} not in {sorted(ALLOWED_PRIORITIES)}")
            priority = 0

        await db.execute(
            text(
                "INSERT INTO custom_dictionary "
                "(id, term, category, project_id, sounds_like, priority, usage_count) "
                "VALUES (:id, :term, :category, :project_id, :sounds_like, :priority, 0)"
            ),
            {
                "id": str(uuid.uuid4()),
                "term": term,
                "category": category,
                "project_id": project_id,
                "sounds_like": _serialize_sounds_like(sounds_like),
                "priority": priority,
            },
        )
        existing.add(term.lower())
        imported += 1

    await db.commit()
    return ImportResponse(imported=imported, skipped=skipped, errors=errors)


# ─── Phase C: Document-upload extraction ────────────────────────────


class ExtractFromDocResponse(BaseModel):
    """Result of extracting vocabulary from an uploaded document."""

    document_id: str | None
    candidates_proposed: int
    accepted: int
    skipped_already_bundled: int
    skipped_already_user: int
    skipped_invalid: int
    skipped_common_english: int
    new_term_ids: list[str]
    errors: list[str] = []


@router.post("/extract-from-document/{document_id}", response_model=ExtractFromDocResponse)
async def extract_from_document_endpoint(
    document_id: str,
    project_id: Annotated[str | None, Query()] = None,
    db: AsyncSession = Depends(get_db),
) -> ExtractFromDocResponse:
    """Run Granite-Tiny over an already-uploaded document and add the
    extracted acronyms / proper nouns / domain terms to the user's
    custom dictionary.

    The document must already exist in the database (uploaded via the
    /documents endpoint). This endpoint pulls its extracted text,
    chunks it, runs the LLM, and writes the survivors of the dedup +
    common-English filter pipeline into custom_dictionary.

    No streaming — returns the full result as JSON. Granite-Tiny on
    CPU can take 5-15 seconds per typical PDF; clients should show a
    spinner. Latency caps at ~3 minutes for very large docs (~30 chunks).
    """
    from persistence.models import Document
    from sqlalchemy import select as _select

    doc_row = await db.execute(_select(Document).where(Document.id == document_id))
    doc = doc_row.scalar_one_or_none()
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")

    # Pull text the same way semantic search does — extracted_text
    # is populated by services/document_processor on upload.
    document_text = (doc.extracted_text or "").strip()
    if not document_text:
        raise HTTPException(
            status_code=400,
            detail="Document has no extracted text. Reprocess via OCR first.",
        )

    # Resolve project context — caller-provided wins, otherwise inherit
    # from the document's project association.
    if project_id is None:
        project_id = doc.project_id

    # AI service — late import so the route can fail fast if Granite
    # isn't loaded.
    try:
        from core.factory import get_factory
        factory = get_factory()
        ai_service = factory.create_ai_service()
    except Exception as e:
        raise HTTPException(
            status_code=503,
            detail=f"AI service unavailable for extraction: {e}",
        )

    from services.vocab_extraction import extract_from_document

    result = await extract_from_document(
        db,
        document_id=document_id,
        document_text=document_text,
        document_title=doc.title,
        ai_service=ai_service,
        project_id=project_id,
    )

    return ExtractFromDocResponse(
        document_id=result.document_id,
        candidates_proposed=result.candidates_proposed,
        accepted=result.accepted,
        skipped_already_bundled=result.skipped_already_bundled,
        skipped_already_user=result.skipped_already_user,
        skipped_invalid=result.skipped_invalid,
        skipped_common_english=result.skipped_common_english,
        new_term_ids=result.new_term_ids,
        errors=result.errors,
    )


# ── Full-corpus download (BM25-only → BM25 + cosine hybrid) ─────────
#
# The installer ships a slim vocab_bundled.db (BM25 only) — ~95 MB,
# enough for full lexical retrieval + category broadcast. Users who
# want semantic recall (e.g. "Marine Corps administration" surfacing
# MARCORSEPMAN even without a matching token) opt into downloading
# the full embedded variant from verbatim-studio-releases.
#
# The runtime locator already prefers ${DATA_DIR}/vocab_bundled.db
# over the resources copy (see services/vocab_retrieval.py:_bundled_db_path),
# so writing the downloaded full DB into user data dir transparently
# upgrades retrieval to hybrid mode without an app restart.

import asyncio  # noqa: E402  (lazy in the corpus block)
import concurrent.futures  # noqa: E402
import json  # noqa: E402
from pathlib import Path  # noqa: E402

# Where the full-corpus artifact lives. `latest/download/<file>` redirects
# to the most recent release's asset with that filename — corpus updates
# follow the app release cadence, so "latest" is correct here.
CORPUS_DOWNLOAD_URL = (
    "https://github.com/JongoDB/verbatim-studio-releases"
    "/releases/latest/download/vocab_bundled_full.db"
)

# Track the in-flight download so a second POST returns 409 instead of
# kicking off a duplicate.
_corpus_download_future: concurrent.futures.Future | None = None
_corpus_download_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
_corpus_download_progress: dict[str, int | str] = {}


def _corpus_target_path() -> Path:
    """Where the downloaded full corpus is written. Takes precedence
    over the bundled slim DB at runtime."""
    from core.config import settings
    settings.ensure_directories()
    return Path(settings.DATA_DIR) / "vocab_bundled.db"


def _do_corpus_download(target: Path) -> None:
    """Stream the corpus DB to a temp file then atomic-rename.

    Updates `_corpus_download_progress` with downloaded/total bytes so
    the SSE wrapper can poll. Errors propagate to the future; the SSE
    wrapper catches them and emits a final `error` event.
    """
    import shutil
    import tempfile
    import urllib.request

    tmp = target.with_suffix(target.suffix + ".part")
    if tmp.exists():
        tmp.unlink()
    target.parent.mkdir(parents=True, exist_ok=True)

    req = urllib.request.Request(
        CORPUS_DOWNLOAD_URL,
        headers={"User-Agent": "verbatim-studio/corpus-download"},
    )
    with urllib.request.urlopen(req) as resp:
        total = int(resp.headers.get("Content-Length") or 0)
        _corpus_download_progress["total_bytes"] = total
        downloaded = 0
        with tmp.open("wb") as f:
            while chunk := resp.read(1024 * 1024):  # 1 MB chunks
                f.write(chunk)
                downloaded += len(chunk)
                _corpus_download_progress["downloaded_bytes"] = downloaded

    # Atomic replace — never leave a partial DB at the canonical path.
    if target.exists():
        target.unlink()
    tmp.rename(target)
    _corpus_download_progress["downloaded_bytes"] = target.stat().st_size

    # Tell the retrieval layer to re-open with the new file.
    try:
        from services.vocab_retrieval import reload_bundled_conn
        has_vec = reload_bundled_conn()
        _corpus_download_progress["has_vec"] = "true" if has_vec else "false"
    except Exception as e:
        logger.warning("corpus reload after download failed: %s", e)
        _corpus_download_progress["has_vec"] = "unknown"


class CorpusStatusResponse(BaseModel):
    """Current state of the full-corpus download."""

    downloaded: bool
    downloading: bool
    has_embeddings: bool
    bytes_on_disk: int | None
    download_url: str


@router.get("/corpus/status", response_model=CorpusStatusResponse)
async def get_corpus_status() -> CorpusStatusResponse:
    """Report whether the full embedded corpus has been downloaded."""
    target = _corpus_target_path()
    downloaded = target.exists()
    downloading = (
        _corpus_download_future is not None
        and not _corpus_download_future.done()
    )

    has_embeddings = False
    if downloaded:
        # Probe sqlite_master directly — the vec virtual table appears in
        # the catalog even without the sqlite-vec extension loaded, so we
        # don't depend on extension-loading capability or URI-mode quirks
        # (URI mode + paths with spaces / unicode is a known foot-gun).
        import sqlite3
        try:
            conn = sqlite3.connect(str(target))
            try:
                row = conn.execute(
                    "SELECT 1 FROM sqlite_master "
                    "WHERE name = 'vocab_bundled_vec' AND type = 'table' LIMIT 1"
                ).fetchone()
                has_embeddings = row is not None
            finally:
                conn.close()
        except Exception as e:
            logger.warning("corpus status probe failed for %s: %s", target, e)
            has_embeddings = False

    return CorpusStatusResponse(
        downloaded=downloaded,
        downloading=downloading,
        has_embeddings=has_embeddings,
        bytes_on_disk=target.stat().st_size if downloaded else None,
        download_url=CORPUS_DOWNLOAD_URL,
    )


@router.post("/corpus/download")
async def download_full_corpus() -> StreamingResponse:
    """Begin downloading the full embedded corpus.

    Streams Server-Sent-Events with `{status, downloaded_bytes, total_bytes,
    percent}` payloads. The download continues even if the client
    disconnects — clients can re-attach via /corpus/status to see
    progress and replay the final state.
    """
    global _corpus_download_future

    if _corpus_download_future is not None and not _corpus_download_future.done():
        raise HTTPException(status_code=409, detail="Download already in progress")

    target = _corpus_target_path()
    _corpus_download_progress.clear()
    _corpus_download_future = _corpus_download_executor.submit(
        _do_corpus_download, target,
    )

    async def _stream():
        yield f"data: {json.dumps({'status': 'starting'})}\n\n"
        last_emitted = -1
        while True:
            await asyncio.sleep(1)
            done = _corpus_download_future.done()
            downloaded = int(_corpus_download_progress.get("downloaded_bytes", 0) or 0)
            total = int(_corpus_download_progress.get("total_bytes", 0) or 0)
            percent = int(downloaded / total * 100) if total else 0
            if percent != last_emitted:
                last_emitted = percent
                yield f"data: {json.dumps({'status': 'progress', 'downloaded_bytes': downloaded, 'total_bytes': total, 'percent': percent})}\n\n"
            if done:
                exc = _corpus_download_future.exception()
                if exc:
                    yield f"data: {json.dumps({'status': 'error', 'message': str(exc)})}\n\n"
                else:
                    has_vec = _corpus_download_progress.get("has_vec", "unknown")
                    yield f"data: {json.dumps({'status': 'complete', 'has_embeddings': has_vec == 'true', 'bytes_on_disk': downloaded})}\n\n"
                return

    return StreamingResponse(_stream(), media_type="text/event-stream")


@router.delete("/corpus")
async def remove_full_corpus() -> dict:
    """Delete the downloaded full corpus, falling back to the slim
    BM25-only DB shipped with the installer.

    Useful when the user wants to free disk space or roll back a bad
    corpus version.
    """
    if _corpus_download_future is not None and not _corpus_download_future.done():
        raise HTTPException(status_code=409, detail="Cannot remove while a download is in progress")

    target = _corpus_target_path()
    if not target.exists():
        raise HTTPException(status_code=404, detail="No downloaded corpus to remove")

    target.unlink()

    try:
        from services.vocab_retrieval import reload_bundled_conn
        has_vec = reload_bundled_conn()
    except Exception as e:
        logger.warning("retrieval reload after removal failed: %s", e)
        has_vec = False

    return {"removed": True, "has_embeddings": has_vec}

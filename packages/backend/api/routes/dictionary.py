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
from fastapi.responses import Response
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

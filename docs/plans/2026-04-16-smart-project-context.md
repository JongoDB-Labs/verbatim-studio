# Smart Project Context Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the naive "dump all transcripts" project context with a compact project index (summaries + metadata) that fits in the token budget, and improve tools so Max can retrieve specific content on demand.

**Architecture:** Instead of loading raw transcripts into context (which overflows at 1-2 recordings), build a structured project index from existing `ai_summary` data. The index gives Max a table of contents (~50-100 tokens per item vs ~5,000+ for a full transcript). When Max needs details, it uses the improved `get_context` tool to pull specific segments. Future Option B (semantic RAG) will add embedding-based retrieval to the tool.

**Tech Stack:** Python/FastAPI, SQLAlchemy, llama.cpp (via existing AI service)

---

## Phase 1: Project Index Builder

### Task 1: Create the project index service

**Files:**
- Create: `packages/backend/services/project_index.py`

**Step 1: Write the project index builder**

This service queries all recordings and documents in a project and builds a compact index string that fits within a token budget.

```python
"""Build a compact project index for AI context injection."""

import logging
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from persistence.models import Document, Recording, Transcript

logger = logging.getLogger(__name__)

# Target: ~100 tokens per item, so 50 items ≈ 5,000 tokens
MAX_SUMMARY_CHARS = 200
MAX_KEY_POINTS = 3


async def build_project_index(
    db: AsyncSession,
    project_ids: list[str],
) -> str:
    """Build a compact index of all content in the given projects.

    Returns a structured string with title, date, duration, speaker count,
    and a short summary for each recording and document. Designed to give
    the LLM a "table of contents" without consuming the full context window.
    """
    parts: list[str] = []

    # --- Recordings ---
    rec_query = (
        select(Recording)
        .where(
            Recording.project_id.in_(project_ids),
            Recording.is_archived == False,
        )
        .order_by(Recording.created_at.desc())
    )
    result = await db.execute(rec_query)
    recordings = result.scalars().all()

    if recordings:
        parts.append("## Recordings\n")

    for rec in recordings:
        # Load transcript for summary
        t_result = await db.execute(
            select(Transcript).where(Transcript.recording_id == rec.id)
        )
        transcript = t_result.scalar_one_or_none()

        line = f"- **{rec.title}**"
        meta = []
        if rec.duration_seconds:
            mins = int(rec.duration_seconds // 60)
            meta.append(f"{mins}m")
        if transcript and transcript.word_count:
            meta.append(f"{transcript.word_count:,} words")
        if rec.created_at:
            meta.append(rec.created_at.strftime("%b %d, %Y"))
        if meta:
            line += f" ({', '.join(meta)})"

        # Add summary if available
        if transcript and transcript.ai_summary:
            summary = transcript.ai_summary
            if isinstance(summary, dict):
                text = summary.get("summary", "")
                if text:
                    truncated = text[:MAX_SUMMARY_CHARS]
                    if len(text) > MAX_SUMMARY_CHARS:
                        truncated = truncated.rsplit(" ", 1)[0] + "..."
                    line += f"\n  {truncated}"

                key_points = summary.get("key_points", [])[:MAX_KEY_POINTS]
                if key_points:
                    for kp in key_points:
                        line += f"\n  - {kp}"

        parts.append(line)

    # --- Documents ---
    doc_query = (
        select(Document)
        .where(
            Document.project_id.in_(project_ids),
            Document.is_archived == False,
        )
        .order_by(Document.created_at.desc())
    )
    doc_result = await db.execute(doc_query)
    documents = doc_result.scalars().all()

    if documents:
        parts.append("\n## Documents\n")

    for doc in documents:
        line = f"- **{doc.title}** ({doc.mime_type or 'unknown'}"
        if doc.page_count:
            line += f", {doc.page_count} pages"
        if doc.created_at:
            line += f", {doc.created_at.strftime('%b %d, %Y')}"
        line += ")"

        # Add a short preview of extracted text
        text = doc.extracted_text or doc.extracted_markdown or ""
        if text:
            preview = text[:MAX_SUMMARY_CHARS].replace("\n", " ").strip()
            if len(text) > MAX_SUMMARY_CHARS:
                preview = preview.rsplit(" ", 1)[0] + "..."
            line += f"\n  {preview}"

        parts.append(line)

    if not parts:
        return ""

    header = f"This project contains {len(recordings)} recording(s) and {len(documents)} document(s).\n\n"
    return header + "\n".join(parts)
```

**Step 2: Commit**

```bash
git add packages/backend/services/project_index.py
git commit -m "feat: add project index builder for smart AI context"
```

---

### Task 2: Replace raw transcript dump with project index in chat endpoint

**Files:**
- Modify: `packages/backend/api/routes/ai.py` (lines 794-845)

**Step 1: Replace the auto-inject block**

Find the block starting at line 794:
```python
    if active_project_ids and not request.recording_ids and not request.document_ids and not request.file_context:
```

Replace the entire block (lines 794-845) with:

```python
    if active_project_ids and not request.recording_ids and not request.document_ids and not request.file_context:
        try:
            from services.project_index import build_project_index
            project_index = await build_project_index(db, active_project_ids)
            if project_index:
                context_parts.append(
                    "=== Project Index ===\n"
                    "Below is a summary of all content in the active project. "
                    "Use the get_context or project_search tools to retrieve "
                    "specific transcript segments or document details when needed.\n\n"
                    f"{project_index}\n"
                )
                logger.info(
                    "Project index: injected %d chars from %d project(s)",
                    len(project_index), len(active_project_ids),
                )
        except Exception as e:
            logger.warning("Project index build failed: %s", e)
```

**Step 2: Commit**

```bash
git add packages/backend/api/routes/ai.py
git commit -m "feat: replace raw transcript dump with compact project index"
```

---

## Phase 2: Improve On-Demand Retrieval Tools

### Task 3: Enhance get_context tool with more results and better formatting

**Files:**
- Modify: `packages/backend/services/tools/context_tool.py`

**Step 1: Improve the tool**

Replace the entire file with an improved version that:
- Returns more context per result (1000 chars instead of 500)
- Includes recording title and timestamp formatting
- Adds a `recording_title` filter parameter so Max can ask for content from a specific recording
- Searches by title match (not just content ILIKE) for recordings

```python
"""Context retrieval tool — pull content from the active project.

Lets Max proactively find relevant transcripts and documents
without requiring the user to manually attach them.
"""

from __future__ import annotations

import logging

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from services.tool_registry import ToolContext, ToolDef, ToolResult

logger = logging.getLogger(__name__)


def _format_time(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


async def handle_get_context(args: dict, ctx: ToolContext) -> ToolResult:
    """Pull relevant content from the active project."""
    query = args.get("query", "")
    recording_title = args.get("recording_title", "")
    content_types = args.get("content_types", ["transcript", "document"])

    from persistence.models import Segment, Transcript, Recording, Document

    parts = []

    # Search transcript segments
    if "transcript" in content_types:
        seg_q = (
            select(Segment, Recording.title)
            .join(Transcript, Segment.transcript_id == Transcript.id)
            .join(Recording, Transcript.recording_id == Recording.id)
            .where(Recording.is_archived == False)
        )

        if recording_title:
            seg_q = seg_q.where(Recording.title.ilike(f"%{recording_title}%"))
        if query:
            seg_q = seg_q.where(Segment.text.ilike(f"%{query}%"))

        seg_q = seg_q.order_by(Segment.start_time).limit(20)

        if ctx.project_id:
            seg_q = seg_q.where(Recording.project_id == ctx.project_id)

        rows = (await ctx.db.execute(seg_q)).all()
        for seg, rec_title in rows:
            speaker = f"[{seg.speaker}] " if seg.speaker else ""
            time = _format_time(seg.start_time) if seg.start_time else ""
            parts.append(
                f"[{rec_title} @ {time}] {speaker}{seg.text}"
            )

    # Search documents
    if "document" in content_types:
        doc_q = select(Document).where(Document.is_archived == False)
        if query:
            doc_q = doc_q.where(
                or_(
                    Document.extracted_text.ilike(f"%{query}%"),
                    Document.title.ilike(f"%{query}%"),
                )
            )
        doc_q = doc_q.limit(5)
        if ctx.project_id:
            doc_q = doc_q.where(Document.project_id == ctx.project_id)
        docs = (await ctx.db.execute(doc_q)).scalars().all()
        for d in docs:
            text_preview = (d.extracted_text or d.extracted_markdown or "")[:1000]
            parts.append(f"[Document: {d.title}]\n{text_preview}")

    if not parts:
        return ToolResult(
            content="No relevant content found in the current project for that query. "
            "Try different search terms or check if the project has transcripts."
        )

    return ToolResult(
        content=f"Found {len(parts)} result(s):\n\n" + "\n\n---\n\n".join(parts)
    )


context_tool = ToolDef(
    name="get_context",
    description=(
        "Retrieve specific content from the active project's transcripts and documents. "
        "Use this when you need the actual text from a recording or document — "
        "the project index only shows summaries. You can search by keyword, "
        "or filter by recording title to get segments from a specific transcript."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search term to find in transcript segments or documents",
            },
            "recording_title": {
                "type": "string",
                "description": "Optional: filter to segments from a specific recording by title (partial match)",
            },
            "content_types": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Types to search: 'transcript', 'document'. Defaults to both.",
            },
        },
        "required": ["query"],
    },
    handler=handle_get_context,
    project_scoped=True,
)
```

**Step 2: Commit**

```bash
git add packages/backend/services/tools/context_tool.py
git commit -m "feat: enhance get_context tool with better formatting and recording title filter"
```

---

### Task 4: Update the system prompt to guide Max on using tools with the index

**Files:**
- Modify: `packages/backend/api/routes/ai.py` (the MAX_SYSTEM_PROMPT constant)

**Step 1: Find the MAX_SYSTEM_PROMPT definition**

Search for `MAX_SYSTEM_PROMPT =` in ai.py and add guidance about the project index pattern.

Add these lines to the end of the system prompt (before the closing quote):

```
When a project is active, you receive a Project Index — a summary of all recordings and documents in that project. This is a table of contents, not the full content. When you need specific transcript text, quotes, or document details, use the get_context tool to retrieve them. Always cite the recording title and timestamp when referencing transcript content.
```

**Step 2: Commit**

```bash
git add packages/backend/api/routes/ai.py
git commit -m "feat: update system prompt with project index guidance"
```

---

## Phase 3: Handle Edge Cases

### Task 5: Support hybrid mode — manual attachments + project index

**Files:**
- Modify: `packages/backend/api/routes/ai.py`

**Step 1: Change the auto-inject condition**

Currently project auto-context is skipped entirely when the user attaches anything. Change it to always inject the project index (it's small), and only skip the raw transcript loading:

Find the condition on line 794:
```python
    if active_project_ids and not request.recording_ids and not request.document_ids and not request.file_context:
```

Change to:
```python
    if active_project_ids:
```

The index is compact enough (~50-100 tokens per item) that it can coexist with manually attached content. The existing explicit attachments (loaded in lines 738-789) remain — they provide raw transcript text for the specific items the user pinned. The index provides awareness of everything else.

**Step 2: Commit**

```bash
git add packages/backend/api/routes/ai.py
git commit -m "feat: always inject project index even with manual attachments"
```

---

### Task 6: Graceful fallback when no ai_summary exists

**Files:**
- Modify: `packages/backend/services/project_index.py`

**Step 1: Add fallback for recordings without summaries**

In the recording loop in `build_project_index`, after the ai_summary block, add a fallback that shows word count and speaker count when no summary is available:

```python
        # Fallback: if no summary, show basic stats
        if not (transcript and transcript.ai_summary):
            if transcript:
                # Count speakers from segments
                from persistence.models import Segment
                speaker_result = await db.execute(
                    select(func.count(func.distinct(Segment.speaker)))
                    .where(Segment.transcript_id == transcript.id)
                )
                speaker_count = speaker_result.scalar() or 0
                if speaker_count > 0:
                    line += f"\n  {speaker_count} speaker(s), no AI summary generated yet"
            else:
                line += "\n  Not yet transcribed"
```

Add this block right after the `if transcript and transcript.ai_summary:` block closes.

**Step 2: Commit**

```bash
git add packages/backend/services/project_index.py
git commit -m "feat: graceful fallback in project index for items without summaries"
```

---

## Future: Option B — Semantic RAG (separate plan)

When ready to implement Option B, the approach is:

1. **New tool: `semantic_search`** — Takes the user's query, embeds it with `nomic-embed-text`, and queries `segment_embeddings` table using cosine similarity. Returns top 20 segments ranked by relevance. This replaces the SQL `ILIKE` approach in `get_context` with vector search.

2. **Auto-embed on transcription** — The `embed` job already exists. Ensure it runs automatically after every transcription completes (not just on manual trigger).

3. **Hybrid retrieval** — Combine keyword search (current) with semantic search (new) and deduplicate results. This gives both exact-match and meaning-based retrieval.

4. **Context window expansion** — When using a larger model (e.g., enterprise with Claude/GPT-4), the project index + retrieved segments can fill a much larger context window. The `ContextManager` already handles this via `n_ctx`.

No code changes needed now — this section documents the future direction.

---

## Summary

| Task | What it does | Files |
|------|-------------|-------|
| 1 | Project index builder service | Create `services/project_index.py` |
| 2 | Replace raw dump with index in chat | Modify `api/routes/ai.py` |
| 3 | Enhance get_context tool | Modify `services/tools/context_tool.py` |
| 4 | Update system prompt | Modify `api/routes/ai.py` |
| 5 | Hybrid mode (index + attachments) | Modify `api/routes/ai.py` |
| 6 | Fallback for items without summaries | Modify `services/project_index.py` |

**Total: 6 tasks, 3 files, ~200 lines of code.**

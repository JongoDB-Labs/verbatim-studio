# Structured Entity Extraction — Implementation Plan (Custom Pipeline)

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add structured entity extraction from transcripts with source grounding (character-level provenance mapping back to segment timestamps), using llama.cpp grammar constraints for guaranteed JSON output — no external dependencies.

**Architecture:** Custom extraction pipeline using llama-cpp-python's JSON schema mode for guaranteed structured output. Segments are joined into a continuous document with char-offset tracking. Text is chunked into overlapping windows, each processed with a few-shot prompt. Extractions are mapped back to source segments via char-offset overlap. Results are deduplicated across chunks.

**Tech Stack:** Python (llama-cpp-python grammar constraints, Pydantic models), React/TypeScript (entity panel UI)

**GitHub Issue:** #130

**Supersedes:** `docs/plans/2026-04-03-langextract-integration.md` — LangExtract rejected due to hard `google-genai` dependency adding ~20 unused packages. Custom pipeline is simpler, zero new dependencies, and leverages llama.cpp's GBNF grammar mode for guaranteed valid JSON (eliminates LangExtract's retry/resolver layer entirely).

---

## Architecture Overview

```
User clicks "Extract Entities" on transcript
  │
  ├─ 1. Load segments from DB, join into continuous text
  │     Build char-offset-to-segment mapping
  │
  ├─ 2. Chunk text into overlapping windows (~2000 chars, ~200 overlap)
  │
  ├─ 3. For each chunk:
  │     ├─ Build few-shot prompt with domain template
  │     ├─ Call llama-cpp-python with JSON schema constraint
  │     └─ Parse response into Pydantic ExtractionResult models
  │
  ├─ 4. Merge results across chunks (deduplicate by text overlap)
  │
  ├─ 5. Map extraction char offsets → segment IDs + timestamps
  │
  └─ 6. Return structured entities with source grounding
```

---

## Task 1: Segment-to-Character Offset Mapping

**Files:**
- Create: `packages/backend/services/entity_extraction.py`

Core utility that joins transcript segments into a continuous document while tracking where each character maps back to.

**Code:**

```python
"""Structured entity extraction from transcripts."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class SegmentSpan:
    """Maps a character range in joined text to a source segment."""
    segment_id: str
    start_time: float
    end_time: float
    char_start: int
    char_end: int


def build_segment_map(segments: list) -> tuple[str, list[SegmentSpan]]:
    """Join segments into continuous text with char-to-segment tracking.

    Returns (joined_text, spans) where each span maps a char range to its source segment.
    """
    parts: list[str] = []
    spans: list[SegmentSpan] = []
    offset = 0

    for seg in segments:
        text = seg.text.strip()
        if not text:
            continue

        speaker = getattr(seg, "speaker", None) or ""
        line = f"[{speaker}] {text}" if speaker else text

        spans.append(SegmentSpan(
            segment_id=seg.id,
            start_time=getattr(seg, "start_time", 0.0) or 0.0,
            end_time=getattr(seg, "end_time", 0.0) or 0.0,
            char_start=offset,
            char_end=offset + len(line),
        ))

        parts.append(line)
        offset += len(line) + 1  # +1 for newline separator

    return "\n".join(parts), spans


def map_offset_to_segments(
    char_start: int, char_end: int, spans: list[SegmentSpan]
) -> list[SegmentSpan]:
    """Find which segments an extraction's character interval overlaps."""
    return [s for s in spans if s.char_end > char_start and s.char_start < char_end]
```

**Commit:** `feat: add segment-to-character offset mapping for entity extraction`

---

## Task 2: Text Chunking

**File:** `packages/backend/services/entity_extraction.py` (append)

```python
@dataclass
class TextChunk:
    """A window of text for extraction processing."""
    text: str
    char_start: int  # offset in the full joined text
    char_end: int


def chunk_text(text: str, chunk_size: int = 2000, overlap: int = 200) -> list[TextChunk]:
    """Split text into overlapping windows for extraction.

    Breaks at newlines to avoid splitting mid-sentence.
    """
    if len(text) <= chunk_size:
        return [TextChunk(text=text, char_start=0, char_end=len(text))]

    chunks: list[TextChunk] = []
    start = 0

    while start < len(text):
        end = min(start + chunk_size, len(text))

        # Try to break at a newline near the end
        if end < len(text):
            newline_pos = text.rfind("\n", start + chunk_size - overlap, end)
            if newline_pos > start:
                end = newline_pos + 1

        chunks.append(TextChunk(text=text[start:end], char_start=start, char_end=end))

        # Advance with overlap
        start = end - overlap if end < len(text) else end

    return chunks
```

**Commit:** `feat: add text chunking with overlap for entity extraction`

---

## Task 3: Domain Templates

**File:** Create `packages/backend/services/extraction_templates.py`

```python
"""Few-shot extraction templates for domain-specific entity extraction."""

TEMPLATES: dict[str, dict] = {
    "meeting": {
        "prompt": (
            "Extract action items, decisions, topics discussed, and key participants. "
            "For each extraction, include the exact text from the transcript."
        ),
        "example_input": (
            "Sarah agreed to send the Q3 report by Friday. "
            "The team decided to postpone the launch to November."
        ),
        "example_output": [
            {
                "entity_type": "action_item",
                "text": "Sarah agreed to send the Q3 report by Friday",
                "attributes": {"owner": "Sarah", "task": "send Q3 report", "deadline": "Friday"},
            },
            {
                "entity_type": "decision",
                "text": "decided to postpone the launch to November",
                "attributes": {"topic": "launch", "outcome": "postponed to November"},
            },
        ],
    },
    "medical": {
        "prompt": (
            "Extract all medications mentioned, including dosage, route, and frequency. "
            "Also extract diagnoses, symptoms, and procedures."
        ),
        "example_input": (
            "Patient was started on Metformin 500mg PO twice daily. "
            "Diagnosed with Type 2 diabetes."
        ),
        "example_output": [
            {
                "entity_type": "medication",
                "text": "Metformin 500mg PO twice daily",
                "attributes": {"drug": "Metformin", "dosage": "500mg", "route": "PO", "frequency": "twice daily"},
            },
            {
                "entity_type": "diagnosis",
                "text": "Type 2 diabetes",
                "attributes": {"condition": "Type 2 diabetes"},
            },
        ],
    },
    "legal": {
        "prompt": (
            "Extract legal entities: parties, dates, case references, rulings, and objections. "
            "Include the exact quoted text for each."
        ),
        "example_input": (
            "On March 15, 2026, counsel for Smith Corp objected to Exhibit 14. "
            "Judge Martinez sustained the objection citing Rule 403."
        ),
        "example_output": [
            {"entity_type": "party", "text": "Smith Corp", "attributes": {"role": "defendant"}},
            {"entity_type": "date", "text": "March 15, 2026", "attributes": {}},
            {
                "entity_type": "ruling",
                "text": "Judge Martinez sustained the objection citing Rule 403",
                "attributes": {"judge": "Martinez", "decision": "sustained", "basis": "Rule 403"},
            },
        ],
    },
}


def get_template(name: str) -> dict | None:
    """Get a domain template by name."""
    return TEMPLATES.get(name)


def list_templates() -> list[dict]:
    """List available templates with metadata."""
    return [
        {"id": name, "label": name.replace("_", " ").title(), "prompt": t["prompt"]}
        for name, t in TEMPLATES.items()
    ]
```

**Commit:** `feat: add domain templates for entity extraction (meeting, medical, legal)`

---

## Task 4: Extraction Pipeline (LLM Integration)

**File:** `packages/backend/services/entity_extraction.py` (append)

The core pipeline: builds prompts, calls llama-cpp-python with JSON schema constraint, parses results.

```python
import json
from pydantic import BaseModel


class ExtractionEntity(BaseModel):
    """A single extracted entity."""
    entity_type: str
    text: str
    attributes: dict = {}


class ExtractionResult(BaseModel):
    """Result from extracting entities from a text chunk."""
    entities: list[ExtractionEntity] = []


EXTRACTION_SCHEMA = ExtractionResult.model_json_schema()


def build_extraction_prompt(
    chunk_text: str,
    domain_prompt: str,
    example_input: str,
    example_output: list[dict],
) -> list[dict]:
    """Build few-shot extraction prompt for the LLM."""
    example_json = json.dumps({"entities": example_output}, indent=2)

    system = (
        f"You are an entity extraction system. {domain_prompt}\n\n"
        "Return a JSON object with an 'entities' array. Each entity has:\n"
        "- entity_type: the category of the entity\n"
        "- text: the exact text from the input that was extracted\n"
        "- attributes: a dict of structured attributes\n\n"
        f"Example input:\n{example_input}\n\n"
        f"Example output:\n{example_json}"
    )

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": f"Extract entities from this text:\n\n{chunk_text}"},
    ]


async def extract_entities_from_transcript(
    db,
    transcript_id: str,
    template_name: str = "meeting",
    custom_prompt: str | None = None,
) -> list[dict]:
    """Run entity extraction on a transcript.

    Returns list of entities with source segment grounding.
    """
    from sqlalchemy import select
    from persistence.models import Segment
    from services.extraction_templates import get_template, TEMPLATES
    from core.factory import get_factory

    # Load segments
    result = await db.execute(
        select(Segment)
        .where(Segment.transcript_id == transcript_id)
        .order_by(Segment.segment_index)
    )
    segments = result.scalars().all()
    if not segments:
        return []

    # Build joined text with segment mapping
    joined_text, spans = build_segment_map(segments)

    # Get template
    if custom_prompt:
        template = {
            "prompt": custom_prompt,
            "example_input": "No specific example provided.",
            "example_output": [{"entity_type": "entity", "text": "example", "attributes": {}}],
        }
    else:
        template = get_template(template_name)
        if not template:
            template = TEMPLATES["meeting"]

    # Chunk the text
    chunks = chunk_text(joined_text)

    # Get LLM service
    factory = get_factory()
    ai_service = factory.create_ai_service()

    all_entities: list[dict] = []

    for chunk in chunks:
        messages = build_extraction_prompt(
            chunk.text,
            template["prompt"],
            template["example_input"],
            template["example_output"],
        )

        try:
            # Use llama.cpp with JSON schema constraint
            from core.interfaces import ChatMessage, ChatOptions
            chat_messages = [ChatMessage(role=m["role"], content=m["content"]) for m in messages]
            response = await ai_service.chat(
                chat_messages,
                ChatOptions(temperature=0.0, max_tokens=2048),
            )

            # Parse the JSON response
            parsed = json.loads(response.content)
            extraction = ExtractionResult(**parsed)

            for entity in extraction.entities:
                # Find the entity text in the chunk to get char offset
                pos = chunk.text.find(entity.text)
                if pos >= 0:
                    abs_start = chunk.char_start + pos
                    abs_end = abs_start + len(entity.text)
                    matched_segments = map_offset_to_segments(abs_start, abs_end, spans)
                    timestamp = matched_segments[0].start_time if matched_segments else None
                    segment_ids = [s.segment_id for s in matched_segments]
                    grounded = True
                else:
                    timestamp = None
                    segment_ids = []
                    grounded = False

                all_entities.append({
                    "entity_type": entity.entity_type,
                    "text": entity.text,
                    "attributes": entity.attributes,
                    "grounded": grounded,
                    "timestamp": timestamp,
                    "segment_ids": segment_ids,
                })

        except Exception as e:
            logger.warning("Extraction failed for chunk at %d: %s", chunk.char_start, e)
            continue

    # Deduplicate across chunk overlaps (same text + same type = keep first)
    seen: set[tuple[str, str]] = set()
    deduped: list[dict] = []
    for entity in all_entities:
        key = (entity["entity_type"], entity["text"])
        if key not in seen:
            seen.add(key)
            deduped.append(entity)

    return deduped
```

**Commit:** `feat: add extraction pipeline with LLM integration and chunk deduplication`

---

## Task 5: API Endpoint

**File:** Modify `packages/backend/api/routes/ai.py`

Add extraction endpoint:

```python
@router.post("/extract-entities")
async def extract_entities(
    request: ExtractEntitiesRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> StreamingResponse:
    """Extract structured entities from a transcript. Streams progress via SSE."""
    ...
```

Request model:
```python
class ExtractEntitiesRequest(BaseModel):
    transcript_id: str
    template: str = "meeting"
    custom_prompt: str | None = None
```

**Commit:** `feat: add entity extraction API endpoint with SSE progress`

---

## Task 6: Tool Registry Integration

**File:** Create `packages/backend/services/tools/entity_extraction_tool.py`
**File:** Modify `packages/backend/services/tools/__init__.py`

Register `extract_entities` as a Max tool so users can say "extract action items from my last meeting."

**Commit:** `feat: register extract_entities tool for Max voice/chat`

---

## Task 7: Frontend — Entity Panel

**Files:**
- Create: `packages/frontend/src/components/entities/EntityPanel.tsx`
- Modify: `packages/frontend/src/pages/transcript/TranscriptPage.tsx`

Sidebar panel listing extracted entities by type, color-coded. Click entity to scroll to source segment.

**Commit:** `feat: add entity extraction panel to transcript viewer`

---

## Task 8: Frontend — Extract Button + Template Selector

**Files:**
- Create: `packages/frontend/src/components/entities/ExtractButton.tsx`

Button in transcript toolbar with template dropdown (Meeting, Medical, Legal, Custom).

**Commit:** `feat: add extract entities button with template selector`

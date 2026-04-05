"""Entity extraction service for transcripts.

Extracts structured entities (action items, medications, legal references, etc.)
from transcript text using the local LLM, with results grounded to specific
transcript segments and timestamps.
"""

import json
import logging
from dataclasses import dataclass

from pydantic import BaseModel
from sqlalchemy import select

from core.interfaces import ChatMessage, ChatOptions
from persistence.database import get_session_factory
from persistence.models import Segment

from .extraction_templates import get_template

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# EE-1: Segment offset mapping and text chunking
# ---------------------------------------------------------------------------


@dataclass
class SegmentSpan:
    """Maps a segment to its character range in the joined transcript text."""

    segment_id: str
    start_time: float
    end_time: float
    char_start: int
    char_end: int


def build_segment_map(segments: list) -> tuple[str, list[SegmentSpan]]:
    """Join segment texts into a single string, tracking character offsets.

    Each segment is optionally prefixed with ``[speaker]`` and separated
    by newlines so the LLM sees natural paragraph breaks.

    Args:
        segments: List of Segment model instances (or dicts with id, text,
                  start_time, end_time, speaker).

    Returns:
        A tuple of (joined_text, list_of_SegmentSpan).
    """
    parts: list[str] = []
    spans: list[SegmentSpan] = []
    offset = 0

    for seg in segments:
        # Support both ORM objects and plain dicts
        if isinstance(seg, dict):
            seg_id = seg["id"]
            text = seg["text"]
            start_time = seg["start_time"]
            end_time = seg["end_time"]
            speaker = seg.get("speaker")
        else:
            seg_id = seg.id
            text = seg.text
            start_time = seg.start_time
            end_time = seg.end_time
            speaker = seg.speaker

        # Build the line for this segment
        if speaker:
            line = f"[{speaker}] {text}"
        else:
            line = text

        char_start = offset
        char_end = offset + len(line)

        spans.append(
            SegmentSpan(
                segment_id=seg_id,
                start_time=start_time,
                end_time=end_time,
                char_start=char_start,
                char_end=char_end,
            )
        )

        parts.append(line)
        # +1 for the newline separator
        offset = char_end + 1

    joined_text = "\n".join(parts)
    return joined_text, spans


def map_offset_to_segments(
    char_start: int, char_end: int, spans: list[SegmentSpan]
) -> list[SegmentSpan]:
    """Return spans that overlap the given character range.

    Args:
        char_start: Start of the character range (inclusive).
        char_end: End of the character range (exclusive).
        spans: List of SegmentSpan from build_segment_map.

    Returns:
        List of overlapping SegmentSpan instances.
    """
    overlapping: list[SegmentSpan] = []
    for span in spans:
        # Two ranges overlap when neither is entirely before or after the other
        if span.char_start < char_end and char_start < span.char_end:
            overlapping.append(span)
    return overlapping


@dataclass
class TextChunk:
    """A window of text with its character offsets in the full transcript."""

    text: str
    char_start: int
    char_end: int


def chunk_text(
    text: str, chunk_size: int = 2000, overlap: int = 200
) -> list[TextChunk]:
    """Split text into overlapping windows, breaking at newlines.

    If the text fits within a single chunk, returns it as-is.

    Args:
        text: The full joined transcript text.
        chunk_size: Target size of each chunk in characters.
        overlap: Number of overlapping characters between consecutive chunks.

    Returns:
        List of TextChunk instances.
    """
    if len(text) <= chunk_size:
        return [TextChunk(text=text, char_start=0, char_end=len(text))]

    chunks: list[TextChunk] = []
    start = 0

    while start < len(text):
        end = min(start + chunk_size, len(text))

        # If we're not at the very end, try to break at a newline
        # Only search the last 20% of the chunk to avoid pulling back too far
        if end < len(text):
            search_start = max(start, end - (chunk_size // 5))
            newline_pos = text.rfind("\n", search_start, end)
            if newline_pos > start:
                end = newline_pos + 1

        chunk_text_str = text[start:end]
        chunks.append(
            TextChunk(text=chunk_text_str, char_start=start, char_end=end)
        )

        # Advance past the overlap
        next_start = end - overlap
        if next_start <= start:
            next_start = end  # no overlap possible, just advance
        if next_start >= len(text):
            break
        start = next_start

    return chunks


# ---------------------------------------------------------------------------
# EE-3: Extraction pipeline (LLM integration)
# ---------------------------------------------------------------------------


class ExtractionEntity(BaseModel):
    """A single entity extracted by the LLM."""

    entity_type: str
    text: str
    attributes: dict = {}


class ExtractionResult(BaseModel):
    """Collection of extracted entities."""

    entities: list[ExtractionEntity] = []


def build_extraction_prompt(
    chunk_text: str,
    domain_prompt: str,
    example_input: str,
    example_output: list[dict],
) -> list[dict]:
    """Build the messages list for an extraction LLM call.

    Returns a system message containing the domain instructions and a
    few-shot example, plus a user message with the actual text to extract from.

    Args:
        chunk_text: The transcript text chunk to extract entities from.
        domain_prompt: Instruction describing what to extract.
        example_input: Sample input for the few-shot example.
        example_output: Expected extraction result for the example input.

    Returns:
        List of message dicts with 'role' and 'content' keys.
    """
    example_output_json = json.dumps(example_output, indent=2)

    system_content = (
        f"{domain_prompt}\n\n"
        f"Respond ONLY with a valid JSON array. Do not include any other text.\n\n"
        f"### Example\n\n"
        f"Input:\n{example_input}\n\n"
        f"Output:\n{example_output_json}"
    )

    return [
        {"role": "system", "content": system_content},
        {"role": "user", "content": f"Extract entities from the following transcript:\n\n{chunk_text}"},
    ]


async def extract_entities_from_transcript(
    db=None,
    transcript_id: str = "",
    template_name: str = "meeting",
    custom_prompt: str | None = None,
) -> list[dict]:
    """Extract structured entities from a transcript using the LLM.

    Loads segments from the database, builds a segment map, chunks the text,
    calls the AI service for each chunk, parses the JSON response, and grounds
    each extracted entity back to source segments via character offsets.

    Args:
        db: Optional database session. If None, creates one internally.
        transcript_id: The transcript ID to extract entities from.
        template_name: Domain template name ('meeting', 'medical', 'legal').
        custom_prompt: Optional custom extraction prompt (overrides template).

    Returns:
        List of entity dicts, each containing:
          - entity_type (str)
          - text (str)
          - attributes (dict)
          - grounded (bool) — whether the entity was found in the source text
          - timestamp (float | None) — start time of the earliest grounded segment
          - segment_ids (list[str]) — IDs of overlapping segments
    """
    from api.routes.ai import _ensure_active_model_loaded
    from core.factory import get_factory

    # ---- Load segments from DB ----
    own_session = db is None
    if own_session:
        session = get_session_factory()()
    else:
        session = db

    try:
        result = await session.execute(
            select(Segment)
            .where(Segment.transcript_id == transcript_id)
            .order_by(Segment.segment_index)
        )
        segments = result.scalars().all()

        if not segments:
            logger.warning("No segments found for transcript %s", transcript_id)
            return []
    finally:
        if own_session:
            await session.close()

    logger.info(
        "Extracting entities from transcript %s (%d segments)",
        transcript_id,
        len(segments),
    )

    # ---- Build segment map and chunk ----
    full_text, spans = build_segment_map(segments)
    chunks = chunk_text(full_text)

    logger.info(
        "Text length: %d chars, split into %d chunks", len(full_text), len(chunks)
    )

    # ---- Resolve template or custom prompt ----
    if custom_prompt:
        domain_prompt = custom_prompt
        example_input = ""
        example_output: list[dict] = []
    else:
        template = get_template(template_name)
        if template is None:
            logger.error("Unknown template: %s", template_name)
            raise ValueError(f"Unknown extraction template: {template_name}")
        domain_prompt = template["prompt"]
        example_input = template["example_input"]
        example_output = template["example_output"]

    # ---- Create AI service ----
    _ensure_active_model_loaded()
    factory = get_factory()
    ai_service = factory.create_ai_service()

    # ---- Extract from each chunk ----
    all_entities: list[dict] = []

    for chunk_idx, chunk in enumerate(chunks):
        logger.info(
            "Processing chunk %d/%d (chars %d-%d)",
            chunk_idx + 1,
            len(chunks),
            chunk.char_start,
            chunk.char_end,
        )

        messages_raw = build_extraction_prompt(
            chunk.text, domain_prompt, example_input, example_output
        )
        messages = [ChatMessage(role=m["role"], content=m["content"]) for m in messages_raw]

        options = ChatOptions(
            temperature=0.1,
            max_tokens=2048,
            response_format={"type": "json_object"},
        )

        try:
            response = await ai_service.chat(messages, options)
        except Exception:
            logger.exception("AI service call failed for chunk %d", chunk_idx)
            continue

        # ---- Parse JSON response ----
        raw_content = response.content.strip()
        parsed_entities: list[dict] = []

        try:
            parsed = json.loads(raw_content)
            # The response might be a bare array or wrapped in an object
            if isinstance(parsed, list):
                parsed_entities = parsed
            elif isinstance(parsed, dict):
                # Try common wrapper keys
                for key in ("entities", "results", "items", "data"):
                    if key in parsed and isinstance(parsed[key], list):
                        parsed_entities = parsed[key]
                        break
                else:
                    # Single entity wrapped in an object
                    if "entity_type" in parsed and "text" in parsed:
                        parsed_entities = [parsed]
        except (json.JSONDecodeError, TypeError):
            logger.warning(
                "Failed to parse JSON from chunk %d response: %s",
                chunk_idx,
                raw_content[:200],
            )
            continue

        # ---- Ground each entity to source segments ----
        for ent in parsed_entities:
            if not isinstance(ent, dict):
                continue
            entity_text = ent.get("text", "")
            entity_type = ent.get("entity_type", "unknown")
            attributes = ent.get("attributes", {})

            # Try to find the entity text in the chunk to get character offsets
            text_pos = chunk.text.find(entity_text)
            grounded = text_pos >= 0

            if grounded:
                abs_char_start = chunk.char_start + text_pos
                abs_char_end = abs_char_start + len(entity_text)
                overlapping = map_offset_to_segments(abs_char_start, abs_char_end, spans)
                segment_ids = [s.segment_id for s in overlapping]
                timestamp = overlapping[0].start_time if overlapping else None
            else:
                segment_ids = []
                timestamp = None

            all_entities.append({
                "entity_type": entity_type,
                "text": entity_text,
                "attributes": attributes if isinstance(attributes, dict) else {},
                "grounded": grounded,
                "timestamp": timestamp,
                "segment_ids": segment_ids,
            })

    # ---- Deduplicate across chunk overlaps ----
    seen: set[tuple[str, str]] = set()
    deduped: list[dict] = []

    for ent in all_entities:
        key = (ent["entity_type"], ent["text"])
        if key not in seen:
            seen.add(key)
            deduped.append(ent)

    logger.info(
        "Extraction complete: %d entities (%d before dedup)",
        len(deduped),
        len(all_entities),
    )

    return deduped

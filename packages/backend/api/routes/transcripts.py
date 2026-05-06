"""Transcript API endpoints."""

import logging
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from persistence import get_db
from persistence.models import Recording, Segment, SegmentComment, SegmentHighlight, Speaker, Transcript
from services.export import ExportData, ExportSegment, compute_speaker_stats, export_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/transcripts", tags=["transcripts"])


# Pydantic models for responses
class SegmentCorrection(BaseModel):
    """A single auto-applied vocabulary correction surfaced to the frontend.

    Carries the audit-trail entry produced by Phase 2 phonetic correction
    or Phase 3 LLM correction, so the transcript viewer can render an
    indicator on corrected words and offer per-correction undo.
    """

    type: str  # "domain_vocabulary" | "llm_vocabulary"
    original: str
    replacement: str
    confidence_before: float | None = None
    edit_distance: int | None = None
    term_id: str | None = None
    word_index: int | None = None


class SegmentResponse(BaseModel):
    """Response model for a segment."""

    id: str
    segment_index: int
    speaker: str | None
    start_time: float
    end_time: float
    text: str
    confidence: float | None
    edited_by: str | None = None
    original_text: str | None = None
    highlight_color: str | None = None
    comment_count: int = 0
    corrections: list[SegmentCorrection] = []
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class AISummary(BaseModel):
    """AI-generated summary response model."""

    summary: str
    key_points: list[str]
    action_items: list[str]
    topics: list[str]
    named_entities: list[str]


class TranscriptResponse(BaseModel):
    """Response model for a transcript."""

    id: str
    recording_id: str
    language: str | None
    model_used: str | None
    confidence_avg: float | None
    word_count: int | None
    ai_summary: AISummary | None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class TranscriptWithSegmentsResponse(TranscriptResponse):
    """Response model for a transcript with its segments."""

    segments: list[SegmentResponse]


class SegmentUpdateRequest(BaseModel):
    """Request model for updating a segment."""

    text: str | None = None
    speaker: str | None = None


class BulkDeleteRequest(BaseModel):
    """Request model for bulk deleting segments."""

    segment_ids: list[str]


class MessageResponse(BaseModel):
    """Simple message response."""

    message: str


class SegmentListResponse(BaseModel):
    """Response model for paginated list of segments."""

    items: list[SegmentResponse]
    total: int
    skip: int
    limit: int


async def _load_segment_annotations(
    db: AsyncSession, segment_ids: list[str]
) -> tuple[dict[str, str], dict[str, int]]:
    """Batch-load highlight colors and comment counts for segments.

    Returns:
        Tuple of (highlight_map, comment_count_map).
    """
    if not segment_ids:
        return {}, {}

    # Highlights: segment_id -> color
    hl_result = await db.execute(
        select(SegmentHighlight.segment_id, SegmentHighlight.color).where(
            SegmentHighlight.segment_id.in_(segment_ids)
        )
    )
    highlight_map = dict(hl_result.all())

    # Comment counts: segment_id -> count
    cc_result = await db.execute(
        select(SegmentComment.segment_id, func.count(SegmentComment.id))
        .where(SegmentComment.segment_id.in_(segment_ids))
        .group_by(SegmentComment.segment_id)
    )
    comment_count_map = dict(cc_result.all())

    return highlight_map, comment_count_map


async def _load_segment_comments(
    db: AsyncSession, segment_ids: list[str]
) -> dict[str, list[str]]:
    """Batch-load comment texts for segments.

    Returns:
        Dict of segment_id -> list of comment texts.
    """
    if not segment_ids:
        return {}

    result = await db.execute(
        select(SegmentComment.segment_id, SegmentComment.text)
        .where(SegmentComment.segment_id.in_(segment_ids))
        .order_by(SegmentComment.created_at)
    )

    comments_map: dict[str, list[str]] = {}
    for segment_id, content in result.all():
        if segment_id not in comments_map:
            comments_map[segment_id] = []
        comments_map[segment_id].append(content)

    return comments_map


def _diff_single_word_changes(
    before: str, after: str,
) -> list[tuple[str, str]]:
    """Return single-word (original, replacement) pairs that distinguish
    *before* from *after*.

    Tokenizes both strings on whitespace, walks them position-aligned,
    and only reports replacements where:
      - the position aligns (same index in tokenized streams)
      - both sides are alphanumeric (no punctuation-only changes)
      - they differ case-insensitively

    Designed to be tight: we only feed unambiguous single-word
    substitutions to auto-learn so we don't promote prose paraphrases
    or punctuation tweaks. Insertion/deletion (length mismatch) is
    skipped — those are usually structural edits, not vocab fixes.
    """
    before_tokens = before.split()
    after_tokens = after.split()
    if len(before_tokens) != len(after_tokens):
        return []

    pairs: list[tuple[str, str]] = []
    for orig_tok, new_tok in zip(before_tokens, after_tokens):
        # Strip surrounding punctuation; compare the cores.
        def _core(s: str) -> str:
            return "".join(c for c in s if c.isalnum() or c == "-" or c == "_")

        orig_core = _core(orig_tok)
        new_core = _core(new_tok)
        if not orig_core or not new_core:
            continue
        if orig_core.lower() == new_core.lower():
            continue
        pairs.append((orig_core, new_core))
    return pairs


def _build_segment_response(
    s: Segment,
    highlight_map: dict[str, str],
    comment_count_map: dict[str, int],
) -> SegmentResponse:
    """Build a SegmentResponse with annotation data."""
    raw_corrections = list(s.corrections_json or [])
    corrections: list[SegmentCorrection] = []
    for c in raw_corrections:
        if not isinstance(c, dict):
            continue
        try:
            corrections.append(SegmentCorrection(**c))
        except Exception:
            # Tolerate older-shaped entries by ignoring unknown fields.
            corrections.append(SegmentCorrection(
                type=c.get("type", "domain_vocabulary"),
                original=c.get("original", ""),
                replacement=c.get("replacement", ""),
                confidence_before=c.get("confidence_before"),
                edit_distance=c.get("edit_distance"),
                term_id=c.get("term_id"),
                word_index=c.get("word_index"),
            ))
    return SegmentResponse(
        id=s.id,
        segment_index=s.segment_index,
        speaker=s.speaker,
        start_time=s.start_time,
        end_time=s.end_time,
        text=s.text,
        confidence=s.confidence,
        edited_by=s.edited_by,
        original_text=s.original_text,
        highlight_color=highlight_map.get(s.id),
        comment_count=comment_count_map.get(s.id, 0),
        corrections=corrections,
        created_at=s.created_at,
        updated_at=s.updated_at,
    )


@router.get("/{transcript_id}", response_model=TranscriptWithSegmentsResponse)
async def get_transcript(
    transcript_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TranscriptWithSegmentsResponse:
    """Get a transcript with all its segments.

    Args:
        transcript_id: The transcript's unique ID.
        db: Database session.

    Returns:
        Transcript details with all segments.

    Raises:
        HTTPException: If transcript not found.
    """
    result = await db.execute(
        select(Transcript)
        .options(selectinload(Transcript.segments))
        .where(Transcript.id == transcript_id)
    )
    transcript = result.scalar_one_or_none()

    if transcript is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Transcript not found: {transcript_id}",
        )

    # Sort segments by segment_index
    sorted_segments = sorted(transcript.segments, key=lambda s: s.segment_index)

    # Batch-load annotations
    segment_ids = [s.id for s in sorted_segments]
    highlight_map, comment_count_map = await _load_segment_annotations(db, segment_ids)

    return TranscriptWithSegmentsResponse(
        id=transcript.id,
        recording_id=transcript.recording_id,
        language=transcript.language,
        model_used=transcript.model_used,
        confidence_avg=transcript.confidence_avg,
        word_count=transcript.word_count,
        ai_summary=AISummary(**transcript.ai_summary) if transcript.ai_summary else None,
        created_at=transcript.created_at,
        updated_at=transcript.updated_at,
        segments=[
            _build_segment_response(s, highlight_map, comment_count_map)
            for s in sorted_segments
        ],
    )


@router.get("/{transcript_id}/segments", response_model=SegmentListResponse)
async def get_transcript_segments(
    transcript_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    skip: Annotated[int, Query(ge=0, description="Number of segments to skip")] = 0,
    limit: Annotated[int, Query(ge=1, le=100, description="Number of segments to return")] = 50,
) -> SegmentListResponse:
    """Get paginated segments for a transcript.

    Args:
        transcript_id: The transcript's unique ID.
        db: Database session.
        skip: Number of segments to skip.
        limit: Maximum number of segments to return.

    Returns:
        Paginated list of segments.

    Raises:
        HTTPException: If transcript not found.
    """
    # Verify transcript exists
    transcript_result = await db.execute(select(Transcript).where(Transcript.id == transcript_id))
    transcript = transcript_result.scalar_one_or_none()

    if transcript is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Transcript not found: {transcript_id}",
        )

    # Get total count efficiently
    count_result = await db.execute(
        select(func.count(Segment.id)).where(Segment.transcript_id == transcript_id)
    )
    total = count_result.scalar() or 0

    # Get paginated segments
    result = await db.execute(
        select(Segment)
        .where(Segment.transcript_id == transcript_id)
        .order_by(Segment.segment_index)
        .offset(skip)
        .limit(limit)
    )
    segments = result.scalars().all()

    # Batch-load annotations
    segment_ids = [s.id for s in segments]
    highlight_map, comment_count_map = await _load_segment_annotations(db, segment_ids)

    return SegmentListResponse(
        items=[
            _build_segment_response(s, highlight_map, comment_count_map)
            for s in segments
        ],
        total=total,
        skip=skip,
        limit=limit,
    )


@router.patch("/{transcript_id}/segments/{segment_id}", response_model=SegmentResponse)
async def update_segment(
    transcript_id: str,
    segment_id: str,
    update_data: SegmentUpdateRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SegmentResponse:
    """Update a segment's text or speaker.

    Args:
        transcript_id: The transcript's unique ID.
        segment_id: The segment's unique ID.
        update_data: Fields to update.
        db: Database session.

    Returns:
        Updated segment details.

    Raises:
        HTTPException: If transcript or segment not found.
    """
    # Verify transcript exists
    transcript_result = await db.execute(select(Transcript).where(Transcript.id == transcript_id))
    transcript = transcript_result.scalar_one_or_none()

    if transcript is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Transcript not found: {transcript_id}",
        )

    # Get the segment
    result = await db.execute(
        select(Segment).where(
            Segment.id == segment_id,
            Segment.transcript_id == transcript_id,
        )
    )
    segment = result.scalar_one_or_none()

    if segment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Segment not found: {segment_id}",
        )

    # Validate that at least one field is provided
    if update_data.text is None and update_data.speaker is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one field (text or speaker) must be provided",
        )

    # Capture original text BEFORE mutating, so auto-learn can compare.
    pre_edit_text = segment.text

    # Update fields
    if update_data.text is not None:
        segment.text = update_data.text
        segment.edited_by = "human"
        segment.original_text = pre_edit_text  # preserve so we can revert + auto-learn
        logger.info("Segment %s text updated", segment_id)

    if update_data.speaker is not None:
        segment.speaker = update_data.speaker
        logger.info("Segment %s speaker updated to '%s'", segment_id, update_data.speaker)

    await db.commit()
    await db.refresh(segment)

    # Auto-learn from the diff between pre_edit_text and the new text.
    # Single-word substitutions get fed into the auto-learn subsystem,
    # which decides via proper-noun classifier (immediate) or threshold
    # counter (3 of same kind) whether to add the term to the dictionary.
    if update_data.text is not None and pre_edit_text and pre_edit_text != update_data.text:
        try:
            from services.auto_learn import observe_correction
            from services.post_transcription_actions import _get_post_transcription_settings
            from persistence.models import Recording
            settings = await _get_post_transcription_settings()
            auto_learn_on = settings.get("auto_learn_vocab", True)

            # Resolve project_id by walking transcript -> recording.
            project_id: str | None = None
            recording_lookup = await db.execute(
                select(Recording).where(Recording.id == transcript.recording_id)
            )
            recording_row = recording_lookup.scalar_one_or_none()
            if recording_row:
                project_id = recording_row.project_id

            for original_w, replacement_w in _diff_single_word_changes(pre_edit_text, update_data.text):
                outcome = await observe_correction(
                    db,
                    original=original_w,
                    replacement=replacement_w,
                    project_id=project_id,
                    auto_learn_enabled=auto_learn_on,
                )
                if outcome.learned:
                    logger.info(
                        "Auto-learned %r → %r via %s",
                        original_w, replacement_w, outcome.rule,
                    )
            await db.commit()
        except Exception as e:
            logger.warning("Auto-learn observation failed (non-fatal): %s", e)

    highlight_map, comment_count_map = await _load_segment_annotations(db, [segment.id])

    return _build_segment_response(segment, highlight_map, comment_count_map)


@router.delete("/{transcript_id}/segments/{segment_id}", response_model=MessageResponse)
async def delete_segment(
    transcript_id: str,
    segment_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> MessageResponse:
    """Delete a segment from a transcript.

    Related comments, highlights, and embeddings are cascade-deleted automatically.
    """
    result = await db.execute(
        select(Segment).where(
            Segment.id == segment_id,
            Segment.transcript_id == transcript_id,
        )
    )
    segment = result.scalar_one_or_none()

    if segment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Segment not found: {segment_id}",
        )

    await db.delete(segment)
    await db.commit()
    logger.info("Segment %s deleted from transcript %s", segment_id, transcript_id)

    return MessageResponse(message="Segment deleted")


@router.post("/{transcript_id}/segments/bulk-delete", response_model=MessageResponse)
async def bulk_delete_segments(
    transcript_id: str,
    request: BulkDeleteRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> MessageResponse:
    """Delete multiple segments from a transcript."""
    # Verify transcript exists
    transcript_result = await db.execute(select(Transcript).where(Transcript.id == transcript_id))
    if transcript_result.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Transcript not found: {transcript_id}",
        )

    result = await db.execute(
        delete(Segment).where(
            Segment.transcript_id == transcript_id,
            Segment.id.in_(request.segment_ids),
        )
    )
    await db.commit()

    deleted_count = result.rowcount
    logger.info("Bulk deleted %d segments from transcript %s", deleted_count, transcript_id)

    return MessageResponse(message=f"Deleted {deleted_count} segments")


class RecorrectResponse(BaseModel):
    corrections_applied: int
    segments_updated: int
    standard_word_gate_skipped: bool


@router.post("/{transcript_id}/recorrect", response_model=RecorrectResponse)
async def recorrect_transcript(
    transcript_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> RecorrectResponse:
    """Re-run Phase 2 phonetic correction on an already-completed transcript.

    For users who add vocabulary terms after their recordings have been
    transcribed. Pulls each segment + its word-level confidence (when
    present in the segment text — we don't store per-word confidence on
    the DB row, so we operate on segment-level confidence as the gate
    proxy) and applies the same 3-gate test as Phase 2.

    The new corrections are appended to the existing corrections_json
    audit trail so a user can revert each independently.

    Idempotent: replaying a re-correct on a transcript that's already
    been corrected won't double-apply because the term substitution
    already happened on the first pass — Whisper's "mctissa" is now
    "MCTSSA" in the segment text, and Phase 2 won't replace "MCTSSA"
    (it's not below the confidence gate AND it phonetically matches the
    canonical spelling exactly, so edit distance is 0 — no change).
    """
    transcript_result = await db.execute(
        select(Transcript).where(Transcript.id == transcript_id)
    )
    transcript = transcript_result.scalar_one_or_none()
    if transcript is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Transcript not found: {transcript_id}",
        )

    from persistence.models import Recording
    rec_result = await db.execute(
        select(Recording).where(Recording.id == transcript.recording_id)
    )
    recording = rec_result.scalar_one_or_none()
    project_id = recording.project_id if recording else None

    from services.custom_dictionary import load_dictionary_entries
    from services.vocab_correction import correct_segments

    entries = await load_dictionary_entries(db=db, project_id=project_id)
    if not entries:
        return RecorrectResponse(
            corrections_applied=0, segments_updated=0,
            standard_word_gate_skipped=False,
        )

    seg_result = await db.execute(
        select(Segment)
        .where(Segment.transcript_id == transcript_id)
        .order_by(Segment.segment_index)
    )
    segments = list(seg_result.scalars().all())

    # Build proxy objects — vocab_correction.correct_segments expects
    # objects with .text and .words. We don't have per-word confidence on
    # DB rows, so we synthesize a single "word" per segment using the
    # segment's confidence as the gate. This is coarser than the live
    # transcription path but still useful: if the whole segment is
    # low-confidence, words within it are candidates for correction.
    @dataclass
    class _Word:
        text: str
        confidence: float

    @dataclass
    class _Seg:
        text: str
        words: list
        corrections: list

    proxies: list[_Seg] = []
    for s in segments:
        seg_conf = s.confidence if s.confidence is not None else 0.5
        words = [_Word(text=tok, confidence=seg_conf) for tok in s.text.split()]
        existing = list(s.corrections_json or [])
        proxies.append(_Seg(text=s.text, words=words, corrections=existing))

    result = correct_segments(proxies, entries)

    segments_updated = 0
    for proxy, seg in zip(proxies, segments):
        if proxy.text != seg.text:
            seg.text = proxy.text
            seg.corrections_json = proxy.corrections
            segments_updated += 1

    await db.commit()

    logger.info(
        "Recorrected transcript %s: %d corrections across %d segments",
        transcript_id, result.count, segments_updated,
    )
    return RecorrectResponse(
        corrections_applied=result.count,
        segments_updated=segments_updated,
        standard_word_gate_skipped=result.standard_word_gate_skipped,
    )


@router.delete(
    "/{transcript_id}/segments/{segment_id}/corrections/{correction_index}",
    response_model=SegmentResponse,
)
async def revert_segment_correction(
    transcript_id: str,
    segment_id: str,
    correction_index: int,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SegmentResponse:
    """Revert a single auto-applied vocabulary correction.

    The corrections_json column is a list of correction-events in
    application order. Reverting one rewrites the segment text by
    replacing the canonical term with the original spelling at the
    matching word position.

    NOTE: this only reverses Phase 2 word-level corrections (which carry
    word_index). Phase 3 LLM corrections are whole-segment replacements
    — those revert by restoring the saved `original` from the audit
    entry directly, no word-index walk.
    """
    seg_row = await db.execute(
        select(Segment).where(
            Segment.id == segment_id,
            Segment.transcript_id == transcript_id,
        )
    )
    segment = seg_row.scalar_one_or_none()
    if segment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Segment not found",
        )

    corrections = list(segment.corrections_json or [])
    if correction_index < 0 or correction_index >= len(corrections):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"correction_index {correction_index} out of range "
                   f"(segment has {len(corrections)} corrections)",
        )

    target = corrections[correction_index]
    original = target.get("original", "")
    replacement = target.get("replacement", "")
    correction_type = target.get("type", "domain_vocabulary")

    if not original or not replacement:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Correction entry malformed (missing original or replacement)",
        )

    if correction_type == "llm_vocabulary":
        # Phase 3 corrections are whole-segment — restore the original.
        segment.text = original
    else:
        # Phase 2 — replace the canonical term with the original at the
        # word position recorded in the entry, when present. Falls back to
        # a literal first-occurrence replacement if no index.
        word_index = target.get("word_index")
        tokens = segment.text.split()
        if (
            isinstance(word_index, int)
            and 0 <= word_index < len(tokens)
            and tokens[word_index] == replacement
        ):
            tokens[word_index] = original
            segment.text = " ".join(tokens)
        else:
            segment.text = segment.text.replace(replacement, original, 1)

    # Drop the reverted entry from the audit trail.
    corrections.pop(correction_index)
    segment.corrections_json = corrections if corrections else None
    segment.edited_by = "human"

    await db.commit()
    await db.refresh(segment)

    highlight_map, comment_count_map = await _load_segment_annotations(db, [segment.id])
    return _build_segment_response(segment, highlight_map, comment_count_map)


@router.get("/by-recording/{recording_id}", response_model=TranscriptWithSegmentsResponse)
async def get_transcript_by_recording(
    recording_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TranscriptWithSegmentsResponse:
    """Get transcript for a recording.

    Args:
        recording_id: The recording's unique ID.
        db: Database session.

    Returns:
        Transcript details with all segments.

    Raises:
        HTTPException: If transcript not found for the recording.
    """
    result = await db.execute(
        select(Transcript)
        .options(selectinload(Transcript.segments))
        .where(Transcript.recording_id == recording_id)
    )
    transcript = result.scalar_one_or_none()

    if transcript is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Transcript not found for recording: {recording_id}",
        )

    # Sort segments by segment_index
    sorted_segments = sorted(transcript.segments, key=lambda s: s.segment_index)

    # Batch-load annotations
    segment_ids = [s.id for s in sorted_segments]
    highlight_map, comment_count_map = await _load_segment_annotations(db, segment_ids)

    return TranscriptWithSegmentsResponse(
        id=transcript.id,
        recording_id=transcript.recording_id,
        language=transcript.language,
        model_used=transcript.model_used,
        confidence_avg=transcript.confidence_avg,
        word_count=transcript.word_count,
        ai_summary=AISummary(**transcript.ai_summary) if transcript.ai_summary else None,
        created_at=transcript.created_at,
        updated_at=transcript.updated_at,
        segments=[
            _build_segment_response(s, highlight_map, comment_count_map)
            for s in sorted_segments
        ],
    )


class ExportFormat(str, Enum):
    """Supported export formats."""

    TXT = "txt"
    SRT = "srt"
    VTT = "vtt"
    DOCX = "docx"
    PDF = "pdf"


# MIME types for export formats
EXPORT_MIME_TYPES = {
    ExportFormat.TXT: "text/plain",
    ExportFormat.SRT: "application/x-subrip",
    ExportFormat.VTT: "text/vtt",
    ExportFormat.DOCX: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ExportFormat.PDF: "application/pdf",
}


@router.get("/{transcript_id}/export")
async def export_transcript(
    transcript_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    format: Annotated[ExportFormat, Query(description="Export format")] = ExportFormat.TXT,
    include_timestamps: Annotated[bool, Query(description="Include timestamps (TXT only)")] = True,
) -> Response:
    """Export a transcript to various formats.

    Supported formats:
    - txt: Plain text with timestamps
    - srt: SubRip subtitle format
    - vtt: WebVTT subtitle format
    - docx: Microsoft Word document
    - pdf: PDF document

    Args:
        transcript_id: The transcript's unique ID.
        db: Database session.
        format: Export format (txt, srt, vtt, docx, pdf).
        include_timestamps: Include timestamps in TXT export.

    Returns:
        File download response in the requested format.

    Raises:
        HTTPException: If transcript not found.
    """
    # Get transcript with segments and recording
    result = await db.execute(
        select(Transcript)
        .options(selectinload(Transcript.segments))
        .options(selectinload(Transcript.recording))
        .options(selectinload(Transcript.speakers))
        .where(Transcript.id == transcript_id)
    )
    transcript = result.scalar_one_or_none()

    if transcript is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Transcript not found: {transcript_id}",
        )

    # Build speaker mapping
    speaker_map = {}
    for speaker in transcript.speakers:
        speaker_map[speaker.speaker_label] = speaker.speaker_name or speaker.speaker_label

    # Sort segments by index
    sorted_segments = sorted(transcript.segments, key=lambda s: s.segment_index)

    # Load annotations for export
    segment_ids = [s.id for s in sorted_segments]
    highlight_map, _ = await _load_segment_annotations(db, segment_ids)
    comments_map = await _load_segment_comments(db, segment_ids)

    # Build export data
    export_segments = [
        ExportSegment(
            index=s.segment_index,
            start_time=s.start_time,
            end_time=s.end_time,
            text=s.text,
            speaker=s.speaker,
            speaker_name=speaker_map.get(s.speaker) if s.speaker else None,
            edited=bool(s.edited_by),
            highlight_color=highlight_map.get(s.id),
            comments=comments_map.get(s.id),
        )
        for s in sorted_segments
    ]

    export_data = ExportData(
        title=transcript.recording.title if transcript.recording else "Transcript",
        language=transcript.language,
        model_used=transcript.model_used,
        word_count=transcript.word_count,
        duration_seconds=transcript.recording.duration_seconds if transcript.recording else None,
        segments=export_segments,
        speakers=speaker_map,
        speaker_stats=compute_speaker_stats(export_segments, speaker_map),
        ai_summary=transcript.ai_summary,
    )

    # Generate filename
    base_name = transcript.recording.title if transcript.recording else "transcript"
    # Sanitize filename
    safe_name = "".join(c if c.isalnum() or c in " -_" else "_" for c in base_name)
    filename = f"{safe_name}.{format.value}"

    # Export based on format
    try:
        if format == ExportFormat.TXT:
            content = export_service.export_txt(export_data, include_timestamps)
            media_type = EXPORT_MIME_TYPES[format]
            return Response(
                content=content.encode("utf-8"),
                media_type=media_type,
                headers={"Content-Disposition": f'attachment; filename="{filename}"'},
            )

        elif format == ExportFormat.SRT:
            content = export_service.export_srt(export_data)
            media_type = EXPORT_MIME_TYPES[format]
            return Response(
                content=content.encode("utf-8"),
                media_type=media_type,
                headers={"Content-Disposition": f'attachment; filename="{filename}"'},
            )

        elif format == ExportFormat.VTT:
            content = export_service.export_vtt(export_data)
            media_type = EXPORT_MIME_TYPES[format]
            return Response(
                content=content.encode("utf-8"),
                media_type=media_type,
                headers={"Content-Disposition": f'attachment; filename="{filename}"'},
            )

        elif format == ExportFormat.DOCX:
            content = export_service.export_docx(export_data)
            media_type = EXPORT_MIME_TYPES[format]
            return Response(
                content=content,
                media_type=media_type,
                headers={"Content-Disposition": f'attachment; filename="{filename}"'},
            )

        elif format == ExportFormat.PDF:
            content = export_service.export_pdf(export_data)
            media_type = EXPORT_MIME_TYPES[format]
            return Response(
                content=content,
                media_type=media_type,
                headers={"Content-Disposition": f'attachment; filename="{filename}"'},
            )

    except ImportError as e:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail=str(e),
        )
    except Exception as e:
        logger.error("Export failed: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Export failed: {str(e)}",
        )

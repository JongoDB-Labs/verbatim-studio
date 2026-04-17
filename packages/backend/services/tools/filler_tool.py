"""Filler detection tool — detect filler words in a transcript.

detect_fillers: Analyze transcript segments for verbal fillers (um, uh, like,
you know, etc.) and report counts, per-word breakdown, and filler rate.
"""

from __future__ import annotations

import logging
from collections import Counter

from sqlalchemy import select

from services.tool_registry import ToolContext, ToolDef, ToolResult

logger = logging.getLogger(__name__)


async def handle_detect_fillers(args: dict, ctx: ToolContext) -> ToolResult:
    """Detect filler words in a transcript and return a formatted summary."""
    transcript_id = args.get("transcript_id", "")

    from persistence.models import Transcript, Segment
    from services.filler_detection import detect_fillers

    # Verify transcript exists
    result = await ctx.db.execute(
        select(Transcript).where(Transcript.id == transcript_id)
    )
    transcript = result.scalar_one_or_none()
    if not transcript:
        return ToolResult(content=f"Transcript '{transcript_id}' not found.")

    # Get segments
    seg_result = await ctx.db.execute(
        select(Segment)
        .where(Segment.transcript_id == transcript_id)
        .order_by(Segment.segment_index)
    )
    segments = seg_result.scalars().all()

    if not segments:
        return ToolResult(content="Transcript has no segments to analyze.")

    # Build the list of dicts the filler detection service expects
    segment_dicts = [
        {"id": seg.id, "text": seg.text, "segment_index": seg.segment_index}
        for seg in segments
    ]

    # Run filler detection
    filler_map = detect_fillers(segment_dicts)

    if not filler_map:
        return ToolResult(content="No filler words detected in this transcript.")

    # Count total words across all segments for filler rate calculation
    total_words = sum(len(seg.text.split()) for seg in segments)

    # Aggregate filler counts
    filler_counter: Counter[str] = Counter()
    for matches in filler_map.values():
        for m in matches:
            filler_counter[m.word] += 1

    total_fillers = sum(filler_counter.values())
    filler_rate = (total_fillers / total_words * 100) if total_words > 0 else 0.0

    # Build formatted summary
    lines = [
        f"**Filler Word Analysis**",
        f"",
        f"Total filler words: **{total_fillers}**",
        f"Segments with fillers: {len(filler_map)} of {len(segments)}",
        f"Filler rate: **{filler_rate:.1f}%** of all words ({total_fillers}/{total_words})",
        f"",
        f"**Top filler words:**",
    ]

    for word, count in filler_counter.most_common(10):
        lines.append(f"- \"{word}\": {count}")

    return ToolResult(content="\n".join(lines))


detect_fillers_tool = ToolDef(
    name="detect_fillers",
    description="Detect filler words (um, uh, like, you know, etc.) in a transcript and report counts and locations.",
    parameters={
        "type": "object",
        "properties": {
            "transcript_id": {"type": "string", "description": "The transcript ID to analyze for fillers"},
        },
        "required": ["transcript_id"],
    },
    handler=handle_detect_fillers,
    project_scoped=True,
)

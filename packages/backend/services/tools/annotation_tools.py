"""Annotation tools — highlight segments and add notes.

highlight_segments: Apply highlight colors to transcript segments via SegmentHighlight rows.
add_note: Create a note anchored to a recording timestamp or document page.
"""

from __future__ import annotations

import logging
import uuid

from services.tool_registry import ToolContext, ToolDef, ToolResult

logger = logging.getLogger(__name__)

VALID_COLORS = {"yellow", "green", "blue", "pink", "orange", "purple", "red"}


async def handle_highlight_segments(args: dict, ctx: ToolContext) -> ToolResult:
    """Apply a highlight color to transcript segments.

    Creates or updates SegmentHighlight rows for each segment ID.
    """
    segment_ids = args.get("segment_ids", [])
    color = args.get("color", "").lower()

    if not segment_ids:
        return ToolResult(content="No segment IDs provided.")

    if color not in VALID_COLORS:
        return ToolResult(
            content=f"Invalid color '{color}'. Valid colors: {', '.join(sorted(VALID_COLORS))}"
        )

    from persistence.models import SegmentHighlight
    from sqlalchemy import delete

    try:
        # Delete existing highlights for these segments, then insert fresh ones
        await ctx.db.execute(
            delete(SegmentHighlight).where(SegmentHighlight.segment_id.in_(segment_ids))
        )

        for seg_id in segment_ids:
            highlight = SegmentHighlight(
                id=str(uuid.uuid4()),
                segment_id=seg_id,
                color=color,
            )
            ctx.db.add(highlight)

        await ctx.db.commit()
        return ToolResult(content=f"Highlighted {len(segment_ids)} segment(s) in {color}.")
    except Exception as e:
        logger.exception("Failed to highlight segments")
        return ToolResult(content=f"Failed to highlight segments: {e}")


async def handle_add_note(args: dict, ctx: ToolContext) -> ToolResult:
    """Create a note anchored to a recording timestamp or document page."""
    content = args.get("content", "").strip()
    recording_id = args.get("recording_id")
    document_id = args.get("document_id")
    timestamp = args.get("timestamp")
    page = args.get("page")

    if not content:
        return ToolResult(content="Note content is required.")

    if not recording_id and not document_id:
        return ToolResult(
            content="Either recording_id or document_id is required to anchor the note."
        )

    from persistence.models import Note

    try:
        # Determine anchor type and data based on provided parameters
        if recording_id and timestamp is not None:
            anchor_type = "timestamp"
            anchor_data = {"timestamp": timestamp}
        elif document_id and page is not None:
            anchor_type = "page"
            anchor_data = {"page": page}
        else:
            anchor_type = "general"
            anchor_data = {}

        note = Note(
            id=str(uuid.uuid4()),
            content=content,
            recording_id=recording_id,
            document_id=document_id,
            anchor_type=anchor_type,
            anchor_data=anchor_data,
        )
        ctx.db.add(note)
        await ctx.db.commit()

        anchor_desc = ""
        if anchor_type == "timestamp":
            anchor_desc = f" at {timestamp:.1f}s"
        elif anchor_type == "page":
            anchor_desc = f" on page {page}"

        truncated = content[:100] + ("..." if len(content) > 100 else "")
        return ToolResult(content=f"Note added{anchor_desc}: \"{truncated}\"")
    except Exception as e:
        logger.exception("Failed to add note")
        return ToolResult(content=f"Failed to add note: {e}")


highlight_segments_tool = ToolDef(
    name="highlight_segments",
    description="Apply highlight colors to transcript segments. Colors: yellow, green, blue, pink, orange, purple, red.",
    parameters={
        "type": "object",
        "properties": {
            "segment_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Segment IDs to highlight",
            },
            "color": {
                "type": "string",
                "enum": ["yellow", "green", "blue", "pink", "orange", "purple", "red"],
                "description": "Highlight color",
            },
        },
        "required": ["segment_ids", "color"],
    },
    handler=handle_highlight_segments,
    project_scoped=True,
)

add_note_tool = ToolDef(
    name="add_note",
    description="Add a note anchored to a recording timestamp or document page.",
    parameters={
        "type": "object",
        "properties": {
            "content": {"type": "string", "description": "Note content"},
            "recording_id": {"type": "string", "description": "Recording to attach note to"},
            "document_id": {"type": "string", "description": "Document to attach note to"},
            "timestamp": {"type": "number", "description": "Timestamp in seconds (for recordings)"},
            "page": {"type": "integer", "description": "Page number (for documents)"},
        },
        "required": ["content"],
    },
    handler=handle_add_note,
    project_scoped=True,
)

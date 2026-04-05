"""Entity extraction tool — extract structured entities from transcripts.

Lets Max extract action items, medications, legal references, etc.
from a transcript using configurable domain templates.
"""

from __future__ import annotations

import logging

from services.tool_registry import ToolContext, ToolDef, ToolResult

logger = logging.getLogger(__name__)


async def handle_extract_entities(args: dict, ctx: ToolContext) -> ToolResult:
    """Extract structured entities from a transcript."""
    transcript_id = args.get("transcript_id", "")
    template = args.get("template", "meeting")

    if not transcript_id:
        return ToolResult(content="Missing required parameter: transcript_id")

    from services.entity_extraction import extract_entities_from_transcript

    try:
        entities = await extract_entities_from_transcript(
            ctx.db, transcript_id, template
        )
    except ValueError as e:
        return ToolResult(content=f"Entity extraction failed: {e}")
    except Exception as e:
        logger.exception("Entity extraction failed")
        return ToolResult(content=f"Entity extraction failed: {e}")

    if not entities:
        return ToolResult(content="No entities found in this transcript.")

    grounded_count = sum(1 for ent in entities if ent.get("grounded"))

    # Format results for chat display
    lines = [
        f"Extracted **{len(entities)}** entities "
        f"({grounded_count} grounded to source) "
        f"using the **{template}** template:\n"
    ]

    # Group by entity type
    by_type: dict[str, list[dict]] = {}
    for ent in entities:
        etype = ent.get("entity_type", "unknown")
        by_type.setdefault(etype, []).append(ent)

    for etype, items in by_type.items():
        label = etype.replace("_", " ").title()
        lines.append(f"### {label} ({len(items)})")
        for item in items:
            text = item.get("text", "")
            timestamp = item.get("timestamp")
            attrs = item.get("attributes", {})

            ts_str = f" @ {timestamp:.1f}s" if timestamp is not None else ""
            attr_parts = [f"{k}: {v}" for k, v in attrs.items() if v]
            attr_str = f" ({', '.join(attr_parts)})" if attr_parts else ""

            lines.append(f"- {text}{ts_str}{attr_str}")
        lines.append("")

    return ToolResult(content="\n".join(lines))


extract_entities_tool = ToolDef(
    name="extract_entities",
    description=(
        "Extract structured entities (action items, medications, legal references, etc.) "
        "from a transcript. Specify a template: meeting, medical, or legal."
    ),
    parameters={
        "type": "object",
        "properties": {
            "transcript_id": {
                "type": "string",
                "description": "The transcript ID to extract entities from",
            },
            "template": {
                "type": "string",
                "description": "Domain template: 'meeting', 'medical', or 'legal'. Defaults to 'meeting'.",
            },
        },
        "required": ["transcript_id"],
    },
    handler=handle_extract_entities,
    project_scoped=True,
)

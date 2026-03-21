"""Tool registration for Max AI assistant."""

from services.tool_registry import ToolRegistry


def register_all_tools(registry: ToolRegistry) -> None:
    """Register all available tools with the registry."""
    from services.tools.web_search_tool import web_search_tool
    registry.register(web_search_tool)

    from services.tools.search_tools import project_search_tool, global_search_tool
    registry.register(project_search_tool)
    registry.register(global_search_tool)

    from services.tools.context_tool import context_tool
    registry.register(context_tool)

    from services.tools.help_tool import help_tool
    registry.register(help_tool)

    from services.tools.document_tools import generate_document_tool, export_transcript_tool
    registry.register(generate_document_tool)
    registry.register(export_transcript_tool)

    from services.tools.analysis_tools import summarize_transcript_tool, quality_review_tool
    registry.register(summarize_transcript_tool)
    registry.register(quality_review_tool)

    from services.tools.annotation_tools import highlight_segments_tool, add_note_tool
    registry.register(highlight_segments_tool)
    registry.register(add_note_tool)

    from services.tools.organization_tools import (
        create_project_tool, tag_recordings_tool,
        get_recording_info_tool, system_status_tool,
    )
    registry.register(create_project_tool)
    registry.register(tag_recordings_tool)
    registry.register(get_recording_info_tool)
    registry.register(system_status_tool)

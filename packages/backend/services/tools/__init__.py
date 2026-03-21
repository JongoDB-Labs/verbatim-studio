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

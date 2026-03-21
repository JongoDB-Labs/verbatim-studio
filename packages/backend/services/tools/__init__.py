"""Tool registration for Max AI assistant."""

from services.tool_registry import ToolRegistry


def register_all_tools(registry: ToolRegistry) -> None:
    """Register all available tools with the registry."""
    from services.tools.web_search_tool import web_search_tool
    registry.register(web_search_tool)

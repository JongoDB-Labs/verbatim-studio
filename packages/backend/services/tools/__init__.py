"""Tool registration for Max AI assistant."""

from services.tool_registry import ToolRegistry


def register_all_tools(registry: ToolRegistry) -> None:
    """Register all available tools with the registry.

    Called once during app startup. Each tool module adds its
    tools to the registry.
    """
    # Phase 2 will add individual tool modules here.
    # For now this is a no-op so the framework can be tested end-to-end.
    pass

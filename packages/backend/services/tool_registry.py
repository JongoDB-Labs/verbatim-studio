"""Tool registry for Max AI assistant.

Provides a central registry for tools that Max can call during chat.
Each tool has a name, description (injected into the system prompt),
parameter schema, and an async handler function.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)


@dataclass
class Artifact:
    """A file, link, or UI action produced by a tool."""

    type: str   # "file_download", "link", "notification"
    data: dict  # e.g. {"url": "/documents/xxx/file", "filename": "report.pdf"}


@dataclass
class ToolResult:
    """Result returned by a tool handler."""

    content: str                          # Text fed back to Max
    artifacts: list[Artifact] = field(default_factory=list)


@dataclass
class ToolContext:
    """Context passed to every tool handler."""

    project_id: str | None
    conversation_id: str | None
    recording_ids: list[str]
    document_ids: list[str]
    db: Any  # AsyncSession — typed as Any to avoid import cycle
    ai_service: Any | None = None  # For tools that need sub-LLM calls


@dataclass
class ToolDef:
    """Definition of a tool available to Max."""

    name: str
    description: str
    parameters: dict            # JSON Schema for args
    handler: Callable           # async fn(args: dict, ctx: ToolContext) -> ToolResult
    project_scoped: bool = True # Auto-filter by active project


class ToolRegistry:
    """Central registry for Max tools."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolDef] = {}

    def register(self, tool: ToolDef) -> None:
        """Register a tool. Raises ValueError if name is already taken."""
        if tool.name in self._tools:
            raise ValueError(f"Tool '{tool.name}' is already registered")
        self._tools[tool.name] = tool
        logger.info("Registered tool: %s", tool.name)

    def unregister(self, name: str) -> None:
        """Remove a tool by name."""
        self._tools.pop(name, None)

    def get(self, name: str) -> ToolDef | None:
        """Get a tool by name, or None if not found."""
        return self._tools.get(name)

    def list_tools(self, *, names: list[str] | None = None) -> list[ToolDef]:
        """List all registered tools, optionally filtered by name."""
        if names is not None:
            return [t for t in self._tools.values() if t.name in names]
        return list(self._tools.values())


# ── Module-level singleton ────────────────────────────────────────────

_registry: ToolRegistry | None = None


def get_registry() -> ToolRegistry:
    """Get or create the global tool registry singleton."""
    global _registry
    if _registry is None:
        _registry = ToolRegistry()
    return _registry

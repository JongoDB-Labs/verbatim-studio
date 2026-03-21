"""Tool registry for Max AI assistant.

Provides a central registry for tools that Max can call during chat.
Each tool has a name, description (injected into the system prompt),
parameter schema, and an async handler function.
"""

from __future__ import annotations

import json
import logging
import re
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

    def generate_tools_prompt(self, *, exclude: list[str] | None = None) -> str:
        """Generate the tools section for the system prompt.

        Returns an empty string if no tools are registered (or all excluded).
        """
        tools = [t for t in self._tools.values() if not exclude or t.name not in exclude]
        if not tools:
            return ""

        lines = [
            "\n\n## Tools\n",
            "You have access to the following tools. To use a tool, output a <tool_call> block.",
            "You may include text before the block to explain what you're doing.",
            "Wait for the result before continuing your response.\n",
            "<tool_call>",
            '{"tool": "tool_name", "args": {"param": "value"}}',
            "</tool_call>\n",
            "### Available Tools\n",
        ]

        for tool in tools:
            param_hints = ""
            props = tool.parameters.get("properties", {})
            if props:
                params = ", ".join(f"{k}: {v.get('type', 'any')}" for k, v in props.items())
                param_hints = f"({params})"
            lines.append(f"- **{tool.name}**{param_hints} — {tool.description}")

        lines.append("\n### Guidelines")
        lines.append("- Call ONE tool at a time. Wait for the result before deciding next steps.")
        lines.append("- Always explain what you're doing before calling a tool.")

        return "\n".join(lines)


# ── Module-level singleton ────────────────────────────────────────────

_registry: ToolRegistry | None = None


def get_registry() -> ToolRegistry:
    """Get or create the global tool registry singleton."""
    global _registry
    if _registry is None:
        _registry = ToolRegistry()
    return _registry


# ── Tool-call parser ─────────────────────────────────────────────────


@dataclass
class ToolCallParsed:
    """A parsed tool call from LLM output."""

    tool_name: str
    args: dict
    prefix: str  # Text before the <tool_call> tag


_TOOL_CALL_PATTERN = re.compile(
    r"<tool_call>\s*(\{.*?\})\s*</tool_call>",
    re.DOTALL,
)


def parse_tool_call(text: str) -> ToolCallParsed | None:
    """Parse a <tool_call> block from LLM output.

    Returns None if no valid tool call is found.
    """
    match = _TOOL_CALL_PATTERN.search(text)
    if not match:
        return None

    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError:
        logger.warning("Malformed JSON in tool call: %s", match.group(1)[:200])
        return None

    tool_name = data.get("tool")
    if not tool_name:
        logger.warning("Tool call missing 'tool' field: %s", data)
        return None

    prefix = text[: match.start()].strip()
    args = data.get("args", {})

    return ToolCallParsed(tool_name=tool_name, args=args, prefix=prefix)

# Max Tool-Calling System Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Give Max a formal tool-calling system with 15 tools (3 converted from implicit behaviors, 12 new), replacing hardcoded heuristics with a model-agnostic `<tool_call>` protocol and multi-turn execution loop.

**Architecture:** Central `ToolRegistry` with async handlers. `<tool_call>` JSON blocks detected in LLM output. Execution loop pauses streaming, runs tool, feeds result back for up to 5 iterations. SSE events notify frontend of tool activity. Tools are thin wrappers around existing services.

**Tech Stack:** Python/FastAPI/SQLAlchemy (backend), React/TypeScript (frontend), SSE streaming, reportlab, python-docx, existing services (export.py, web_search.py, quality_review.py)

**Design doc:** `docs/plans/2026-03-19-max-tool-calling-design.md`

---

## Phase 1: Core Framework

### Task 1: Tool Registry — Data Structures

**Files:**
- Create: `packages/backend/services/tool_registry.py`
- Test: `packages/backend/tests/test_tool_registry.py`

**Step 1: Write the failing test**

Create `packages/backend/tests/test_tool_registry.py`:

```python
"""Tests for ToolRegistry data structures and registration."""

import pytest
from services.tool_registry import (
    ToolDef,
    ToolResult,
    Artifact,
    ToolContext,
    ToolRegistry,
)


class TestToolDefDataclass:
    def test_tool_def_creation(self):
        def dummy_handler(args, ctx):
            pass

        tool = ToolDef(
            name="test_tool",
            description="A test tool",
            parameters={"type": "object", "properties": {"query": {"type": "string"}}},
            handler=dummy_handler,
        )
        assert tool.name == "test_tool"
        assert tool.project_scoped is True  # default

    def test_tool_def_not_project_scoped(self):
        tool = ToolDef(
            name="global",
            description="Global tool",
            parameters={},
            handler=lambda a, c: None,
            project_scoped=False,
        )
        assert tool.project_scoped is False


class TestToolResult:
    def test_result_with_artifacts(self):
        result = ToolResult(
            content="Found 5 results",
            artifacts=[Artifact(type="file_download", data={"url": "/files/x", "filename": "report.pdf"})],
        )
        assert len(result.artifacts) == 1
        assert result.artifacts[0].type == "file_download"

    def test_result_empty_artifacts(self):
        result = ToolResult(content="Done", artifacts=[])
        assert result.artifacts == []


class TestToolRegistry:
    def test_register_and_get(self):
        registry = ToolRegistry()
        tool = ToolDef(
            name="my_tool",
            description="desc",
            parameters={},
            handler=lambda a, c: None,
        )
        registry.register(tool)
        assert registry.get("my_tool") is tool

    def test_get_unknown_returns_none(self):
        registry = ToolRegistry()
        assert registry.get("nonexistent") is None

    def test_register_duplicate_raises(self):
        registry = ToolRegistry()
        tool = ToolDef(name="dup", description="", parameters={}, handler=lambda a, c: None)
        registry.register(tool)
        with pytest.raises(ValueError, match="already registered"):
            registry.register(tool)

    def test_list_tools(self):
        registry = ToolRegistry()
        t1 = ToolDef(name="a", description="Tool A", parameters={}, handler=lambda a, c: None)
        t2 = ToolDef(name="b", description="Tool B", parameters={}, handler=lambda a, c: None)
        registry.register(t1)
        registry.register(t2)
        names = [t.name for t in registry.list_tools()]
        assert "a" in names
        assert "b" in names

    def test_unregister(self):
        registry = ToolRegistry()
        tool = ToolDef(name="removable", description="", parameters={}, handler=lambda a, c: None)
        registry.register(tool)
        registry.unregister("removable")
        assert registry.get("removable") is None

    def test_list_tools_with_filter(self):
        registry = ToolRegistry()
        t1 = ToolDef(name="web", description="", parameters={}, handler=lambda a, c: None)
        t2 = ToolDef(name="search", description="", parameters={}, handler=lambda a, c: None)
        registry.register(t1)
        registry.register(t2)
        filtered = registry.list_tools(names=["web"])
        assert len(filtered) == 1
        assert filtered[0].name == "web"
```

**Step 2: Run test to verify it fails**

Run: `cd packages/backend && python3 -m pytest tests/test_tool_registry.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'services.tool_registry'`

**Step 3: Write the implementation**

Create `packages/backend/services/tool_registry.py`:

```python
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
```

**Step 4: Run test to verify it passes**

Run: `cd packages/backend && python3 -m pytest tests/test_tool_registry.py -v`
Expected: All 9 tests PASS

**Step 5: Commit**

```bash
git add packages/backend/services/tool_registry.py packages/backend/tests/test_tool_registry.py
git commit -m "feat: add ToolRegistry with data structures for Max tool-calling"
```

---

### Task 2: Tool Call Parser

**Files:**
- Modify: `packages/backend/services/tool_registry.py`
- Test: `packages/backend/tests/test_tool_registry.py`

**Step 1: Write the failing tests**

Append to `packages/backend/tests/test_tool_registry.py`:

```python
from services.tool_registry import parse_tool_call, ToolCallParsed


class TestParseToolCall:
    def test_valid_tool_call(self):
        text = 'Let me search.\n\n<tool_call>\n{"tool": "web_search", "args": {"query": "AI benchmarks"}}\n</tool_call>'
        result = parse_tool_call(text)
        assert result is not None
        assert result.tool_name == "web_search"
        assert result.args == {"query": "AI benchmarks"}
        assert result.prefix == "Let me search."

    def test_no_tool_call(self):
        result = parse_tool_call("Just a normal response with no tools.")
        assert result is None

    def test_malformed_json(self):
        text = '<tool_call>\n{not valid json}\n</tool_call>'
        result = parse_tool_call(text)
        assert result is None

    def test_missing_tool_field(self):
        text = '<tool_call>\n{"args": {"query": "test"}}\n</tool_call>'
        result = parse_tool_call(text)
        assert result is None

    def test_prefix_preserved(self):
        text = 'I will search for that information.\n\n<tool_call>\n{"tool": "search", "args": {}}\n</tool_call>'
        result = parse_tool_call(text)
        assert result is not None
        assert "I will search" in result.prefix

    def test_suffix_after_tool_call_ignored(self):
        text = '<tool_call>\n{"tool": "search", "args": {}}\n</tool_call>\nExtra text after'
        result = parse_tool_call(text)
        assert result is not None
        assert result.tool_name == "search"

    def test_empty_args(self):
        text = '<tool_call>\n{"tool": "system_status", "args": {}}\n</tool_call>'
        result = parse_tool_call(text)
        assert result is not None
        assert result.args == {}

    def test_partial_opening_tag_not_matched(self):
        result = parse_tool_call("Here is a <tool_call without closing")
        assert result is None
```

**Step 2: Run test to verify it fails**

Run: `cd packages/backend && python3 -m pytest tests/test_tool_registry.py::TestParseToolCall -v`
Expected: FAIL with `ImportError: cannot import name 'parse_tool_call'`

**Step 3: Add parser to tool_registry.py**

Add to `packages/backend/services/tool_registry.py`:

```python
import json
import re

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
```

**Step 4: Run test to verify it passes**

Run: `cd packages/backend && python3 -m pytest tests/test_tool_registry.py -v`
Expected: All 17 tests PASS

**Step 5: Commit**

```bash
git add packages/backend/services/tool_registry.py packages/backend/tests/test_tool_registry.py
git commit -m "feat: add tool call parser for <tool_call> blocks in LLM output"
```

---

### Task 3: System Prompt Generator

**Files:**
- Modify: `packages/backend/services/tool_registry.py`
- Test: `packages/backend/tests/test_tool_registry.py`

**Step 1: Write the failing tests**

Append to `packages/backend/tests/test_tool_registry.py`:

```python
class TestPromptGeneration:
    def test_generate_tools_prompt(self):
        registry = ToolRegistry()
        registry.register(ToolDef(
            name="web_search",
            description="Search the internet for current information. Use when the user asks about current events.",
            parameters={"type": "object", "properties": {"query": {"type": "string"}}},
            handler=lambda a, c: None,
        ))
        registry.register(ToolDef(
            name="system_status",
            description="Check GPU, model, and storage health.",
            parameters={"type": "object", "properties": {}},
            handler=lambda a, c: None,
        ))
        prompt = registry.generate_tools_prompt()
        assert "<tool_call>" in prompt
        assert "web_search" in prompt
        assert "system_status" in prompt
        assert "Search the internet" in prompt

    def test_empty_registry_returns_empty(self):
        registry = ToolRegistry()
        prompt = registry.generate_tools_prompt()
        assert prompt == ""

    def test_filtered_tools_prompt(self):
        registry = ToolRegistry()
        registry.register(ToolDef(name="a", description="Tool A", parameters={}, handler=lambda a, c: None))
        registry.register(ToolDef(name="b", description="Tool B", parameters={}, handler=lambda a, c: None))
        prompt = registry.generate_tools_prompt(exclude=["b"])
        assert "Tool A" in prompt
        assert "Tool B" not in prompt
```

**Step 2: Run test to verify it fails**

Run: `cd packages/backend && python3 -m pytest tests/test_tool_registry.py::TestPromptGeneration -v`
Expected: FAIL with `AttributeError: 'ToolRegistry' object has no attribute 'generate_tools_prompt'`

**Step 3: Add prompt generation to ToolRegistry**

Add to the `ToolRegistry` class in `packages/backend/services/tool_registry.py`:

```python
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
```

**Step 4: Run test to verify it passes**

Run: `cd packages/backend && python3 -m pytest tests/test_tool_registry.py -v`
Expected: All 20 tests PASS

**Step 5: Commit**

```bash
git add packages/backend/services/tool_registry.py packages/backend/tests/test_tool_registry.py
git commit -m "feat: add system prompt generation from registered tools"
```

---

### Task 4: Tool Execution Loop

This is the core change — replacing the single-pass `generate()` in `ai.py` with a multi-turn loop that detects `<tool_call>` blocks and executes tools.

**Files:**
- Create: `packages/backend/services/tool_executor.py`
- Test: `packages/backend/tests/test_tool_executor.py`

**Step 1: Write the failing tests**

Create `packages/backend/tests/test_tool_executor.py`:

```python
"""Tests for the tool execution loop."""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock

from services.tool_registry import (
    ToolDef,
    ToolResult,
    ToolContext,
    ToolRegistry,
    Artifact,
)
from services.tool_executor import ToolExecutor


def make_context(**kwargs) -> ToolContext:
    defaults = dict(
        project_id=None,
        conversation_id=None,
        recording_ids=[],
        document_ids=[],
        db=MagicMock(),
        ai_service=None,
    )
    defaults.update(kwargs)
    return ToolContext(**defaults)


class TestToolExecutor:
    @pytest.mark.asyncio
    async def test_no_tool_call_streams_normally(self):
        """When LLM output has no <tool_call>, tokens stream through unchanged."""
        registry = ToolRegistry()
        executor = ToolExecutor(registry)

        chunks = [
            MagicMock(content="Hello ", finish_reason=None),
            MagicMock(content="world", finish_reason=None),
            MagicMock(content=None, finish_reason="stop"),
        ]

        async def mock_stream(messages, options):
            for c in chunks:
                yield c

        ai_service = MagicMock()
        ai_service.chat_stream = mock_stream
        ctx = make_context(ai_service=ai_service)

        events = []
        async for event in executor.execute(messages=[], options=MagicMock(), ctx=ctx):
            events.append(event)

        tokens = [e for e in events if e.get("token")]
        assert len(tokens) == 2
        assert any(e.get("done") for e in events)

    @pytest.mark.asyncio
    async def test_tool_call_detected_and_executed(self):
        """When LLM emits a <tool_call>, the tool runs and result feeds back."""
        registry = ToolRegistry()

        async def search_handler(args, ctx):
            return ToolResult(content=f"Found results for: {args['query']}")

        registry.register(ToolDef(
            name="project_search",
            description="Search",
            parameters={"type": "object", "properties": {"query": {"type": "string"}}},
            handler=search_handler,
        ))

        executor = ToolExecutor(registry)

        call_count = 0

        async def mock_stream(messages, options):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First call: LLM decides to use a tool
                yield MagicMock(content='Let me search.\n\n<tool_call>\n{"tool": "project_search", "args": {"query": "revenue"}}\n</tool_call>', finish_reason=None)
                yield MagicMock(content=None, finish_reason="stop")
            else:
                # Second call: LLM responds with tool result in context
                yield MagicMock(content="Revenue was discussed in 3 transcripts.", finish_reason=None)
                yield MagicMock(content=None, finish_reason="stop")

        ai_service = MagicMock()
        ai_service.chat_stream = mock_stream
        ctx = make_context(ai_service=ai_service)

        events = []
        async for event in executor.execute(messages=[], options=MagicMock(), ctx=ctx):
            events.append(event)

        # Should see: prefix token, tool_call event, tool_result event, final token, done
        tool_calls = [e for e in events if "tool_call" in e]
        tool_results = [e for e in events if "tool_result" in e]
        assert len(tool_calls) == 1
        assert tool_calls[0]["tool_call"]["name"] == "project_search"
        assert len(tool_results) == 1
        assert "Found results" in tool_results[0]["tool_result"]["summary"]

    @pytest.mark.asyncio
    async def test_max_iterations_cap(self):
        """Tool calls are capped at MAX_TOOL_CALLS per turn."""
        registry = ToolRegistry()

        async def echo_handler(args, ctx):
            return ToolResult(content="echo")

        registry.register(ToolDef(
            name="echo", description="", parameters={}, handler=echo_handler,
        ))

        executor = ToolExecutor(registry, max_tool_calls=2)

        async def always_call_tool(messages, options):
            yield MagicMock(content='<tool_call>\n{"tool": "echo", "args": {}}\n</tool_call>', finish_reason=None)
            yield MagicMock(content=None, finish_reason="stop")

        ai_service = MagicMock()
        ai_service.chat_stream = always_call_tool
        ctx = make_context(ai_service=ai_service)

        events = []
        async for event in executor.execute(messages=[], options=MagicMock(), ctx=ctx):
            events.append(event)

        tool_calls = [e for e in events if "tool_call" in e]
        assert len(tool_calls) == 2  # Capped at max

    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self):
        """Calling an unregistered tool returns an error message, not a crash."""
        registry = ToolRegistry()
        executor = ToolExecutor(registry)

        async def mock_stream(messages, options):
            yield MagicMock(content='<tool_call>\n{"tool": "nonexistent", "args": {}}\n</tool_call>', finish_reason=None)
            yield MagicMock(content=None, finish_reason="stop")

        ai_service = MagicMock()
        ai_service.chat_stream = mock_stream
        ctx = make_context(ai_service=ai_service)

        events = []
        async for event in executor.execute(messages=[], options=MagicMock(), ctx=ctx):
            events.append(event)

        tool_results = [e for e in events if "tool_result" in e]
        assert len(tool_results) == 1
        assert "not available" in tool_results[0]["tool_result"]["summary"].lower()

    @pytest.mark.asyncio
    async def test_tool_handler_exception_graceful(self):
        """If a tool handler raises, the error is returned gracefully."""
        registry = ToolRegistry()

        async def failing_handler(args, ctx):
            raise RuntimeError("DB connection lost")

        registry.register(ToolDef(
            name="broken", description="", parameters={}, handler=failing_handler,
        ))
        executor = ToolExecutor(registry)

        async def mock_stream(messages, options):
            yield MagicMock(content='<tool_call>\n{"tool": "broken", "args": {}}\n</tool_call>', finish_reason=None)
            yield MagicMock(content=None, finish_reason="stop")

        ai_service = MagicMock()
        ai_service.chat_stream = mock_stream
        ctx = make_context(ai_service=ai_service)

        events = []
        async for event in executor.execute(messages=[], options=MagicMock(), ctx=ctx):
            events.append(event)

        tool_results = [e for e in events if "tool_result" in e]
        assert len(tool_results) == 1
        assert "error" in tool_results[0]["tool_result"]["summary"].lower()

    @pytest.mark.asyncio
    async def test_artifacts_accumulated(self):
        """Artifacts from tool results are accumulated and returned with done."""
        registry = ToolRegistry()

        async def doc_handler(args, ctx):
            return ToolResult(
                content="Document created",
                artifacts=[Artifact(type="file_download", data={"url": "/files/report.pdf", "filename": "report.pdf"})],
            )

        registry.register(ToolDef(
            name="generate_document", description="", parameters={}, handler=doc_handler,
        ))
        executor = ToolExecutor(registry)

        call_count = 0

        async def mock_stream(messages, options):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                yield MagicMock(content='<tool_call>\n{"tool": "generate_document", "args": {}}\n</tool_call>', finish_reason=None)
                yield MagicMock(content=None, finish_reason="stop")
            else:
                yield MagicMock(content="Here is your report.", finish_reason=None)
                yield MagicMock(content=None, finish_reason="stop")

        ai_service = MagicMock()
        ai_service.chat_stream = mock_stream
        ctx = make_context(ai_service=ai_service)

        events = []
        async for event in executor.execute(messages=[], options=MagicMock(), ctx=ctx):
            events.append(event)

        done_events = [e for e in events if e.get("done")]
        assert len(done_events) == 1
        assert len(done_events[0]["artifacts"]) == 1
        assert done_events[0]["artifacts"][0]["type"] == "file_download"
```

**Step 2: Run test to verify it fails**

Run: `cd packages/backend && python3 -m pytest tests/test_tool_executor.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'services.tool_executor'`

**Step 3: Write the implementation**

Create `packages/backend/services/tool_executor.py`:

```python
"""Tool execution loop for Max AI assistant.

Handles the multi-turn cycle: stream LLM output → detect <tool_call> →
execute tool → feed result back → continue streaming. Caps at a maximum
number of tool calls per turn to prevent runaway loops.
"""

from __future__ import annotations

import logging
from typing import Any, AsyncGenerator

from core.interfaces import ChatMessage, ChatOptions
from services.tool_registry import (
    Artifact,
    ToolCallParsed,
    ToolContext,
    ToolRegistry,
    ToolResult,
    parse_tool_call,
)

logger = logging.getLogger(__name__)

# Default maximum tool calls per chat turn
_DEFAULT_MAX_TOOL_CALLS = 5


class ToolExecutor:
    """Executes the tool-calling loop for a single chat turn.

    Yields SSE-ready dicts: {"token": ...}, {"tool_call": ...},
    {"tool_result": ...}, {"done": True, "artifacts": [...]}.
    """

    def __init__(
        self,
        registry: ToolRegistry,
        max_tool_calls: int = _DEFAULT_MAX_TOOL_CALLS,
    ) -> None:
        self._registry = registry
        self._max_tool_calls = max_tool_calls

    async def execute(
        self,
        messages: list[ChatMessage],
        options: ChatOptions,
        ctx: ToolContext,
    ) -> AsyncGenerator[dict, None]:
        """Run the multi-turn tool execution loop.

        Streams SSE event dicts to the caller.
        """
        artifacts: list[dict] = []
        tool_call_count = 0
        current_messages = list(messages)

        while True:
            # Collect the full LLM response (we need to check for tool calls)
            full_response = ""
            async for chunk in ctx.ai_service.chat_stream(current_messages, options):
                if chunk.content:
                    full_response += chunk.content

            # Check for a tool call in the response
            parsed = parse_tool_call(full_response)

            if parsed is None:
                # No tool call — stream the response normally
                if full_response:
                    yield {"token": full_response}
                yield {"done": True, "artifacts": artifacts}
                return

            # Tool call detected
            tool_call_count += 1

            # Stream the prefix text (Max's explanation before the tool call)
            if parsed.prefix:
                yield {"token": parsed.prefix}

            # Emit tool_call event for the frontend
            yield {"tool_call": {"name": parsed.tool_name, "args": parsed.args}}

            # Execute the tool
            tool_result = await self._run_tool(parsed, ctx)

            # Collect artifacts
            for artifact in tool_result.artifacts:
                artifacts.append({"type": artifact.type, **artifact.data})

            # Emit tool_result event for the frontend
            summary = tool_result.content[:200] + ("..." if len(tool_result.content) > 200 else "")
            yield {"tool_result": {"name": parsed.tool_name, "summary": summary}}

            # Check iteration cap
            if tool_call_count >= self._max_tool_calls:
                logger.warning(
                    "Tool call cap reached (%d), forcing final response",
                    self._max_tool_calls,
                )
                yield {"token": "\n\n[Reached maximum tool calls for this turn]"}
                yield {"done": True, "artifacts": artifacts}
                return

            # Feed tool result back to the LLM for the next iteration
            current_messages = list(messages) + [
                ChatMessage(role="assistant", content=full_response),
                ChatMessage(role="user", content=f"<tool_result>\n{tool_result.content}\n</tool_result>"),
            ]

    async def _run_tool(self, parsed: ToolCallParsed, ctx: ToolContext) -> ToolResult:
        """Execute a single tool call, handling errors gracefully."""
        tool = self._registry.get(parsed.tool_name)
        if tool is None:
            return ToolResult(
                content=f"Error: Tool '{parsed.tool_name}' is not available.",
            )

        try:
            result = await tool.handler(parsed.args, ctx)
            return result
        except Exception as e:
            logger.exception("Tool '%s' failed", parsed.tool_name)
            return ToolResult(
                content=f"Error running {parsed.tool_name}: {e}",
            )
```

**Step 4: Run test to verify it passes**

Run: `cd packages/backend && python3 -m pytest tests/test_tool_executor.py -v`
Expected: All 6 tests PASS

**Step 5: Commit**

```bash
git add packages/backend/services/tool_executor.py packages/backend/tests/test_tool_executor.py
git commit -m "feat: add ToolExecutor with multi-turn execution loop"
```

---

### Task 5: Integrate Tool Executor into Chat Endpoint

**Files:**
- Modify: `packages/backend/api/routes/ai.py:170-181` (MultiChatRequest)
- Modify: `packages/backend/api/routes/ai.py:826-916` (web search block — will be removed, replaced by tool)
- Modify: `packages/backend/api/routes/ai.py:969-974` (system prompt + help injection — replaced by tools prompt)
- Modify: `packages/backend/api/routes/ai.py:1068-1084` (generate() function — replaced by ToolExecutor)
- Modify: `packages/backend/api/main.py:149-180` (lifespan — register tools at startup)

**Step 1: Register tools at startup**

Create `packages/backend/services/tools/__init__.py`:

```python
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
```

In `packages/backend/api/main.py`, add after line 174 (`_plugin_registry.apply_job_handlers(job_queue)`):

```python
    # Register Max AI tools
    from services.tools import register_all_tools
    from services.tool_registry import get_registry
    register_all_tools(get_registry())
    logger.info("Registered %d Max AI tools", len(get_registry().list_tools()))
```

**Step 2: Update the generate() function in ai.py**

Replace the `generate()` function at line 1070-1083 with:

```python
    # Build tool executor
    from services.tool_registry import get_registry, ToolContext
    from services.tool_executor import ToolExecutor

    registry = get_registry()
    tool_ctx = ToolContext(
        project_id=getattr(request, 'project_id', None),
        conversation_id=request.conversation_id,
        recording_ids=request.recording_ids,
        document_ids=request.document_ids,
        db=db,
        ai_service=ai_service,
    )

    # Determine which tools are available for this request
    exclude_tools = []
    if not request.web_search_enabled:
        exclude_tools.append("web_search")

    # Inject tools into system prompt
    tools_prompt = registry.generate_tools_prompt(exclude=exclude_tools)
    if tools_prompt:
        # Prepend tools to the system message content
        messages[0] = ChatMessage(
            role="system",
            content=messages[0].content + tools_prompt,
        )

    executor = ToolExecutor(registry)

    async def generate():
        try:
            # Send web sources early if from pre-tool web search (backwards compat)
            if web_sources:
                yield f"data: {json.dumps({'web_sources': web_sources})}\n\n"
            async for event in executor.execute(messages, options, tool_ctx):
                yield f"data: {json.dumps(event)}\n\n"
        except Exception as e:
            logger.exception("Multi-chat stream failed")
            yield f"data: {json.dumps({'error': str(e)})}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")
```

**Step 3: Verify the app starts and basic chat still works**

Run: `cd packages/backend && python3 -m uvicorn api.main:app --port 52780`
Expected: App starts, logs "Registered 0 Max AI tools"

**Step 4: Run all existing tests to verify no regressions**

Run: `cd packages/backend && python3 -m pytest tests/ -v`
Expected: All existing tests still pass

**Step 5: Commit**

```bash
git add packages/backend/services/tools/__init__.py packages/backend/api/main.py packages/backend/api/routes/ai.py
git commit -m "feat: integrate ToolExecutor into chat endpoint with dynamic prompt injection"
```

---

### Task 6: Frontend SSE Event Handling

**Files:**
- Modify: `packages/frontend/src/lib/api.ts:754-761` (ChatStreamToken interface)
- Modify: `packages/frontend/src/components/ai/ChatPanel.tsx:31-121` (SSE handling)
- Modify: `packages/frontend/src/components/ai/ChatMessages.tsx:3-8` (ChatMessage interface)
- Create: `packages/frontend/src/components/ai/ToolActivityCard.tsx`

**Step 1: Extend the ChatStreamToken type**

In `packages/frontend/src/lib/api.ts`, update the `ChatStreamToken` interface (line 754):

```typescript
export interface ChatStreamToken {
  token?: string;
  done?: boolean;
  compressed_memory?: string | null;
  web_sources?: Array<{ title: string; url: string }>;
  model?: string;
  error?: string;
  tool_call?: { name: string; args: Record<string, unknown> };
  tool_result?: { name: string; summary: string };
  artifacts?: Array<{ type: string; url: string; filename: string }>;
}
```

**Step 2: Extend the ChatMessage interface**

In `packages/frontend/src/components/ai/ChatMessages.tsx`, update lines 3-8:

```typescript
export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  webSources?: Array<{ title: string; url: string }>;
  artifacts?: Array<{ type: string; url: string; filename: string }>;
  toolCalls?: Array<{ name: string; summary: string }>;
}
```

**Step 3: Create ToolActivityCard component**

Create `packages/frontend/src/components/ai/ToolActivityCard.tsx`:

```tsx
interface ToolActivity {
  name: string;
  args?: Record<string, unknown>;
  summary?: string;
  status: 'running' | 'complete';
}

const TOOL_ICONS: Record<string, string> = {
  web_search: 'M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z',
  project_search: 'M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z',
  global_search: 'M3.055 11H5a2 2 0 012 2v1a2 2 0 002 2 2 2 0 012 2v2.945M8 3.935V5.5A2.5 2.5 0 0010.5 8h.5a2 2 0 012 2 2 2 0 104 0 2 2 0 012-2h1.064M15 20.488V18a2 2 0 012-2h3.064',
  generate_document: 'M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z',
  export_transcript: 'M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z',
  summarize_transcript: 'M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2',
  get_context: 'M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10',
  highlight_segments: 'M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z',
  add_note: 'M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z',
};

const TOOL_LABELS: Record<string, string> = {
  web_search: 'Searching the web',
  project_search: 'Searching project',
  global_search: 'Searching all projects',
  generate_document: 'Creating document',
  export_transcript: 'Exporting transcript',
  summarize_transcript: 'Summarizing',
  quality_review: 'Reviewing quality',
  get_context: 'Reading content',
  app_help: 'Looking up help',
  highlight_segments: 'Highlighting',
  add_note: 'Adding note',
  create_project: 'Creating project',
  tag_recordings: 'Tagging',
  get_recording_info: 'Looking up info',
  system_status: 'Checking system',
};

const DEFAULT_ICON = 'M13 10V3L4 14h7v7l9-11h-7z';

export function ToolActivityCard({ activity }: { activity: ToolActivity }) {
  const icon = TOOL_ICONS[activity.name] || DEFAULT_ICON;
  const label = TOOL_LABELS[activity.name] || activity.name;
  const queryArg = activity.args?.query as string | undefined;

  return (
    <div className="flex items-center gap-2 rounded-lg border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-800 px-3 py-2 text-xs">
      <svg
        className={`w-3.5 h-3.5 flex-shrink-0 ${activity.status === 'running' ? 'text-blue-500 animate-pulse' : 'text-gray-400 dark:text-gray-500'}`}
        fill="none"
        viewBox="0 0 24 24"
        stroke="currentColor"
        strokeWidth="2"
        aria-hidden="true"
      >
        <path strokeLinecap="round" strokeLinejoin="round" d={icon} />
      </svg>
      <span className="text-gray-600 dark:text-gray-300">
        {activity.status === 'complete' && activity.summary
          ? activity.summary
          : `${label}${queryArg ? `: "${queryArg}"` : ''}...`}
      </span>
    </div>
  );
}

export type { ToolActivity };
```

**Step 4: Update ChatPanel.tsx to handle tool events**

In `packages/frontend/src/components/ai/ChatPanel.tsx`, add state for tool activities and artifacts (after line 33):

```typescript
const [toolActivities, setToolActivities] = useState<Array<import('./ToolActivityCard').ToolActivity>>([]);
const [streamingArtifacts, setStreamingArtifacts] = useState<Array<{ type: string; url: string; filename: string }>>([]);
```

In the SSE token handling loop (after the `token.web_sources` block at line 97), add:

```typescript
        if (token.tool_call) {
          setToolActivities((prev) => [
            ...prev,
            { name: token.tool_call!.name, args: token.tool_call!.args, status: 'running' },
          ]);
        }
        if (token.tool_result) {
          setToolActivities((prev) =>
            prev.map((a) =>
              a.name === token.tool_result!.name && a.status === 'running'
                ? { ...a, summary: token.tool_result!.summary, status: 'complete' as const }
                : a
            )
          );
        }
        if (token.artifacts) {
          setStreamingArtifacts(token.artifacts);
        }
```

Update the `done` handler (line 98-107) to include artifacts and tool calls:

```typescript
        if (token.done) {
          const assistantMessage: ChatMessage = {
            id: crypto.randomUUID(),
            role: 'assistant',
            content: fullContent,
            webSources: currentWebSources,
            artifacts: token.artifacts || streamingArtifacts.length > 0 ? (token.artifacts || streamingArtifacts) : undefined,
            toolCalls: toolActivities.length > 0 ? toolActivities.map(a => ({ name: a.name, summary: a.summary || '' })) : undefined,
          };
          setMessages((prev) => [...prev, assistantMessage]);
          setStreamingContent('');
        }
```

Clear tool state in the `finally` block (line 117-121):

```typescript
    } finally {
      setIsStreaming(false);
      setStreamingContent('');
      setStreamingWebSources([]);
      setToolActivities([]);
      setStreamingArtifacts([]);
    }
```

Also clear in the `setStreamingWebSources([])` call at line 50:

```typescript
    setToolActivities([]);
    setStreamingArtifacts([]);
```

Pass tool activities to ChatMessages (line 201-206):

```tsx
      <ChatMessages
        messages={messages}
        isStreaming={isStreaming}
        streamingContent={streamingContent}
        streamingWebSources={streamingWebSources}
        toolActivities={toolActivities}
      />
```

**Step 5: Update ChatMessages.tsx to render tool activity cards and artifact downloads**

In `packages/frontend/src/components/ai/ChatMessages.tsx`:

Add to the interface (line 10-15):

```typescript
import { ToolActivityCard, type ToolActivity } from './ToolActivityCard';

interface ChatMessagesProps {
  messages: ChatMessage[];
  isStreaming: boolean;
  streamingContent: string;
  streamingWebSources?: Array<{ title: string; url: string }>;
  toolActivities?: ToolActivity[];
}
```

Update the function signature (line 17):

```typescript
export function ChatMessages({ messages, isStreaming, streamingContent, streamingWebSources, toolActivities }: ChatMessagesProps) {
```

Add artifact rendering inside the message bubble, after the webSources block (after line 74):

```tsx
            {msg.role === 'assistant' && msg.artifacts && msg.artifacts.length > 0 && (
              <div className="mt-2 pt-2 border-t border-gray-200 dark:border-gray-600 space-y-1.5">
                {msg.artifacts.map((artifact, i) => (
                  <a
                    key={i}
                    href={artifact.url}
                    download={artifact.filename}
                    className="flex items-center gap-2 rounded-md border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-800 px-3 py-2 text-xs text-gray-700 dark:text-gray-300 hover:border-blue-400 transition-colors"
                  >
                    <svg className="w-4 h-4 text-blue-500 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2" aria-hidden="true">
                      <path strokeLinecap="round" strokeLinejoin="round" d="M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                    </svg>
                    <span>{artifact.filename}</span>
                  </a>
                ))}
              </div>
            )}
```

Add tool activity cards section between the web source cards and the thinking indicator (after line 123):

```tsx
      {/* Tool activity cards - show what Max is doing */}
      {isStreaming && toolActivities && toolActivities.length > 0 && (
        <div className="flex justify-start" aria-label="Tool activity">
          <div className="max-w-[90%] space-y-1.5">
            {toolActivities.map((activity, i) => (
              <ToolActivityCard key={`${activity.name}-${i}`} activity={activity} />
            ))}
          </div>
        </div>
      )}
```

**Step 6: Verify frontend compiles**

Run: `cd /Users/JonWFH/jondev/verbatim-studio/packages/frontend && npx tsc --noEmit`
Expected: No type errors

**Step 7: Commit**

```bash
git add packages/frontend/src/lib/api.ts packages/frontend/src/components/ai/ChatPanel.tsx packages/frontend/src/components/ai/ChatMessages.tsx packages/frontend/src/components/ai/ToolActivityCard.tsx
git commit -m "feat: frontend SSE handling for tool_call, tool_result, and artifact events"
```

---

## Phase 2: Tool Implementations

Each tool is a thin wrapper around existing services. Implement one at a time, register in `tools/__init__.py`.

### Task 7: web_search Tool (Converted)

**Files:**
- Create: `packages/backend/services/tools/web_search_tool.py`
- Modify: `packages/backend/services/tools/__init__.py`
- Modify: `packages/backend/api/routes/ai.py` (remove hardcoded web search block)
- Test: `packages/backend/tests/test_tools/test_web_search_tool.py`

**Step 1: Write the failing test**

Create `packages/backend/tests/test_tools/__init__.py` (empty) and `packages/backend/tests/test_tools/test_web_search_tool.py`:

```python
"""Tests for web_search tool."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from services.tool_registry import ToolContext
from services.tools.web_search_tool import web_search_tool, handle_web_search


def make_ctx(**kwargs):
    defaults = dict(
        project_id=None, conversation_id=None,
        recording_ids=[], document_ids=[],
        db=MagicMock(), ai_service=None,
    )
    defaults.update(kwargs)
    return ToolContext(**defaults)


class TestWebSearchTool:
    def test_tool_def_exists(self):
        assert web_search_tool.name == "web_search"
        assert "internet" in web_search_tool.description.lower() or "search" in web_search_tool.description.lower()

    @pytest.mark.asyncio
    async def test_search_returns_results(self):
        from services.web_search import WebSearchResult

        mock_results = [
            WebSearchResult(title="Result 1", url="https://example.com", content="Content 1", relevance_score=0.9),
            WebSearchResult(title="Result 2", url="https://test.com", content="Content 2", relevance_score=0.7),
        ]

        with patch("services.tools.web_search_tool.load_web_search_config") as mock_config, \
             patch("services.tools.web_search_tool.create_search_provider") as mock_provider_factory:
            mock_config.return_value = MagicMock(provider="tavily", api_key="test")
            mock_provider = AsyncMock()
            mock_provider.search.return_value = mock_results
            mock_provider_factory.return_value = mock_provider

            result = await handle_web_search({"query": "AI benchmarks"}, make_ctx())

        assert "Result 1" in result.content
        assert "Result 2" in result.content

    @pytest.mark.asyncio
    async def test_search_no_api_key_returns_error(self):
        with patch("services.tools.web_search_tool.load_web_search_config", return_value=None):
            result = await handle_web_search({"query": "test"}, make_ctx())

        assert "not configured" in result.content.lower() or "no api key" in result.content.lower()

    @pytest.mark.asyncio
    async def test_url_extraction(self):
        from services.web_search import WebSearchResult

        mock_results = [
            WebSearchResult(title="Page", url="https://example.com/article", content="Full page content", relevance_score=1.0),
        ]

        with patch("services.tools.web_search_tool.load_web_search_config") as mock_config, \
             patch("services.tools.web_search_tool.create_search_provider") as mock_provider_factory:
            mock_config.return_value = MagicMock()
            mock_provider = AsyncMock()
            mock_provider.extract.return_value = mock_results
            mock_provider_factory.return_value = mock_provider

            result = await handle_web_search(
                {"query": "summarize this", "urls": ["https://example.com/article"]},
                make_ctx(),
            )

        mock_provider.extract.assert_called_once()
        assert "Full page content" in result.content
```

**Step 2: Run test to verify it fails**

Run: `cd packages/backend && python3 -m pytest tests/test_tools/test_web_search_tool.py -v`
Expected: FAIL with `ModuleNotFoundError`

**Step 3: Write the implementation**

Create `packages/backend/services/tools/web_search_tool.py`:

```python
"""Web search tool — provider-agnostic internet search.

Wraps the existing web_search service. Supports Tavily, Brave Search,
SearXNG, and custom self-hosted providers via the provider factory.
"""

from __future__ import annotations

import logging

from services.tool_registry import Artifact, ToolContext, ToolDef, ToolResult
from services.web_search import (
    create_search_provider,
    format_results_for_context,
    load_web_search_config,
    get_cached_results,
    cache_results,
)

logger = logging.getLogger(__name__)


async def handle_web_search(args: dict, ctx: ToolContext) -> ToolResult:
    """Execute a web search or URL extraction."""
    query = args.get("query", "")
    urls = args.get("urls")

    config = await load_web_search_config()
    if not config:
        return ToolResult(content="Web search is not configured. No API key found.")

    # Check cache first
    if not urls:
        cached = get_cached_results(query)
        if cached:
            formatted = format_results_for_context(cached, max_tokens=3000)
            return ToolResult(content=formatted)

    provider = create_search_provider(config)

    try:
        if urls and hasattr(provider, "extract"):
            results = await provider.extract(urls)
        else:
            results = await provider.search(query)
            # Enrich top 3 with full content
            if results and hasattr(provider, "extract"):
                top_urls = [r.url for r in results[:3]]
                try:
                    extracted = await provider.extract(top_urls)
                    extracted_map = {e.url: e.content for e in extracted}
                    for r in results:
                        if r.url in extracted_map:
                            r.content = extracted_map[r.url]
                except Exception as e:
                    logger.debug("Extract enrichment failed: %s", e)

        if results:
            cache_results(query, results)
            formatted = format_results_for_context(results, max_tokens=3000)
            return ToolResult(content=formatted)
        else:
            return ToolResult(content="No results found.")

    except Exception as e:
        logger.exception("Web search failed")
        return ToolResult(content=f"Web search failed: {e}")


web_search_tool = ToolDef(
    name="web_search",
    description="Search the internet for current information. Use when the user asks about current events, recent data, or asks you to look something up. Also extracts content from URLs.",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "The search query"},
            "urls": {"type": "array", "items": {"type": "string"}, "description": "Optional: specific URLs to extract content from"},
        },
        "required": ["query"],
    },
    handler=handle_web_search,
    project_scoped=False,
)
```

Update `packages/backend/services/tools/__init__.py`:

```python
"""Tool registration for Max AI assistant."""

from services.tool_registry import ToolRegistry


def register_all_tools(registry: ToolRegistry) -> None:
    """Register all available tools with the registry."""
    from services.tools.web_search_tool import web_search_tool
    registry.register(web_search_tool)
```

**Step 4: Run test to verify it passes**

Run: `cd packages/backend && python3 -m pytest tests/test_tools/test_web_search_tool.py -v`
Expected: All 4 tests PASS

**Step 5: Remove the hardcoded web search block from ai.py**

In `packages/backend/api/routes/ai.py`, remove the entire block from line 826 (`# --- Web search (if enabled and query detected) ---`) through line 915 (end of except block). Replace with:

```python
    # Web search is now handled by the web_search tool in the execution loop.
    # The web_search_enabled flag controls whether the tool is available.
    web_results_text = None
    web_sources = []
```

Also remove the `is_help_intent` function and the help injection block (lines 917-974), replacing the system prompt build with:

```python
    # Build system message (tools prompt injected by ToolExecutor)
    system_content = MAX_SYSTEM_PROMPT_GENERAL if request.general_mode else MAX_SYSTEM_PROMPT
```

**Step 6: Run all tests**

Run: `cd packages/backend && python3 -m pytest tests/ -v`
Expected: All tests pass (some web search integration tests may need updating to account for the tool-based flow)

**Step 7: Commit**

```bash
git add packages/backend/services/tools/ packages/backend/tests/test_tools/ packages/backend/api/routes/ai.py
git commit -m "feat: convert web_search to explicit tool, remove heuristic detection"
```

---

### Task 8: project_search and global_search Tools

**Files:**
- Create: `packages/backend/services/tools/search_tools.py`
- Modify: `packages/backend/services/tools/__init__.py`
- Test: `packages/backend/tests/test_tools/test_search_tools.py`

**Step 1: Write the failing test**

Create `packages/backend/tests/test_tools/test_search_tools.py`:

```python
"""Tests for project_search and global_search tools."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from sqlalchemy.ext.asyncio import AsyncSession

from services.tool_registry import ToolContext
from services.tools.search_tools import (
    project_search_tool,
    global_search_tool,
    handle_project_search,
    handle_global_search,
)


def make_ctx(**kwargs):
    defaults = dict(
        project_id="proj-123", conversation_id=None,
        recording_ids=[], document_ids=[],
        db=MagicMock(spec=AsyncSession), ai_service=None,
    )
    defaults.update(kwargs)
    return ToolContext(**defaults)


class TestProjectSearchTool:
    def test_tool_def(self):
        assert project_search_tool.name == "project_search"
        assert project_search_tool.project_scoped is True

    @pytest.mark.asyncio
    async def test_search_with_project_scope(self):
        """project_search should pass project_id to the search function."""
        mock_results = [
            MagicMock(type="segment", title="Recording A", text="quarterly revenue discussion", id="seg-1", recording_id="rec-1", recording_title="Meeting", start_time=30.0),
        ]

        with patch("services.tools.search_tools._run_global_search", return_value=mock_results) as mock_search:
            result = await handle_project_search({"query": "quarterly revenue"}, make_ctx())

        mock_search.assert_called_once()
        call_kwargs = mock_search.call_args
        assert "quarterly revenue" in str(call_kwargs)
        assert "quarterly revenue" in result.content.lower() or "Recording A" in result.content


class TestGlobalSearchTool:
    def test_tool_def(self):
        assert global_search_tool.name == "global_search"
        assert global_search_tool.project_scoped is False

    @pytest.mark.asyncio
    async def test_search_crosses_projects(self):
        """global_search should NOT filter by project_id."""
        mock_results = []

        with patch("services.tools.search_tools._run_global_search", return_value=mock_results) as mock_search:
            result = await handle_global_search({"query": "test"}, make_ctx())

        mock_search.assert_called_once()
        assert "no results" in result.content.lower() or result.content
```

**Step 2: Run test to verify it fails**

Run: `cd packages/backend && python3 -m pytest tests/test_tools/test_search_tools.py -v`
Expected: FAIL

**Step 3: Write the implementation**

Create `packages/backend/services/tools/search_tools.py`:

```python
"""Search tools — project-scoped and global search.

Wraps the existing global search endpoint logic to search across
transcripts, documents, OCR text, notes, and conversations.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.tool_registry import ToolContext, ToolDef, ToolResult

logger = logging.getLogger(__name__)


async def _run_global_search(
    query: str,
    db: AsyncSession,
    project_id: str | None = None,
    limit: int = 10,
) -> list:
    """Run the global search logic (shared between project and global tools).

    Searches recordings, segments, documents, notes, and conversations.
    Returns a list of result dicts with type, title, text, and metadata.
    """
    from persistence.models import Recording, Segment, Transcript, Document, Note, Conversation, ConversationMessage

    results = []
    per_type_limit = max(limit // 5, 2)

    # Search recordings by title
    rec_q = select(Recording).where(Recording.title.ilike(f"%{query}%")).limit(per_type_limit)
    if project_id:
        rec_q = rec_q.where(Recording.project_id == project_id)
    recs = (await db.execute(rec_q)).scalars().all()
    for r in recs:
        results.append({"type": "recording", "title": r.title, "id": r.id})

    # Search segments by text
    seg_q = (
        select(Segment)
        .join(Transcript, Segment.transcript_id == Transcript.id)
        .join(Recording, Transcript.recording_id == Recording.id)
        .where(Segment.text.ilike(f"%{query}%"))
        .limit(per_type_limit)
    )
    if project_id:
        seg_q = seg_q.where(Recording.project_id == project_id)
    segs = (await db.execute(seg_q)).scalars().all()
    for s in segs:
        results.append({"type": "segment", "text": s.text[:200], "id": s.id, "start_time": s.start_time})

    # Search documents
    doc_q = select(Document).where(
        Document.title.ilike(f"%{query}%") | Document.extracted_text.ilike(f"%{query}%")
    ).limit(per_type_limit)
    if project_id:
        doc_q = doc_q.where(Document.project_id == project_id)
    docs = (await db.execute(doc_q)).scalars().all()
    for d in docs:
        results.append({"type": "document", "title": d.title, "id": d.id})

    return results


def _format_search_results(results: list) -> str:
    """Format search results into readable text for Max."""
    if not results:
        return "No results found."

    lines = [f"Found {len(results)} result(s):\n"]
    for r in results:
        if r["type"] == "recording":
            lines.append(f"- Recording: \"{r['title']}\" (id: {r['id']})")
        elif r["type"] == "segment":
            lines.append(f"- Transcript segment: \"{r['text']}\" (at {r.get('start_time', '?')}s)")
        elif r["type"] == "document":
            lines.append(f"- Document: \"{r['title']}\" (id: {r['id']})")
        else:
            lines.append(f"- {r['type']}: {r.get('title', r.get('text', ''))}")
    return "\n".join(lines)


async def handle_project_search(args: dict, ctx: ToolContext) -> ToolResult:
    """Search within the active project workspace."""
    query = args.get("query", "")
    results = await _run_global_search(query, ctx.db, project_id=ctx.project_id)
    return ToolResult(content=_format_search_results(results))


async def handle_global_search(args: dict, ctx: ToolContext) -> ToolResult:
    """Search across all projects."""
    query = args.get("query", "")
    results = await _run_global_search(query, ctx.db, project_id=None)
    return ToolResult(content=_format_search_results(results))


project_search_tool = ToolDef(
    name="project_search",
    description="Search transcripts, documents, notes, and chats within the current project workspace.",
    parameters={
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    },
    handler=handle_project_search,
    project_scoped=True,
)

global_search_tool = ToolDef(
    name="global_search",
    description="Search across ALL projects. Use when the user says 'across all projects' or names a specific different project.",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "project_name": {"type": "string", "description": "Optional: search a specific project by name"},
        },
        "required": ["query"],
    },
    handler=handle_global_search,
    project_scoped=False,
)
```

Update `packages/backend/services/tools/__init__.py` to register:

```python
    from services.tools.search_tools import project_search_tool, global_search_tool
    registry.register(project_search_tool)
    registry.register(global_search_tool)
```

**Step 4: Run tests**

Run: `cd packages/backend && python3 -m pytest tests/test_tools/ -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add packages/backend/services/tools/search_tools.py packages/backend/tests/test_tools/test_search_tools.py packages/backend/services/tools/__init__.py
git commit -m "feat: add project_search and global_search tools"
```

---

### Task 9: get_context Tool (Converted)

**Files:**
- Create: `packages/backend/services/tools/context_tool.py`
- Modify: `packages/backend/services/tools/__init__.py`
- Test: `packages/backend/tests/test_tools/test_context_tool.py`

This tool lets Max proactively pull content from any file in the active project — not just what the user attached. It uses semantic search (embeddings) when available, falling back to keyword search.

**Step 1: Write the test, Step 2: Verify fails, Step 3: Implement, Step 4: Verify passes, Step 5: Commit**

Follow the same TDD pattern as Tasks 7-8. The handler should:
1. Accept `query` and optional `content_types` (transcript, document)
2. Query the project's recordings/documents for matching content
3. Return extracted text from the most relevant matches
4. Use `DocumentEmbedding` for semantic search when the index exists

```bash
git commit -m "feat: add get_context tool for project-wide content retrieval"
```

---

### Task 10: app_help Tool (Converted)

**Files:**
- Create: `packages/backend/services/tools/help_tool.py`
- Modify: `packages/backend/services/tools/__init__.py`
- Modify: `packages/backend/api/routes/ai.py` (remove `is_help_intent()` and `MAX_HELP_CONTEXT` injection)
- Test: `packages/backend/tests/test_tools/test_help_tool.py`

Break `MAX_HELP_CONTEXT` into topic sections. The handler accepts an optional `topic` parameter and returns only the relevant section.

```bash
git commit -m "feat: convert app_help to explicit tool, remove keyword detection"
```

---

### Task 11: generate_document Tool

**Files:**
- Create: `packages/backend/services/tools/document_tools.py`
- Modify: `packages/backend/services/tools/__init__.py`
- Test: `packages/backend/tests/test_tools/test_document_tools.py`

Uses `reportlab` (PDF) and `python-docx` (DOCX) — both already in dependencies. The handler accepts `title`, `format`, and `sections` (list of `{heading, content}`). Generates the file, saves to a temp location, and returns an `Artifact` with the download URL.

```bash
git commit -m "feat: add generate_document tool for PDF/DOCX creation"
```

---

### Task 12: export_transcript Tool

**Files:**
- Modify: `packages/backend/services/tools/document_tools.py`
- Modify: `packages/backend/services/tools/__init__.py`
- Test: `packages/backend/tests/test_tools/test_document_tools.py`

Wraps the existing `ExportService` in `services/export.py`. Accepts `transcript_id` and `format`. Builds `ExportData` from the transcript's segments, calls the appropriate export method, returns the file as an `Artifact`.

```bash
git commit -m "feat: add export_transcript tool wrapping ExportService"
```

---

### Task 13: summarize_transcript Tool

**Files:**
- Create: `packages/backend/services/tools/analysis_tools.py`
- Modify: `packages/backend/services/tools/__init__.py`
- Test: `packages/backend/tests/test_tools/test_analysis_tools.py`

Wraps the existing summarization logic from `ai.py:1087-1138`. Calls `ai_service.summarize_transcript()`, persists to DB, returns structured summary text.

```bash
git commit -m "feat: add summarize_transcript tool"
```

---

### Task 14: quality_review Tool

**Files:**
- Modify: `packages/backend/services/tools/analysis_tools.py`
- Modify: `packages/backend/services/tools/__init__.py`
- Test: `packages/backend/tests/test_tools/test_analysis_tools.py`

Enqueues a quality review job via the existing job queue. Returns the job ID so Max can tell the user to check the quality review panel. Does NOT block waiting for completion — the review runs in the background.

```bash
git commit -m "feat: add quality_review tool"
```

---

### Task 15: Annotation Tools (highlight_segments, add_note)

**Files:**
- Create: `packages/backend/services/tools/annotation_tools.py`
- Modify: `packages/backend/services/tools/__init__.py`
- Test: `packages/backend/tests/test_tools/test_annotation_tools.py`

**highlight_segments**: Wraps the bulk highlight endpoint logic. Accepts `segment_ids` and `color`.

**add_note**: Wraps the note creation endpoint. Accepts `content`, `anchor_type`, `anchor_value`, and either `recording_id` or `document_id`.

```bash
git commit -m "feat: add highlight_segments and add_note tools"
```

---

### Task 16: Organization Tools (create_project, tag_recordings, get_recording_info, system_status)

**Files:**
- Create: `packages/backend/services/tools/organization_tools.py`
- Modify: `packages/backend/services/tools/__init__.py`
- Test: `packages/backend/tests/test_tools/test_organization_tools.py`

Each handler wraps existing endpoint logic:
- **create_project**: Creates a Project record, returns name and ID
- **tag_recordings**: Creates tags if needed, assigns to recordings
- **get_recording_info**: Queries recording metadata, or lists recent recordings
- **system_status**: Calls system info endpoints, returns formatted status

```bash
git commit -m "feat: add organization tools (create_project, tag_recordings, get_recording_info, system_status)"
```

---

## Phase 3: Migration Cleanup & Integration Tests

### Task 17: Remove Legacy Implicit Behaviors

**Files:**
- Modify: `packages/backend/api/routes/ai.py` (final cleanup)

Remove any remaining dead code from the implicit behaviors:
- `extract_search_query()` calls (moved to web_search tool)
- `is_help_intent()` function and `MAX_HELP_CONTEXT` injection (moved to app_help tool)
- Inline web search block (moved to web_search tool)
- The `web_search_enabled` flag still controls tool availability, but the heuristic code is gone

Verify the `MultiChatRequest.web_search_enabled` field still works — it now controls whether the `web_search` tool is included in the tools prompt (via `exclude` parameter in `generate_tools_prompt()`).

```bash
git commit -m "refactor: remove legacy implicit behavior code from chat endpoint"
```

---

### Task 18: End-to-End Integration Tests

**Files:**
- Create: `packages/backend/tests/test_tool_integration.py`

Test the full pipeline with a mock LLM that emits tool calls:
1. Chat request → LLM calls `project_search` → result fed back → LLM responds
2. Chat request → LLM calls `generate_document` → artifact returned in SSE
3. Chat request with `web_search_enabled=False` → LLM cannot see web_search tool
4. Chat request → LLM calls 6 tools → capped at 5
5. SSE event ordering: tool_call → tool_result → token → done
6. Migration parity: web search via tool produces same quality as old heuristic path

```bash
git commit -m "test: add end-to-end integration tests for tool execution pipeline"
```

---

### Task 19: Frontend Manual Testing & Polish

**Manual testing checklist:**
- [ ] Regular chat (no tools) still works normally
- [ ] Web search tool fires when asking "what are the latest AI benchmarks?"
- [ ] Tool activity cards appear and update during streaming
- [ ] Source cards still appear for web search results
- [ ] Artifact download cards appear for generate_document
- [ ] Multiple tool calls in one turn display correctly
- [ ] Toggling web search off hides it from Max's capabilities
- [ ] Error handling: broken tool doesn't crash the stream
- [ ] General mode still works
- [ ] Conversation save/load preserves artifacts and tool call history

Fix any UI issues found during manual testing.

```bash
git commit -m "fix: frontend polish for tool activity cards and artifact downloads"
```

---

### Task 20: Final Commit & Version Bump

Follow the release workflow in `CLAUDE_CONTEXT.md`:

1. Stage all remaining changes, commit
2. Bump version in all 3 files (next minor)
3. Commit version bump
4. Push
5. Tag and push tag
6. Create GitHub release with notes

```bash
git commit -m "chore: bump version to X.Y.Z"
git tag vX.Y.Z
git push origin main && git push origin vX.Y.Z
```

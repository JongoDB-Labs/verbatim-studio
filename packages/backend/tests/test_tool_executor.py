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

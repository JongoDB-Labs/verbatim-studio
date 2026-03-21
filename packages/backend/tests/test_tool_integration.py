"""End-to-end integration tests for the tool execution pipeline.

Tests the full flow: chat request -> LLM emits tool_call -> tool executes ->
result fed back -> LLM responds, including SSE event ordering, artifact
propagation, tool exclusion, iteration caps, and error handling.
"""

import pytest
from dataclasses import dataclass
from unittest.mock import MagicMock

from services.tool_registry import (
    Artifact,
    ToolContext,
    ToolDef,
    ToolRegistry,
    ToolResult,
)
from services.tool_executor import ToolExecutor


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@dataclass
class MockChunk:
    """Mimics the chunk objects yielded by a real AI service's chat_stream."""

    content: str | None
    finish_reason: str | None = None


class MockAIService:
    """Mock AI service that returns pre-configured responses in sequence.

    Each call to chat_stream consumes the next response string from the list.
    If calls exceed the list length, the last response is re-used.
    """

    def __init__(self, responses: list[str]) -> None:
        self._responses = responses
        self._call_index = 0

    async def chat_stream(self, messages, options):
        response = self._responses[min(self._call_index, len(self._responses) - 1)]
        self._call_index += 1
        yield MockChunk(content=response)
        yield MockChunk(content=None, finish_reason="stop")


def _make_context(ai_service=None, **overrides) -> ToolContext:
    """Build a minimal ToolContext for testing."""
    defaults = dict(
        project_id=None,
        conversation_id=None,
        recording_ids=[],
        document_ids=[],
        db=MagicMock(),
        ai_service=ai_service,
    )
    defaults.update(overrides)
    return ToolContext(**defaults)


async def _collect_events(executor: ToolExecutor, ctx: ToolContext) -> list[dict]:
    """Run execute() and collect all emitted SSE events into a list."""
    events: list[dict] = []
    async for event in executor.execute(messages=[], options=MagicMock(), ctx=ctx):
        events.append(event)
    return events


# ---------------------------------------------------------------------------
# Test 1: Full round-trip — LLM calls a tool, result fed back, LLM responds
# ---------------------------------------------------------------------------


class TestFullRoundTrip:
    @pytest.mark.asyncio
    async def test_tool_call_then_text_response(self):
        """LLM first emits a tool_call, then on 2nd call responds with plain
        text that incorporates the tool result."""
        registry = ToolRegistry()

        async def search_handler(args, ctx):
            return ToolResult(content=f"Found 42 results for '{args['query']}'")

        registry.register(
            ToolDef(
                name="project_search",
                description="Search recordings",
                parameters={
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                },
                handler=search_handler,
            )
        )

        ai_service = MockAIService(
            responses=[
                # 1st call: LLM decides to use a tool
                'Let me search for that.\n\n<tool_call>\n{"tool": "project_search", "args": {"query": "revenue"}}\n</tool_call>',
                # 2nd call: LLM produces final answer using tool result
                "Based on the search results, revenue was mentioned 42 times across your recordings.",
            ]
        )

        executor = ToolExecutor(registry)
        ctx = _make_context(ai_service=ai_service)
        events = await _collect_events(executor, ctx)

        # Verify the tool was called
        tool_calls = [e for e in events if "tool_call" in e]
        assert len(tool_calls) == 1
        assert tool_calls[0]["tool_call"]["name"] == "project_search"
        assert tool_calls[0]["tool_call"]["args"] == {"query": "revenue"}

        # Verify tool result was emitted
        tool_results = [e for e in events if "tool_result" in e]
        assert len(tool_results) == 1
        assert "Found 42 results" in tool_results[0]["tool_result"]["summary"]

        # Verify final text contains the LLM's second response
        tokens = [e["token"] for e in events if "token" in e]
        final_text = "".join(tokens)
        assert "42 times" in final_text

        # Verify done event
        done_events = [e for e in events if e.get("done")]
        assert len(done_events) == 1

    @pytest.mark.asyncio
    async def test_prefix_text_streamed_before_tool_call(self):
        """The text before <tool_call> (the LLM's explanation) is streamed
        as a token event before the tool_call event."""
        registry = ToolRegistry()

        async def handler(args, ctx):
            return ToolResult(content="result")

        registry.register(
            ToolDef(name="my_tool", description="desc", parameters={}, handler=handler)
        )

        ai_service = MockAIService(
            responses=[
                'I will look that up now.\n\n<tool_call>\n{"tool": "my_tool", "args": {}}\n</tool_call>',
                "Here is what I found.",
            ]
        )

        executor = ToolExecutor(registry)
        ctx = _make_context(ai_service=ai_service)
        events = await _collect_events(executor, ctx)

        # The prefix text should appear before the tool_call event
        tc_index = next(i for i, e in enumerate(events) if "tool_call" in e)
        prefix_tokens = [e for e in events[:tc_index] if "token" in e]
        assert any("look that up" in t["token"] for t in prefix_tokens)


# ---------------------------------------------------------------------------
# Test 2: Artifact returned via tool, visible in done event
# ---------------------------------------------------------------------------


class TestArtifactPropagation:
    @pytest.mark.asyncio
    async def test_generate_document_artifact_in_done(self):
        """When a tool returns an artifact, it appears in the done event."""
        registry = ToolRegistry()

        async def doc_handler(args, ctx):
            return ToolResult(
                content="Generated your quarterly report.",
                artifacts=[
                    Artifact(
                        type="file_download",
                        data={"url": "/files/report.pdf", "filename": "report.pdf"},
                    )
                ],
            )

        registry.register(
            ToolDef(
                name="generate_document",
                description="Generate a document",
                parameters={
                    "type": "object",
                    "properties": {
                        "format": {"type": "string"},
                        "content": {"type": "string"},
                    },
                },
                handler=doc_handler,
            )
        )

        ai_service = MockAIService(
            responses=[
                '<tool_call>\n{"tool": "generate_document", "args": {"format": "pdf", "content": "Q4 Report"}}\n</tool_call>',
                "Here is your quarterly report. You can download it using the link above.",
            ]
        )

        executor = ToolExecutor(registry)
        ctx = _make_context(ai_service=ai_service)
        events = await _collect_events(executor, ctx)

        done_events = [e for e in events if e.get("done")]
        assert len(done_events) == 1
        artifacts = done_events[0]["artifacts"]
        assert len(artifacts) == 1
        assert artifacts[0]["type"] == "file_download"
        assert artifacts[0]["url"] == "/files/report.pdf"
        assert artifacts[0]["filename"] == "report.pdf"

    @pytest.mark.asyncio
    async def test_multiple_artifacts_accumulated(self):
        """Artifacts from multiple tool calls are all accumulated in done."""
        registry = ToolRegistry()
        call_count = 0

        async def doc_handler(args, ctx):
            nonlocal call_count
            call_count += 1
            return ToolResult(
                content=f"Generated document {call_count}",
                artifacts=[
                    Artifact(
                        type="file_download",
                        data={"url": f"/files/doc{call_count}.pdf", "filename": f"doc{call_count}.pdf"},
                    )
                ],
            )

        registry.register(
            ToolDef(name="generate_document", description="Generate", parameters={}, handler=doc_handler)
        )

        ai_service = MockAIService(
            responses=[
                '<tool_call>\n{"tool": "generate_document", "args": {}}\n</tool_call>',
                '<tool_call>\n{"tool": "generate_document", "args": {}}\n</tool_call>',
                "Both documents are ready.",
            ]
        )

        executor = ToolExecutor(registry)
        ctx = _make_context(ai_service=ai_service)
        events = await _collect_events(executor, ctx)

        done_events = [e for e in events if e.get("done")]
        assert len(done_events) == 1
        assert len(done_events[0]["artifacts"]) == 2


# ---------------------------------------------------------------------------
# Test 3: Tool exclusion via generate_tools_prompt
# ---------------------------------------------------------------------------


class TestToolExclusion:
    def test_exclude_removes_tool_from_prompt(self):
        """generate_tools_prompt(exclude=[...]) omits the specified tool."""
        registry = ToolRegistry()
        registry.register(
            ToolDef(
                name="web_search",
                description="Search the web",
                parameters={"type": "object", "properties": {"query": {"type": "string"}}},
                handler=lambda a, c: None,
            )
        )
        registry.register(
            ToolDef(
                name="project_search",
                description="Search projects",
                parameters={"type": "object", "properties": {"query": {"type": "string"}}},
                handler=lambda a, c: None,
            )
        )
        registry.register(
            ToolDef(
                name="system_status",
                description="Check system health",
                parameters={},
                handler=lambda a, c: None,
            )
        )

        prompt = registry.generate_tools_prompt(exclude=["web_search"])

        assert "web_search" not in prompt
        assert "Search the web" not in prompt
        assert "project_search" in prompt
        assert "system_status" in prompt

    def test_exclude_multiple_tools(self):
        """Multiple tools can be excluded at once."""
        registry = ToolRegistry()
        for name in ["alpha_tool", "beta_tool", "gamma_tool"]:
            registry.register(
                ToolDef(name=name, description=f"Desc {name}", parameters={}, handler=lambda a, c: None)
            )

        prompt = registry.generate_tools_prompt(exclude=["alpha_tool", "gamma_tool"])
        assert "alpha_tool" not in prompt
        assert "gamma_tool" not in prompt
        assert "beta_tool" in prompt

    def test_exclude_all_returns_empty(self):
        """Excluding all tools results in an empty prompt."""
        registry = ToolRegistry()
        registry.register(
            ToolDef(name="only_tool", description="The only one", parameters={}, handler=lambda a, c: None)
        )

        prompt = registry.generate_tools_prompt(exclude=["only_tool"])
        assert prompt == ""


# ---------------------------------------------------------------------------
# Test 4: Tool call iteration cap (default 5)
# ---------------------------------------------------------------------------


class TestIterationCap:
    @pytest.mark.asyncio
    async def test_capped_at_default_five(self):
        """LLM that always emits tool calls is capped at 5 iterations."""
        registry = ToolRegistry()

        handler_call_count = 0

        async def echo_handler(args, ctx):
            nonlocal handler_call_count
            handler_call_count += 1
            return ToolResult(content=f"echo {handler_call_count}")

        registry.register(
            ToolDef(name="echo", description="Echo tool", parameters={}, handler=echo_handler)
        )

        # The mock always returns a tool call, which should trigger the cap
        ai_service = MockAIService(
            responses=[
                '<tool_call>\n{"tool": "echo", "args": {}}\n</tool_call>',
            ]
        )

        executor = ToolExecutor(registry)  # default max_tool_calls = 5
        ctx = _make_context(ai_service=ai_service)
        events = await _collect_events(executor, ctx)

        tool_calls = [e for e in events if "tool_call" in e]
        tool_results = [e for e in events if "tool_result" in e]

        assert len(tool_calls) == 5
        assert len(tool_results) == 5
        assert handler_call_count == 5

        # Should have a cap-reached message
        tokens = [e["token"] for e in events if "token" in e]
        cap_text = "".join(tokens)
        assert "maximum tool calls" in cap_text.lower()

        # Done event present
        done_events = [e for e in events if e.get("done")]
        assert len(done_events) == 1

    @pytest.mark.asyncio
    async def test_custom_cap_respected(self):
        """Custom max_tool_calls value is respected."""
        registry = ToolRegistry()

        async def handler(args, ctx):
            return ToolResult(content="ok")

        registry.register(
            ToolDef(name="t", description="", parameters={}, handler=handler)
        )

        ai_service = MockAIService(
            responses=['<tool_call>\n{"tool": "t", "args": {}}\n</tool_call>']
        )

        executor = ToolExecutor(registry, max_tool_calls=3)
        ctx = _make_context(ai_service=ai_service)
        events = await _collect_events(executor, ctx)

        tool_calls = [e for e in events if "tool_call" in e]
        assert len(tool_calls) == 3

    @pytest.mark.asyncio
    async def test_stops_before_cap_when_llm_finishes(self):
        """If LLM stops calling tools before the cap, execution ends normally."""
        registry = ToolRegistry()

        async def handler(args, ctx):
            return ToolResult(content="done")

        registry.register(
            ToolDef(name="t", description="", parameters={}, handler=handler)
        )

        ai_service = MockAIService(
            responses=[
                '<tool_call>\n{"tool": "t", "args": {}}\n</tool_call>',
                '<tool_call>\n{"tool": "t", "args": {}}\n</tool_call>',
                "All done, no more tools needed.",
            ]
        )

        executor = ToolExecutor(registry, max_tool_calls=5)
        ctx = _make_context(ai_service=ai_service)
        events = await _collect_events(executor, ctx)

        tool_calls = [e for e in events if "tool_call" in e]
        assert len(tool_calls) == 2  # stopped at 2, well before cap of 5

        # No cap message
        tokens = "".join(e["token"] for e in events if "token" in e)
        assert "maximum tool calls" not in tokens.lower()


# ---------------------------------------------------------------------------
# Test 5: SSE event ordering
# ---------------------------------------------------------------------------


class TestSSEEventOrdering:
    @pytest.mark.asyncio
    async def test_single_tool_event_order(self):
        """Events follow: token(prefix) -> tool_call -> tool_result -> token(final) -> done."""
        registry = ToolRegistry()

        async def handler(args, ctx):
            return ToolResult(content="search result")

        registry.register(
            ToolDef(
                name="search",
                description="Search",
                parameters={"type": "object", "properties": {"q": {"type": "string"}}},
                handler=handler,
            )
        )

        ai_service = MockAIService(
            responses=[
                'Searching now.\n\n<tool_call>\n{"tool": "search", "args": {"q": "test"}}\n</tool_call>',
                "Here are the results.",
            ]
        )

        executor = ToolExecutor(registry)
        ctx = _make_context(ai_service=ai_service)
        events = await _collect_events(executor, ctx)

        # Classify events by type
        event_types = []
        for e in events:
            if "tool_call" in e:
                event_types.append("tool_call")
            elif "tool_result" in e:
                event_types.append("tool_result")
            elif "token" in e:
                event_types.append("token")
            elif e.get("done"):
                event_types.append("done")

        # Find indices
        tc_idx = event_types.index("tool_call")
        tr_idx = event_types.index("tool_result")

        # Prefix token before tool_call
        assert event_types[0] == "token"
        assert tc_idx > 0

        # tool_call immediately before tool_result
        assert tr_idx == tc_idx + 1

        # Final token(s) after tool_result
        final_tokens = [i for i, t in enumerate(event_types) if t == "token" and i > tr_idx]
        assert len(final_tokens) > 0

        # done is always last
        assert event_types[-1] == "done"

    @pytest.mark.asyncio
    async def test_multi_tool_event_order(self):
        """With two tool calls, events follow: (tool_call -> tool_result) x2 -> token -> done."""
        registry = ToolRegistry()

        async def handler(args, ctx):
            return ToolResult(content="result")

        registry.register(
            ToolDef(name="tool_a", description="A", parameters={}, handler=handler)
        )

        ai_service = MockAIService(
            responses=[
                '<tool_call>\n{"tool": "tool_a", "args": {}}\n</tool_call>',
                '<tool_call>\n{"tool": "tool_a", "args": {}}\n</tool_call>',
                "Final answer after two tool calls.",
            ]
        )

        executor = ToolExecutor(registry)
        ctx = _make_context(ai_service=ai_service)
        events = await _collect_events(executor, ctx)

        event_types = []
        for e in events:
            if "tool_call" in e:
                event_types.append("tool_call")
            elif "tool_result" in e:
                event_types.append("tool_result")
            elif "token" in e:
                event_types.append("token")
            elif e.get("done"):
                event_types.append("done")

        # Each tool_call should be immediately followed by its tool_result
        tc_indices = [i for i, t in enumerate(event_types) if t == "tool_call"]
        tr_indices = [i for i, t in enumerate(event_types) if t == "tool_result"]
        assert len(tc_indices) == 2
        assert len(tr_indices) == 2
        for tc_i, tr_i in zip(tc_indices, tr_indices):
            assert tr_i == tc_i + 1

        # done is last
        assert event_types[-1] == "done"

    @pytest.mark.asyncio
    async def test_no_tool_call_order(self):
        """Without tool calls: token(s) -> done."""
        registry = ToolRegistry()
        ai_service = MockAIService(responses=["Just a plain text response."])

        executor = ToolExecutor(registry)
        ctx = _make_context(ai_service=ai_service)
        events = await _collect_events(executor, ctx)

        event_types = []
        for e in events:
            if "token" in e:
                event_types.append("token")
            elif e.get("done"):
                event_types.append("done")

        assert event_types[-1] == "done"
        assert all(t == "token" for t in event_types[:-1])


# ---------------------------------------------------------------------------
# Test 6: Error handling — tool raises exception
# ---------------------------------------------------------------------------


class TestErrorHandling:
    @pytest.mark.asyncio
    async def test_exception_produces_error_result_and_stops(self):
        """A tool that raises an exception produces an error tool_result and
        stops the loop (no further LLM calls)."""
        registry = ToolRegistry()

        async def failing_handler(args, ctx):
            raise RuntimeError("Database connection lost")

        registry.register(
            ToolDef(
                name="broken_tool",
                description="Always fails",
                parameters={},
                handler=failing_handler,
            )
        )

        ai_service = MockAIService(
            responses=[
                '<tool_call>\n{"tool": "broken_tool", "args": {}}\n</tool_call>',
                # This second response should never be reached
                "This should not appear.",
            ]
        )

        executor = ToolExecutor(registry)
        ctx = _make_context(ai_service=ai_service)
        events = await _collect_events(executor, ctx)

        # tool_call was emitted
        tool_calls = [e for e in events if "tool_call" in e]
        assert len(tool_calls) == 1

        # tool_result contains the error
        tool_results = [e for e in events if "tool_result" in e]
        assert len(tool_results) == 1
        assert "error" in tool_results[0]["tool_result"]["summary"].lower()
        assert "Database connection lost" in tool_results[0]["tool_result"]["summary"]

        # done event present
        done_events = [e for e in events if e.get("done")]
        assert len(done_events) == 1

        # No final text tokens (loop stopped after error)
        tokens = [e for e in events if "token" in e]
        assert len(tokens) == 0

        # Only 1 call to the AI service (no second call after error)
        assert ai_service._call_index == 1

    @pytest.mark.asyncio
    async def test_unknown_tool_error_stops_loop(self):
        """Calling an unregistered tool produces an error and stops."""
        registry = ToolRegistry()
        # Register nothing -- the tool call will reference a non-existent tool

        ai_service = MockAIService(
            responses=[
                '<tool_call>\n{"tool": "nonexistent_tool", "args": {}}\n</tool_call>',
                "This should not appear.",
            ]
        )

        executor = ToolExecutor(registry)
        ctx = _make_context(ai_service=ai_service)
        events = await _collect_events(executor, ctx)

        tool_results = [e for e in events if "tool_result" in e]
        assert len(tool_results) == 1
        assert "not available" in tool_results[0]["tool_result"]["summary"].lower()

        # Loop stopped -- only 1 AI call
        assert ai_service._call_index == 1

    @pytest.mark.asyncio
    async def test_error_preserves_prior_artifacts(self):
        """If a tool error occurs after a successful tool call, artifacts from
        the successful call are still present in done."""
        registry = ToolRegistry()

        call_count = 0

        async def mixed_handler(args, ctx):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return ToolResult(
                    content="Success",
                    artifacts=[Artifact(type="file_download", data={"url": "/f.pdf", "filename": "f.pdf"})],
                )
            raise ValueError("Second call fails")

        registry.register(
            ToolDef(name="mixed", description="", parameters={}, handler=mixed_handler)
        )

        ai_service = MockAIService(
            responses=[
                '<tool_call>\n{"tool": "mixed", "args": {}}\n</tool_call>',
                '<tool_call>\n{"tool": "mixed", "args": {}}\n</tool_call>',
                "This should not appear.",
            ]
        )

        executor = ToolExecutor(registry)
        ctx = _make_context(ai_service=ai_service)
        events = await _collect_events(executor, ctx)

        done_events = [e for e in events if e.get("done")]
        assert len(done_events) == 1
        # The artifact from the first successful call is preserved
        assert len(done_events[0]["artifacts"]) == 1
        assert done_events[0]["artifacts"][0]["type"] == "file_download"


# ---------------------------------------------------------------------------
# Test: Messages fed back to LLM correctly
# ---------------------------------------------------------------------------


class TestMessageFeedback:
    @pytest.mark.asyncio
    async def test_tool_result_injected_into_messages(self):
        """After a tool call, the tool result is injected into messages for
        the next LLM call as a <tool_result> block."""
        registry = ToolRegistry()

        async def handler(args, ctx):
            return ToolResult(content="The answer is 42.")

        registry.register(
            ToolDef(name="answer", description="", parameters={}, handler=handler)
        )

        captured_messages = []

        class SpyAIService:
            def __init__(self):
                self._call_index = 0

            async def chat_stream(self, messages, options):
                captured_messages.append(list(messages))
                self._call_index += 1
                if self._call_index == 1:
                    yield MockChunk(content='<tool_call>\n{"tool": "answer", "args": {}}\n</tool_call>')
                else:
                    yield MockChunk(content="Got it.")
                yield MockChunk(content=None, finish_reason="stop")

        executor = ToolExecutor(registry)
        ctx = _make_context(ai_service=SpyAIService())
        await _collect_events(executor, ctx)

        # Second call should include the tool result
        assert len(captured_messages) == 2
        second_call_msgs = captured_messages[1]
        # Last message should be the tool_result injection (ChatMessage object)
        last_msg = second_call_msgs[-1]
        assert last_msg.role == "user"
        assert "<tool_result>" in last_msg.content
        assert "The answer is 42." in last_msg.content

        # Second-to-last should be assistant's original response
        assistant_msg = second_call_msgs[-2]
        assert assistant_msg.role == "assistant"
        assert "tool_call" in assistant_msg.content

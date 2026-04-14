"""Tool execution loop for Max AI assistant.

Handles the multi-turn cycle: stream LLM output -> detect <tool_call> ->
execute tool -> feed result back -> continue streaming. Caps at a maximum
number of tool calls per turn to prevent runaway loops.
"""

from __future__ import annotations

import logging
from typing import Any, AsyncGenerator

from core.interfaces import ChatMessage
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
        messages: list,
        options: Any,
        ctx: ToolContext,
    ) -> AsyncGenerator[dict, None]:
        """Run the multi-turn tool execution loop.

        Streams SSE event dicts to the caller.
        """
        artifacts: list[dict] = []
        tool_call_count = 0
        current_messages = list(messages)

        while True:
            # Stream tokens in real-time while accumulating for tool call detection
            full_response = ""
            streamed_up_to = 0

            async for chunk in ctx.ai_service.chat_stream(current_messages, options):
                if chunk.content:
                    full_response += chunk.content

                    # Stream tokens immediately unless we detect a tool call starting
                    # Tool calls start with { or <tool_call> — hold back once detected
                    pending = full_response[streamed_up_to:]
                    if '{' not in pending and '<tool_call>' not in pending:
                        yield {"token": chunk.content}
                        streamed_up_to = len(full_response)

            # Check for a tool call in the full response
            parsed = parse_tool_call(full_response)

            if parsed is None:
                # No tool call — stream any remaining unstreamed content
                remaining = full_response[streamed_up_to:]
                if remaining:
                    yield {"token": remaining}
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
            tool_result, is_error = await self._run_tool(parsed, ctx)

            # Collect artifacts
            for artifact in tool_result.artifacts:
                artifacts.append({"type": artifact.type, **artifact.data})

            # Emit tool_result event for the frontend
            summary = tool_result.content[:200] + ("..." if len(tool_result.content) > 200 else "")
            yield {"tool_result": {"name": parsed.tool_name, "summary": summary}}

            # If the tool errored, stop the loop — no point re-asking the LLM
            if is_error:
                yield {"done": True, "artifacts": artifacts}
                return

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

    async def _run_tool(
        self, parsed: ToolCallParsed, ctx: ToolContext
    ) -> tuple[ToolResult, bool]:
        """Execute a single tool call, handling errors gracefully.

        Returns (result, is_error) tuple.
        """
        tool = self._registry.get(parsed.tool_name)
        if tool is None:
            return ToolResult(
                content=f"Error: Tool '{parsed.tool_name}' is not available.",
            ), True

        try:
            result = await tool.handler(parsed.args, ctx)
            return result, False
        except Exception as e:
            logger.exception("Tool '%s' failed", parsed.tool_name)
            return ToolResult(
                content=f"Error running {parsed.tool_name}: {e}",
            ), True

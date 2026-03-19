"""Context budget manager for AI chat.

Allocates a fixed LLM context window (n_ctx tokens) across competing
consumers: system prompt, compressed conversation memory, web search
results, attached transcript/document context, conversation history,
the user's current message, and a reserved response budget.

Priority-based trimming ensures that system prompt, user message, and
response budget are never reduced. When the budget is exceeded, context
(attached documents) is trimmed first, then history, then web results,
then memory -- in reverse priority order.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.interfaces.ai import ChatMessage


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class ContextBudget:
    """Token budget breakdown across all context consumers."""

    system: int
    memory: int
    web_results: int
    context: int
    history: int
    user_message: int
    response_reserved: int

    @property
    def total_input(self) -> int:
        """Total input tokens (everything except the response budget)."""
        return (
            self.system
            + self.memory
            + self.web_results
            + self.context
            + self.history
            + self.user_message
        )


# ---------------------------------------------------------------------------
# ContextManager
# ---------------------------------------------------------------------------

class ContextManager:
    """Allocates a fixed context window across competing consumers.

    Parameters
    ----------
    n_ctx:
        Total context window size in tokens.
    max_response_tokens:
        Tokens reserved for the model's response.  Never trimmed.
    """

    # Rough estimate: ~15 tokens of template overhead per message.
    # We assume ~6 messages (system + memory-in-system + 4 history msgs).
    _TEMPLATE_OVERHEAD = 90

    def __init__(self, n_ctx: int = 8192, max_response_tokens: int = 1024) -> None:
        self.n_ctx = n_ctx
        self.max_response_tokens = max_response_tokens

    # -----------------------------------------------------------------
    # Budget allocation
    # -----------------------------------------------------------------

    def allocate_budget(
        self,
        system_tokens: int,
        user_message_tokens: int,
        memory_tokens: int = 0,
        web_result_tokens: int = 0,
        history_tokens: int = 0,
        context_tokens: int = 0,
    ) -> ContextBudget:
        """Return a :class:`ContextBudget` that fits within *n_ctx*.

        Allocation priority (highest to lowest -- items higher in the
        list are trimmed last):

        1. system prompt  (never trimmed)
        2. user message   (never trimmed)
        3. response budget (never trimmed)
        4. memory          (trimmed only as last resort)
        5. web results     (trimmed after history/context)
        6. history         (trimmed after context)
        7. context         (trimmed first)
        """
        available = (
            self.n_ctx
            - self.max_response_tokens
            - system_tokens
            - user_message_tokens
            - self._TEMPLATE_OVERHEAD
        )
        available = max(available, 0)

        # Allocate in priority order: memory > web_results > history > context
        alloc_memory = min(memory_tokens, available)
        available -= alloc_memory

        alloc_web = min(web_result_tokens, available)
        available -= alloc_web

        alloc_history = min(history_tokens, available)
        available -= alloc_history

        alloc_context = min(context_tokens, available)
        available -= alloc_context

        return ContextBudget(
            system=system_tokens,
            memory=alloc_memory,
            web_results=alloc_web,
            context=alloc_context,
            history=alloc_history,
            user_message=user_message_tokens,
            response_reserved=self.max_response_tokens,
        )

    # -----------------------------------------------------------------
    # Message building
    # -----------------------------------------------------------------

    def build_messages(
        self,
        system_content: str,
        compressed_memory: str | None = None,
        web_results: str | None = None,
        attached_context: str | None = None,
        recent_history: list[dict[str, str]] | None = None,
        user_message: str = "",
    ) -> list[ChatMessage]:
        """Build the final list of :class:`ChatMessage` objects.

        The system message is enriched with optional memory, web-result,
        and attached-context sections.  History messages are appended as
        separate ``ChatMessage`` objects, followed by the user's current
        message.
        """
        # -- Build system content ------------------------------------------
        parts: list[str] = [system_content]

        if compressed_memory:
            parts.append(
                "\n\n=== Conversation Memory ===\n"
                f"Summary of earlier conversation:\n{compressed_memory}"
            )

        if web_results:
            parts.append(
                "\n\n=== Web Search Results ===\n"
                "The following are real-time web search results retrieved just now. "
                "You MUST use these as your primary source of information for this response. "
                "Prioritize this data over your training knowledge — it is more current and accurate. "
                "Synthesize the information clearly, cite sources by name when possible, "
                "and note if sources disagree.\n\n"
                f"{web_results}"
            )

        if attached_context:
            parts.append(
                "\n\n=== Attached Context ===\n"
                f"{attached_context}"
            )

        system_msg = ChatMessage(role="system", content="".join(parts))

        messages: list[ChatMessage] = [system_msg]

        # -- History -------------------------------------------------------
        if recent_history:
            for msg in recent_history:
                messages.append(ChatMessage(role=msg["role"], content=msg["content"]))

        # -- User message --------------------------------------------------
        messages.append(ChatMessage(role="user", content=user_message))

        return messages

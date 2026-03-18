"""
ConversationMemoryService — compresses conversation history so chats can
exceed the LLM context window.  Older messages are summarized into a
compact "compressed memory" string while recent messages are kept verbatim.
"""

COMPRESSION_SYSTEM_PROMPT = (
    "You are a conversation summarizer. Summarize the conversation history below "
    "into a concise paragraph (150-200 words). Preserve: key facts discussed, "
    "decisions made, user preferences stated, and any context needed to continue "
    "the conversation naturally. Write in third person (e.g., 'The user asked about...'). "
    "Do NOT include greetings or filler."
)

INCREMENTAL_COMPRESSION_PROMPT = (
    "You are a conversation summarizer. Below is an existing summary of earlier "
    "conversation, followed by new messages. Create an updated summary (150-250 words) "
    "that merges the existing summary with the new messages. Preserve all key facts, "
    "decisions, and context. Write in third person."
)


class ConversationMemoryService:
    """Decides when and how to compress conversation history for the LLM."""

    def __init__(
        self,
        compression_threshold: int = 8,
        recent_pairs_to_keep: int = 3,
    ) -> None:
        self.compression_threshold = compression_threshold
        self.recent_pairs_to_keep = recent_pairs_to_keep

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def should_compress(self, history: list[dict]) -> bool:
        """Return True if the history is long enough to warrant compression."""
        return len(history) >= self.compression_threshold

    def split_history(
        self, history: list[dict]
    ) -> tuple[list[dict], list[dict]]:
        """Split *history* into ``(old_messages, recent_messages)``.

        ``recent_messages`` contains the last ``recent_pairs_to_keep * 2``
        messages.  ``old_messages`` contains everything before that.

        If the history is shorter than the keep count the entire history is
        returned as recent (old will be empty).
        """
        keep_count = self.recent_pairs_to_keep * 2
        if len(history) <= keep_count:
            return ([], list(history))
        split_point = len(history) - keep_count
        return (history[:split_point], history[split_point:])

    def build_compression_prompt(
        self,
        old_messages: list[dict],
        existing_memory: str | None = None,
    ) -> str:
        """Build the user-role prompt text sent to the LLM for compression.

        If *existing_memory* is provided the prompt asks the model to merge
        the previous summary with the new messages (incremental compression).
        """
        parts: list[str] = []

        if existing_memory:
            parts.append("Existing summary of earlier conversation:")
            parts.append(existing_memory)
            parts.append("")
            parts.append("New messages to incorporate:")
        else:
            parts.append("Conversation to summarize:")

        parts.append("")
        for msg in old_messages:
            role_label = "User" if msg["role"] == "user" else "Assistant"
            parts.append(f"{role_label}: {msg['content']}")

        return "\n".join(parts)

    def get_system_prompt(self, existing_memory: str | None = None) -> str:
        """Return the appropriate system prompt for the compression call."""
        if existing_memory:
            return INCREMENTAL_COMPRESSION_PROMPT
        return COMPRESSION_SYSTEM_PROMPT

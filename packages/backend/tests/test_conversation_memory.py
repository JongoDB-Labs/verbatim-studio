"""Tests for ConversationMemoryService."""

from services.conversation_memory import (
    COMPRESSION_SYSTEM_PROMPT,
    INCREMENTAL_COMPRESSION_PROMPT,
    ConversationMemoryService,
)


class TestConversationMemory:
    def test_should_compress_returns_false_below_threshold(self):
        service = ConversationMemoryService(compression_threshold=10)
        history = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi!"},
        ]
        assert service.should_compress(history) is False

    def test_should_compress_returns_true_above_threshold(self):
        service = ConversationMemoryService(compression_threshold=4)
        history = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi!"},
            {"role": "user", "content": "How are you?"},
            {"role": "assistant", "content": "Good!"},
            {"role": "user", "content": "What's new?"},
            {"role": "assistant", "content": "Not much!"},
        ]
        assert service.should_compress(history) is True

    def test_should_compress_returns_true_at_exact_threshold(self):
        service = ConversationMemoryService(compression_threshold=4)
        history = [
            {"role": "user", "content": "a"},
            {"role": "assistant", "content": "b"},
            {"role": "user", "content": "c"},
            {"role": "assistant", "content": "d"},
        ]
        assert service.should_compress(history) is True

    def test_split_history_keeps_recent_messages(self):
        service = ConversationMemoryService(
            compression_threshold=4, recent_pairs_to_keep=2
        )
        history = [
            {"role": "user", "content": "msg1"},
            {"role": "assistant", "content": "resp1"},
            {"role": "user", "content": "msg2"},
            {"role": "assistant", "content": "resp2"},
            {"role": "user", "content": "msg3"},
            {"role": "assistant", "content": "resp3"},
        ]
        old, recent = service.split_history(history)
        assert len(old) == 2  # msg1, resp1
        assert len(recent) == 4  # msg2, resp2, msg3, resp3
        assert recent[0]["content"] == "msg2"

    def test_split_history_short_history_returns_empty_old(self):
        service = ConversationMemoryService(
            compression_threshold=8, recent_pairs_to_keep=3
        )
        history = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi!"},
        ]
        old, recent = service.split_history(history)
        assert old == []
        assert len(recent) == 2

    def test_split_history_exact_keep_count(self):
        service = ConversationMemoryService(recent_pairs_to_keep=2)
        history = [
            {"role": "user", "content": "a"},
            {"role": "assistant", "content": "b"},
            {"role": "user", "content": "c"},
            {"role": "assistant", "content": "d"},
        ]
        old, recent = service.split_history(history)
        assert old == []
        assert len(recent) == 4

    def test_build_compression_prompt(self):
        service = ConversationMemoryService()
        old_messages = [
            {"role": "user", "content": "What is Verbatim?"},
            {
                "role": "assistant",
                "content": "Verbatim Studio is a transcription app.",
            },
        ]
        prompt = service.build_compression_prompt(
            old_messages, existing_memory=None
        )
        assert "What is Verbatim?" in prompt
        assert "transcription app" in prompt
        assert "Conversation to summarize:" in prompt

    def test_build_compression_prompt_with_existing_memory(self):
        service = ConversationMemoryService()
        old_messages = [
            {"role": "user", "content": "Can it do OCR?"},
            {"role": "assistant", "content": "Yes, using Qwen VL."},
        ]
        existing_memory = (
            "User asked about Verbatim Studio, a transcription app."
        )
        prompt = service.build_compression_prompt(
            old_messages, existing_memory
        )
        assert "User asked about Verbatim Studio" in prompt
        assert "Can it do OCR?" in prompt
        assert "Existing summary of earlier conversation:" in prompt

    def test_get_system_prompt_without_existing_memory(self):
        service = ConversationMemoryService()
        prompt = service.get_system_prompt(existing_memory=None)
        assert prompt == COMPRESSION_SYSTEM_PROMPT

    def test_get_system_prompt_with_existing_memory(self):
        service = ConversationMemoryService()
        prompt = service.get_system_prompt(
            existing_memory="Some earlier summary."
        )
        assert prompt == INCREMENTAL_COMPRESSION_PROMPT

    def test_default_config(self):
        service = ConversationMemoryService()
        assert service.compression_threshold == 8
        assert service.recent_pairs_to_keep == 3

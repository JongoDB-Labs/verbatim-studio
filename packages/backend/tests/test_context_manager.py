"""Tests for the ContextManager service."""

import pytest

from services.context_manager import ContextBudget, ContextManager


class TestContextManager:
    def test_allocate_budget_basic(self):
        mgr = ContextManager(n_ctx=8192, max_response_tokens=1024)
        budget = mgr.allocate_budget(
            system_tokens=300,
            user_message_tokens=50,
            memory_tokens=0,
            web_result_tokens=0,
            history_tokens=500,
            context_tokens=2000,
        )
        assert budget.total_input <= 8192 - 1024
        assert budget.system == 300
        assert budget.user_message == 50

    def test_allocate_budget_exceeds_trims_context_first(self):
        mgr = ContextManager(n_ctx=4096, max_response_tokens=1024)
        budget = mgr.allocate_budget(
            system_tokens=300,
            user_message_tokens=50,
            memory_tokens=500,
            web_result_tokens=0,
            history_tokens=1000,
            context_tokens=5000,
        )
        assert budget.context < 5000  # Context was trimmed
        assert budget.history == 1000  # History preserved
        assert budget.memory == 500  # Memory preserved

    def test_allocate_budget_exceeds_trims_history_after_context(self):
        mgr = ContextManager(n_ctx=2048, max_response_tokens=512)
        budget = mgr.allocate_budget(
            system_tokens=300,
            user_message_tokens=50,
            memory_tokens=500,
            web_result_tokens=0,
            history_tokens=2000,
            context_tokens=0,
        )
        assert budget.history < 2000  # History was trimmed
        assert budget.memory == 500  # Memory preserved

    def test_build_messages_with_memory(self):
        mgr = ContextManager(n_ctx=8192, max_response_tokens=1024)
        messages = mgr.build_messages(
            system_content="You are Max.",
            compressed_memory="User discussed transcription features.",
            web_results=None,
            attached_context="Transcript A: Hello world.",
            recent_history=[{"role": "user", "content": "Summarize this"}],
            user_message="What did we discuss earlier?",
        )
        assert "User discussed transcription features" in messages[0].content
        assert "Conversation Memory" in messages[0].content
        assert messages[-1].content == "What did we discuss earlier?"
        assert messages[-1].role == "user"

    def test_build_messages_without_memory(self):
        mgr = ContextManager(n_ctx=8192, max_response_tokens=1024)
        messages = mgr.build_messages(
            system_content="You are Max.",
            compressed_memory=None,
            web_results=None,
            attached_context=None,
            recent_history=[],
            user_message="Hello",
        )
        assert len(messages) == 2  # system + user
        assert "Conversation Memory" not in messages[0].content

    def test_build_messages_with_web_results(self):
        mgr = ContextManager(n_ctx=8192, max_response_tokens=1024)
        messages = mgr.build_messages(
            system_content="You are Max.",
            compressed_memory=None,
            web_results="[Result 1](https://example.com)\nSome content",
            attached_context=None,
            recent_history=[],
            user_message="Search for something",
        )
        assert "Web Search Results" in messages[0].content
        assert "example.com" in messages[0].content

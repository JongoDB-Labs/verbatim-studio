"""Tests for LlamaCppAIService state management and singleton caching.

Validates that:
- KV cache + recurrent state are cleared before each completion call
- Singleton cache invalidates on model_path, n_ctx, or n_gpu_layers change
- Error recovery retries after llama_decode failure
"""

import asyncio
from unittest.mock import MagicMock, patch, AsyncMock
import pytest

from adapters.ai.llama_cpp import (
    LlamaCppAIService,
    get_llama_service,
    cleanup_llama_service,
)
from core.interfaces import ChatMessage, ChatOptions


# Helper: mock asyncio.to_thread to just call the function synchronously
async def _sync_to_thread(fn, *args, **kwargs):
    return fn(*args, **kwargs)


# ── State Reset ─────────────────────────────────────────────────────


class TestStateReset:
    """Ensure _reset_state clears KV cache and token count."""

    def test_reset_state_clears_kv_cache(self):
        service = LlamaCppAIService(model_path="/fake/model.gguf")
        mock_llm = MagicMock()
        mock_ctx = MagicMock()
        mock_llm._ctx = mock_ctx
        mock_llm.n_tokens = 42
        service._llm = mock_llm

        service._reset_state()

        mock_ctx.kv_cache_clear.assert_called_once()
        assert mock_llm.n_tokens == 0

    def test_reset_state_noop_when_unloaded(self):
        service = LlamaCppAIService(model_path="/fake/model.gguf")
        # Should not raise when _llm is None
        service._reset_state()


# ── Chat calls _reset_state ─────────────────────────────────────────


class TestChatResetsState:
    """Verify that chat() and chat_stream() clear state before each call."""

    @pytest.mark.asyncio
    async def test_chat_resets_before_call(self):
        service = LlamaCppAIService(model_path="/fake/model.gguf")
        mock_llm = MagicMock()
        mock_ctx = MagicMock()
        mock_llm._ctx = mock_ctx
        mock_llm.n_tokens = 100
        mock_llm.create_chat_completion.return_value = {
            "choices": [{"message": {"content": "Hello"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }
        service._llm = mock_llm

        messages = [ChatMessage(role="user", content="Hi")]

        with patch("asyncio.to_thread", side_effect=_sync_to_thread):
            result = await service.chat(messages)

        # kv_cache_clear should be called (state reset before completion)
        mock_ctx.kv_cache_clear.assert_called_once()
        assert result.content == "Hello"

    @pytest.mark.asyncio
    async def test_chat_retries_on_decode_failure(self):
        service = LlamaCppAIService(model_path="/fake/model.gguf")
        mock_llm = MagicMock()
        mock_ctx = MagicMock()
        mock_llm._ctx = mock_ctx
        service._llm = mock_llm

        success_result = {
            "choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}],
            "usage": {},
        }

        call_count = 0

        async def counting_to_thread(fn, *args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("llama_decode returned -1")
            return fn(*args, **kwargs)

        mock_llm.create_chat_completion.return_value = success_result
        messages = [ChatMessage(role="user", content="Hi")]

        with patch("asyncio.to_thread", side_effect=counting_to_thread):
            result = await service.chat(messages)

        assert result.content == "OK"
        # Should have been called twice for kv_cache_clear (initial + retry)
        assert mock_ctx.kv_cache_clear.call_count == 2

    @pytest.mark.asyncio
    async def test_chat_raises_non_decode_errors(self):
        service = LlamaCppAIService(model_path="/fake/model.gguf")
        mock_llm = MagicMock()
        mock_ctx = MagicMock()
        mock_llm._ctx = mock_ctx
        service._llm = mock_llm

        async def failing_to_thread(fn, *args, **kwargs):
            raise RuntimeError("some other error")

        messages = [ChatMessage(role="user", content="Hi")]

        with patch("asyncio.to_thread", side_effect=failing_to_thread):
            with pytest.raises(RuntimeError, match="some other error"):
                await service.chat(messages)


# ── Singleton Cache ─────────────────────────────────────────────────


class TestSingletonCache:
    """Test that singleton cache invalidates on config changes."""

    def setup_method(self):
        cleanup_llama_service()

    def teardown_method(self):
        cleanup_llama_service()

    def test_same_config_returns_same_instance(self):
        with patch("adapters.ai.llama_cpp.LlamaCppAIService") as MockService:
            MockService.return_value = MagicMock(_llm=None)

            s1 = get_llama_service(model_path="/a.gguf", n_ctx=4096, n_gpu_layers=0)
            s2 = get_llama_service(model_path="/a.gguf", n_ctx=4096, n_gpu_layers=0)

            assert s1 is s2
            assert MockService.call_count == 1

    def test_different_model_path_invalidates(self):
        with patch("adapters.ai.llama_cpp.LlamaCppAIService") as MockService:
            instances = [MagicMock(_llm=None), MagicMock(_llm=None)]
            MockService.side_effect = instances

            s1 = get_llama_service(model_path="/a.gguf", n_ctx=4096)
            s2 = get_llama_service(model_path="/b.gguf", n_ctx=4096)

            assert s1 is not s2
            assert MockService.call_count == 2

    def test_different_n_ctx_invalidates(self):
        with patch("adapters.ai.llama_cpp.LlamaCppAIService") as MockService:
            instances = [MagicMock(_llm=None), MagicMock(_llm=None)]
            MockService.side_effect = instances

            s1 = get_llama_service(model_path="/a.gguf", n_ctx=4096)
            s2 = get_llama_service(model_path="/a.gguf", n_ctx=131072)

            assert s1 is not s2
            assert MockService.call_count == 2

    def test_different_gpu_layers_invalidates(self):
        with patch("adapters.ai.llama_cpp.LlamaCppAIService") as MockService:
            instances = [MagicMock(_llm=None), MagicMock(_llm=None)]
            MockService.side_effect = instances

            s1 = get_llama_service(model_path="/a.gguf", n_gpu_layers=0)
            s2 = get_llama_service(model_path="/a.gguf", n_gpu_layers=99)

            assert s1 is not s2
            assert MockService.call_count == 2

    def test_cleanup_clears_cache(self):
        with patch("adapters.ai.llama_cpp.LlamaCppAIService") as MockService:
            instances = [MagicMock(_llm=None), MagicMock(_llm=None)]
            MockService.side_effect = instances

            get_llama_service(model_path="/a.gguf")
            cleanup_llama_service()
            get_llama_service(model_path="/a.gguf")

            assert MockService.call_count == 2

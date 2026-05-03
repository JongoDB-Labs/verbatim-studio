"""Llama.cpp AI service adapter.

Implements IAIService for local LLM inference using llama-cpp-python.
Uses create_chat_completion() for correct chat template handling.

Includes singleton caching with automatic invalidation when model path changes.
"""

import asyncio
import logging
import re
import sys
from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from core.interfaces import (
    AnalysisResult,
    ChatMessage,
    ChatOptions,
    ChatResponse,
    ChatStreamChunk,
    IAIService,
    SummarizationResult,
)

logger = logging.getLogger(__name__)

# Module-level cache for singleton pattern with config-based invalidation
_cached_service: "LlamaCppAIService | None" = None
_cached_config: tuple[str | None, int, int] | None = None


def get_llama_service(
    model_path: str | None = None,
    n_ctx: int = 4096,
    n_gpu_layers: int = 0,
) -> "LlamaCppAIService":
    """Get a cached LlamaCppAIService, creating or replacing as needed.

    Invalidates the cache when model_path, n_ctx, or n_gpu_layers change.
    This allows model switching and context size adjustments without
    restarting the server.

    Args:
        model_path: Path to the GGUF model file
        n_ctx: Context window size
        n_gpu_layers: Number of layers to offload to GPU

    Returns:
        Cached or newly created LlamaCppAIService instance
    """
    global _cached_service, _cached_config

    new_config = (model_path, n_ctx, n_gpu_layers)

    # Invalidate cache if any config parameter changed
    if new_config != _cached_config:
        if _cached_service is not None:
            logger.info(
                "LLM config changed (%s → %s), reloading model",
                _cached_config,
                new_config,
            )
            # Release the old model
            if _cached_service._llm is not None:
                del _cached_service._llm
                _cached_service._llm = None
        _cached_service = None
        _cached_config = new_config

    # Create new service if needed
    if _cached_service is None:
        logger.info("Creating new LlamaCppAIService (model_path=%s, n_ctx=%d)", model_path, n_ctx)
        _cached_service = LlamaCppAIService(
            model_path=model_path,
            n_ctx=n_ctx,
            n_gpu_layers=n_gpu_layers,
        )

    return _cached_service


# Separate voice LLM cache — runs Granite Tiny for fast voice responses
# without evicting the user's selected model from the main cache
_voice_service: "LlamaCppAIService | None" = None
_voice_model_path: str | None = None


def get_voice_llm_service(model_path: str, n_gpu_layers: int = -1) -> "LlamaCppAIService":
    """Get a dedicated LLM instance for voice chat (separate from main cache).

    Uses a small context (8K) for fast inference. Does NOT evict the main
    LLM model — both can be resident simultaneously.
    """
    global _voice_service, _voice_model_path

    if model_path != _voice_model_path or _voice_service is None:
        if _voice_service is not None and _voice_service._llm is not None:
            del _voice_service._llm
            _voice_service._llm = None
        _voice_model_path = model_path
        _voice_service = LlamaCppAIService(
            model_path=model_path,
            n_ctx=8192,  # Small context for fast voice responses
            n_gpu_layers=n_gpu_layers,
        )
        logger.info("Created voice LLM instance (model=%s, n_ctx=8K)", Path(model_path).name)

    return _voice_service


def cleanup_llama_service() -> None:
    """Unload the cached LLM service to free memory.

    Deletes the llama.cpp model from memory, clears the singleton cache,
    and runs garbage collection. Frees ~4-5 GB for an 8B Q4 model.
    """
    import gc

    global _cached_service, _cached_config

    if _cached_service is not None:
        logger.info("Unloading llama.cpp model to free memory")
        if _cached_service._llm is not None:
            del _cached_service._llm
            _cached_service._llm = None
        del _cached_service
        _cached_service = None
    _cached_config = None

    gc.collect()

    try:
        import torch
        if torch.backends.mps.is_available():
            torch.mps.empty_cache()
        elif torch.cuda.is_available():
            torch.cuda.empty_cache()
    except ImportError:
        pass


class LlamaCppAIService(IAIService):
    """Llama.cpp-based AI service for local LLM inference.

    Uses lazy loading to avoid import errors when llama-cpp-python is not installed.
    Supports various GGUF models for summarization and analysis.
    """

    def __init__(
        self,
        model_path: str | None = None,
        n_ctx: int = 4096,
        n_gpu_layers: int = 0,
    ):
        self._model_path = model_path
        self._n_ctx = n_ctx
        self._n_gpu_layers = n_gpu_layers
        self._llm = None
        self._available: bool | None = None
        self._llm_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="llm")

    def _reset_state(self) -> None:
        """Force-clear all model state (KV cache + recurrent/SSM state).

        Critical for hybrid Mamba-2 models (e.g. granite-4.0-h-tiny) where
        llama-cpp-python's built-in prefix-match optimization leaves stale
        recurrent state that causes ``llama_decode returned -1`` on the
        second request.
        """
        if self._llm is not None:
            self._llm._ctx.kv_cache_clear()
            self._llm.n_tokens = 0

    def _ensure_loaded(self) -> None:
        """Ensure llama.cpp is loaded and model is ready."""
        if self._llm is not None:
            return

        if not self._model_path:
            raise ValueError("No model path configured. Set model_path in configuration.")

        path = Path(self._model_path)
        if not path.exists():
            raise FileNotFoundError(f"Model file not found: {self._model_path}")

        try:
            from llama_cpp import Llama
        except ImportError as e:
            raise ImportError(
                "llama-cpp-python is not installed. Install with: pip install llama-cpp-python"
            ) from e

        ctx_kb = self._n_ctx // 1024
        logger.info(
            "Loading llama.cpp model: %s (context=%dK tokens, gpu_layers=%d)",
            Path(self._model_path).name,
            ctx_kb,
            self._n_gpu_layers,
        )

        try:
            self._llm = Llama(
                model_path=self._model_path,
                n_ctx=self._n_ctx,
                n_gpu_layers=self._n_gpu_layers,
                verbose=False,
            )
        except OSError as e:
            error_str = str(e)
            # Windows Error 0xc000001d = STATUS_ILLEGAL_INSTRUCTION.
            # This typically means Smart App Control (SAC) or Windows Defender
            # Application Control (WDAC) is blocking the native llama.dll
            # from executing.  Provide a clear diagnostic message.
            if "0xc000001d" in error_str or "-1073741795" in error_str:
                logger.error(
                    "llama.cpp native library blocked by Windows security policy "
                    "(STATUS_ILLEGAL_INSTRUCTION). Smart App Control or WDAC is "
                    "preventing the AI model from loading. To fix: open Windows "
                    "Security → App & Browser Control → Smart App Control and "
                    "set it to Off, then restart Verbatim Studio."
                )
                raise OSError(
                    "AI model blocked by Windows Smart App Control. "
                    "Open Windows Security → App & Browser Control → "
                    "Smart App Control → Off, then restart the app."
                ) from e
            raise
        except Exception as e:
            # Upstream llama-cpp-python raises bare Exception("Failed to load
            # model from file: ...") when the GGUF format isn't recognized.
            # On Windows the most common cause is GPU acceleration installed
            # llama-cpp-python 0.3.4 (the last version with a Windows CUDA
            # wheel), which can't read newer GGUF formats like Granite 4.0
            # hybrid Mamba-2. Detect that case and point at the recovery
            # endpoint.
            error_str = str(e)
            if "Failed to load model" in error_str and sys.platform == "win32":
                try:
                    import llama_cpp
                    installed = getattr(llama_cpp, "__version__", "unknown")
                except Exception:
                    installed = "unknown"
                logger.error(
                    "llama.cpp could not load %s with installed version %s. "
                    "If you recently enabled GPU acceleration, the bundled "
                    "CUDA llama-cpp-python wheel (v0.3.4) may be too old for "
                    "this model. Recover via Settings → AI → 'Restore CPU LLM' "
                    "or POST /system/disable-gpu-llm.",
                    Path(self._model_path).name, installed,
                )
                raise RuntimeError(
                    f"Failed to load AI model with llama-cpp-python {installed}. "
                    "If you enabled GPU acceleration, the Windows CUDA wheel "
                    "(0.3.4) is too old for this model. Open Settings → AI and "
                    "click 'Restore CPU LLM' to recover."
                ) from e
            raise

        logger.info(
            "Model loaded successfully — context window: %dK tokens (%d). "
            "KV cache is pre-allocated; memory usage is expected.",
            ctx_kb, self._n_ctx,
        )

    def _count_tokens(self, text: str) -> int:
        """Count tokens using the model's tokenizer if loaded, else estimate.

        Uses a conservative 1 token ≈ 3 chars estimate as fallback.
        """
        if self._llm is not None:
            try:
                return len(self._llm.tokenize(text.encode("utf-8")))
            except Exception:
                pass
        # Conservative estimate: 1 token ≈ 3 characters
        return len(text) // 3 + 1

    def _truncate_to_fit(self, text: str, max_tokens: int) -> str:
        """Truncate text to fit within a token budget.

        Uses the model's tokenizer when available for accuracy.
        """
        token_count = self._count_tokens(text)
        if token_count <= max_tokens:
            return text

        if self._llm is not None:
            try:
                tokens = self._llm.tokenize(text.encode("utf-8"))
                truncated_tokens = tokens[:max_tokens]
                return self._llm.detokenize(truncated_tokens).decode("utf-8", errors="replace")
            except Exception:
                pass

        # Fallback: estimate chars per token from the ratio
        ratio = len(text) / token_count
        max_chars = int(max_tokens * ratio)
        return text[:max_chars]

    def _split_into_chunks(
        self,
        text: str,
        max_tokens_per_chunk: int,
        overlap_tokens: int = 200,
    ) -> list[str]:
        """Split text into token-budget-aware chunks with overlap.

        Splits on sentence/newline boundaries to avoid cutting mid-thought.
        """
        self._ensure_loaded()
        total_tokens = self._count_tokens(text)

        if total_tokens <= max_tokens_per_chunk:
            return [text]

        chunks = []
        sentences = re.split(r'(?<=[.!?\n])\s+', text)

        current_chunk: list[str] = []
        current_tokens = 0

        for sentence in sentences:
            sentence_tokens = self._count_tokens(sentence)

            if current_tokens + sentence_tokens > max_tokens_per_chunk and current_chunk:
                chunks.append(" ".join(current_chunk))

                # Overlap from end of previous chunk
                overlap_text = []
                overlap_count = 0
                for s in reversed(current_chunk):
                    s_tokens = self._count_tokens(s)
                    if overlap_count + s_tokens > overlap_tokens:
                        break
                    overlap_text.insert(0, s)
                    overlap_count += s_tokens

                current_chunk = overlap_text
                current_tokens = overlap_count

            current_chunk.append(sentence)
            current_tokens += sentence_tokens

        if current_chunk:
            chunks.append(" ".join(current_chunk))

        logger.info(
            "Split transcript into %d chunks (total %d tokens, %d per chunk)",
            len(chunks), total_tokens, max_tokens_per_chunk,
        )
        return chunks

    def _parse_summarization_response(self, content: str) -> SummarizationResult:
        """Parse a summarization response into structured result."""
        summary = ""
        key_points = []
        action_items = []
        topics = []
        named_entities = []

        current_section = None
        list_sections = ("key_points", "action_items", "topics", "named_entities")
        for line in content.split("\n"):
            line = line.strip()
            upper = line.upper()
            if upper.startswith("SUMMARY:"):
                current_section = "summary"
                summary = line[8:].strip()
            elif upper.startswith("KEY POINTS:") or upper.startswith("KEY_POINTS:"):
                current_section = "key_points"
            elif upper.startswith("ACTION ITEMS:") or upper.startswith("ACTION_ITEMS:"):
                current_section = "action_items"
            elif upper.startswith("TOPICS:"):
                current_section = "topics"
            elif upper.startswith("NAMED ENTITIES:") or upper.startswith("NAMED_ENTITIES:") or upper.startswith("PEOPLE:"):
                current_section = "named_entities"
            elif current_section in list_sections and line:
                item = re.sub(r'^(?:[-*•]\s*|\d+[.)]\s*)', '', line).strip()
                if not item:
                    continue
                if current_section == "key_points":
                    key_points.append(item)
                elif current_section == "action_items":
                    action_items.append(item)
                elif current_section == "topics":
                    topics.append(item)
                elif current_section == "named_entities":
                    named_entities.append(item)
            elif current_section == "summary" and line:
                summary += " " + line

        return SummarizationResult(
            summary=summary.strip(),
            key_points=key_points if key_points else None,
            action_items=action_items if action_items else None,
            topics=topics if topics else None,
            named_entities=named_entities if named_entities else None,
        )

    async def chat(
        self,
        messages: list[ChatMessage],
        options: ChatOptions | None = None,
    ) -> ChatResponse:
        """Send a chat completion request using create_chat_completion."""
        options = options or ChatOptions()
        self._ensure_loaded()

        msgs = [{"role": m.role, "content": m.content} for m in messages]

        kwargs: dict[str, Any] = {
            "messages": msgs,
            "max_tokens": options.max_tokens or 512,
            "temperature": options.temperature,
            "top_p": options.top_p,
        }
        if options.response_format:
            kwargs["response_format"] = options.response_format

        # Force clean state before each call (prevents Mamba-2 state corruption)
        self._reset_state()

        try:
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(
                self._llm_executor,
                lambda: self._llm.create_chat_completion(**kwargs),
            )
        except RuntimeError as e:
            if "llama_decode" in str(e):
                logger.warning("llama_decode failed, resetting state and retrying: %s", e)
                self._reset_state()
                loop = asyncio.get_running_loop()
                result = await loop.run_in_executor(
                    self._llm_executor,
                    lambda: self._llm.create_chat_completion(**kwargs),
                )
            else:
                raise

        content = result["choices"][0]["message"]["content"]
        usage = result.get("usage", {})

        return ChatResponse(
            content=content.strip(),
            model=Path(self._model_path).stem if self._model_path else "unknown",
            usage={
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
                "total_tokens": usage.get("total_tokens", 0),
            },
            finish_reason=result["choices"][0].get("finish_reason"),
        )

    async def chat_stream(
        self,
        messages: list[ChatMessage],
        options: ChatOptions | None = None,
    ) -> AsyncIterator[ChatStreamChunk]:
        """Send a streaming chat completion request."""
        options = options or ChatOptions()
        self._ensure_loaded()

        msgs = [{"role": m.role, "content": m.content} for m in messages]

        # Create the streaming generator in a thread-safe way
        kwargs: dict[str, Any] = {
            "messages": msgs,
            "max_tokens": options.max_tokens or 512,
            "temperature": options.temperature,
            "top_p": options.top_p,
            "stream": True,
        }
        if options.response_format:
            kwargs["response_format"] = options.response_format

        # Force clean state before each call (prevents Mamba-2 state corruption)
        self._reset_state()

        loop = asyncio.get_running_loop()
        stream = await loop.run_in_executor(
            self._llm_executor,
            lambda: self._llm.create_chat_completion(**kwargs),
        )

        # Iterate over the synchronous generator using the SAME executor
        def _next_chunk(iterator):
            try:
                return next(iterator)
            except StopIteration:
                return None

        while True:
            chunk = await loop.run_in_executor(self._llm_executor, _next_chunk, stream)
            if chunk is None:
                break

            delta = chunk["choices"][0].get("delta", {})
            content = delta.get("content", "")
            finish_reason = chunk["choices"][0].get("finish_reason")

            yield ChatStreamChunk(
                content=content,
                finish_reason=finish_reason,
            )

    async def summarize_transcript(
        self,
        transcript_text: str,
        options: ChatOptions | None = None,
    ) -> SummarizationResult:
        """Generate a summary of a transcript.

        Uses single-pass summarization when the transcript fits in context,
        otherwise falls back to map-reduce chunked processing.
        """
        options = options or ChatOptions()

        system_prompt = """You are a transcript summarization assistant.
Analyze the following transcript and provide:
1. A concise summary (2-3 paragraphs)
2. Key points discussed
3. Any action items mentioned (omit this section if there are none)
4. Main topics covered
5. Named entities — people mentioned or who spoke in the transcript

Format your response as:
SUMMARY:
[Your summary here]

KEY POINTS:
- [Point 1]
- [Point 2]
...

ACTION ITEMS:
- [Action 1]
- [Action 2]
...

TOPICS:
- [Topic 1]
- [Topic 2]
...

NAMED ENTITIES:
- [Person 1]
- [Person 2]
..."""

        max_response = options.max_tokens or 2048
        self._ensure_loaded()
        system_tokens = self._count_tokens(system_prompt) + 20  # +20 for message framing
        available_tokens = self._n_ctx - system_tokens - max_response

        transcript_tokens = self._count_tokens(transcript_text)

        if transcript_tokens <= available_tokens:
            logger.info(
                "Summarize: single-pass (%d tokens, context has %d available)",
                transcript_tokens, available_tokens,
            )
            return await self._summarize_single(transcript_text, system_prompt, options)
        else:
            logger.info(
                "Summarize: map-reduce — transcript (%d tokens) exceeds single-pass capacity (%d tokens)",
                transcript_tokens, available_tokens,
            )
            return await self._summarize_chunked(transcript_text, system_prompt, options)

    async def _summarize_single(
        self,
        transcript_text: str,
        system_prompt: str,
        options: ChatOptions,
    ) -> SummarizationResult:
        """Summarize a transcript that fits within a single context window."""
        messages = [
            ChatMessage(role="system", content=system_prompt),
            ChatMessage(role="user", content=f"Please summarize this transcript:\n\n{transcript_text}"),
        ]

        response = await self.chat(messages, options)
        return self._parse_summarization_response(response.content)

    async def _summarize_chunked(
        self,
        transcript_text: str,
        system_prompt: str,
        options: ChatOptions,
    ) -> SummarizationResult:
        """Summarize a long transcript using map-reduce chunked processing.

        MAP phase: Summarize each chunk independently with a concise prompt.
        REDUCE phase: Combine chunk summaries using the full system prompt.
        """
        max_response = options.max_tokens or 2048

        # --- MAP phase ---
        chunk_prompt = """You are summarizing a section of a larger transcript.
Provide a concise summary of this section, noting:
- Key points discussed
- Any action items mentioned
- Main topics covered
- People mentioned or speaking

Be thorough — your summary will be combined with summaries of other sections."""

        chunk_response_tokens = min(1024, max_response)
        chunk_prompt_tokens = self._count_tokens(chunk_prompt) + 20
        chunk_available = self._n_ctx - chunk_prompt_tokens - chunk_response_tokens

        chunks = self._split_into_chunks(transcript_text, chunk_available)

        chunk_options = ChatOptions(
            max_tokens=chunk_response_tokens,
            temperature=options.temperature,
            top_p=options.top_p,
        )

        chunk_summaries = []
        for i, chunk in enumerate(chunks):
            logger.info("Summarizing chunk %d/%d", i + 1, len(chunks))
            messages = [
                ChatMessage(role="system", content=chunk_prompt),
                ChatMessage(role="user", content=f"Summarize this section:\n\n{chunk}"),
            ]
            response = await self.chat(messages, chunk_options)
            chunk_summaries.append(f"--- Section {i + 1} ---\n{response.content}")

        # --- REDUCE phase ---
        logger.info("Map phase complete (%d chunks). Starting reduce phase...", len(chunks))
        combined = "\n\n".join(chunk_summaries)

        # Safety net: truncate combined summaries if they still exceed available space
        system_tokens = self._count_tokens(system_prompt) + 20
        reduce_available = self._n_ctx - system_tokens - max_response
        combined = self._truncate_to_fit(combined, reduce_available)

        messages = [
            ChatMessage(role="system", content=system_prompt),
            ChatMessage(
                role="user",
                content=f"Combine these summaries of transcript sections into a single comprehensive summary:\n\n{combined}",
            ),
        ]

        response = await self.chat(messages, options)
        logger.info("Summarization complete (map-reduce, %d chunks)", len(chunks))
        return self._parse_summarization_response(response.content)

    async def analyze_transcript(
        self,
        transcript_text: str,
        analysis_type: str,
        options: ChatOptions | None = None,
    ) -> AnalysisResult:
        """Perform analysis on a transcript.

        Uses single-pass analysis when the transcript fits in context,
        otherwise falls back to map-reduce chunked processing.
        """
        options = options or ChatOptions()

        prompts = {
            "sentiment": "Analyze the sentiment of this transcript. Identify overall tone, emotional shifts, and key emotional moments.",
            "topics": "Extract the main topics and themes discussed in this transcript. List them in order of prominence.",
            "entities": "Extract all named entities (people, organizations, places, dates, products) from this transcript.",
            "questions": "List all questions asked in this transcript, who asked them, and whether they were answered.",
            "action_items": "Extract all action items, tasks, and commitments mentioned in this transcript.",
        }

        prompt = prompts.get(analysis_type, f"Perform {analysis_type} analysis on this transcript.")

        system_content = f"You are a transcript analyst. {prompt}"
        self._ensure_loaded()
        max_response = options.max_tokens or 512
        system_tokens = self._count_tokens(system_content) + 20
        available_tokens = self._n_ctx - system_tokens - max_response

        transcript_tokens = self._count_tokens(transcript_text)

        if transcript_tokens <= available_tokens:
            # Single pass — transcript fits in context
            messages = [
                ChatMessage(role="system", content=system_content),
                ChatMessage(role="user", content=f"Analyze this transcript:\n\n{transcript_text}"),
            ]

            response = await self.chat(messages, options)

            return AnalysisResult(
                analysis_type=analysis_type,
                content={"raw_analysis": response.content},
            )
        else:
            # Map-reduce chunked analysis
            logger.info(
                "Transcript (%d tokens) exceeds single-pass capacity (%d tokens) for %s analysis, using map-reduce",
                transcript_tokens, available_tokens, analysis_type,
            )

            # --- MAP phase ---
            chunk_response_tokens = min(1024, max_response)
            chunk_system_tokens = self._count_tokens(system_content) + 20
            chunk_available = self._n_ctx - chunk_system_tokens - chunk_response_tokens

            chunks = self._split_into_chunks(transcript_text, chunk_available)

            chunk_options = ChatOptions(
                max_tokens=chunk_response_tokens,
                temperature=options.temperature,
                top_p=options.top_p,
            )

            chunk_analyses = []
            for i, chunk in enumerate(chunks):
                logger.info("Analyzing chunk %d/%d (%s)", i + 1, len(chunks), analysis_type)
                messages = [
                    ChatMessage(role="system", content=system_content),
                    ChatMessage(role="user", content=f"Analyze this transcript section:\n\n{chunk}"),
                ]
                response = await self.chat(messages, chunk_options)
                chunk_analyses.append(f"--- Section {i + 1} ---\n{response.content}")

            # --- REDUCE phase ---
            combined = "\n\n".join(chunk_analyses)

            reduce_system = f"You are a transcript analyst. Merge these section-level analyses into one cohesive {analysis_type} analysis."
            reduce_system_tokens = self._count_tokens(reduce_system) + 20
            reduce_available = self._n_ctx - reduce_system_tokens - max_response

            # Safety net: truncate combined analyses if they still exceed available space
            combined = self._truncate_to_fit(combined, reduce_available)

            messages = [
                ChatMessage(role="system", content=reduce_system),
                ChatMessage(
                    role="user",
                    content=f"Merge these section analyses into a single comprehensive {analysis_type} analysis:\n\n{combined}",
                ),
            ]

            response = await self.chat(messages, options)

            return AnalysisResult(
                analysis_type=analysis_type,
                content={"raw_analysis": response.content},
            )

    async def get_available_models(self) -> list[dict[str, str]]:
        """Get list of available models."""
        models = []
        if self._model_path:
            path = Path(self._model_path)
            models.append({
                "id": path.stem,
                "name": path.name,
            })
        return models

    async def is_available(self) -> bool:
        """Check if the AI service is available and a model is configured."""
        # Check if llama_cpp library is installed
        try:
            from llama_cpp import Llama
        except ImportError:
            logger.debug("llama_cpp library not installed")
            return False

        # Check if a model is configured and exists
        if not self._model_path:
            logger.debug("No model path configured for LlamaCpp service")
            return False

        exists = Path(self._model_path).exists()
        if not exists:
            logger.warning("Model path configured but file does not exist: %s", self._model_path)
        return exists

    async def get_service_info(self) -> dict[str, str | int | float | bool]:
        """Get information about the AI service."""
        available = await self.is_available()
        info: dict[str, str | int | float | bool] = {
            "name": "llama.cpp",
            "available": available,
            "model_loaded": self._llm is not None,
            "n_ctx": self._n_ctx,
            "n_gpu_layers": self._n_gpu_layers,
        }

        if self._model_path:
            info["model_path"] = self._model_path
            info["model_exists"] = Path(self._model_path).exists()

        return info

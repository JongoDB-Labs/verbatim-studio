"""Voice agent worker for full-duplex voice assistant.

Bridges existing Verbatim adapters (Whisper STT, Granite LLM, Qwen3-TTS)
into a LiveKit Agents session. Each adapter class wraps the corresponding
Verbatim service so that LiveKit can drive the STT -> LLM -> TTS pipeline.

The module is importable even when livekit-agents is not installed; all
LiveKit imports are guarded and raise clear errors at runtime only when
an agent session is actually requested.

TODO: The LiveKit Agents Python SDK API surface may differ from what is
implemented here. Each adapter and the session wiring carry TODO markers
where the exact LiveKit API needs validation during integration testing.
"""

from __future__ import annotations

import asyncio
import json
import logging
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING, Any

from core.config import settings

if TYPE_CHECKING:
    from core.interfaces.ai import IAIService
    from core.interfaces.transcription import ITranscriptionEngine
    from core.interfaces.tts import ITTSService

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# LiveKit imports (graceful degradation)
# ---------------------------------------------------------------------------
try:
    from livekit import agents  # noqa: F401
    from livekit.agents import (
        AgentSession,
        Agent,
        RunContext,
    )

    LIVEKIT_AGENTS_AVAILABLE = True
except ImportError:
    LIVEKIT_AGENTS_AVAILABLE = False
    AgentSession = None  # type: ignore[assignment,misc]
    Agent = object  # type: ignore[assignment,misc]
    RunContext = None  # type: ignore[assignment,misc]
    logger.debug(
        "livekit-agents is not installed. Voice agent features are disabled. "
        "Install with: pip install 'livekit-agents[silero]'"
    )

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_VOICE_RESULT_CHARS = 500

VOICE_TOOLS = [
    "web_search",
    "project_search",
    "global_search",
    "get_context",
    "app_help",
    "generate_document",
    "export_transcript",
    "summarize_transcript",
    "quality_review",
    "extract_entities",
    "highlight_segments",
    "add_note",
    "create_project",
    "tag_recordings",
    "get_recording_info",
    "system_status",
]

AGENT_INSTRUCTIONS = """\
You are Max, a concise voice assistant for Verbatim Studio. \
Your responses will be read aloud by a text-to-speech system. \
Today's date is {today}.

CRITICAL RULES FOR SPOKEN OUTPUT:
- Respond in 1-3 plain sentences. Never exceed 3 sentences.
- NEVER use bullet points, numbered lists, dashes, asterisks, or any markdown formatting.
- NEVER use special characters like #, *, -, or > at the start of lines.
- Write exactly as you would speak naturally in a conversation.
- For simple questions, answer directly from what you know.
- For complex requests (summarize, analyze, review, search), a specialist will handle the work. Just answer naturally.
- If something fails, say so briefly and suggest what the user can try.
- Be friendly but efficient.
- ALWAYS finish your sentences completely. Never stop mid-word or mid-sentence.
"""

# ---------------------------------------------------------------------------
# STT Adapter — wraps existing Whisper transcription engine
# ---------------------------------------------------------------------------


class WhisperSTTAdapter:
    """Bridges the Verbatim ITranscriptionEngine to LiveKit's STT interface.

    Uses a dedicated thread pool so STT doesn't compete with LLM/TTS
    for the default asyncio executor.
    """

    _stt_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="stt")

    def __init__(self, engine: ITranscriptionEngine) -> None:
        self._engine = engine

    async def recognize(self, audio_data: bytes, *, sample_rate: int = 16000) -> str:
        """Transcribe audio bytes via Whisper.

        Writes audio to a temporary WAV file (Whisper expects file input),
        runs transcription, and returns the concatenated text.

        Args:
            audio_data: Raw PCM audio bytes (16-bit mono).
            sample_rate: Sample rate of the audio (default 16000).

        Returns:
            Transcribed text string.
        """
        import wave

        # Write audio to temp file — close handle before wave.open() for Windows
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tmp.close()
        try:
            with wave.open(tmp.name, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)  # 16-bit
                wf.setframerate(sample_rate)
                wf.writeframes(audio_data)

            # Run transcription through existing engine
            result = await self._engine.transcribe(tmp.name)
            text = " ".join(seg.text for seg in result.segments).strip()
            return text
        finally:
            Path(tmp.name).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# LLM Adapter — wraps existing IAIService (Granite / llama.cpp)
# ---------------------------------------------------------------------------


class GraniteLLMAdapter:
    """Bridges the Verbatim IAIService to LiveKit's LLM interface.

    Wraps the existing chat() method to provide responses for voice
    conversations.

    TODO: The exact LiveKit LLM adapter interface (base class, method
    signatures for streaming) needs validation against the livekit-agents
    SDK. This implementation provides both blocking and streaming methods.
    """

    def __init__(self, ai_service: IAIService) -> None:
        self._ai_service = ai_service

    async def chat(self, messages: list[dict[str, str]], max_retries: int = 2) -> str:
        """Send a chat request and return the response text.

        Retries on llama_decode errors (state corruption from concurrent access).

        Args:
            messages: List of {"role": ..., "content": ...} dicts.
            max_retries: Number of retries on transient errors.

        Returns:
            Response text from the LLM.
        """
        from core.interfaces.ai import ChatMessage, ChatOptions

        chat_messages = [
            ChatMessage(role=m["role"], content=m["content"])
            for m in messages
        ]
        options = ChatOptions(temperature=0.7, max_tokens=512)

        for attempt in range(max_retries + 1):
            try:
                response = await self._ai_service.chat(chat_messages, options)
                return response.content
            except RuntimeError as e:
                if "llama_decode" in str(e) and attempt < max_retries:
                    logger.warning("LLM state error, retrying (attempt %d)...", attempt + 1)
                    await asyncio.sleep(0.5)
                    continue
                raise

    async def chat_stream(self, messages: list[dict[str, str]]):
        """Stream a chat response, yielding content chunks.

        Args:
            messages: List of {"role": ..., "content": ...} dicts.

        Yields:
            String chunks of the response.
        """
        from core.interfaces.ai import ChatMessage, ChatOptions

        chat_messages = [
            ChatMessage(role=m["role"], content=m["content"])
            for m in messages
        ]
        options = ChatOptions(temperature=0.7, max_tokens=512)
        async for chunk in self._ai_service.chat_stream(chat_messages, options):
            if chunk.content:
                yield chunk.content


# ---------------------------------------------------------------------------
# TTS Adapter — wraps existing Qwen3-TTS service
# ---------------------------------------------------------------------------


class Qwen3TTSAdapter:
    """Bridges the Verbatim ITTSService to LiveKit's TTS interface.

    Wraps the existing Qwen3-TTS synthesize() method to produce audio
    for voice responses.

    TODO: The exact LiveKit TTS adapter interface (base class, method
    signatures, audio format requirements) needs validation against
    the livekit-agents SDK. LiveKit may expect specific audio formats
    (e.g., raw PCM frames rather than WAV).
    """

    def __init__(self, tts_service: ITTSService, voice: str | None = None) -> None:
        self._tts_service = tts_service
        self._voice = voice

    async def synthesize(self, text: str) -> bytes:
        """Synthesize speech from text.

        Args:
            text: Text to convert to speech.

        Returns:
            Audio data as bytes (WAV format from Qwen3-TTS).
        """
        return await self._tts_service.synthesize(text, voice=self._voice)


# ---------------------------------------------------------------------------
# Tool bridging — convert ToolDef objects for voice agent use
# ---------------------------------------------------------------------------


def get_voice_tool_definitions() -> list[dict[str, Any]]:
    """Convert registered ToolDef objects into voice-friendly descriptions.

    Returns a list of tool definition dicts for the voice-eligible tools.
    Each dict contains name, description, and parameters from the existing
    ToolRegistry.

    TODO: The exact format LiveKit Agents expects for tool/function
    definitions needs validation. This returns a generic dict format
    that can be adapted to the SDK's requirements.
    """
    from services.tool_registry import get_registry

    registry = get_registry()
    tools = registry.list_tools(names=VOICE_TOOLS)

    definitions = []
    for tool in tools:
        definitions.append({
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters,
        })

    return definitions


def get_voice_tools_prompt(exclude: list[str] | None = None) -> str:
    """Generate a system-prompt section describing available voice tools.

    Args:
        exclude: Tool names to exclude from the prompt.

    Returns a concise tool description string suitable for injection into
    the LLM system prompt during voice sessions.
    """
    definitions = get_voice_tool_definitions()
    if exclude:
        definitions = [d for d in definitions if d["name"] not in exclude]
    if not definitions:
        return ""

    lines = [
        "\n\nYou have access to these tools. To use one, respond with a JSON block:",
        '{"tool": "tool_name", "args": {"param": "value"}}',
        "",
        "Available tools:",
    ]
    for defn in definitions:
        params = defn["parameters"].get("properties", {})
        param_str = ", ".join(f"{k}: {v.get('type', 'any')}" for k, v in params.items())
        lines.append(f"- {defn['name']}({param_str}) -- {defn['description']}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool execution callback
# ---------------------------------------------------------------------------


async def execute_tool(tool_name: str, args: dict, ctx: Any = None) -> str:
    """Execute a voice tool and return a truncated string result.

    Looks up the tool in the global ToolRegistry, calls its handler with
    the provided args and context, and truncates the result to
    MAX_VOICE_RESULT_CHARS for voice delivery.

    Args:
        tool_name: Name of the tool to execute.
        args: Arguments dict to pass to the tool handler.
        ctx: A ToolContext instance. If None, a minimal context is created.

    Returns:
        Result string, truncated to MAX_VOICE_RESULT_CHARS.
    """
    from services.tool_registry import ToolContext, get_registry

    registry = get_registry()
    tool = registry.get(tool_name)

    if tool is None:
        return f"Tool '{tool_name}' is not available."

    # Build a minimal ToolContext if none provided
    if ctx is None:
        ctx = ToolContext(
            project_id=None,
            conversation_id=None,
            recording_ids=[],
            document_ids=[],
            db=None,
        )

    try:
        result = await tool.handler(args, ctx)
        content = result.content

        # Truncate for voice delivery
        if len(content) > MAX_VOICE_RESULT_CHARS:
            content = content[:MAX_VOICE_RESULT_CHARS] + "..."

        return content
    except Exception as e:
        logger.exception("Voice tool '%s' failed", tool_name)
        return f"Tool {tool_name} failed: {e}"


# ---------------------------------------------------------------------------
# VerbatimVoiceAgent — main agent class
# ---------------------------------------------------------------------------


class VerbatimVoiceAgent:
    """Main voice agent that orchestrates STT, LLM, TTS, and tools.

    Wraps the existing Verbatim adapters and presents them as a unified
    agent that can be connected to a LiveKit room.

    TODO: This class may need to inherit from a LiveKit Agent base class
    or implement specific protocols. The exact integration point with
    the livekit-agents framework needs validation.
    """

    def __init__(
        self,
        stt: WhisperSTTAdapter,
        llm: GraniteLLMAdapter,
        tts: Qwen3TTSAdapter,
        main_llm: GraniteLLMAdapter | None = None,
        web_search_enabled: bool = False,
    ) -> None:
        self.stt = stt
        self.llm = llm  # Fast voice LLM (Granite Tiny)
        self.main_llm = main_llm  # Full-power LLM for tool calls/analysis
        self.tts = tts
        from datetime import datetime
        self.web_search_enabled = web_search_enabled
        tools_prompt = get_voice_tools_prompt(exclude=[] if web_search_enabled else ["web_search"])
        self.instructions = AGENT_INSTRUCTIONS.format(today=datetime.now().strftime('%B %d, %Y')) + tools_prompt
        if web_search_enabled:
            self.instructions += "\n\nWeb search is ENABLED. Use the web_search tool to look up current information when asked about news, events, or anything requiring up-to-date data."
        self._conversation: list[dict[str, str]] = [
            {"role": "system", "content": self.instructions},
        ]
        self._max_history_turns = 6  # Keep voice conversations lean for speed
        self._tool_context: Any = None

    def set_context(self, context_parts: list[str]) -> None:
        """Inject attached transcript/document content into the system prompt."""
        if not context_parts:
            return
        context_text = (
            f"\n\nYou have access to {len(context_parts)} attached item(s):\n\n"
            + "\n".join(context_parts)
        )
        # Append to the system message
        self._conversation[0]["content"] += context_text

    def set_tool_context(self, ctx: Any) -> None:
        """Set the ToolContext for tool execution during this session.

        Args:
            ctx: A ToolContext instance with db session, project info, etc.
        """
        self._tool_context = ctx

    async def handle_user_audio_streaming(self, audio_data: bytes, publish_fn=None, transcript_fn=None):
        """Process user audio and stream TTS responses sentence-by-sentence.

        STT -> LLM -> TTS (streamed per sentence)

        Args:
            audio_data: Raw PCM audio from the user.
            publish_fn: Async callback to publish each audio chunk immediately.
            transcript_fn: Async callback to publish transcript messages as they happen.
                           Signature: async (role: str, content: str) -> None

        Returns:
            Tuple of (user_text, assistant_text). Both may be None.
        """
        # Step 1: Speech-to-text
        user_text = await self.stt.recognize(audio_data)
        if not user_text.strip():
            return None, None

        # Drop Whisper hallucinations (silence/music/short noise produces
        # "Thanks for watching!", repeated words, etc.). Shared filter
        # with live transcription — see services.transcript_filter.
        from services.transcript_filter import is_hallucination
        if is_hallucination(user_text):
            logger.debug("Filtered STT hallucination: %r", user_text[:60])
            return None, None

        logger.info("Voice STT result: %s", user_text[:100])

        # Stream user transcript immediately (before LLM call)
        if transcript_fn:
            await transcript_fn("user", user_text)

        # Step 2a: Auto web search
        web_context = ""
        if self.web_search_enabled:
            try:
                from services.web_search import (
                    extract_search_query,
                    create_search_provider,
                    format_results_for_context,
                    load_web_search_config,
                )
                search_query = extract_search_query(user_text)
                if search_query:
                    config = await load_web_search_config()
                    provider = create_search_provider(config)
                    results = await provider.search(search_query.text)
                    if results:
                        web_context = format_results_for_context(results)
                        logger.info("Voice web search: %d results for '%s'", len(results), search_query.text)
            except Exception:
                logger.warning("Voice web search failed", exc_info=True)

        # Step 2b: Decide which LLM to use
        # Granite Tiny handles everything by default (fast responses)
        # Only delegates to main LLM when user explicitly requests deep work
        user_msg = user_text
        if web_context:
            user_msg += (
                "\n\n=== Web Search Results (CURRENT DATA — USE THIS TO ANSWER) ===\n"
                f"{web_context}\n"
                "IMPORTANT: Base your answer on the web search results above, NOT your training data."
            )

        import re as _re
        DELEGATE_PATTERNS = _re.compile(
            r'\b(summarize|analyze|extract|review|compare|detail|in[- ]depth|'
            r'break down|explain in detail|thorough|comprehensive|'
            r'search for|look up|find information|what does .+ say about)\b',
            _re.IGNORECASE,
        )
        needs_delegation = bool(DELEGATE_PATTERNS.search(user_text)) and self.main_llm is not None

        if needs_delegation:
            logger.info("Delegating to main LLM: '%s'", user_text[:80])
            # Speak and display filler as its own message
            if publish_fn:
                try:
                    filler = await self.tts.synthesize("Let me look into that for you.")
                    if filler:
                        await publish_fn(filler)
                except Exception:
                    pass
            if transcript_fn:
                await transcript_fn("assistant_token", "Let me look into that for you.")
                await transcript_fn("assistant_done", "")  # Finalize filler as separate message

        active_llm = self.main_llm if needs_delegation else self.llm

        self._conversation.append({"role": "user", "content": user_msg})

        # Trim history to keep context small for fast inference
        max_msgs = 1 + (self._max_history_turns * 2)
        if len(self._conversation) > max_msgs:
            self._conversation = [self._conversation[0]] + self._conversation[-(max_msgs - 1):]

        # Stream LLM → TTS with parallel pipeline:
        # LLM generates tokens → sentences queued → TTS runs in background
        full_response = ""
        sentence_buffer = ""
        tts_queue: asyncio.Queue = asyncio.Queue()
        has_tool_call = False

        # TTS worker: pulls sentences from queue and synthesizes/publishes
        async def tts_worker():
            while True:
                sentence = await tts_queue.get()
                if sentence is None:  # poison pill
                    break
                try:
                    audio_chunk = await self.tts.synthesize(sentence)
                    if not audio_chunk:
                        logger.error(
                            "TTS produced empty audio for sentence: %r", sentence[:80]
                        )
                    elif not publish_fn:
                        logger.warning("TTS produced %d bytes but no publish_fn", len(audio_chunk))
                    else:
                        logger.info(
                            "TTS produced %d bytes for sentence: %r — publishing",
                            len(audio_chunk), sentence[:60],
                        )
                        await publish_fn(audio_chunk)
                except Exception:
                    logger.exception("TTS failed for sentence: %r", sentence[:80])
                tts_queue.task_done()

        tts_task = asyncio.create_task(tts_worker())

        import re
        sentences_queued = 0

        async for chunk in active_llm.chat_stream(self._conversation):
            full_response += chunk
            sentence_buffer += chunk

            # Stream each token to the transcript display
            if transcript_fn and chunk.strip():
                await transcript_fn("assistant_token", chunk)

            # Check for complete sentences
            sentence_match = re.search(r'[.!?]\s', sentence_buffer)
            if sentence_match:
                end_pos = sentence_match.end()
                sentence = sentence_buffer[:end_pos].strip()
                sentence_buffer = sentence_buffer[end_pos:]

                # Check for tool call
                if '{"tool"' in sentence or any(t + '{' in sentence for t in VOICE_TOOLS):
                    has_tool_call = True
                    break

                if sentence:
                    sentences_queued += 1
                    if sentences_queued == 1:
                        logger.info("First sentence ready, queuing TTS")
                    await tts_queue.put(sentence)

        # Queue any remaining text
        if sentence_buffer.strip() and not has_tool_call:
            clean = re.sub(r'\{[^{}]*\}', '', sentence_buffer).strip()
            if clean:
                await tts_queue.put(clean)

        self._conversation.append({"role": "assistant", "content": full_response})

        # Signal assistant response complete BEFORE waiting for TTS
        # This ensures the transcript is saved even if user disconnects during TTS
        if transcript_fn:
            await transcript_fn("assistant_done", "")

        # Handle tool calls in the complete response
        response_text = await self._handle_tool_calls(full_response)

        # If tool call changed the response, speak the tool result
        if response_text != full_response and response_text.strip() and publish_fn:
            sentences = self._split_sentences(response_text)
            for sentence in sentences:
                if sentence.strip():
                    try:
                        audio_chunk = await self.tts.synthesize(sentence.strip())
                        if audio_chunk:
                            await publish_fn(audio_chunk)
                    except Exception:
                        logger.warning("TTS failed for tool result: %s", sentence[:50])

        # Wait for TTS to finish (but transcript is already saved)
        await tts_queue.put(None)
        await tts_task

        return user_text, response_text or full_response

    @staticmethod
    def _split_sentences(text: str) -> list[str]:
        """Split text into sentences for incremental TTS."""
        import re
        # Split on sentence-ending punctuation followed by a space or end
        parts = re.split(r'(?<=[.!?])\s+', text)
        return [p for p in parts if p.strip()]

    @staticmethod
    def _concat_wav(chunks: list[bytes]) -> bytes:
        """Concatenate multiple WAV byte strings into one."""
        import io
        import wave

        if len(chunks) == 1:
            return chunks[0]

        # Read all chunks and combine raw PCM data
        all_frames = bytearray()
        sample_rate = 24000
        sample_width = 2
        channels = 1

        for chunk in chunks:
            try:
                with wave.open(io.BytesIO(chunk), "rb") as wf:
                    sample_rate = wf.getframerate()
                    sample_width = wf.getsampwidth()
                    channels = wf.getnchannels()
                    all_frames.extend(wf.readframes(wf.getnframes()))
            except Exception:
                continue

        # Write combined WAV
        out = io.BytesIO()
        with wave.open(out, "wb") as wf:
            wf.setnchannels(channels)
            wf.setsampwidth(sample_width)
            wf.setframerate(sample_rate)
            wf.writeframes(bytes(all_frames))

        return out.getvalue()

    async def _handle_tool_calls(self, response_text: str) -> str:
        """Detect and execute tool calls embedded in LLM output.

        If the response contains a JSON tool call, execute it and feed
        the result back to the LLM for a natural language summary.

        Args:
            response_text: The raw LLM response that may contain tool calls.

        Returns:
            Final response text (either original or post-tool summary).
        """
        # Tool call detection — multiple formats the LLM may use
        try:
            import re

            tool_name = None
            tool_args = {}
            clean_text = response_text

            # Format 1: {"tool": "name", "args": {...}}
            match = re.search(r'\{[^{}]*"tool"\s*:', response_text)
            if match:
                start = match.start()
                depth = 0
                end = start
                for i, c in enumerate(response_text[start:], start=start):
                    if c == '{':
                        depth += 1
                    elif c == '}':
                        depth -= 1
                        if depth == 0:
                            end = i + 1
                            break
                try:
                    call_data = json.loads(response_text[start:end])
                    tool_name = call_data.get("tool")
                    tool_args = call_data.get("args", {})
                    clean_text = response_text[:start].strip()
                except json.JSONDecodeError:
                    pass

            # Format 2: tool_name{"key": "value"} (LLM shorthand)
            if not tool_name:
                match2 = re.search(r'(\w+)\s*(\{[^{}]*\})', response_text)
                if match2 and match2.group(1) in VOICE_TOOLS:
                    tool_name = match2.group(1)
                    try:
                        tool_args = json.loads(match2.group(2))
                    except json.JSONDecodeError:
                        tool_name = None
                    if tool_name:
                        clean_text = response_text[:match2.start()].strip()

            if not tool_name:
                # Strip any remaining JSON-like content before sending to TTS
                cleaned = re.sub(r'\{[^{}]*\}', '', response_text).strip()
                return cleaned if cleaned else response_text

            if tool_name not in VOICE_TOOLS:
                return response_text

            logger.info("Voice agent calling tool: %s(%s)", tool_name, tool_args)

            # Execute the tool
            tool_result = await execute_tool(
                tool_name, tool_args, ctx=self._tool_context
            )

            # Use MAIN LLM (full-power) to summarize tool results
            # Falls back to voice LLM if main not available
            summarizer = self.main_llm or self.llm
            logger.info("Summarizing tool result with %s",
                        "main LLM" if self.main_llm else "voice LLM")

            self._conversation.append({
                "role": "user",
                "content": f"Tool result for {tool_name}:\n{tool_result}\n\n"
                "Summarize this result conversationally in 1-2 sentences.",
            })

            summary = await summarizer.chat(self._conversation)
            self._conversation.append({"role": "assistant", "content": summary})

            # Combine any pre-tool text with the tool result summary
            import re as _re
            summary_clean = _re.sub(r'\{[^{}]*\}', '', summary).strip()
            if clean_text:
                return f"{clean_text} {summary_clean}"
            return summary_clean

        except (json.JSONDecodeError, KeyError, TypeError):
            # Strip any JSON artifacts before returning to TTS
            import re as _re
            cleaned = _re.sub(r'\{[^{}]*\}', '', response_text).strip()
            return cleaned if cleaned else response_text


# ---------------------------------------------------------------------------
# Factory: create_agent_session()
# ---------------------------------------------------------------------------


def _get_tts_service() -> ITTSService:
    """Get the active TTS service instance.

    Reads the active TTS model from the voice route helpers and creates
    the appropriate TTS service for the current platform:
    - macOS: Qwen3-TTS via MLX (Apple Silicon)
    - Windows/Linux: Kokoro ONNX (CPU/CUDA)

    Returns:
        Configured ITTSService instance.

    Raises:
        RuntimeError: If no TTS model is active or downloaded.
    """
    import sys

    from api.routes.voice import _get_active_tts_model, _tts_model_dir

    active_model = _get_active_tts_model()
    if not active_model:
        raise RuntimeError(
            "No TTS model is active. Download and activate a model via "
            "POST /voice/tts/models/{model_id}/download first."
        )

    model_dir = _tts_model_dir(active_model)
    if not model_dir.exists():
        raise RuntimeError(
            f"TTS model directory not found: {model_dir}. "
            "Re-download the model via the voice settings."
        )

    # Platform dispatch — import the correct adapter
    if sys.platform == "darwin":
        from adapters.ai.qwen3_tts import get_tts_service
    else:
        from adapters.ai.kokoro_onnx_tts import get_tts_service

    return get_tts_service(str(model_dir))


def create_agent_session(voice: str | None = None, web_search_enabled: bool = False, has_attachments: bool = False) -> VerbatimVoiceAgent:
    """Factory that creates a fully configured VerbatimVoiceAgent.

    Wires together:
    - STT: Whisper via the adapter factory
    - LLM: Granite / llama.cpp via the adapter factory
    - TTS: Qwen3-TTS via the active model
    - Tools: Bridged from the existing ToolRegistry

    Args:
        voice: Optional voice/speaker ID for TTS (e.g. "Chelsie", "Ryan").

    Returns:
        A configured VerbatimVoiceAgent ready to process audio.

    Raises:
        RuntimeError: If required services cannot be initialized.
    """
    from core.factory import get_factory

    factory = get_factory()

    # Create STT adapter wrapping Whisper
    try:
        transcription_engine = factory.create_transcription_engine()
        stt = WhisperSTTAdapter(transcription_engine)
        logger.info("Voice STT adapter created (Whisper)")
    except Exception as e:
        raise RuntimeError(f"Failed to create STT adapter: {e}") from e

    # Create LLM adapter — use dedicated Granite Tiny for fast voice responses
    # This runs separately from the user's selected model (no eviction)
    try:
        from adapters.ai.llama_cpp import get_voice_llm_service
        from core.transcription_settings import detect_llm_gpu_layers

        # Find Granite Tiny model path
        granite_path = None
        models_dir = settings.MODELS_DIR
        for candidate in ["granite-4.0-h-tiny-Q4_K_M.gguf", "granite-4.0-h-tiny.gguf"]:
            p = models_dir / candidate
            if p.exists():
                granite_path = str(p)
                break

        if granite_path:
            gpu_layers = detect_llm_gpu_layers()
            ai_service = get_voice_llm_service(granite_path, n_gpu_layers=gpu_layers)
            logger.info("Voice LLM: Granite Tiny (dedicated instance, 8K context)")
        else:
            # Fallback to whatever model is active
            ai_service = factory.create_ai_service()
            logger.info("Voice LLM: using active model (Granite Tiny not found)")

        llm = GraniteLLMAdapter(ai_service)
    except Exception as e:
        raise RuntimeError(f"Failed to create LLM adapter: {e}") from e

    # Create main LLM adapter (user's selected model) for tool calls and analysis
    main_llm = None
    main_ai_service = None
    try:
        from api.routes.ai import _ensure_active_model_loaded
        _ensure_active_model_loaded()  # Ensure model path is set in factory config
        main_ai_service = factory.create_ai_service()
        if main_ai_service._model_path:
            main_llm = GraniteLLMAdapter(main_ai_service)
            logger.info("Main LLM adapter created (%s)", Path(main_ai_service._model_path).name)
        else:
            logger.warning("Main LLM has no model path configured")
    except Exception as e:
        logger.warning("Main LLM not available (%s) — voice agent will use Granite Tiny for everything", e)

    # Create TTS adapter
    try:
        tts_service = _get_tts_service()
        tts = Qwen3TTSAdapter(tts_service, voice=voice)
        logger.info("Voice TTS adapter created (voice=%s)", voice or "default")
    except Exception as e:
        raise RuntimeError(f"Failed to create TTS adapter: {e}") from e

    # Create the agent with both LLMs
    agent = VerbatimVoiceAgent(
        stt=stt, llm=llm, tts=tts,
        main_llm=main_llm,
        web_search_enabled=web_search_enabled,
    )
    logger.info("VerbatimVoiceAgent created (voice LLM + main LLM)")

    # Preload ALL models simultaneously for fast first response
    # 1. Voice LLM (Granite Tiny)
    try:
        ai_service._ensure_loaded()
        logger.info("Voice LLM preloaded (Granite Tiny)")
    except Exception:
        logger.debug("Voice LLM preload skipped")

    # 2. Main LLM (user's selected model)
    if main_ai_service:
        try:
            main_ai_service._ensure_loaded()
            logger.info("Main LLM preloaded (%s)", factory._config.ai_model_path)
        except Exception:
            logger.debug("Main LLM preload skipped")

    # 2. TTS — force load into memory. Failures here cause silent TTS
    # later (audio worker swallows errors), so surface them clearly now.
    try:
        tts_service._ensure_loaded()
        logger.info("TTS model preloaded for voice session")
    except Exception:
        logger.exception(
            "TTS model FAILED to preload — voice chat will be silent. "
            "Check that all model dependencies are installed (mlx-audio, "
            "kokoro-onnx, addict, num2words, spacy, dlinfo, segments)."
        )

    # 3. STT preload happens in the voice session endpoint (async context)

    return agent


# ---------------------------------------------------------------------------
# LiveKit room connection (requires livekit-agents)
# ---------------------------------------------------------------------------


async def connect_agent_to_room(
    agent: VerbatimVoiceAgent,
    room_name: str,
    token: str,
    url: str = "ws://127.0.0.1:7880",
) -> None:
    """Connect the voice agent to a LiveKit room and run the audio loop.

    Joins the room, subscribes to user audio tracks, buffers audio frames
    for VAD-based segmentation, runs STT→LLM→TTS, and publishes the
    response audio back.

    The agent stays connected until the user disconnects or the room closes.

    Args:
        agent: The configured VerbatimVoiceAgent.
        room_name: LiveKit room name to join.
        token: Access token for the agent participant.
        url: LiveKit server WebSocket URL.
    """
    if not LIVEKIT_AGENTS_AVAILABLE:
        raise RuntimeError(
            "livekit-agents is not installed. "
            "Install with: pip install 'livekit-agents[silero]'"
        )

    from livekit import rtc
    import numpy as np
    import struct

    room = rtc.Room()

    # Create an AudioSource for publishing agent responses
    AGENT_SAMPLE_RATE = 24000  # Qwen3-TTS output rate
    audio_source = rtc.AudioSource(sample_rate=AGENT_SAMPLE_RATE, num_channels=1)
    agent_track = rtc.LocalAudioTrack.create_audio_track("agent-voice", audio_source)

    # Track whether we're still connected
    connected = asyncio.Event()
    connected.set()

    try:
        await room.connect(url, token)
        logger.info("Voice agent connected to room %s", room_name)

        # Publish the agent's audio track
        await room.local_participant.publish_track(agent_track)
        logger.info("Agent audio track published")

        # Buffer for accumulating user audio
        audio_buffer = bytearray()
        SILENCE_THRESHOLD = 500  # int16 amplitude RMS threshold
        SILENCE_DURATION_MS = 700  # ms of silence before processing
        MIN_AUDIO_MS = 1200  # minimum audio to process
        speaking_state = {"active": False, "cooldown_until": 0.0}  # echo suppression

        @room.on("track_subscribed")
        def on_track_subscribed(track, publication, participant):
            if track.kind == rtc.TrackKind.KIND_AUDIO:
                logger.info("Subscribed to audio from %s", participant.identity)
                asyncio.ensure_future(_process_audio_track(track))

        @room.on("disconnected")
        def on_disconnected():
            logger.info("Room %s disconnected", room_name)
            connected.clear()

        async def _process_audio_track(track):
            """Read audio frames from user, detect speech, run agent pipeline."""
            import time as _time
            from collections import deque

            stream = rtc.AudioStream.from_track(
                track=track,
                sample_rate=16000,
                num_channels=1,
            )

            frames_per_ms = 16
            is_speaking = False
            silence_frames = 0

            # Pre-buffer: keep last 2 seconds of audio so speech start isn't clipped
            PRE_BUFFER_MS = 2000
            pre_buffer: deque = deque()

            async for event in stream:
                if not connected.is_set():
                    break

                if speaking_state["active"] or _time.monotonic() < speaking_state["cooldown_until"]:
                    audio_buffer.clear()
                    silence_frames = 0
                    is_speaking = False
                    pre_buffer.clear()
                    continue

                frame = event.frame
                frame_data = bytes(frame.data)

                samples = np.frombuffer(frame_data, dtype=np.int16)
                rms = np.sqrt(np.mean(samples.astype(np.float32) ** 2))

                if rms >= SILENCE_THRESHOLD:
                    if not is_speaking:
                        is_speaking = True
                        cutoff_time = _time.monotonic() - 0.3
                        for ts, prebuf_chunk in pre_buffer:
                            if ts >= cutoff_time:
                                audio_buffer.extend(prebuf_chunk)
                        pre_buffer.clear()
                        logger.info("Speech started (RMS=%d, threshold=%d)", int(rms), SILENCE_THRESHOLD)

                    audio_buffer.extend(frame_data)
                    silence_frames = 0
                else:
                    if is_speaking:
                        # In speech, accumulating silence
                        audio_buffer.extend(frame_data)
                        silence_frames += frame.samples_per_channel
                    else:
                        # Not speaking — add to pre-buffer with timestamp
                        pre_buffer.append((_time.monotonic(), frame_data))
                        # Evict frames older than PRE_BUFFER_MS
                        cutoff = _time.monotonic() - (PRE_BUFFER_MS / 1000.0)
                        while pre_buffer and pre_buffer[0][0] < cutoff:
                            pre_buffer.popleft()

                # Check if speech ended (enough silence after speech)
                silence_ms = silence_frames / frames_per_ms
                buffer_ms = (len(audio_buffer) / 2) / frames_per_ms

                if is_speaking and silence_ms >= SILENCE_DURATION_MS and buffer_ms >= MIN_AUDIO_MS:
                    pcm_data = bytes(audio_buffer)
                    audio_buffer.clear()
                    silence_frames = 0
                    is_speaking = False

                    logger.info("Processing %d ms of user audio", int(buffer_ms))

                    try:
                        speaking_state["active"] = True

                        async def publish_sentence(wav_data: bytes):
                            await _publish_audio_response(audio_source, wav_data)

                        async def publish_transcript(role: str, content: str):
                            """Send transcript message via data channel immediately."""
                            await room.local_participant.publish_data(
                                json.dumps({"type": "transcript", "role": role, "content": content}).encode(),
                                reliable=True,
                            )

                        user_text, assistant_text = await agent.handle_user_audio_streaming(
                            pcm_data,
                            publish_fn=publish_sentence,
                            transcript_fn=publish_transcript,
                        )

                        speaking_state["active"] = False
                        speaking_state["cooldown_until"] = _time.monotonic() + 0.5
                        audio_buffer.clear()
                        silence_frames = 0
                    except Exception:
                        speaking_state["active"] = False
                        logger.exception("Agent pipeline error")

                # Prevent unbounded buffer growth when no speech detected
                elif not is_speaking and len(audio_buffer) > 0:
                    audio_buffer.clear()
                    silence_frames = 0

        async def _publish_audio_response(source: rtc.AudioSource, wav_data: bytes):
            """Parse WAV bytes and publish as LiveKit AudioFrames."""
            import io
            import wave

            if not wav_data:
                logger.error("_publish_audio_response called with empty wav_data")
                return

            try:
                with wave.open(io.BytesIO(wav_data), "rb") as wf:
                    sr = wf.getframerate()
                    nch = wf.getnchannels()
                    sw = wf.getsampwidth()
                    raw = wf.readframes(wf.getnframes())

                logger.debug(
                    "Publishing audio: sr=%d, nch=%d, sw=%d, raw_bytes=%d",
                    sr, nch, sw, len(raw),
                )

                if sw != 2:
                    logger.error(
                        "Unsupported sample width %d (expected 2). Skipping.", sw
                    )
                    return

                # Convert to the agent's sample rate if needed
                samples = np.frombuffer(raw, dtype=np.int16)
                if nch > 1:
                    samples = samples[::nch]  # Take first channel

                if sr != AGENT_SAMPLE_RATE:
                    ratio = AGENT_SAMPLE_RATE / sr
                    new_len = int(len(samples) * ratio)
                    indices = np.linspace(0, len(samples) - 1, new_len)
                    samples = np.interp(indices, np.arange(len(samples)), samples.astype(np.float32)).astype(np.int16)
                    logger.debug("Resampled %d → %d samples", new_len, len(samples))

                # Verify audio has actual content (not silent)
                rms = float(np.sqrt(np.mean(samples.astype(np.float32) ** 2)))
                peak = int(np.max(np.abs(samples))) if len(samples) > 0 else 0
                if peak == 0:
                    logger.error("TTS audio is completely silent — peak=0")
                    return
                if rms < 50:
                    logger.warning(
                        "TTS audio is very quiet: rms=%.1f peak=%d", rms, peak
                    )

                # Publish in chunks (20ms frames)
                chunk_samples = AGENT_SAMPLE_RATE // 50  # 20ms
                frame_count = 0
                for i in range(0, len(samples), chunk_samples):
                    chunk = samples[i:i + chunk_samples]
                    if len(chunk) < chunk_samples:
                        chunk = np.pad(chunk, (0, chunk_samples - len(chunk)))

                    frame = rtc.AudioFrame.create(
                        sample_rate=AGENT_SAMPLE_RATE,
                        num_channels=1,
                        samples_per_channel=len(chunk),
                    )
                    # frame.data is a memoryview of int16 ('h' format)
                    frame.data[:len(chunk)] = memoryview(chunk)

                    await source.capture_frame(frame)
                    frame_count += 1

                await source.wait_for_playout()
                logger.info(
                    "Agent audio published: %d frames, %.2fs, rms=%.1f peak=%d",
                    frame_count, len(samples) / AGENT_SAMPLE_RATE, rms, peak,
                )

            except Exception:
                logger.exception("Failed to publish audio response")

        # Keep the agent alive until disconnected
        await asyncio.wait_for(
            _wait_for_disconnect(connected),
            timeout=3600,  # Max 1 hour session
        )

    except asyncio.TimeoutError:
        logger.info("Voice session timed out for room %s", room_name)
    except Exception as e:
        logger.exception("Failed to connect agent to room %s", room_name)
        raise RuntimeError(f"Failed to connect to LiveKit room: {e}") from e
    finally:
        if room.isconnected:
            await room.disconnect()
        logger.info("Agent disconnected from room %s", room_name)


async def _wait_for_disconnect(connected: asyncio.Event) -> None:
    """Wait until the connected event is cleared."""
    while connected.is_set():
        await asyncio.sleep(0.5)

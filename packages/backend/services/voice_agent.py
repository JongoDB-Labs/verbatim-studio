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
    "project_search",
    "global_search",
    "summarize_transcript",
    "get_recording_info",
    "get_context",
]

AGENT_INSTRUCTIONS = """\
You are Max, a concise voice assistant for Verbatim Studio.

Key rules:
- Keep responses SHORT and conversational (1-3 sentences).
- You are speaking out loud, so avoid markdown, bullet points, or long lists.
- Use tools to look up data rather than guessing. Say "let me check" before calling a tool.
- When reporting tool results, summarize the key info in a natural spoken style.
- If something fails, say so briefly and suggest what the user can try.
- Be friendly but efficient. The user is busy.
"""

# ---------------------------------------------------------------------------
# STT Adapter — wraps existing Whisper transcription engine
# ---------------------------------------------------------------------------


class WhisperSTTAdapter:
    """Bridges the Verbatim ITranscriptionEngine to LiveKit's STT interface.

    LiveKit Agents expects an STT that can process audio frames. Our Whisper
    engine expects a file path, so we buffer incoming audio to a temp WAV file
    and run transcription on it.

    TODO: The exact LiveKit STT adapter interface (base class, method
    signatures) needs validation against the livekit-agents SDK. This
    implementation assumes a recognize() method that receives audio frames.
    """

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

        # Write audio to temp file
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
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

    async def chat(self, messages: list[dict[str, str]]) -> str:
        """Send a chat request and return the response text.

        Converts plain dicts to ChatMessage objects for the underlying service.

        Args:
            messages: List of {"role": ..., "content": ...} dicts.

        Returns:
            Response text from the LLM.
        """
        from core.interfaces.ai import ChatMessage, ChatOptions

        chat_messages = [
            ChatMessage(role=m["role"], content=m["content"])
            for m in messages
        ]
        options = ChatOptions(temperature=0.7, max_tokens=300)
        response = await self._ai_service.chat(chat_messages, options)
        return response.content

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
        options = ChatOptions(temperature=0.7, max_tokens=300)
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

    def __init__(self, tts_service: ITTSService) -> None:
        self._tts_service = tts_service

    async def synthesize(self, text: str) -> bytes:
        """Synthesize speech from text.

        Args:
            text: Text to convert to speech.

        Returns:
            Audio data as bytes (WAV format from Qwen3-TTS).
        """
        return await self._tts_service.synthesize(text, voice="default")


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


def get_voice_tools_prompt() -> str:
    """Generate a system-prompt section describing available voice tools.

    Returns a concise tool description string suitable for injection into
    the LLM system prompt during voice sessions.
    """
    definitions = get_voice_tool_definitions()
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
    ) -> None:
        self.stt = stt
        self.llm = llm
        self.tts = tts
        self.instructions = AGENT_INSTRUCTIONS + get_voice_tools_prompt()
        self._conversation: list[dict[str, str]] = [
            {"role": "system", "content": self.instructions},
        ]
        self._tool_context: Any = None

    def set_tool_context(self, ctx: Any) -> None:
        """Set the ToolContext for tool execution during this session.

        Args:
            ctx: A ToolContext instance with db session, project info, etc.
        """
        self._tool_context = ctx

    async def handle_user_audio(self, audio_data: bytes) -> bytes | None:
        """Process a chunk of user audio through the full pipeline.

        STT -> LLM (with optional tool calls) -> TTS

        Args:
            audio_data: Raw PCM audio from the user.

        Returns:
            Synthesized audio response bytes, or None if no response.
        """
        # Step 1: Speech-to-text
        user_text = await self.stt.recognize(audio_data)
        if not user_text.strip():
            return None

        logger.info("Voice STT result: %s", user_text[:100])

        # Step 2: LLM response (with tool call handling)
        self._conversation.append({"role": "user", "content": user_text})
        response_text = await self.llm.chat(self._conversation)
        self._conversation.append({"role": "assistant", "content": response_text})

        # Step 3: Check for tool calls in the response
        response_text = await self._handle_tool_calls(response_text)

        # Step 4: Text-to-speech
        if response_text.strip():
            audio_response = await self.tts.synthesize(response_text)
            return audio_response

        return None

    async def _handle_tool_calls(self, response_text: str) -> str:
        """Detect and execute tool calls embedded in LLM output.

        If the response contains a JSON tool call, execute it and feed
        the result back to the LLM for a natural language summary.

        Args:
            response_text: The raw LLM response that may contain tool calls.

        Returns:
            Final response text (either original or post-tool summary).
        """
        # Simple JSON tool call detection
        try:
            # Look for {"tool": "...", "args": {...}} pattern
            import re
            match = re.search(r'\{[^{}]*"tool"\s*:\s*"[^"]+?"[^{}]*\}', response_text)
            if not match:
                return response_text

            call_data = json.loads(match.group())
            tool_name = call_data.get("tool")
            tool_args = call_data.get("args", {})

            if tool_name not in VOICE_TOOLS:
                return response_text

            logger.info("Voice agent calling tool: %s(%s)", tool_name, tool_args)

            # Execute the tool
            tool_result = await execute_tool(
                tool_name, tool_args, ctx=self._tool_context
            )

            # Feed result back to LLM for spoken summary
            self._conversation.append({
                "role": "user",
                "content": f"Tool result for {tool_name}:\n{tool_result}\n\n"
                "Summarize this result conversationally in 1-2 sentences.",
            })

            summary = await self.llm.chat(self._conversation)
            self._conversation.append({"role": "assistant", "content": summary})

            return summary

        except (json.JSONDecodeError, KeyError, TypeError):
            # Not a valid tool call, return original response
            return response_text


# ---------------------------------------------------------------------------
# Factory: create_agent_session()
# ---------------------------------------------------------------------------


def _get_tts_service() -> ITTSService:
    """Get the active TTS service instance.

    Reads the active TTS model from the voice route helpers and creates
    the Qwen3-TTS service.

    Returns:
        Configured Qwen3TTSService instance.

    Raises:
        RuntimeError: If no TTS model is active or downloaded.
    """
    from api.routes.voice import _get_active_tts_model, _tts_model_dir
    from adapters.ai.qwen3_tts import get_tts_service

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

    return get_tts_service(str(model_dir))


def create_agent_session() -> VerbatimVoiceAgent:
    """Factory that creates a fully configured VerbatimVoiceAgent.

    Wires together:
    - STT: Whisper via the adapter factory
    - LLM: Granite / llama.cpp via the adapter factory
    - TTS: Qwen3-TTS via the active model
    - Tools: Bridged from the existing ToolRegistry

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

    # Create LLM adapter wrapping Granite / llama.cpp
    try:
        ai_service = factory.create_ai_service()
        llm = GraniteLLMAdapter(ai_service)
        logger.info("Voice LLM adapter created (llama.cpp)")
    except Exception as e:
        raise RuntimeError(f"Failed to create LLM adapter: {e}") from e

    # Create TTS adapter wrapping Qwen3-TTS
    try:
        tts_service = _get_tts_service()
        tts = Qwen3TTSAdapter(tts_service)
        logger.info("Voice TTS adapter created (Qwen3-TTS)")
    except Exception as e:
        raise RuntimeError(f"Failed to create TTS adapter: {e}") from e

    # Create the agent
    agent = VerbatimVoiceAgent(stt=stt, llm=llm, tts=tts)
    logger.info("VerbatimVoiceAgent created successfully")

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
    """Connect the voice agent to a LiveKit room.

    This is the integration point where the VerbatimVoiceAgent is wired
    into the LiveKit real-time infrastructure.

    TODO: The exact LiveKit Agents SDK API for connecting an agent to a
    room needs validation. This implementation is a best-effort sketch
    based on expected SDK patterns. Key areas to validate:
    - How to join a room as an agent participant
    - How to subscribe to audio tracks
    - How to publish audio tracks
    - The event loop / callback model for audio processing

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

    try:
        from livekit import rtc

        # TODO: Validate exact LiveKit RTC API for room connection
        room = rtc.Room()

        await room.connect(url, token)
        logger.info("Voice agent connected to room: %s", room_name)

        # TODO: Set up audio track subscription and processing callbacks.
        # The exact API for:
        #   - Subscribing to participant audio tracks
        #   - Processing incoming audio frames via agent.stt.recognize()
        #   - Publishing synthesized audio via agent.tts.synthesize()
        # needs validation against the LiveKit Python SDK.

        @room.on("track_subscribed")
        def on_track_subscribed(track, publication, participant):
            if track.kind == rtc.TrackKind.KIND_AUDIO:
                logger.info(
                    "Subscribed to audio track from %s",
                    participant.identity,
                )
                # TODO: Set up audio frame processing pipeline
                # audio_stream = rtc.AudioStream(track)
                # Process frames through agent.handle_user_audio()

    except Exception as e:
        logger.exception("Failed to connect agent to room %s", room_name)
        raise RuntimeError(f"Failed to connect to LiveKit room: {e}") from e

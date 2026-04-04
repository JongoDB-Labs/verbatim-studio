"""Qwen3-TTS adapter for local text-to-speech via MLX.

Implements ITTSService for Apple Silicon inference using Qwen3-TTS MLX models.
Uses a singleton pattern with lazy loading, matching the llama_cpp adapter style.

NOTE: The exact Qwen3-TTS MLX generation API (model.generate / processor calls)
needs validation during integration testing. The inference code below is based on
the expected MLX-LM + transformers pipeline but may require adjustments once
tested against actual Qwen3-TTS-MLX weights.
"""

import asyncio
import io
import logging
import wave
from collections.abc import AsyncIterator

import numpy as np

from core.interfaces.tts import ITTSService

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level singleton — mirrors llama_cpp.py pattern
# ---------------------------------------------------------------------------
_tts_service: "Qwen3TTSService | None" = None
_tts_model_path: str | None = None

SAMPLE_RATE = 24000


def get_tts_service(model_path: str) -> "Qwen3TTSService":
    """Get a cached Qwen3TTSService, creating or replacing as needed.

    If *model_path* differs from the currently cached instance the old
    model is unloaded and a fresh service is returned.

    Args:
        model_path: Path to the MLX model directory.

    Returns:
        Cached or newly created Qwen3TTSService instance.
    """
    global _tts_service, _tts_model_path

    if model_path != _tts_model_path:
        if _tts_service is not None:
            logger.info(
                "TTS model path changed (%s -> %s), reloading",
                _tts_model_path,
                model_path,
            )
            _cleanup_model()
        _tts_model_path = model_path

    if _tts_service is None:
        logger.info("Creating new Qwen3TTSService (model_path=%s)", model_path)
        _tts_service = Qwen3TTSService(model_path)

    return _tts_service


def cleanup_tts_service() -> None:
    """Unload the cached TTS model and free memory.

    Clears the singleton, runs garbage collection, and empties the
    MLX Metal cache (similar to the MPS cache clearing in llama_cpp).
    """
    global _tts_service, _tts_model_path

    _cleanup_model()
    _tts_model_path = None


def _cleanup_model() -> None:
    """Internal helper — tear down model objects and reclaim memory."""
    import gc

    global _tts_service

    if _tts_service is not None:
        logger.info("Unloading Qwen3-TTS model to free memory")
        if _tts_service._model is not None:
            del _tts_service._model
            _tts_service._model = None
        if _tts_service._processor is not None:
            del _tts_service._processor
            _tts_service._processor = None
        del _tts_service
        _tts_service = None

    gc.collect()

    # Clear MLX Metal cache (Apple Silicon VRAM)
    try:
        import mlx.core as mx

        mx.metal.clear_cache()
        logger.debug("MLX Metal cache cleared")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Preset voices
# ---------------------------------------------------------------------------
_PRESET_VOICES: list[dict] = [
    {
        "id": "default",
        "name": "Default Narrator",
        "description": "A clear, neutral English narrator voice with a natural pace.",
        "prompt": "A clear, neutral English narrator voice with a natural pace.",
    },
    {
        "id": "warm-female",
        "name": "Warm Female",
        "description": "A warm, friendly female voice. Calm and conversational.",
        "prompt": "A warm, friendly female voice. Calm and conversational tone.",
    },
    {
        "id": "professional-male",
        "name": "Professional Male",
        "description": "A confident, professional male voice. Authoritative and measured.",
        "prompt": "A confident, professional male voice. Authoritative and measured pace.",
    },
]


def _get_voice_prompt(voice: str | None) -> str:
    """Resolve a voice identifier to its prompt string.

    Falls back to the default voice when *voice* is ``None`` or not found.
    """
    if voice is None:
        voice = "default"
    for v in _PRESET_VOICES:
        if v["id"] == voice:
            return v["prompt"]
    # Unknown id — treat the raw string as a freeform voice description
    return voice


# ---------------------------------------------------------------------------
# Qwen3TTSService
# ---------------------------------------------------------------------------
class Qwen3TTSService(ITTSService):
    """Qwen3-TTS adapter using MLX for Apple Silicon inference.

    Lazy-loads the model on first synthesis request.  The model is kept
    resident until :func:`cleanup_tts_service` is called (e.g. when the
    user switches models or the app shuts down).
    """

    def __init__(self, model_path: str) -> None:
        self._model_path = model_path
        self._model = None
        self._processor = None

    # -- lazy loading -------------------------------------------------------

    def _ensure_loaded(self) -> None:
        """Load model + processor if not already in memory."""
        if self._model is not None:
            return

        logger.info("Loading Qwen3-TTS model from %s …", self._model_path)

        try:
            from transformers import AutoProcessor
        except ImportError as exc:
            raise ImportError(
                "transformers is required for Qwen3-TTS. "
                "Install with: pip install transformers"
            ) from exc

        try:
            from mlx_lm import load as mlx_load
        except ImportError as exc:
            raise ImportError(
                "mlx-lm is required for Qwen3-TTS on Apple Silicon. "
                "Install with: pip install mlx-lm"
            ) from exc

        self._model, _ = mlx_load(self._model_path)
        self._processor = AutoProcessor.from_pretrained(
            self._model_path, trust_remote_code=True
        )

        logger.info("Qwen3-TTS model loaded successfully")

    # -- ITTSService implementation -----------------------------------------

    async def synthesize(
        self,
        text: str,
        voice: str | None = None,
    ) -> bytes:
        """Synthesize speech from *text*, returning complete WAV bytes."""
        return await asyncio.to_thread(self._synthesize_sync, text, voice)

    def _synthesize_sync(self, text: str, voice: str | None) -> bytes:
        """Run TTS inference synchronously (called via ``asyncio.to_thread``).

        NOTE: The exact generate / decode calls below are best-effort based
        on the expected Qwen3-TTS MLX pipeline.  They may need adjustment
        once validated against actual model weights during integration
        testing.
        """
        import mlx.core as mx

        self._ensure_loaded()

        voice_prompt = _get_voice_prompt(voice)

        # Build the chat-style prompt expected by Qwen3-TTS
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": voice_prompt},
                    {"type": "text", "text": text},
                ],
            }
        ]

        # Tokenize via the processor's chat template
        prompt_text = self._processor.apply_chat_template(
            messages, add_generation_prompt=True, tokenize=False
        )
        inputs = self._processor(
            prompt_text, return_tensors="np", add_special_tokens=False
        )

        # Convert numpy inputs to MLX arrays
        input_ids = mx.array(inputs["input_ids"])

        # --- Generate audio tokens ---
        # The exact generation API depends on the MLX model wrapper.
        # This is the expected call pattern; adjust during integration.
        output_ids = self._model.generate(
            input_ids,
            max_tokens=2048,
            temp=0.0,
        )

        # Decode generated token IDs into audio waveform
        # Qwen3-TTS models produce codec tokens that the processor
        # decodes into a float32 waveform.
        audio_float = self._processor.decode_audio(output_ids, sample_rate=SAMPLE_RATE)

        if isinstance(audio_float, mx.array):
            audio_float = np.array(audio_float, dtype=np.float32)
        elif not isinstance(audio_float, np.ndarray):
            audio_float = np.asarray(audio_float, dtype=np.float32)

        # Clip and convert to 16-bit PCM
        audio_float = np.clip(audio_float, -1.0, 1.0)
        audio_int16 = (audio_float * 32767).astype(np.int16)

        return self._to_wav(audio_int16)

    @staticmethod
    def _to_wav(audio_int16: np.ndarray) -> bytes:
        """Pack a 16-bit PCM numpy array into a WAV file (RIFF) in memory.

        Args:
            audio_int16: Mono 16-bit integer samples at :data:`SAMPLE_RATE`.

        Returns:
            Complete WAV file as ``bytes``.
        """
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)  # 16-bit
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(audio_int16.tobytes())
        return buf.getvalue()

    async def synthesize_stream(
        self,
        text: str,
        voice: str | None = None,
    ) -> AsyncIterator[bytes]:
        """Stream synthesized audio.

        Initial implementation: generates the full audio and yields it as
        a single chunk.  True streaming can be added later if the MLX
        model supports incremental decoding.
        """
        full_audio = await self.synthesize(text, voice)
        yield full_audio

    async def list_voices(self) -> list[dict]:
        """Return the built-in preset voices."""
        return list(_PRESET_VOICES)

    async def is_available(self) -> bool:
        """Return ``True`` if the model is loaded and ready."""
        return self._model is not None

    async def load(self, model_path: str) -> None:
        """Load (or switch to) a TTS model at *model_path*.

        Forces a reload even if a model is already resident.
        Inlines teardown to avoid orphaning self from the module singleton.
        """
        import gc

        if self._model is not None:
            del self._model
            self._model = None
        if self._processor is not None:
            del self._processor
            self._processor = None
        gc.collect()

        self._model_path = model_path
        await asyncio.to_thread(self._ensure_loaded)

    async def unload(self) -> None:
        """Unload the model and free memory."""
        cleanup_tts_service()

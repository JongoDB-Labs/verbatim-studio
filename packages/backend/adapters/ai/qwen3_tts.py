"""Qwen3-TTS adapter for local text-to-speech via MLX.

Implements ITTSService for Apple Silicon inference using Qwen3-TTS MLX models.
Uses a singleton pattern with lazy loading, matching the llama_cpp adapter style.

Uses the mlx-audio library for model loading and generation.
"""

import asyncio
import io
import logging
import wave
from collections.abc import AsyncIterator
from pathlib import Path

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
    {"id": "serena", "name": "Serena", "description": "Calm female voice"},
    {"id": "vivian", "name": "Vivian", "description": "Warm female voice"},
    {"id": "ryan", "name": "Ryan", "description": "Natural male English voice"},
    {"id": "aiden", "name": "Aiden", "description": "Young male voice"},
    {"id": "dylan", "name": "Dylan", "description": "Male voice (Beijing accent)"},
    {"id": "eric", "name": "Eric", "description": "Male voice (Sichuan accent)"},
    {"id": "ono_anna", "name": "Ono Anna", "description": "Japanese female voice"},
    {"id": "sohee", "name": "Sohee", "description": "Korean female voice"},
]


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

    # -- lazy loading -------------------------------------------------------

    def _ensure_loaded(self) -> None:
        """Load model if not already in memory."""
        if self._model is not None:
            return

        logger.info("Loading Qwen3-TTS model from %s …", self._model_path)

        try:
            from mlx_audio.tts.utils import load_model
        except ImportError as exc:
            raise ImportError(
                "mlx-audio is required for Qwen3-TTS on Apple Silicon. "
                "Install with: pip install 'mlx-audio[tts]'"
            ) from exc

        self._model = load_model(self._model_path)

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
        """Run TTS inference synchronously (called via ``asyncio.to_thread``)."""
        self._ensure_loaded()

        # Use ICL (voice cloning) with a reference clip to ensure consistent
        # voice across all generations. Without this, the Base model produces
        # a different voice each time.
        ref_audio_path = str(Path(self._model_path).parent / "max_voice_ref.wav")
        ref_text = "I'm very sorry I can't be with you all today and such an important gathering. Some have speculated that I've seen more of the natural world than anyone else."

        generate_kwargs: dict = {
            "text": text,
            "lang_code": "auto",
            "speed": 1.3,
        }

        if Path(ref_audio_path).exists():
            generate_kwargs["ref_audio"] = ref_audio_path
            generate_kwargs["ref_text"] = ref_text

        results = list(self._model.generate(**generate_kwargs))

        audio = results[0].audio  # mx.array waveform

        # Convert mx.array to numpy, then to WAV bytes
        audio_np = np.array(audio, dtype=np.float32)

        # Normalize and convert to int16
        if audio_np.max() > 0:
            audio_np = audio_np / max(abs(audio_np.max()), abs(audio_np.min()))
        audio_int16 = (audio_np * 32767).astype(np.int16)

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
        gc.collect()

        self._model_path = model_path
        await asyncio.to_thread(self._ensure_loaded)

    async def unload(self) -> None:
        """Unload the model and free memory."""
        cleanup_tts_service()

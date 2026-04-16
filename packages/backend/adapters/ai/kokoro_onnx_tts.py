"""Kokoro ONNX TTS adapter for cross-platform text-to-speech.

Implements ITTSService using kokoro-onnx for Windows/Linux inference.
Uses ONNX Runtime with optional CUDA support for GPU acceleration.
Mirrors the singleton + lazy-load pattern from qwen3_tts.py.
"""

import asyncio
import io
import logging
import wave
from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

from core.interfaces.tts import ITTSService

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level singleton — mirrors qwen3_tts.py pattern
# ---------------------------------------------------------------------------
_tts_service: "KokoroOnnxTTSService | None" = None
_tts_model_path: str | None = None

SAMPLE_RATE = 24000


def get_tts_service(model_path: str) -> "KokoroOnnxTTSService":
    """Get a cached KokoroOnnxTTSService, creating or replacing as needed.

    Args:
        model_path: Path to the directory containing kokoro ONNX model files.

    Returns:
        Cached or newly created KokoroOnnxTTSService instance.
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
        logger.info("Creating new KokoroOnnxTTSService (model_path=%s)", model_path)
        _tts_service = KokoroOnnxTTSService(model_path)

    return _tts_service


def cleanup_tts_service() -> None:
    """Unload the cached TTS model and free memory."""
    global _tts_service, _tts_model_path

    _cleanup_model()
    _tts_model_path = None


def _cleanup_model() -> None:
    """Internal helper — tear down model objects and reclaim memory."""
    import gc

    global _tts_service

    if _tts_service is not None:
        logger.info("Unloading Kokoro ONNX TTS model to free memory")
        if _tts_service._kokoro is not None:
            del _tts_service._kokoro
            _tts_service._kokoro = None
        del _tts_service
        _tts_service = None

    gc.collect()


# ---------------------------------------------------------------------------
# Preset voices — subset of Kokoro's 26 built-in voices
# ---------------------------------------------------------------------------
_PRESET_VOICES: list[dict] = [
    {"id": "af_sarah", "name": "Sarah", "description": "American female voice"},
    {"id": "af_bella", "name": "Bella", "description": "American female voice"},
    {"id": "af_nicole", "name": "Nicole", "description": "American female voice"},
    {"id": "am_adam", "name": "Adam", "description": "American male voice"},
    {"id": "am_michael", "name": "Michael", "description": "American male voice"},
    {"id": "bf_emma", "name": "Emma", "description": "British female voice"},
    {"id": "bm_lewis", "name": "Lewis", "description": "British male voice"},
    {"id": "bm_george", "name": "George", "description": "British male voice"},
]

DEFAULT_VOICE = "bm_lewis"
DEFAULT_LANG = "b"  # British English


# ---------------------------------------------------------------------------
# KokoroOnnxTTSService
# ---------------------------------------------------------------------------
class KokoroOnnxTTSService(ITTSService):
    """Kokoro ONNX TTS adapter for cross-platform inference.

    Lazy-loads the model on first synthesis request. The model is kept
    resident until :func:`cleanup_tts_service` is called.
    """

    def __init__(self, model_path: str) -> None:
        self._model_path = model_path
        self._kokoro = None

    # -- lazy loading -------------------------------------------------------

    def _ensure_loaded(self) -> None:
        """Load model if not already in memory."""
        if self._kokoro is not None:
            return

        logger.info("Loading Kokoro ONNX TTS model from %s …", self._model_path)

        try:
            from kokoro_onnx import Kokoro
        except ImportError as exc:
            raise ImportError(
                "kokoro-onnx is required for TTS. "
                "Install with: pip install kokoro-onnx"
            ) from exc

        model_dir = Path(self._model_path)

        # Find model and voices files in the directory
        onnx_file = self._find_file(model_dir, "*.onnx")
        voices_file = self._find_file(model_dir, "voices*.bin")

        if not onnx_file:
            raise FileNotFoundError(
                f"No .onnx model file found in {model_dir}"
            )
        if not voices_file:
            raise FileNotFoundError(
                f"No voices .bin file found in {model_dir}"
            )

        self._kokoro = Kokoro(str(onnx_file), str(voices_file))

        logger.info("Kokoro ONNX TTS model loaded successfully")

    @staticmethod
    def _find_file(directory: Path, pattern: str) -> Path | None:
        """Find first file matching glob pattern in directory."""
        matches = list(directory.glob(pattern))
        return matches[0] if matches else None

    # -- ITTSService implementation -----------------------------------------

    _tts_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="tts")

    async def synthesize(
        self,
        text: str,
        voice: str | None = None,
    ) -> bytes:
        """Synthesize speech from *text*, returning complete WAV bytes."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._tts_executor, self._synthesize_sync, text, voice
        )

    def _synthesize_sync(self, text: str, voice: str | None) -> bytes:
        """Run TTS inference synchronously."""
        self._ensure_loaded()

        voice = voice or DEFAULT_VOICE
        # Detect language from voice prefix
        lang = "b" if voice.startswith("b") else "a"

        samples, sample_rate = self._kokoro.create(
            text=text,
            voice=voice,
            speed=1.1,
            lang=f"en-{'gb' if lang == 'b' else 'us'}",
        )

        audio_np = np.array(samples, dtype=np.float32)
        if audio_np.max() > 0:
            audio_np = audio_np / max(abs(audio_np.max()), abs(audio_np.min()))
        audio_int16 = (audio_np * 32767).astype(np.int16)

        return self._to_wav(audio_int16)

    @staticmethod
    def _to_wav(audio_int16: np.ndarray) -> bytes:
        """Pack a 16-bit PCM numpy array into a WAV file in memory."""
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

        Uses kokoro-onnx's native async streaming when available,
        falls back to single-chunk for compatibility.
        """
        full_audio = await self.synthesize(text, voice)
        yield full_audio

    async def list_voices(self) -> list[dict]:
        """Return the built-in preset voices."""
        return list(_PRESET_VOICES)

    async def is_available(self) -> bool:
        """Return ``True`` if the model is loaded and ready."""
        return self._kokoro is not None

    async def load(self, model_path: str) -> None:
        """Load (or switch to) a TTS model at *model_path*."""
        import gc

        if self._kokoro is not None:
            del self._kokoro
            self._kokoro = None
        gc.collect()

        self._model_path = model_path
        await asyncio.to_thread(self._ensure_loaded)

    async def unload(self) -> None:
        """Unload the model and free memory."""
        cleanup_tts_service()

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
from concurrent.futures import ThreadPoolExecutor
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
# Single voice per engine — Max is the Verbatim Studio assistant identity.
# Qwen3 clones from a bundled reference WAV; Kokoro uses a single preset.
# ---------------------------------------------------------------------------
# Qwen3 always speaks as "Max" via the bundled voice_clones/max.wav reference
_QWEN3_PRESET_VOICES: list[dict] = [
    {"id": "max", "name": "Max", "description": "Verbatim assistant voice (cloned)"},
]

# Kokoro fallback for systems without Qwen3 — single American male voice
_KOKORO_PRESET_VOICES: list[dict] = [
    {"id": "am_adam", "name": "Max", "description": "Verbatim assistant voice"},
]

# Backwards-compatible alias for code that imports _PRESET_VOICES directly.
_PRESET_VOICES = _QWEN3_PRESET_VOICES


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
        # Detect engine from path or catalog
        self._engine = "kokoro" if "kokoro" in model_path.lower() else "qwen3"

    # -- lazy loading -------------------------------------------------------

    def _ensure_loaded(self) -> None:
        """Load model if not already in memory."""
        if self._model is not None:
            return

        logger.info("Loading TTS model (%s) from %s …", self._engine, self._model_path)

        try:
            from mlx_audio.tts.utils import load_model
        except ImportError as exc:
            raise ImportError(
                "mlx-audio is required for TTS on Apple Silicon. "
                "Install with: pip install 'mlx-audio[tts]'"
            ) from exc

        self._model = load_model(self._model_path)

        logger.info("TTS model loaded successfully (%s)", self._engine)

    # -- ITTSService implementation -----------------------------------------

    # Dedicated thread pool so TTS doesn't compete with LLM for the default pool
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
        """Run TTS inference synchronously (called via ``asyncio.to_thread``)."""
        self._ensure_loaded()

        if self._engine == "kokoro":
            return self._synthesize_kokoro(text, voice)
        else:
            return self._synthesize_qwen3(text, voice)

    # -- Kokoro voice handling ----------------------------------------------

    _KOKORO_DEFAULT_VOICE = "af_heart"

    def _resolve_kokoro_voice(self, voice: str | None) -> str:
        """Resolve a frontend voice ID to a Kokoro voice file name.

        Accepts native Kokoro voice IDs (af_heart, bm_lewis, etc.) directly.
        Returns the default voice when the requested one isn't available.
        """
        if not voice:
            return self._KOKORO_DEFAULT_VOICE

        # If the voice file exists in the model's voices/ dir, use it directly
        voices_dir = Path(self._model_path) / "voices"
        for ext in (".pt", ".safetensors"):
            if (voices_dir / f"{voice}{ext}").exists():
                return voice

        # Frontend may send Qwen3 preset names (serena, vivian, etc.) when
        # the engine doesn't match — fall back to the default voice.
        logger.warning(
            "Kokoro voice '%s' not found in %s, using default '%s'",
            voice, voices_dir, self._KOKORO_DEFAULT_VOICE,
        )
        return self._KOKORO_DEFAULT_VOICE

    def _synthesize_kokoro(self, text: str, voice: str | None) -> bytes:
        """Synthesize via Kokoro 82M — ultra-fast.

        Uses the voice file from the model's voices/ subdirectory.
        Voice ID prefix determines language: 'a*' = American, 'b*' = British.
        """
        resolved_voice = self._resolve_kokoro_voice(voice)
        # Kokoro voice IDs encode language in the prefix (a/b/e/f/h/i/j/p/z)
        lang_code = resolved_voice[0] if resolved_voice else "a"

        logger.info(
            "Kokoro synthesize: voice=%s, lang_code=%s, text=%r",
            resolved_voice, lang_code, text[:60],
        )

        results = list(self._model.generate(
            text=text,
            voice=resolved_voice,
            lang_code=lang_code,
            speed=1.1,
        ))

        if not results:
            logger.error("Kokoro generate returned no results for text=%r", text[:60])
            return b""

        audio = results[0].audio
        audio_np = np.array(audio, dtype=np.float32)

        peak = max(abs(audio_np.max()), abs(audio_np.min()))
        if peak > 0:
            audio_np = audio_np / peak
        audio_int16 = (audio_np * 32767).astype(np.int16)

        wav_bytes = self._to_wav(audio_int16)
        logger.debug(
            "Kokoro synth complete: %d samples, peak=%.3f, %d wav bytes",
            len(audio_np), peak, len(wav_bytes),
        )
        return wav_bytes

    # -- Qwen3 voice cloning ------------------------------------------------

    def _find_max_voice(self) -> tuple[Path | None, str | None]:
        """Locate the bundled "Max" reference audio for Qwen3 cloning.

        The Max voice is the single Verbatim assistant identity, bundled
        with the app at build time and migrated to user data on first
        launch. Returns (path, transcript) — transcript loaded from
        sibling max.txt if present.
        """
        tts_root = Path(self._model_path).parent  # {models_dir}/tts
        wav_path = tts_root / "voice_clones" / "max.wav"

        if not wav_path.is_file():
            return None, None

        txt_path = wav_path.with_suffix(".txt")
        ref_text = None
        if txt_path.is_file():
            try:
                ref_text = txt_path.read_text(encoding="utf-8").strip()
            except OSError:
                pass
        return wav_path, ref_text

    def _synthesize_qwen3(self, text: str, voice: str | None) -> bytes:
        """Synthesize via Qwen3-TTS 1.7B — always clones the bundled Max voice.

        Without the reference audio Qwen3 generates a different random
        voice for every utterance, so the bundled max.wav is required
        for the assistant to have a consistent identity.
        """
        ref_audio, ref_text = self._find_max_voice()

        generate_kwargs: dict = {
            "text": text,
            "lang_code": "auto",
            "speed": 1.3,
        }

        if ref_audio is not None:
            generate_kwargs["ref_audio"] = str(ref_audio)
            if ref_text:
                generate_kwargs["ref_text"] = ref_text
            logger.info(
                "Qwen3 synthesize as Max (ref=%s, ref_text=%s)",
                ref_audio.name, "yes" if ref_text else "no",
            )
        else:
            logger.error(
                "Qwen3 voice reference missing at %s/voice_clones/max.wav — "
                "voice will be random. The bundled max.wav should have been "
                "migrated on first launch; reinstall the app or place the "
                "file manually.",
                Path(self._model_path).parent,
            )

        results = list(self._model.generate(**generate_kwargs))

        if not results:
            logger.error("Qwen3 generate returned no results for text=%r", text[:60])
            return b""

        audio = results[0].audio
        audio_np = np.array(audio, dtype=np.float32)

        peak = max(abs(audio_np.max()), abs(audio_np.min()))
        if peak > 0:
            audio_np = audio_np / peak
        audio_int16 = (audio_np * 32767).astype(np.int16)

        wav_bytes = self._to_wav(audio_int16)
        logger.debug(
            "Qwen3 synth complete: %d samples, peak=%.3f, %d wav bytes",
            len(audio_np), peak, len(wav_bytes),
        )
        return wav_bytes

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
        """Return preset voices appropriate for the current engine."""
        if self._engine == "kokoro":
            return list(_KOKORO_PRESET_VOICES)
        return list(_QWEN3_PRESET_VOICES)

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

"""WhisperX transcription engine adapter.

Implements ITranscriptionEngine for local transcription using WhisperX
with optional GPU acceleration.
"""

import logging
import threading
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from core.interfaces import (
    ITranscriptionEngine,
    TranscriptionOptions,
    TranscriptionProgress,
    TranscriptionResult,
    TranscriptionSegment,
    TranscriptionWord,
)

logger = logging.getLogger(__name__)

# Available WhisperX model sizes
AVAILABLE_MODELS = ["tiny", "base", "small", "medium", "large-v2", "large-v3"]

# Supported languages (subset - WhisperX supports many more)
SUPPORTED_LANGUAGES = [
    "en", "es", "fr", "de", "it", "pt", "nl", "ru", "zh", "ja", "ko",
    "ar", "hi", "pl", "tr", "vi", "th", "cs", "ro", "hu", "el", "sv",
    "da", "fi", "no", "sk", "uk", "he", "id", "ms", "ca", "hr", "bg",
]


class WhisperXTranscriptionEngine(ITranscriptionEngine):
    """WhisperX-based transcription engine for local processing.

    Uses lazy loading to avoid import errors when WhisperX is not installed.
    Supports GPU acceleration via CUDA and Apple Silicon via MPS.
    """

    # Class-level cache and lock to prevent concurrent model loads across instances
    _model_cache: dict[tuple[str, str, str], Any] = {}
    _model_lock = threading.Lock()

    def __init__(
        self,
        model_size: str = "base",
        device: str = "cpu",
        compute_type: str = "int8",
    ):
        """Initialize the WhisperX transcription engine.

        Args:
            model_size: WhisperX model size (tiny, base, small, medium, large-v2, large-v3)
            device: Device to run inference on (cpu, cuda, mps)
            compute_type: Compute type for inference (int8, float16, float32)
        """
        self._model_size = model_size
        self._device = device
        self._compute_type = compute_type

        # Lazy-loaded components
        self._whisperx = None
        self._model = None
        self._align_model = None
        self._align_metadata = None
        self._align_language: str | None = None

    def _ensure_loaded(self) -> None:
        """Ensure WhisperX is loaded and model is ready.

        Uses a class-level lock + cache so concurrent jobs share one model
        instead of loading duplicates that exhaust GPU memory.
        """
        if self._model is not None:
            return

        with WhisperXTranscriptionEngine._model_lock:
            # Double-check after acquiring lock
            if self._model is not None:
                return

            # Apply PyTorch 2.6+ compatibility patches
            self._patch_torch_load()

            try:
                import whisperx
            except ImportError as e:
                raise ImportError(
                    "WhisperX is not installed. Install with: pip install whisperx"
                ) from e

            self._whisperx = whisperx

            cache_key = (self._model_size, self._device, self._compute_type)
            if cache_key in WhisperXTranscriptionEngine._model_cache:
                logger.info(
                    "Reusing cached WhisperX model: %s (device=%s, compute_type=%s)",
                    self._model_size, self._device, self._compute_type,
                )
                self._model = WhisperXTranscriptionEngine._model_cache[cache_key]
            else:
                logger.info(
                    "Loading WhisperX model: %s (device=%s, compute_type=%s)",
                    self._model_size, self._device, self._compute_type,
                )
                self._model = whisperx.load_model(
                    self._model_size,
                    self._device,
                    compute_type=self._compute_type,
                )
                WhisperXTranscriptionEngine._model_cache[cache_key] = self._model
                logger.info("WhisperX model loaded and cached successfully")

    def _patch_torch_load(self) -> None:
        """Apply PyTorch 2.6+ compatibility patches for pyannote."""
        import torch

        # Only patch if not already patched
        if hasattr(torch.load, "_verbatim_patched"):
            return

        _original_torch_load = torch.load

        def _patched_torch_load(*args, weights_only=None, **kwargs):
            # Force weights_only=False unless caller explicitly passed True.
            # lightning_fabric passes weights_only=None which PyTorch 2.6+ treats as True.
            if not weights_only:
                weights_only = False
            return _original_torch_load(*args, weights_only=weights_only, **kwargs)

        _patched_torch_load._verbatim_patched = True
        torch.load = _patched_torch_load

        # Also patch lightning_fabric if available
        try:
            from lightning_fabric.utilities import cloud_io

            _original_pl_load = cloud_io._load

            def _patched_pl_load(path_or_url, map_location=None, **kwargs):
                kwargs["weights_only"] = False
                return _original_torch_load(path_or_url, map_location=map_location, **kwargs)

            cloud_io._load = _patched_pl_load
        except ImportError:
            pass

    def _load_align_model(self, language: str) -> None:
        """Load alignment model for the specified language."""
        if self._align_language == language and self._align_model is not None:
            return

        logger.info("Loading alignment model for language: %s", language)
        self._align_model, self._align_metadata = self._whisperx.load_align_model(
            language_code=language,
            device=self._device,
        )
        self._align_language = language

    async def transcribe(
        self,
        audio_path: str,
        options: TranscriptionOptions | None = None,
    ) -> TranscriptionResult:
        """Transcribe an audio file."""
        options = options or TranscriptionOptions()
        path = Path(audio_path)

        if not path.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")

        self._ensure_loaded()

        # Load audio
        logger.info("Loading audio: %s", audio_path)
        audio = self._whisperx.load_audio(str(path))

        # Transcribe
        logger.info("Starting transcription...")
        transcribe_kwargs: dict[str, Any] = {
            "batch_size": options.batch_size,
            "language": options.language,
        }
        if options.initial_prompt:
            transcribe_kwargs["initial_prompt"] = options.initial_prompt
        result = self._model.transcribe(audio, **transcribe_kwargs)

        detected_language = result.get("language", options.language or "en")
        logger.info("Transcription complete. Detected language: %s", detected_language)

        # Align timestamps for word-level timing if requested
        if options.word_timestamps:
            logger.info("Aligning timestamps...")
            self._load_align_model(detected_language)
            result = self._whisperx.align(
                result["segments"],
                self._align_model,
                self._align_metadata,
                audio,
                self._device,
                return_char_alignments=False,
            )

        # Convert to domain objects
        segments = self._convert_segments(result.get("segments", []))

        logger.info("Transcription complete: %d segments", len(segments))

        return TranscriptionResult(
            segments=segments,
            language=detected_language,
            language_probability=result.get("language_probability"),
            model_used=self._model_size,
        )

    async def transcribe_stream(
        self,
        audio_path: str,
        options: TranscriptionOptions | None = None,
    ) -> AsyncIterator[TranscriptionProgress | TranscriptionResult]:
        """Transcribe with streaming progress updates."""
        options = options or TranscriptionOptions()
        path = Path(audio_path)

        if not path.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")

        yield TranscriptionProgress(
            stage="loading",
            progress=0.05,
            message="Loading transcription model...",
        )

        self._ensure_loaded()

        yield TranscriptionProgress(
            stage="loading",
            progress=0.1,
            message="Loading audio file...",
        )

        # Load audio
        audio = self._whisperx.load_audio(str(path))

        yield TranscriptionProgress(
            stage="transcribing",
            progress=0.2,
            message="Transcribing audio...",
        )

        # Transcribe
        transcribe_kwargs: dict[str, Any] = {
            "batch_size": options.batch_size,
            "language": options.language,
        }
        if options.initial_prompt:
            transcribe_kwargs["initial_prompt"] = options.initial_prompt
        result = self._model.transcribe(audio, **transcribe_kwargs)

        detected_language = result.get("language", options.language or "en")

        yield TranscriptionProgress(
            stage="transcribing",
            progress=0.6,
            message=f"Detected language: {detected_language}",
        )

        # Align timestamps
        if options.word_timestamps:
            yield TranscriptionProgress(
                stage="aligning",
                progress=0.7,
                message="Aligning word timestamps...",
            )

            self._load_align_model(detected_language)
            result = self._whisperx.align(
                result["segments"],
                self._align_model,
                self._align_metadata,
                audio,
                self._device,
                return_char_alignments=False,
            )

        yield TranscriptionProgress(
            stage="complete",
            progress=0.9,
            message="Processing segments...",
        )

        # Convert to domain objects
        segments = self._convert_segments(result.get("segments", []))

        yield TranscriptionProgress(
            stage="complete",
            progress=1.0,
            message=f"Transcription complete: {len(segments)} segments",
        )

        yield TranscriptionResult(
            segments=segments,
            language=detected_language,
            language_probability=result.get("language_probability"),
            model_used=self._model_size,
        )

    def _convert_segments(self, raw_segments: list[dict[str, Any]]) -> list[TranscriptionSegment]:
        """Convert WhisperX segments to domain objects."""
        segments = []
        for seg in raw_segments:
            words = []
            for word_data in seg.get("words", []):
                words.append(
                    TranscriptionWord(
                        word=word_data.get("word", ""),
                        start=word_data.get("start", 0.0),
                        end=word_data.get("end", 0.0),
                        confidence=word_data.get("score"),
                    )
                )

            segments.append(
                TranscriptionSegment(
                    start=seg.get("start", 0.0),
                    end=seg.get("end", 0.0),
                    text=seg.get("text", "").strip(),
                    speaker=seg.get("speaker"),
                    words=words,
                    confidence=seg.get("score"),
                )
            )

        return segments

    async def get_available_models(self) -> list[str]:
        """Get list of available model sizes."""
        return AVAILABLE_MODELS

    async def get_supported_languages(self) -> list[str]:
        """Get list of supported language codes."""
        return SUPPORTED_LANGUAGES

    async def detect_language(self, audio_path: str) -> tuple[str, float]:
        """Detect the language of an audio file."""
        path = Path(audio_path)
        if not path.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")

        self._ensure_loaded()

        # Load audio and run transcription with language detection
        audio = self._whisperx.load_audio(str(path))
        result = self._model.transcribe(audio, batch_size=16)

        language = result.get("language", "en")
        probability = result.get("language_probability", 0.0)

        return language, probability

    async def is_available(self) -> bool:
        """Check if the transcription engine is available."""
        try:
            import whisperx
            return True
        except ImportError:
            return False

    async def get_engine_info(self) -> dict[str, str | int | float | bool]:
        """Get information about the transcription engine."""
        available = await self.is_available()
        info: dict[str, str | int | float | bool] = {
            "name": "WhisperX",
            "available": available,
            "model_size": self._model_size,
            "device": self._device,
            "compute_type": self._compute_type,
            "model_loaded": self._model is not None,
        }

        if available:
            # CTranslate2 CUDA detection (independent of PyTorch CUDA)
            try:
                import ctranslate2
                cuda_count = ctranslate2.get_cuda_device_count()
                info["ctranslate2_cuda_devices"] = cuda_count
                info["cuda_available"] = cuda_count > 0
            except (ImportError, Exception):
                info["cuda_available"] = False

            # MPS detection (Apple Silicon)
            try:
                import torch
                info["mps_available"] = (
                    hasattr(torch.backends, "mps") and torch.backends.mps.is_available()
                )
            except (ImportError, AttributeError):
                info["mps_available"] = False

        return info

    def cleanup(self) -> None:
        """Unload models and free GPU memory.

        Call this after transcription jobs complete to release memory.
        """
        import gc

        if self._model is not None:
            logger.info("Unloading WhisperX transcription model")
            del self._model
            self._model = None

        if self._align_model is not None:
            logger.info("Unloading WhisperX alignment model")
            del self._align_model
            self._align_model = None
            self._align_metadata = None
            self._align_language = None

        # Force garbage collection
        gc.collect()

        # Clear GPU cache
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
                logger.debug("Cleared CUDA cache")
            elif torch.backends.mps.is_available():
                torch.mps.empty_cache()
                logger.debug("Cleared MPS cache")
        except Exception as e:
            logger.debug("Could not clear GPU cache: %s", e)

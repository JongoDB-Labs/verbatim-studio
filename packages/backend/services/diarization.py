"""Diarization service using WhisperX's DiarizationPipeline (pyannote-based)."""

import logging
import os
import threading
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

# PyTorch 2.6+ changed weights_only=True default for torch.load()
# This breaks pyannote model loading which uses many custom classes.
# Patch must happen BEFORE any pyannote/whisperx imports.
try:
    import torch
    _original_torch_load = torch.load

    def _patched_torch_load(*args, **kwargs):
        # Force weights_only=False for pyannote compatibility.
        # Use direct assignment (not setdefault) because callers like
        # lightning_fabric pass weights_only=None explicitly, and
        # PyTorch 2.6+ treats None as True (the new default).
        if not kwargs.get("weights_only"):
            kwargs["weights_only"] = False
        return _original_torch_load(*args, **kwargs)

    torch.load = _patched_torch_load

    # Also patch lightning_fabric if available (used by pyannote internally)
    try:
        from lightning_fabric.utilities import cloud_io
        _original_pl_load = cloud_io._load

        def _patched_pl_load(path_or_url, map_location=None, **kwargs):
            kwargs["weights_only"] = False
            return _original_torch_load(path_or_url, map_location=map_location, **kwargs)

        cloud_io._load = _patched_pl_load
    except ImportError:
        pass
except ImportError:
    pass  # torch not installed

logger = logging.getLogger(__name__)

# Type for progress callback
ProgressCallback = Callable[[float], Coroutine[Any, Any, None]]


class DiarizationService:
    """Service for speaker diarization using WhisperX's DiarizationPipeline.

    Uses lazy loading to avoid import errors when dependencies are not installed.
    """

    # Class-level lock to prevent concurrent pipeline loads across instances
    _load_lock = threading.Lock()
    _shared_pipeline: Any = None
    _shared_pipeline_device: str | None = None

    def __init__(
        self,
        device: str = "cpu",
        hf_token: str | None = None,
    ):
        """Initialize the diarization service.

        Args:
            device: Device to run inference on (cpu, cuda, mps).
            hf_token: HuggingFace token for pyannote models.
        """
        self.device = device
        self.hf_token = hf_token or os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_TOKEN")
        self._pipeline = None
        self._whisperx = None

    def _ensure_loaded(self) -> None:
        """Ensure WhisperX diarization pipeline is loaded.

        Uses a class-level lock so concurrent jobs share one pipeline
        instead of loading duplicates that exhaust GPU memory.

        Raises:
            ImportError: If dependencies are not installed.
            RuntimeError: If diarization models are not downloaded.
        """
        if self._pipeline is not None:
            return

        with DiarizationService._load_lock:
            # Double-check after acquiring lock
            if self._pipeline is not None:
                return

            # Reuse shared pipeline if device matches
            if (
                DiarizationService._shared_pipeline is not None
                and DiarizationService._shared_pipeline_device == self.device
            ):
                logger.info("Reusing cached diarization pipeline (device=%s)", self.device)
                self._pipeline = DiarizationService._shared_pipeline
                try:
                    import whisperx
                    self._whisperx = whisperx
                except ImportError:
                    pass
                return

            # Gate on model availability — refuse to auto-download
            from core.pyannote_catalog import are_all_models_downloaded, get_missing_components
            if not are_all_models_downloaded():
                missing = get_missing_components()
                raise RuntimeError(
                    f"Diarization models not downloaded: {missing}. "
                    "Download them in Settings → AI → Speaker Diarization."
                )

            try:
                import whisperx
                from whisperx.diarize import DiarizationPipeline
            except ImportError as e:
                raise ImportError(
                    "WhisperX/pyannote is not installed. Install with: pip install 'verbatim-backend[ml]'"
                ) from e

            self._whisperx = whisperx

            logger.info("Loading WhisperX diarization pipeline (device=%s)", self.device)

            # Prevent any auto-downloads during pipeline construction
            old_offline = os.environ.get("HF_HUB_OFFLINE")
            os.environ["HF_HUB_OFFLINE"] = "1"
            try:
                self._pipeline = DiarizationPipeline(
                    use_auth_token=self.hf_token,
                    device=self.device,
                )
            finally:
                if old_offline is None:
                    os.environ.pop("HF_HUB_OFFLINE", None)
                else:
                    os.environ["HF_HUB_OFFLINE"] = old_offline

            # Cache for reuse by other instances
            DiarizationService._shared_pipeline = self._pipeline
            DiarizationService._shared_pipeline_device = self.device

            logger.info("WhisperX diarization pipeline loaded and cached successfully")

    async def diarize(
        self,
        audio_path: str | Path,
        segments: list[dict[str, Any]],
        progress_callback: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        """Run diarization on audio and assign speakers to segments.

        Args:
            audio_path: Path to the audio file.
            segments: List of transcript segments with start, end, text, and words.
            progress_callback: Optional async callback for progress updates.

        Returns:
            Dictionary with:
                - segments: List of segments with speaker labels added
                - speakers: List of unique speaker labels found

        Raises:
            ImportError: If dependencies are not installed.
            FileNotFoundError: If audio file doesn't exist.
        """
        audio_path = Path(audio_path)

        if not audio_path.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")

        if progress_callback:
            await progress_callback(5)

        # Load model (lazy)
        self._ensure_loaded()

        if progress_callback:
            await progress_callback(10)

        # Run diarization via WhisperX's pipeline (returns pandas DataFrame)
        logger.info("Running diarization on: %s", audio_path)
        diarize_df = self._pipeline(str(audio_path))

        if progress_callback:
            await progress_callback(70)

        logger.info("Diarization found %d speaker turns", len(diarize_df))

        # Assign speakers to transcript segments using whisperx
        logger.info("Assigning speakers to transcript segments...")
        result = self._whisperx.assign_word_speakers(diarize_df, {"segments": segments})

        if progress_callback:
            await progress_callback(90)

        # Extract unique speakers
        speakers = set()
        for segment in result.get("segments", []):
            speaker = segment.get("speaker")
            if speaker:
                speakers.add(speaker)

        logger.info("Diarization complete: %d unique speakers found", len(speakers))

        if progress_callback:
            await progress_callback(100)

        return {
            "segments": result.get("segments", []),
            "speakers": sorted(speakers),
        }

    def cleanup(self) -> None:
        """Unload diarization pipeline and free GPU memory.

        Call this after diarization jobs complete to release memory.
        The pyannote pipeline is ~1GB and should be unloaded when not in use.
        """
        import gc

        if self._pipeline is not None:
            logger.info("Unloading diarization pipeline")
            # Clear shared cache if it points to our pipeline
            with DiarizationService._load_lock:
                if DiarizationService._shared_pipeline is self._pipeline:
                    DiarizationService._shared_pipeline = None
                    DiarizationService._shared_pipeline_device = None
            del self._pipeline
            self._pipeline = None

        self._whisperx = None

        # Force garbage collection
        gc.collect()

        # Clear GPU cache
        try:
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
                logger.debug("Cleared CUDA cache")
            elif torch.backends.mps.is_available():
                torch.mps.empty_cache()
                logger.debug("Cleared MPS cache")
        except Exception as e:
            logger.debug("Could not clear GPU cache: %s", e)


# Default diarization service instance (configured from app settings)
def _create_diarization_service() -> DiarizationService:
    from core.config import settings
    return DiarizationService(
        device=settings.WHISPERX_DEVICE,
        hf_token=settings.HF_TOKEN,
    )

diarization_service = _create_diarization_service()

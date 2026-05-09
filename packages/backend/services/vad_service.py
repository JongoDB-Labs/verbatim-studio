"""Silero VAD wrapper for pre-transcription speech detection.

Whisper hallucinates badly on silence and music. Running every chunk
through Silero VAD first lets us skip transcription on chunks that
contain no speech — eliminates the canned "Thanks for watching!" type
hallucinations, and saves the CPU/GPU cost of transcribing nothing.

Public API:
    has_speech(pcm) -> bool
    speech_ratio(pcm) -> float          # 0.0 = all silence, 1.0 = all speech
    speech_timestamps(pcm) -> list[tuple[int, int]]   # (start_sample, end_sample)

The model loads lazily and is cached at module level. ~16 MB resident.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16_000

# Probability above which a window is considered speech. Silero's
# default is 0.5 — we tighten slightly for the live use case where
# the cost of skipping a real word is higher than skipping silence.
DEFAULT_SPEECH_THRESHOLD = 0.4

# Minimum span of speech (in ms) for a chunk to count as "has speech".
# Below this we treat noise spikes as silence to avoid hallucinations
# on coughs, chair scrapes, etc.
MIN_SPEECH_MS = 200

_model_lock = threading.Lock()
_model: Any = None
_get_speech_timestamps: Any = None
_torch: Any = None


def _ensure_loaded() -> None:
    """Lazy-load Silero VAD on first call. Thread-safe."""
    global _model, _get_speech_timestamps, _torch

    if _model is not None:
        return

    with _model_lock:
        if _model is not None:
            return

        try:
            import torch
            from silero_vad import get_speech_timestamps, load_silero_vad
        except ImportError as exc:
            raise ImportError(
                "silero-vad / torch not installed. Install with:"
                " pip install 'verbatim-backend[ml]'"
            ) from exc

        logger.info("Loading Silero VAD model")
        _model = load_silero_vad(onnx=False)
        _get_speech_timestamps = get_speech_timestamps
        _torch = torch
        logger.info("Silero VAD ready")


def is_available() -> bool:
    """Return True if Silero VAD can be loaded.

    Cheap check that doesn't trigger model load.
    """
    try:
        import importlib.util
        return importlib.util.find_spec("silero_vad") is not None
    except Exception:
        return False


def speech_timestamps(
    pcm: np.ndarray,
    *,
    threshold: float = DEFAULT_SPEECH_THRESHOLD,
    min_speech_ms: int = MIN_SPEECH_MS,
) -> list[tuple[int, int]]:
    """Run VAD and return speech regions as (start_sample, end_sample).

    Returns an empty list if no speech is detected, or if the input is
    too short for the model (< 31 ms at 16 kHz).
    """
    if pcm.size == 0:
        return []

    # Silero VAD's window is ~30 ms; chunks shorter than that just
    # return [], so don't bother loading the model.
    if pcm.size < 512:
        return []

    _ensure_loaded()

    tensor = _torch.from_numpy(np.ascontiguousarray(pcm.astype(np.float32)))

    raw = _get_speech_timestamps(
        tensor,
        _model,
        sampling_rate=SAMPLE_RATE,
        threshold=threshold,
        min_speech_duration_ms=min_speech_ms,
        return_seconds=False,
    )
    return [(int(d["start"]), int(d["end"])) for d in raw]


def has_speech(
    pcm: np.ndarray,
    *,
    threshold: float = DEFAULT_SPEECH_THRESHOLD,
    min_speech_ms: int = MIN_SPEECH_MS,
) -> bool:
    """True if VAD detects at least one speech region in *pcm*."""
    return bool(speech_timestamps(
        pcm,
        threshold=threshold,
        min_speech_ms=min_speech_ms,
    ))


def speech_ratio(pcm: np.ndarray) -> float:
    """Fraction of *pcm* covered by speech regions, in [0, 1].

    Useful as a "noise floor" indicator — if a chunk is 95% silence we
    can still safely transcribe the 5% that is speech, but if it's 0%
    we want to skip entirely.
    """
    if pcm.size == 0:
        return 0.0
    spans = speech_timestamps(pcm)
    if not spans:
        return 0.0
    speech_samples = sum(end - start for start, end in spans)
    return min(1.0, speech_samples / pcm.size)

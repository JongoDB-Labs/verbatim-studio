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
# Sticky flag — set on first failed load so we stop spamming logs and
# hot-loop importlib lookups every chunk.
_load_failed: bool = False


def _ensure_loaded() -> bool:
    """Lazy-load Silero VAD on first call. Thread-safe.

    Returns True if the model is ready, False if silero-vad / torch
    aren't installed. Callers should treat False as "VAD unavailable —
    proceed without gating", NOT as an error: a missing VAD just means
    we lose the silence-skip optimization, transcription itself is
    unaffected.
    """
    global _model, _get_speech_timestamps, _torch, _load_failed

    if _model is not None:
        return True
    if _load_failed:
        return False

    with _model_lock:
        if _model is not None:
            return True
        if _load_failed:
            return False

        try:
            import torch
            from silero_vad import get_speech_timestamps, load_silero_vad
        except ImportError as exc:
            # Bundled-Python builds prior to v0.65.10 shipped without
            # silero-vad in requirements-ml.txt; mark unavailable and
            # let the caller fall back to "treat all chunks as voiced".
            logger.warning(
                "Silero VAD unavailable (%s) — live transcription will run"
                " without the silence gate (Whisper may produce occasional"
                " hallucinations on dead audio).",
                exc,
            )
            _load_failed = True
            return False

        logger.info("Loading Silero VAD model")
        _model = load_silero_vad(onnx=False)
        _get_speech_timestamps = get_speech_timestamps
        _torch = torch
        logger.info("Silero VAD ready")
        return True


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
) -> list[tuple[int, int]] | None:
    """Run VAD and return speech regions as (start_sample, end_sample).

    Returns an empty list if the input has no speech or is too short.
    Returns None if Silero VAD isn't installed — callers should treat
    None as "VAD unavailable, don't gate" rather than as silence.
    """
    if pcm.size == 0:
        return []

    # Silero VAD's window is ~30 ms; chunks shorter than that just
    # return [], so don't bother loading the model.
    if pcm.size < 512:
        return []

    if not _ensure_loaded():
        return None

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
    """True if VAD detects at least one speech region in *pcm*.

    When Silero VAD isn't installed we return True so the live pipeline
    keeps working without the silence-skip optimization.
    """
    spans = speech_timestamps(pcm, threshold=threshold, min_speech_ms=min_speech_ms)
    if spans is None:
        return True
    return bool(spans)


def speech_ratio(pcm: np.ndarray) -> float:
    """Fraction of *pcm* covered by speech regions, in [0, 1].

    Returns 1.0 when VAD isn't available — same fallback rationale as
    has_speech: pretend the buffer is all speech so callers don't gate.
    """
    if pcm.size == 0:
        return 0.0
    spans = speech_timestamps(pcm)
    if spans is None:
        return 1.0
    if not spans:
        return 0.0
    speech_samples = sum(end - start for start, end in spans)
    return min(1.0, speech_samples / pcm.size)

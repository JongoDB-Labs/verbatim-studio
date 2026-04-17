"""Transcription settings helper with DB persistence and fallback chain."""

import logging
import platform
import sys
from typing import Any

from sqlalchemy import select

from core.config import settings as env_settings
from persistence.database import get_session_factory
from persistence.models import Setting

logger = logging.getLogger(__name__)

# Hardcoded defaults (last resort in fallback chain)
DEFAULTS: dict[str, Any] = {
    "engine": "auto",  # auto, whisperx, mlx-whisper
    "model": "base",
    "device": "cpu",
    "compute_type": "int8",
    "batch_size": 16,
    "diarize": True,
    "hf_token": None,
    # Audio preprocessing
    "noise_reduction": False,  # Pre-process audio to reduce background noise
    "normalize_audio": False,  # Normalize audio volume before transcription
    # External WhisperX service (enterprise feature)
    "external_url": None,
    "external_api_key": None,
}

VALID_ENGINES = ["auto", "whisperx", "mlx-whisper"]

VALID_MODELS = ["tiny", "base", "small", "medium", "large-v3"]

# Device support depends on engine:
# - WhisperX (ctranslate2): cpu, cuda only
# - MLX Whisper: mps only (Apple Silicon)
VALID_DEVICES = ["cpu", "cuda", "mps"]

VALID_COMPUTE_TYPES = ["int8", "float16", "float32"]

VALID_BATCH_SIZES = [1, 2, 4, 8, 16, 32, 64]


def is_apple_silicon() -> bool:
    """Check if running on Apple Silicon Mac."""
    return sys.platform == "darwin" and platform.machine() == "arm64"


def is_mlx_whisper_available() -> bool:
    """Check if mlx-whisper is importable."""
    try:
        import mlx_whisper
        return True
    except ImportError:
        return False


def detect_optimal_engine() -> str:
    """Auto-detect the best transcription engine for this system.

    Returns:
        Engine identifier: 'mlx-whisper' for Apple Silicon with mlx installed,
        otherwise 'whisperx'.
    """
    if is_apple_silicon() and is_mlx_whisper_available():
        return "mlx-whisper"
    return "whisperx"


def get_engine_for_device(device: str) -> str:
    """Map device to required engine.

    Args:
        device: Compute device (cpu, cuda, mps)

    Returns:
        Engine that supports the given device.
    """
    if device == "mps":
        return "mlx-whisper"
    return "whisperx"


def get_devices_for_engine(engine: str) -> list[str]:
    """Get supported devices for a specific engine.

    Args:
        engine: Engine identifier (whisperx, mlx-whisper)

    Returns:
        List of device identifiers supported by the engine.
    """
    if engine == "mlx-whisper":
        return ["mps"]
    # whisperx supports cpu and cuda (via CTranslate2's own CUDA bindings)
    devices = ["cpu"]
    try:
        import ctranslate2
        if ctranslate2.get_cuda_device_count() > 0:
            devices.append("cuda")
    except (ImportError, Exception):
        pass
    return devices


def get_available_engines() -> list[str]:
    """Get list of available engines on this system.

    Returns:
        List of engine identifiers that are available.
    """
    engines = []
    # WhisperX is always potentially available (just needs install)
    try:
        import whisperx
        engines.append("whisperx")
    except ImportError:
        pass

    # MLX Whisper only on Apple Silicon
    if is_apple_silicon() and is_mlx_whisper_available():
        engines.append("mlx-whisper")

    return engines


PRESETS: dict[str, dict[str, Any]] = {
    "fast": {
        "model": "tiny",
        "compute_type": "int8",
        "batch_size": 32,
    },
    "balanced": {
        "model": "base",
        "compute_type": "int8",
        "batch_size": 16,
    },
    "accurate": {
        "model": "large-v3",
        "compute_type": "float16",
        "batch_size": 8,
    },
    "cpu_only": {
        "model": "base",
        "compute_type": "int8",
        "batch_size": 8,
    },
}

# Cached device detection result
_available_devices: list[str] | None = None


def detect_available_devices() -> list[str]:
    """Detect available compute devices for transcription.

    Returns devices based on available engines:
    - cpu: Always available (WhisperX)
    - cuda: Available if NVIDIA GPU + torch (WhisperX)
    - mps: Available if Apple Silicon + mlx-whisper (MLX Whisper)

    Result is cached.
    """
    global _available_devices
    if _available_devices is not None:
        return _available_devices

    devices = ["cpu"]

    # Check CTranslate2 CUDA support (used by WhisperX/faster-whisper)
    try:
        import ctranslate2

        if ctranslate2.get_cuda_device_count() > 0:
            devices.append("cuda")
    except (ImportError, Exception):
        pass

    # MPS is only available with mlx-whisper on Apple Silicon
    if is_apple_silicon() and is_mlx_whisper_available():
        devices.append("mps")

    _available_devices = devices
    logger.info("Detected available transcription devices: %s", devices)
    return devices


def detect_diarization_device() -> str:
    """Detect the best device for pyannote diarization.

    Pyannote supports cpu, cuda, and mps (Apple Silicon).
    Returns the best available device.
    """
    try:
        import torch

        if torch.cuda.is_available():
            return "cuda"
        if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            return "mps"
    except ImportError:
        pass
    return "cpu"


def detect_whisperx_device() -> str:
    """Detect the best device for WhisperX transcription.

    WhisperX (ctranslate2) supports cpu and cuda only.
    Uses CTranslate2's native CUDA detection (independent of PyTorch CUDA).
    MPS is handled by engine auto-detect selecting mlx-whisper instead.
    """
    try:
        import ctranslate2

        if ctranslate2.get_cuda_device_count() > 0:
            return "cuda"
    except (ImportError, Exception):
        pass
    return "cpu"


def detect_compute_type(device: str) -> str:
    """Detect the optimal compute type for a given device.

    Args:
        device: Compute device (cpu, cuda)

    Returns:
        Optimal compute type: float16 for cuda, int8 for cpu.
    """
    if device == "cuda":
        return "float16"
    return "int8"


def detect_llm_gpu_layers() -> int:
    """Detect optimal GPU layer offload for llama.cpp.

    Returns:
        -1 (all layers on GPU) for Apple Silicon or CUDA systems,
        0 (CPU only) otherwise.
    """
    if is_apple_silicon():
        logger.info("Apple Silicon detected — defaulting to full GPU offload for llama.cpp")
        return -1
    try:
        from llama_cpp import llama_supports_gpu_offload

        if llama_supports_gpu_offload():
            logger.info("CUDA llama-cpp detected — defaulting to full GPU offload")
            return -1
    except (ImportError, Exception):
        pass
    # Fallback: check CTranslate2 CUDA (for transcription GPU, not LLM)
    try:
        import ctranslate2

        if ctranslate2.get_cuda_device_count() > 0:
            logger.info("CUDA (CTranslate2) detected — defaulting to full GPU offload for llama.cpp")
            return -1
    except (ImportError, Exception):
        pass
    return 0


async def get_transcription_settings() -> dict[str, Any]:
    """Get effective transcription settings.

    Fallback chain: DB setting → env var → hardcoded default.
    """
    # Start with hardcoded defaults
    effective = dict(DEFAULTS)

    # Layer 2: env var overrides
    if env_settings.WHISPERX_MODEL != "base":
        effective["model"] = env_settings.WHISPERX_MODEL
    if env_settings.WHISPERX_DEVICE != "auto":
        effective["device"] = env_settings.WHISPERX_DEVICE
    if env_settings.WHISPERX_COMPUTE_TYPE != "auto":
        effective["compute_type"] = env_settings.WHISPERX_COMPUTE_TYPE
    if env_settings.HF_TOKEN:
        effective["hf_token"] = env_settings.HF_TOKEN
    # External WhisperX service from env vars
    if env_settings.WHISPERX_EXTERNAL_URL:
        effective["external_url"] = env_settings.WHISPERX_EXTERNAL_URL
    if env_settings.WHISPERX_API_KEY:
        effective["external_api_key"] = env_settings.WHISPERX_API_KEY

    # Layer 3: DB overrides (highest priority)
    try:
        async with get_session_factory()() as session:
            result = await session.execute(
                select(Setting).where(Setting.key == "transcription")
            )
            setting = result.scalar_one_or_none()
            if setting and setting.value:
                for key in DEFAULTS:
                    if key in setting.value and setting.value[key] is not None:
                        effective[key] = setting.value[key]
    except Exception as e:
        logger.warning("Failed to read transcription settings from DB, using defaults", exc_info=True)

    return effective


async def save_transcription_settings(data: dict[str, Any]) -> dict[str, Any]:
    """Save transcription settings to DB. Merges with existing values.

    Returns the saved settings dict.
    """
    async with get_session_factory()() as session:
        result = await session.execute(
            select(Setting).where(Setting.key == "transcription")
        )
        setting = result.scalar_one_or_none()

        if setting:
            # Merge new values into existing
            merged = dict(setting.value)
            for key, value in data.items():
                if key in DEFAULTS:
                    merged[key] = value
            setting.value = merged
        else:
            # Create new setting with only provided fields
            merged = {}
            for key, value in data.items():
                if key in DEFAULTS:
                    merged[key] = value
            setting = Setting(key="transcription", value=merged)
            session.add(setting)

        await session.commit()
        return merged

"""System information endpoints."""

import asyncio
import json
import logging
import os
import platform
import shutil
import subprocess
import sys
from enum import Enum
from pathlib import Path
from typing import AsyncIterator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from core.config import settings

logger = logging.getLogger(__name__)
from persistence.database import get_session_factory
from sqlalchemy import text


def _get_git_version() -> str:
    """Read version from pyproject.toml or git tags."""
    # First try pyproject.toml (works in packaged Electron app)
    try:
        import tomllib
        pyproject_path = Path(__file__).parent.parent.parent / "pyproject.toml"
        if pyproject_path.exists():
            with open(pyproject_path, "rb") as f:
                data = tomllib.load(f)
                version = data.get("project", {}).get("version")
                if version:
                    return f"v{version}"
    except (OSError, ValueError, KeyError):
        pass

    # Fall back to git describe (works in development)
    try:
        result = subprocess.run(
            ["git", "describe", "--tags", "--always"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return "dev"

router = APIRouter(prefix="/system", tags=["system"])


class StoragePaths(BaseModel):
    """Storage paths info."""

    data_dir: str
    media_dir: str
    models_dir: str
    database_path: str


class DiskUsage(BaseModel):
    """Disk usage info."""

    total_bytes: int
    used_bytes: int
    free_bytes: int
    percent_used: float


class ModelBreakdown(BaseModel):
    """Breakdown of AI models by type."""

    llm_count: int
    llm_bytes: int
    asr_count: int
    asr_bytes: int
    diarization_count: int
    diarization_bytes: int
    ocr_count: int
    ocr_bytes: int


class StorageBreakdown(BaseModel):
    """Breakdown of Verbatim storage usage."""

    media_bytes: int
    media_count: int
    database_bytes: int
    models: ModelBreakdown
    total_bytes: int


class ContentCounts(BaseModel):
    """Content counts from database."""

    recordings: int
    transcripts: int
    segments: int


class SystemInfo(BaseModel):
    """Complete system information."""

    # App info
    app_version: str
    python_version: str
    platform: str
    platform_version: str

    # Storage
    paths: StoragePaths
    disk_usage: DiskUsage
    storage_breakdown: StorageBreakdown
    content_counts: ContentCounts

    # Limits
    max_upload_bytes: int


def get_dir_size(path: Path) -> tuple[int, int]:
    """Get total size and file count of a directory."""
    total_size = 0
    file_count = 0
    if path.exists() and path.is_dir():
        for entry in path.rglob("*"):
            if entry.is_file():
                try:
                    total_size += entry.stat().st_size
                    file_count += 1
                except (OSError, PermissionError):
                    pass
    return total_size, file_count


def get_file_size(path: Path) -> int:
    """Get size of a single file."""
    if path.exists() and path.is_file():
        try:
            return path.stat().st_size
        except (OSError, PermissionError):
            pass
    return 0


def count_llm_models(models_dir: Path) -> tuple[int, int]:
    """Count LLM models (.gguf files) in the models directory."""
    count, size = 0, 0
    if models_dir.exists() and models_dir.is_dir():
        for f in models_dir.glob("*.gguf"):
            try:
                count += 1
                size += f.stat().st_size
            except (OSError, PermissionError):
                pass
    return count, size


def count_huggingface_models(pattern: str) -> tuple[int, int]:
    """Count models in HuggingFace cache matching a pattern.

    Args:
        pattern: Glob pattern to match model directories (e.g., '*whisper*')

    Returns:
        Tuple of (model_count, total_bytes)
    """
    hf_cache = Path.home() / ".cache" / "huggingface" / "hub"
    count, size = 0, 0
    if not hf_cache.exists():
        return count, size

    for d in hf_cache.glob(f"models--{pattern}"):
        if d.is_dir():
            count += 1
            for f in d.rglob("*"):
                if f.is_file():
                    try:
                        size += f.stat().st_size
                    except (OSError, PermissionError):
                        pass
    return count, size


def count_pyannote_models() -> tuple[int, int]:
    """Count pyannote diarization models in torch cache.

    Pyannote stores models in ~/.cache/torch/pyannote/ rather than HuggingFace cache.

    Returns:
        Tuple of (model_count, total_bytes)
    """
    pyannote_cache = Path.home() / ".cache" / "torch" / "pyannote"
    count, size = 0, 0
    if not pyannote_cache.exists():
        return count, size

    for d in pyannote_cache.glob("models--pyannote--*"):
        if d.is_dir():
            count += 1
            for f in d.rglob("*"):
                if f.is_file():
                    try:
                        size += f.stat().st_size
                    except (OSError, PermissionError):
                        pass
    return count, size


def count_ocr_models(models_dir: Path) -> tuple[int, int]:
    """Count OCR/VLM models in the models/ocr directory.

    OCR models are stored as subdirectories under models/ocr/.

    Returns:
        Tuple of (model_count, total_bytes)
    """
    ocr_dir = models_dir / "ocr"
    count, size = 0, 0
    if not ocr_dir.exists():
        return count, size

    for d in ocr_dir.iterdir():
        if d.is_dir():
            count += 1
            for f in d.rglob("*"):
                if f.is_file():
                    try:
                        size += f.stat().st_size
                    except (OSError, PermissionError):
                        pass
    return count, size


class HardwareInfo(BaseModel):
    """Hardware information for memory-aware model recommendations."""

    platform: str  # "macos" | "windows" | "linux"
    total_ram_gb: float
    gpu_vram_gb: float | None  # NVIDIA VRAM on Windows, None on macOS
    cuda_available: bool


# Cache hardware info per session (doesn't change)
_cached_hardware_info: HardwareInfo | None = None


@router.get("/hardware", response_model=HardwareInfo)
async def get_hardware_info() -> HardwareInfo:
    """Get system hardware information for model recommendations.

    Returns platform, total RAM, GPU VRAM (if available), and CUDA status.
    Cached per session since hardware doesn't change.
    """
    global _cached_hardware_info
    if _cached_hardware_info is not None:
        return _cached_hardware_info

    plat = sys.platform
    if plat == "darwin":
        platform_name = "macos"
    elif plat == "win32":
        platform_name = "windows"
    else:
        platform_name = "linux"

    # Get total RAM
    total_ram_gb = 0.0
    try:
        import psutil
        total_ram_gb = round(psutil.virtual_memory().total / (1024 ** 3), 1)
    except ImportError:
        # Fallback: read from /proc/meminfo on Linux or sysctl on macOS
        try:
            if plat == "darwin":
                result = subprocess.run(
                    ["sysctl", "-n", "hw.memsize"],
                    capture_output=True, text=True, timeout=5,
                )
                if result.returncode == 0:
                    total_ram_gb = round(int(result.stdout.strip()) / (1024 ** 3), 1)
            elif plat == "linux":
                with open("/proc/meminfo") as f:
                    for line in f:
                        if line.startswith("MemTotal:"):
                            kb = int(line.split()[1])
                            total_ram_gb = round(kb / (1024 ** 2), 1)
                            break
            elif plat == "win32":
                import ctypes

                class MEMORYSTATUSEX(ctypes.Structure):
                    _fields_ = [
                        ("dwLength", ctypes.c_ulong),
                        ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong),
                        ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong),
                        ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong),
                        ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                    ]

                stat = MEMORYSTATUSEX()
                stat.dwLength = ctypes.sizeof(stat)
                ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
                total_ram_gb = round(stat.ullTotalPhys / (1024 ** 3), 1)
        except Exception:
            pass

    # Get GPU VRAM
    gpu_vram_gb = None
    cuda_available = False

    try:
        import torch
        if torch.cuda.is_available():
            cuda_available = True
            gpu_vram_gb = round(torch.cuda.get_device_properties(0).total_memory / (1024 ** 3), 1)
    except (ImportError, Exception):
        pass

    # Fallback: try nvidia-smi for VRAM
    if gpu_vram_gb is None and plat == "win32":
        try:
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                vram_mb = int(result.stdout.strip().split("\n")[0])
                gpu_vram_gb = round(vram_mb / 1024, 1)
        except (FileNotFoundError, subprocess.TimeoutExpired, ValueError):
            pass

    _cached_hardware_info = HardwareInfo(
        platform=platform_name,
        total_ram_gb=total_ram_gb,
        gpu_vram_gb=gpu_vram_gb,
        cuda_available=cuda_available,
    )

    return _cached_hardware_info


@router.get("/info", response_model=SystemInfo)
async def get_system_info() -> SystemInfo:
    """Get system information including storage usage and content counts."""
    # Get database path from URL
    db_url = settings.DATABASE_URL
    if db_url.startswith("sqlite"):
        # Extract path from sqlite URL (sqlite+aiosqlite:///./verbatim.db)
        db_path_str = db_url.split("///")[-1]
        db_path = Path(db_path_str).resolve()
    else:
        db_path = Path("verbatim.db")

    # Storage paths
    paths = StoragePaths(
        data_dir=str(settings.DATA_DIR),
        media_dir=str(settings.MEDIA_DIR),
        models_dir=str(settings.MODELS_DIR),
        database_path=str(db_path),
    )

    # Disk usage (based on data directory's filesystem)
    try:
        disk = shutil.disk_usage(settings.DATA_DIR)
        disk_usage = DiskUsage(
            total_bytes=disk.total,
            used_bytes=disk.used,
            free_bytes=disk.free,
            percent_used=round((disk.used / disk.total) * 100, 1) if disk.total > 0 else 0,
        )
    except (OSError, PermissionError):
        disk_usage = DiskUsage(
            total_bytes=0,
            used_bytes=0,
            free_bytes=0,
            percent_used=0,
        )

    # Storage breakdown
    media_bytes, media_count = get_dir_size(settings.MEDIA_DIR)
    database_bytes = get_file_size(db_path)

    # Count models by type
    llm_count, llm_bytes = count_llm_models(settings.MODELS_DIR)
    asr_count, asr_bytes = count_huggingface_models("*whisper*")
    diarization_count, diarization_bytes = count_pyannote_models()
    ocr_count, ocr_bytes = count_ocr_models(settings.MODELS_DIR)

    models = ModelBreakdown(
        llm_count=llm_count,
        llm_bytes=llm_bytes,
        asr_count=asr_count,
        asr_bytes=asr_bytes,
        diarization_count=diarization_count,
        diarization_bytes=diarization_bytes,
        ocr_count=ocr_count,
        ocr_bytes=ocr_bytes,
    )

    total_models_bytes = llm_bytes + asr_bytes + diarization_bytes + ocr_bytes

    storage_breakdown = StorageBreakdown(
        media_bytes=media_bytes,
        media_count=media_count,
        database_bytes=database_bytes,
        models=models,
        total_bytes=media_bytes + database_bytes + total_models_bytes,
    )

    # Content counts from database
    async with get_session_factory()() as session:
        # Count recordings
        result = await session.execute(text("SELECT COUNT(*) FROM recordings"))
        recordings_count = result.scalar() or 0

        # Count transcripts
        result = await session.execute(text("SELECT COUNT(*) FROM transcripts"))
        transcripts_count = result.scalar() or 0

        # Count segments
        result = await session.execute(text("SELECT COUNT(*) FROM segments"))
        segments_count = result.scalar() or 0

    content_counts = ContentCounts(
        recordings=recordings_count,
        transcripts=transcripts_count,
        segments=segments_count,
    )

    # Platform info
    platform_info = platform.platform()
    platform_system = platform.system()

    return SystemInfo(
        app_version=_get_git_version(),
        python_version=sys.version.split()[0],
        platform=platform_system,
        platform_version=platform_info,
        paths=paths,
        disk_usage=disk_usage,
        storage_breakdown=storage_breakdown,
        content_counts=content_counts,
        max_upload_bytes=10 * 1024 * 1024 * 1024,  # 10 GB
    )


# ============ ML Dependencies Management ============


class MLStatus(BaseModel):
    """ML dependencies status."""

    whisperx_installed: bool
    mlx_whisper_installed: bool
    torch_installed: bool
    pyannote_installed: bool
    is_apple_silicon: bool
    recommended_engine: str | None
    install_in_progress: bool = False


class DependencyStatus(BaseModel):
    """Comprehensive dependency status for the app."""

    # System binaries
    ffmpeg_available: bool
    ffmpeg_path: str | None
    ffmpeg_bundled: bool

    # ML packages
    transcription_ready: bool
    diarization_ready: bool
    hf_token_set: bool

    # Features availability
    video_processing: bool
    audio_transcription: bool
    speaker_diarization: bool
    ocr_available: bool
    embeddings_available: bool
    llm_available: bool


def _check_ffmpeg() -> tuple[bool, str | None, bool]:
    """Check if ffmpeg is available and whether it's bundled.

    Returns: (available, path, is_bundled)
    """
    import shutil

    # Check for bundled ffmpeg first
    if os.environ.get("VERBATIM_ELECTRON") == "1":
        python_path = Path(sys.executable)
        if sys.platform == "win32":
            resources_path = python_path.parent.parent          # resources/python/python.exe
        else:
            resources_path = python_path.parent.parent.parent   # resources/python/bin/python3

        if sys.platform == "win32":
            bundled_ffmpeg = resources_path / "ffmpeg" / "ffmpeg.exe"
        else:
            bundled_ffmpeg = resources_path / "ffmpeg" / "ffmpeg"

        if bundled_ffmpeg.exists():
            return True, str(bundled_ffmpeg), True

    # Check system PATH
    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        return True, system_ffmpeg, False

    return False, None, False


def _is_apple_silicon() -> bool:
    """Check if running on Apple Silicon Mac."""
    return sys.platform == "darwin" and platform.machine() == "arm64"


def _check_package_installed(package: str) -> bool:
    """Check if a Python package is installed."""
    try:
        __import__(package.replace("-", "_"))
        return True
    except ImportError:
        return False


# Track installation status
_ml_install_in_progress = False


@router.get("/ml-status", response_model=MLStatus)
async def get_ml_status() -> MLStatus:
    """Get ML dependencies installation status."""
    whisperx = _check_package_installed("whisperx")
    mlx_whisper = _check_package_installed("mlx_whisper")
    torch = _check_package_installed("torch")
    pyannote = _check_package_installed("pyannote.audio")
    is_apple = _is_apple_silicon()

    # Determine recommended engine
    recommended = None
    if is_apple and mlx_whisper:
        recommended = "mlx-whisper"
    elif whisperx:
        recommended = "whisperx"

    return MLStatus(
        whisperx_installed=whisperx,
        mlx_whisper_installed=mlx_whisper,
        torch_installed=torch,
        pyannote_installed=pyannote,
        is_apple_silicon=is_apple,
        recommended_engine=recommended,
        install_in_progress=_ml_install_in_progress,
    )


@router.get("/dependency-check", response_model=DependencyStatus)
async def check_dependencies() -> DependencyStatus:
    """Check all system dependencies and their availability.

    This helps users understand what features are available and what's missing.
    """
    from core.transcription_settings import get_transcription_settings

    # Check FFmpeg
    ffmpeg_available, ffmpeg_path, ffmpeg_bundled = _check_ffmpeg()

    # Check ML packages
    whisperx = _check_package_installed("whisperx")
    mlx_whisper = _check_package_installed("mlx_whisper")
    torch = _check_package_installed("torch")
    pyannote = _check_package_installed("pyannote.audio")
    transformers = _check_package_installed("transformers")
    sentence_transformers = _check_package_installed("sentence_transformers")
    llama_cpp = _check_package_installed("llama_cpp")

    # Check HF token
    settings = await get_transcription_settings()
    hf_token_set = bool(settings.get("hf_token"))

    # Determine feature availability
    is_apple = _is_apple_silicon()
    transcription_ready = mlx_whisper if is_apple else (whisperx and torch)
    diarization_ready = whisperx and pyannote and torch and hf_token_set

    return DependencyStatus(
        ffmpeg_available=ffmpeg_available,
        ffmpeg_path=ffmpeg_path,
        ffmpeg_bundled=ffmpeg_bundled,
        transcription_ready=transcription_ready,
        diarization_ready=diarization_ready,
        hf_token_set=hf_token_set,
        video_processing=ffmpeg_available,
        audio_transcription=transcription_ready,
        speaker_diarization=diarization_ready,
        ocr_available=transformers,
        embeddings_available=sentence_transformers,
        llm_available=llama_cpp,
    )


@router.post("/install-ml")
async def install_ml_dependencies():
    """Install ML dependencies for local transcription.

    Returns a streaming response with installation progress.
    For Apple Silicon, installs mlx-whisper (lightweight).
    For other systems, installs whisperx + torch.

    Note: Uses asyncio.create_subprocess_exec with hardcoded package names
    (no shell injection risk - packages are not user input).
    """
    global _ml_install_in_progress

    if _ml_install_in_progress:
        return StreamingResponse(
            iter([f"data: {json.dumps({'status': 'error', 'message': 'Installation already in progress'})}\n\n"]),
            media_type="text/event-stream",
        )

    async def install_stream() -> AsyncIterator[str]:
        global _ml_install_in_progress
        _ml_install_in_progress = True

        try:
            # Determine what to install based on platform
            is_apple = _is_apple_silicon()

            if is_apple:
                # For Apple Silicon:
                # - mlx-whisper for fast GPU transcription
                # - whisperx + pyannote for speaker diarization
                packages = [
                    "mlx-whisper>=0.4.0",
                    "whisperx>=3.1.0",
                    "pyannote.audio>=3.1.0",
                ]
                yield f"data: {json.dumps({'status': 'progress', 'message': 'Installing MLX Whisper + diarization for Apple Silicon...'})}\n\n"
            else:
                # For other systems, install whisperx + torch
                packages = [
                    "torch>=2.0.0",
                    "torchaudio>=2.0.0",
                    "whisperx>=3.1.0",
                    "pyannote.audio>=3.1.0",
                ]
                yield f"data: {json.dumps({'status': 'progress', 'message': 'Installing WhisperX and dependencies...'})}\n\n"

            # Find pip executable - use sys.executable to ensure we use the right Python
            python_exe = sys.executable
            # Build command as list (no shell, safe from injection)
            pip_cmd = [python_exe, "-m", "pip", "install", "--upgrade"] + packages

            logger.info("Running pip install: %s", " ".join(pip_cmd))
            packages_str = " ".join(packages)
            yield f"data: {json.dumps({'status': 'progress', 'message': f'Running: pip install {packages_str}'})}\n\n"

            # Run pip install using create_subprocess_exec (not shell)
            process = await asyncio.create_subprocess_exec(
                *pip_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )

            # Stream output
            while True:
                line = await process.stdout.readline()
                if not line:
                    break
                line_text = line.decode().strip()
                if line_text:
                    logger.info("pip: %s", line_text)
                    # Send progress updates for key lines
                    if "Downloading" in line_text or "Installing" in line_text or "Successfully" in line_text:
                        yield f"data: {json.dumps({'status': 'progress', 'message': line_text[:200]})}\n\n"

            await process.wait()

            if process.returncode == 0:
                logger.info("ML dependencies installed successfully")
                yield f"data: {json.dumps({'status': 'complete', 'message': 'Installation complete! Restart the app to use local transcription.'})}\n\n"
            else:
                logger.error("pip install failed with code %d", process.returncode)
                yield f"data: {json.dumps({'status': 'error', 'message': f'Installation failed with exit code {process.returncode}'})}\n\n"

        except Exception as e:
            logger.exception("ML installation failed")
            yield f"data: {json.dumps({'status': 'error', 'message': str(e)})}\n\n"
        finally:
            _ml_install_in_progress = False

    return StreamingResponse(install_stream(), media_type="text/event-stream")


class MemoryInfo(BaseModel):
    """Memory usage information."""

    # Process memory
    process_rss_bytes: int  # Resident Set Size (physical memory)
    process_vms_bytes: int  # Virtual Memory Size

    # GPU memory (if available)
    gpu_available: bool
    gpu_type: str | None  # "cuda", "mps", or None
    gpu_allocated_bytes: int | None
    gpu_reserved_bytes: int | None

    # Model status
    models_loaded: list[str]  # List of currently loaded model names


@router.get("/memory", response_model=MemoryInfo)
async def get_memory_info() -> MemoryInfo:
    """Get current memory usage including GPU memory if available."""
    import os

    # Get process memory using os (cross-platform fallback)
    try:
        import psutil
        process = psutil.Process(os.getpid())
        mem_info = process.memory_info()
        process_rss = mem_info.rss
        process_vms = mem_info.vms
    except ImportError:
        # Fallback if psutil not available
        process_rss = 0
        process_vms = 0

    # Check GPU memory
    gpu_available = False
    gpu_type = None
    gpu_allocated = None
    gpu_reserved = None

    # Check CTranslate2 CUDA first (used by WhisperX, independent of PyTorch CUDA)
    try:
        import ctranslate2
        if ctranslate2.get_cuda_device_count() > 0:
            gpu_available = True
            gpu_type = "cuda"
    except (ImportError, Exception):
        pass

    # Check PyTorch GPU (for detailed memory reporting and MPS)
    try:
        import torch
        if torch.cuda.is_available():
            gpu_available = True
            gpu_type = "cuda"
            gpu_allocated = torch.cuda.memory_allocated()
            gpu_reserved = torch.cuda.memory_reserved()
        elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            gpu_available = True
            gpu_type = "mps"
            try:
                gpu_allocated = torch.mps.current_allocated_memory()
                gpu_reserved = gpu_allocated  # MPS doesn't distinguish reserved
            except AttributeError:
                gpu_allocated = None
                gpu_reserved = None
    except ImportError:
        pass

    # Check which models are loaded
    models_loaded = []

    # Check transcription models
    try:
        from core.factory import get_factory
        factory = get_factory()
        # We can't easily check if models are loaded without accessing internal state
        # For now, we'll leave this empty - could be enhanced later
    except ImportError:
        pass

    return MemoryInfo(
        process_rss_bytes=process_rss,
        process_vms_bytes=process_vms,
        gpu_available=gpu_available,
        gpu_type=gpu_type,
        gpu_allocated_bytes=gpu_allocated,
        gpu_reserved_bytes=gpu_reserved,
        models_loaded=models_loaded,
    )


class GpuFeatureStatus(BaseModel):
    """GPU acceleration status for a single feature."""
    feature: str
    gpu_accelerated: bool
    device: str  # "cuda", "mps", "cpu"
    detail: str  # Human-readable explanation


class GpuStatus(BaseModel):
    """Overall GPU acceleration status."""
    platform: str
    cuda_available: bool
    torch_cuda_available: bool
    nvidia_gpu_detected: bool
    gpu_name: str | None
    cuda_pytorch_installed: bool
    cuda_llama_installed: bool
    features: list[GpuFeatureStatus]
    upgrade_available: bool
    estimated_download_bytes: int


@router.get("/gpu-status", response_model=GpuStatus)
async def get_gpu_status() -> GpuStatus:
    """Get GPU acceleration status for all AI features."""
    plat = sys.platform
    features: list[GpuFeatureStatus] = []

    # 1. Check CTranslate2 CUDA (bundled, always works if GPU present)
    cuda_available = False
    try:
        import ctranslate2
        cuda_available = ctranslate2.get_cuda_device_count() > 0
    except (ImportError, Exception):
        pass

    # 2. Check PyTorch CUDA
    torch_cuda = False
    gpu_name = None
    try:
        import torch
        torch_cuda = torch.cuda.is_available()
        if torch_cuda:
            gpu_name = torch.cuda.get_device_name(0)
    except (ImportError, Exception):
        pass

    # 3. Detect NVIDIA GPU even without CUDA torch (via ctranslate2 or nvidia-smi)
    nvidia_detected = cuda_available
    if not nvidia_detected and plat == "win32":
        try:
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                nvidia_detected = True
                if not gpu_name:
                    gpu_name = result.stdout.strip().split("\n")[0]
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

    # 4. Check llama-cpp-python CUDA support
    cuda_llama = False
    try:
        from llama_cpp import llama_cpp as _lib
        cuda_llama = hasattr(_lib, 'ggml_backend_cuda_init')
    except Exception:
        cuda_llama = False

    # 5. Build per-feature status
    features.append(GpuFeatureStatus(
        feature="Transcription (Whisper)",
        gpu_accelerated=cuda_available,
        device="cuda" if cuda_available else "cpu",
        detail="CTranslate2 native CUDA" if cuda_available else "CPU mode",
    ))

    features.append(GpuFeatureStatus(
        feature="Speaker ID (pyannote)",
        gpu_accelerated=torch_cuda,
        device="cuda" if torch_cuda else "cpu",
        detail="PyTorch CUDA" if torch_cuda else "Requires CUDA PyTorch",
    ))

    features.append(GpuFeatureStatus(
        feature="Semantic Search",
        gpu_accelerated=torch_cuda,
        device="cuda" if torch_cuda else "cpu",
        detail="PyTorch CUDA" if torch_cuda else "Requires CUDA PyTorch",
    ))

    features.append(GpuFeatureStatus(
        feature="AI Assistant (Granite 8B)",
        gpu_accelerated=cuda_llama,
        device="cuda" if cuda_llama else "cpu",
        detail="CUDA offload" if cuda_llama else "Requires CUDA llama-cpp-python",
    ))

    features.append(GpuFeatureStatus(
        feature="OCR (Qwen2-VL)",
        gpu_accelerated=torch_cuda,
        device="cuda" if torch_cuda else "cpu",
        detail="PyTorch CUDA" if torch_cuda else "Requires CUDA PyTorch",
    ))

    estimated_bytes = 2800 * 1024 * 1024  # ~2.8 GB download

    return GpuStatus(
        platform=plat,
        cuda_available=cuda_available,
        torch_cuda_available=torch_cuda,
        nvidia_gpu_detected=nvidia_detected,
        gpu_name=gpu_name,
        cuda_pytorch_installed=torch_cuda,
        cuda_llama_installed=cuda_llama,
        features=features,
        upgrade_available=nvidia_detected and not torch_cuda,
        estimated_download_bytes=estimated_bytes,
    )


# Track GPU installation status
_gpu_install_in_progress = False


@router.post("/enable-gpu")
async def enable_gpu_acceleration():
    """Install CUDA-enabled PyTorch and llama-cpp-python for full GPU acceleration.

    Windows-only. Replaces CPU PyTorch with CUDA PyTorch and installs
    CUDA llama-cpp-python. Returns SSE stream with progress.

    Requires app restart after completion to reload modules.
    """
    global _gpu_install_in_progress

    if sys.platform != "win32":
        return StreamingResponse(
            iter([f"data: {json.dumps({'status': 'error', 'message': 'GPU acceleration upgrade is only available on Windows'})}\n\n"]),
            media_type="text/event-stream",
        )

    if _gpu_install_in_progress:
        return StreamingResponse(
            iter([f"data: {json.dumps({'status': 'error', 'message': 'GPU installation already in progress'})}\n\n"]),
            media_type="text/event-stream",
        )

    # Pre-flight: check disk space (need ~6 GB free)
    free_bytes = shutil.disk_usage(Path.home()).free
    if free_bytes < 6 * 1024 * 1024 * 1024:
        free_gb = free_bytes / (1024 ** 3)
        return StreamingResponse(
            iter([f"data: {json.dumps({'status': 'error', 'message': f'Insufficient disk space. Need ~6 GB free, only {free_gb:.1f} GB available.'})}\n\n"]),
            media_type="text/event-stream",
        )

    async def install_stream() -> AsyncIterator[str]:
        global _gpu_install_in_progress
        _gpu_install_in_progress = True

        try:
            python_exe = sys.executable
            cuda_torch_index = "https://download.pytorch.org/whl/cu126"
            cuda_llama_index = "https://abetlen.github.io/llama-cpp-python/whl/cu124"

            # Skip if CUDA PyTorch already installed (e.g. from OCR deps)
            torch_already_cuda = False
            try:
                import torch
                if torch.cuda.is_available():
                    torch_already_cuda = True
                    yield f"data: {json.dumps({'status': 'progress', 'phase': 'torch', 'message': 'CUDA PyTorch already installed — skipping'})}\n\n"
            except (ImportError, Exception):
                pass

            if not torch_already_cuda:
                # Phase 1: Install CUDA PyTorch (replaces CPU version)
                yield f"data: {json.dumps({'status': 'progress', 'phase': 'torch', 'message': 'Installing CUDA PyTorch (~2.5 GB)...'})}\n\n"

                # --index-url ensures torch comes from CUDA index, not PyPI.
                # Pin setuptools<72 to preserve pkg_resources (required by ctranslate2).
                torch_cmd = [
                    python_exe, "-m", "pip", "install",
                    "--upgrade", "--force-reinstall",
                    "--index-url", cuda_torch_index,
                    "torch==2.8.0", "torchaudio==2.8.0",
                    "setuptools>=69,<72",
                ]

                logger.info("Installing CUDA PyTorch: %s", " ".join(torch_cmd))

                process = await asyncio.create_subprocess_exec(
                    *torch_cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                )

                while True:
                    line = await process.stdout.readline()
                    if not line:
                        break
                    line_text = line.decode().strip()
                    if line_text:
                        logger.info("pip (torch): %s", line_text)
                        if "Successfully installed" in line_text:
                            yield f"data: {json.dumps({'status': 'progress', 'phase': 'torch', 'message': line_text[:200]})}\n\n"
                        elif any(kw in line_text for kw in ("Downloading", "Installing", "Using cached")):
                            yield f"data: {json.dumps({'status': 'progress', 'phase': 'torch', 'message': line_text[:200]})}\n\n"
                        elif "Uninstalling" in line_text or "Successfully uninstalled" in line_text:
                            pkg = line_text.replace("Successfully uninstalled ", "").replace("Uninstalling ", "").rstrip(":")
                            yield f"data: {json.dumps({'status': 'progress', 'phase': 'torch', 'message': f'Replacing {pkg} with CUDA version...'})}\n\n"

                await process.wait()

                if process.returncode != 0:
                    yield f"data: {json.dumps({'status': 'error', 'message': 'CUDA PyTorch installation failed. CPU PyTorch may need reinstalling — restart the app to check.'})}\n\n"
                    return

                yield f"data: {json.dumps({'status': 'progress', 'phase': 'torch', 'message': 'CUDA PyTorch installed successfully'})}\n\n"

            # Phase 2: Install CUDA llama-cpp-python
            # Pre-built Windows CUDA wheels only exist up to v0.3.4 on the cu124 index.
            # Try that first; if it fails, attempt building from source with CUDA.
            yield f"data: {json.dumps({'status': 'progress', 'phase': 'llama', 'message': 'Installing CUDA llama-cpp-python...'})}\n\n"

            llama_cmd = [
                python_exe, "-m", "pip", "install",
                "--upgrade", "--force-reinstall",
                "--extra-index-url", cuda_llama_index,
                "llama-cpp-python==0.3.4",
            ]

            logger.info("Installing CUDA llama-cpp-python (pre-built): %s", " ".join(llama_cmd))

            process = await asyncio.create_subprocess_exec(
                *llama_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )

            while True:
                line = await process.stdout.readline()
                if not line:
                    break
                line_text = line.decode().strip()
                if line_text:
                    logger.info("pip (llama): %s", line_text)
                    if "Successfully installed" in line_text:
                        yield f"data: {json.dumps({'status': 'progress', 'phase': 'llama', 'message': line_text[:200]})}\n\n"
                    elif any(kw in line_text for kw in ("Downloading", "Installing", "Using cached")):
                        yield f"data: {json.dumps({'status': 'progress', 'phase': 'llama', 'message': line_text[:200]})}\n\n"
                    elif "Uninstalling" in line_text or "Successfully uninstalled" in line_text:
                        pkg = line_text.replace("Successfully uninstalled ", "").replace("Uninstalling ", "").rstrip(":")
                        yield f"data: {json.dumps({'status': 'progress', 'phase': 'llama', 'message': f'Replacing {pkg} with CUDA version...'})}\n\n"

            await process.wait()

            llama_ok = process.returncode == 0

            # Fallback: build from source with CUDA if pre-built wheel unavailable
            if not llama_ok:
                logger.info("Pre-built CUDA wheel failed, trying source build with DGGML_CUDA=on")
                yield f"data: {json.dumps({'status': 'progress', 'phase': 'llama', 'message': 'Pre-built wheel unavailable, building from source (may take a few minutes)...'})}\n\n"

                build_env = {**os.environ, "CMAKE_ARGS": "-DGGML_CUDA=on"}
                llama_src_cmd = [
                    python_exe, "-m", "pip", "install",
                    "--upgrade", "--force-reinstall", "--no-cache-dir",
                    "llama-cpp-python",
                ]

                process = await asyncio.create_subprocess_exec(
                    *llama_src_cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    env=build_env,
                )

                while True:
                    line = await process.stdout.readline()
                    if not line:
                        break
                    line_text = line.decode().strip()
                    if line_text:
                        logger.info("pip (llama-src): %s", line_text)
                        if any(kw in line_text for kw in ("Building", "Downloading", "Installing", "Successfully installed", "cmake", "Running")):
                            yield f"data: {json.dumps({'status': 'progress', 'phase': 'llama', 'message': line_text[:200]})}\n\n"

                await process.wait()
                llama_ok = process.returncode == 0

            if not llama_ok:
                logger.warning("CUDA llama-cpp-python failed — CPU version will continue working")
                yield f"data: {json.dumps({'status': 'progress', 'phase': 'llama', 'message': 'CUDA llama-cpp-python not available — AI assistant will use CPU'})}\n\n"
            else:
                yield f"data: {json.dumps({'status': 'progress', 'phase': 'llama', 'message': 'CUDA llama-cpp-python installed successfully'})}\n\n"

            # Done — signal restart required
            yield f"data: {json.dumps({'status': 'complete', 'message': 'GPU acceleration enabled. Restarting...', 'restart_required': True})}\n\n"

        except Exception as e:
            logger.exception("GPU acceleration installation failed")
            yield f"data: {json.dumps({'status': 'error', 'message': str(e)})}\n\n"
        finally:
            _gpu_install_in_progress = False

    return StreamingResponse(install_stream(), media_type="text/event-stream")


@router.post("/clear-memory")
async def clear_memory():
    """Force garbage collection and clear GPU caches.

    Call this to free memory after intensive operations.
    """
    import gc

    # Force garbage collection
    gc.collect()

    # Clear GPU caches
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            logger.info("Cleared CUDA cache")
        elif torch.backends.mps.is_available():
            torch.mps.empty_cache()
            logger.info("Cleared MPS cache")
    except ImportError:
        pass
    except Exception as e:
        logger.warning("Failed to clear GPU cache: %s", e)

    # Try to unload OCR model if loaded
    try:
        from services.document_processor import cleanup_ocr_model
        cleanup_ocr_model()
        logger.info("Cleaned up OCR model")
    except Exception as e:
        logger.debug("OCR cleanup skipped: %s", e)

    return {"status": "ok", "message": "Memory cleared"}


class ResetDatabaseRequest(BaseModel):
    """Request body for database reset."""

    delete_media: bool = False


class ResetDatabaseResponse(BaseModel):
    """Response from database reset."""

    success: bool
    deleted: dict[str, int]
    message: str


@router.post("/reset-database", response_model=ResetDatabaseResponse)
async def reset_database(request: ResetDatabaseRequest) -> ResetDatabaseResponse:
    """Reset the database by deleting all user data.

    This is a destructive operation that cannot be undone.
    Preserves system configuration (storage_locations, settings, templates).

    If delete_media is True, also deletes all files in the media directory.
    """
    from persistence.models import (
        Conversation,
        ConversationMessage,
        Document,
        DocumentEmbedding,
        Job,
        Note,
        Project,
        ProjectRecording,
        Recording,
        RecordingTag,
        Segment,
        SegmentComment,
        SegmentEmbedding,
        SegmentHighlight,
        Speaker,
        Tag,
        Transcript,
    )

    deleted_counts: dict[str, int] = {}

    try:
        async with get_session_factory()() as db:
            # Delete in dependency order (children first to respect foreign keys)
            # 1. Embeddings
            result = await db.execute(text("DELETE FROM segment_embeddings"))
            deleted_counts["segment_embeddings"] = result.rowcount
            result = await db.execute(text("DELETE FROM document_embeddings"))
            deleted_counts["document_embeddings"] = result.rowcount

            # 2. Segment children
            result = await db.execute(text("DELETE FROM segment_comments"))
            deleted_counts["segment_comments"] = result.rowcount
            result = await db.execute(text("DELETE FROM segment_highlights"))
            deleted_counts["segment_highlights"] = result.rowcount

            # 3. Notes
            result = await db.execute(text("DELETE FROM notes"))
            deleted_counts["notes"] = result.rowcount

            # 4. Segments and speakers
            result = await db.execute(text("DELETE FROM segments"))
            deleted_counts["segments"] = result.rowcount
            result = await db.execute(text("DELETE FROM speakers"))
            deleted_counts["speakers"] = result.rowcount

            # 5. Transcripts
            result = await db.execute(text("DELETE FROM transcripts"))
            deleted_counts["transcripts"] = result.rowcount

            # 6. Junction tables
            result = await db.execute(text("DELETE FROM recording_tags"))
            deleted_counts["recording_tags"] = result.rowcount
            result = await db.execute(text("DELETE FROM project_recordings"))
            deleted_counts["project_recordings"] = result.rowcount

            # 7. Conversation messages then conversations
            result = await db.execute(text("DELETE FROM conversation_messages"))
            deleted_counts["conversation_messages"] = result.rowcount
            result = await db.execute(text("DELETE FROM conversations"))
            deleted_counts["conversations"] = result.rowcount

            # 8. Main content tables
            result = await db.execute(text("DELETE FROM recordings"))
            deleted_counts["recordings"] = result.rowcount
            result = await db.execute(text("DELETE FROM documents"))
            deleted_counts["documents"] = result.rowcount

            # 9. Organization tables
            result = await db.execute(text("DELETE FROM projects"))
            deleted_counts["projects"] = result.rowcount
            result = await db.execute(text("DELETE FROM tags"))
            deleted_counts["tags"] = result.rowcount

            # 10. Jobs queue
            result = await db.execute(text("DELETE FROM jobs"))
            deleted_counts["jobs"] = result.rowcount

            # 11. Search history
            result = await db.execute(text("DELETE FROM search_history"))
            deleted_counts["search_history"] = result.rowcount

            await db.commit()

            # Run VACUUM to reclaim space (must be outside transaction)
            await db.execute(text("VACUUM"))

        # Delete media files if requested
        media_deleted = 0
        if request.delete_media:
            media_dir = Path(settings.MEDIA_DIR)
            if media_dir.exists():
                for item in media_dir.iterdir():
                    try:
                        if item.is_file():
                            item.unlink()
                            media_deleted += 1
                        elif item.is_dir():
                            shutil.rmtree(item)
                            media_deleted += 1
                    except Exception as e:
                        logger.warning("Failed to delete %s: %s", item, e)
            deleted_counts["media_files"] = media_deleted

        total_deleted = sum(deleted_counts.values())
        logger.info("Database reset complete. Deleted: %s", deleted_counts)

        return ResetDatabaseResponse(
            success=True,
            deleted=deleted_counts,
            message=f"Successfully deleted {total_deleted} items. Database has been reset.",
        )

    except Exception as e:
        logger.exception("Database reset failed")
        return ResetDatabaseResponse(
            success=False,
            deleted=deleted_counts,
            message=f"Reset failed: {str(e)}",
        )


class ClearableCategory(str, Enum):
    """Categories that can be selectively cleared."""

    RECORDINGS = "recordings"  # Includes transcripts, segments, speakers, embeddings
    PROJECTS = "projects"  # Unlinks recordings, doesn't delete them
    DOCUMENTS = "documents"  # Includes document embeddings
    TAGS = "tags"  # All tags
    CONVERSATIONS = "conversations"  # Chat history
    SEARCH_HISTORY = "search_history"
    JOBS = "jobs"  # Job queue


class SelectiveClearRequest(BaseModel):
    """Request body for selective database clearing."""

    categories: list[ClearableCategory]


class CategoryCount(BaseModel):
    """Count for a clearable category."""

    category: ClearableCategory
    count: int
    label: str
    description: str


class CategoryCountsResponse(BaseModel):
    """Response with counts for all clearable categories."""

    categories: list[CategoryCount]


class SelectiveClearResponse(BaseModel):
    """Response from selective clearing."""

    success: bool
    deleted: dict[str, int]
    message: str


@router.get("/category-counts", response_model=CategoryCountsResponse)
async def get_category_counts() -> CategoryCountsResponse:
    """Get counts for all clearable database categories."""
    from persistence.models import (
        Conversation,
        Document,
        Job,
        Project,
        Recording,
        Tag,
    )
    from persistence.models import SearchHistory

    async with get_session_factory()() as db:
        # Get counts for each category
        recordings_count = (await db.execute(text("SELECT COUNT(*) FROM recordings"))).scalar() or 0
        projects_count = (await db.execute(text("SELECT COUNT(*) FROM projects"))).scalar() or 0
        documents_count = (await db.execute(text("SELECT COUNT(*) FROM documents"))).scalar() or 0
        tags_count = (await db.execute(text("SELECT COUNT(*) FROM tags"))).scalar() or 0
        conversations_count = (await db.execute(text("SELECT COUNT(*) FROM conversations"))).scalar() or 0
        search_history_count = (await db.execute(text("SELECT COUNT(*) FROM search_history"))).scalar() or 0
        jobs_count = (await db.execute(text("SELECT COUNT(*) FROM jobs"))).scalar() or 0

    return CategoryCountsResponse(
        categories=[
            CategoryCount(
                category=ClearableCategory.RECORDINGS,
                count=recordings_count,
                label="Recordings",
                description="Includes transcripts, segments, speakers, and embeddings",
            ),
            CategoryCount(
                category=ClearableCategory.PROJECTS,
                count=projects_count,
                label="Projects",
                description="Unlinks recordings but does not delete them",
            ),
            CategoryCount(
                category=ClearableCategory.DOCUMENTS,
                count=documents_count,
                label="Documents",
                description="Includes document embeddings",
            ),
            CategoryCount(
                category=ClearableCategory.TAGS,
                count=tags_count,
                label="Tags",
                description="Removes from all recordings, projects, and documents",
            ),
            CategoryCount(
                category=ClearableCategory.CONVERSATIONS,
                count=conversations_count,
                label="Chat History",
                description="All AI conversations and messages",
            ),
            CategoryCount(
                category=ClearableCategory.SEARCH_HISTORY,
                count=search_history_count,
                label="Search History",
                description="Recent search queries",
            ),
            CategoryCount(
                category=ClearableCategory.JOBS,
                count=jobs_count,
                label="Job Queue",
                description="Pending and completed background jobs",
            ),
        ]
    )


@router.post("/clear-selective", response_model=SelectiveClearResponse)
async def clear_selective(request: SelectiveClearRequest) -> SelectiveClearResponse:
    """Selectively clear specific database categories.

    This allows granular control over what data to delete.
    """
    deleted_counts: dict[str, int] = {}

    try:
        async with get_session_factory()() as db:
            for category in request.categories:
                if category == ClearableCategory.RECORDINGS:
                    # Delete in dependency order
                    result = await db.execute(text("DELETE FROM segment_embeddings"))
                    deleted_counts["segment_embeddings"] = result.rowcount
                    result = await db.execute(text("DELETE FROM segment_comments"))
                    deleted_counts["segment_comments"] = result.rowcount
                    result = await db.execute(text("DELETE FROM segment_highlights"))
                    deleted_counts["segment_highlights"] = result.rowcount
                    result = await db.execute(text("DELETE FROM notes"))
                    deleted_counts["notes"] = result.rowcount
                    result = await db.execute(text("DELETE FROM segments"))
                    deleted_counts["segments"] = result.rowcount
                    result = await db.execute(text("DELETE FROM speakers"))
                    deleted_counts["speakers"] = result.rowcount
                    result = await db.execute(text("DELETE FROM transcripts"))
                    deleted_counts["transcripts"] = result.rowcount
                    result = await db.execute(text("DELETE FROM recording_tags"))
                    deleted_counts["recording_tags"] = result.rowcount
                    result = await db.execute(text("DELETE FROM project_recordings"))
                    deleted_counts["project_recordings"] = result.rowcount
                    result = await db.execute(text("DELETE FROM recordings"))
                    deleted_counts["recordings"] = result.rowcount

                elif category == ClearableCategory.PROJECTS:
                    # Clear project assignments but don't delete recordings
                    result = await db.execute(text("DELETE FROM project_recordings"))
                    deleted_counts["project_recordings"] = deleted_counts.get("project_recordings", 0) + result.rowcount
                    result = await db.execute(text("DELETE FROM projects"))
                    deleted_counts["projects"] = result.rowcount

                elif category == ClearableCategory.DOCUMENTS:
                    result = await db.execute(text("DELETE FROM document_embeddings"))
                    deleted_counts["document_embeddings"] = result.rowcount
                    result = await db.execute(text("DELETE FROM documents"))
                    deleted_counts["documents"] = result.rowcount

                elif category == ClearableCategory.TAGS:
                    # Delete junction tables first
                    result = await db.execute(text("DELETE FROM recording_tags"))
                    deleted_counts["recording_tags"] = deleted_counts.get("recording_tags", 0) + result.rowcount
                    result = await db.execute(text("DELETE FROM tags"))
                    deleted_counts["tags"] = result.rowcount

                elif category == ClearableCategory.CONVERSATIONS:
                    result = await db.execute(text("DELETE FROM conversation_messages"))
                    deleted_counts["conversation_messages"] = result.rowcount
                    result = await db.execute(text("DELETE FROM conversations"))
                    deleted_counts["conversations"] = result.rowcount

                elif category == ClearableCategory.SEARCH_HISTORY:
                    result = await db.execute(text("DELETE FROM search_history"))
                    deleted_counts["search_history"] = result.rowcount

                elif category == ClearableCategory.JOBS:
                    result = await db.execute(text("DELETE FROM jobs"))
                    deleted_counts["jobs"] = result.rowcount

            await db.commit()

        total_deleted = sum(deleted_counts.values())
        categories_str = ", ".join(c.value for c in request.categories)
        logger.info("Selective clear complete. Categories: %s, Deleted: %s", categories_str, deleted_counts)

        return SelectiveClearResponse(
            success=True,
            deleted=deleted_counts,
            message=f"Successfully cleared {total_deleted} items from: {categories_str}",
        )

    except Exception as e:
        logger.exception("Selective clear failed")
        return SelectiveClearResponse(
            success=False,
            deleted=deleted_counts,
            message=f"Clear failed: {str(e)}",
        )

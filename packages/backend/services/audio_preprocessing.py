"""Audio preprocessing via FFmpeg — noise reduction and loudness normalization.

Applies ffmpeg audio filters to improve transcription accuracy before audio
reaches the Whisper pipeline. Designed as a graceful preprocessor: if ffmpeg
fails for any reason the original file is returned unchanged so transcription
can still proceed.
"""

import logging
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class AudioPreprocessOptions:
    """Options for audio preprocessing filters."""

    noise_reduction: bool = False
    """Apply afftdn noise-reduction filter."""

    noise_reduction_strength: float = 0.21
    """afftdn noise floor (0.0-1.0). Higher = more aggressive."""

    normalize: bool = False
    """Apply EBU R128 loudness normalization."""

    target_lufs: float = -16.0
    """Target integrated loudness in LUFS."""


# ---------------------------------------------------------------------------
# FFmpeg path resolution
# ---------------------------------------------------------------------------


def get_ffmpeg_path() -> str:
    """Return the path to the ffmpeg binary.

    Resolution order:
    1. ``FFMPEG_PATH`` environment variable (if set and non-empty).
    2. ``shutil.which("ffmpeg")`` (system PATH lookup).

    Raises:
        FileNotFoundError: If ffmpeg cannot be located.
    """
    env_path = os.environ.get("FFMPEG_PATH", "").strip()
    if env_path:
        return env_path

    system_path = shutil.which("ffmpeg")
    if system_path:
        return system_path

    raise FileNotFoundError(
        "ffmpeg not found. Set the FFMPEG_PATH environment variable or install ffmpeg."
    )


# ---------------------------------------------------------------------------
# Preprocessing
# ---------------------------------------------------------------------------


def preprocess_audio(audio_path: str, options: AudioPreprocessOptions) -> str:
    """Apply optional noise-reduction / normalization to *audio_path*.

    Parameters
    ----------
    audio_path:
        Path to the source audio file.
    options:
        Preprocessing toggles. When both ``noise_reduction`` and ``normalize``
        are ``False`` the function is a no-op and returns *audio_path* unchanged.

    Returns
    -------
    str
        Path to the (possibly new) audio file.  On ffmpeg failure the original
        path is returned so transcription can proceed unaffected.

    Raises
    ------
    FileNotFoundError
        If *audio_path* does not exist on disk.
    """
    if not os.path.exists(audio_path):
        raise FileNotFoundError(f"Input audio file not found: {audio_path}")

    # No-op fast path
    if not options.noise_reduction and not options.normalize:
        return audio_path

    # Build the filter chain
    filters: list[str] = []
    if options.noise_reduction:
        nr = max(0.0, min(1.0, options.noise_reduction_strength))
        filters.append(f"afftdn=nr={nr}:nt=w")
    if options.normalize:
        filters.append(f"loudnorm=I={options.target_lufs}:LRA=11:TP=-1.5")

    af_chain = ",".join(filters)

    # Create temp output with the same suffix as the input
    suffix = Path(audio_path).suffix or ".wav"
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=suffix)
    os.close(tmp_fd)

    ffmpeg_bin = get_ffmpeg_path()
    cmd = [
        ffmpeg_bin,
        "-y",               # overwrite output
        "-i", audio_path,
        "-af", af_chain,
        "-ar", "16000",     # 16 kHz  (Whisper requirement)
        "-ac", "1",         # mono    (Whisper requirement)
        tmp_path,
    ]

    logger.info("Running ffmpeg preprocessing: %s", " ".join(cmd))

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,  # 5-minute timeout
        )
        if result.returncode != 0:
            logger.error("ffmpeg failed (rc=%d): %s", result.returncode, result.stderr)
            _cleanup(tmp_path)
            return audio_path  # graceful fallback

        logger.info("Preprocessing complete: %s", tmp_path)
        return tmp_path

    except subprocess.TimeoutExpired:
        logger.error("ffmpeg timed out after 300 s for %s", audio_path)
        _cleanup(tmp_path)
        return audio_path  # graceful fallback

    except Exception:
        _cleanup(tmp_path)
        raise


def _cleanup(path: str) -> None:
    """Remove *path* if it exists, swallowing errors."""
    try:
        os.unlink(path)
    except OSError:
        pass

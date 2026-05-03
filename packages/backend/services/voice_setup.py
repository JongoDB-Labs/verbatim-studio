"""Lazy installer for voice-runtime Python packages.

The Windows installer deliberately strips the LiveKit suite (livekit, livekit-
agents, livekit-api, livekit-protocol, livekit-plugins-silero) and Kokoro because
livekit-rtc's native DLL alone is ~50MB and pushes the NSIS package past the
2GB mmap ceiling. Instead we install the voice runtime on first need.

Two entry points trigger an install:
    1. TTS model download (via api/routes/voice.py) — historical path
    2. Voice session creation (via api/routes/voice.py) — self-healing path
       for users who downloaded a TTS model in a build that predated the
       on-demand install hook

Both call install_voice_runtime() and get the same idempotent behavior.
"""

from __future__ import annotations

import logging
import subprocess
import sys

logger = logging.getLogger(__name__)

# (pip name, importable module path, pip spec). The module path is what
# importlib will try; if any of the imports fails, the corresponding pip
# spec is added to the install list.
VOICE_DEPS: list[tuple[str, str, str]] = [
    ("livekit-protocol", "livekit.protocol", "livekit-protocol>=1.1.0"),
    ("livekit", "livekit.rtc", "livekit>=1.1.0"),
    ("livekit-api", "livekit.api", "livekit-api>=1.1.0"),
    ("livekit-agents", "livekit.agents", "livekit-agents>=1.5.0"),
    ("livekit-plugins-silero", "livekit.plugins.silero", "livekit-plugins-silero>=1.5.0"),
]


def missing_voice_deps() -> list[str]:
    """Return pip specs for any voice deps that aren't importable."""
    missing: list[str] = []
    for _name, import_path, spec in VOICE_DEPS:
        try:
            __import__(import_path)
        except ImportError:
            missing.append(spec)
    return missing


def install_voice_runtime(timeout: int = 600) -> tuple[bool, str]:
    """Install missing voice deps via pip into the running interpreter.

    On macOS this is a no-op — voice deps are bundled.

    Returns:
        (ok, message). ok is False if the install failed; message gives
        a user-facing description.
    """
    if sys.platform == "darwin":
        return True, "macOS bundle ships voice runtime"

    missing = missing_voice_deps()
    if not missing:
        return True, "Voice runtime already installed"

    logger.info("Installing missing voice runtime packages: %s", missing)
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", *missing],
            capture_output=True, text=True, check=True, timeout=timeout,
        )
        logger.info("Voice runtime installed:\n%s", result.stdout[-2000:])
        return True, f"Installed {len(missing)} voice packages"
    except subprocess.CalledProcessError as exc:
        logger.error("Voice runtime install failed (rc=%s):\n%s", exc.returncode, exc.stderr)
        return False, f"pip install failed: {exc.stderr[-500:] if exc.stderr else 'unknown error'}"
    except subprocess.TimeoutExpired:
        logger.error("Voice runtime install timed out after %ds", timeout)
        return False, f"Install timed out after {timeout}s"

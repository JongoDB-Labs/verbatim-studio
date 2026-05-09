"""Decode WebM/Opus audio chunks to mono float32 PCM at 16 kHz.

Used by the live transcription pipeline to convert browser-encoded
WebM/Opus blobs into the raw PCM that Whisper, Silero VAD, and pyannote
all consume. Uses PyAV (Python bindings for ffmpeg) to keep decoding in
the same process — much lower per-chunk overhead than spawning ffmpeg
as a subprocess for each chunk.

Public API:
    decode_chunk_to_pcm(audio_bytes) -> np.ndarray of shape (samples,)

The output is always 16 kHz mono float32 in [-1, 1].
"""

from __future__ import annotations

import io
import logging

import numpy as np

logger = logging.getLogger(__name__)

TARGET_SAMPLE_RATE = 16_000


def decode_chunk_to_pcm(audio_bytes: bytes) -> np.ndarray:
    """Decode a WebM/Opus (or similar) blob to 16 kHz mono float32 PCM.

    Returns an empty array if the blob has no audio frames or fails to
    decode. Errors are logged at debug level — a single bad chunk should
    not stop the whole live session.

    Args:
        audio_bytes: Raw bytes of an audio container (WebM/Opus is the
            primary case from MediaRecorder, but anything ffmpeg can
            decode is fine — Ogg/Opus, WAV, MP4, etc.).

    Returns:
        1-D numpy array of float32 samples in [-1, 1] at 16 kHz mono.
    """
    if not audio_bytes:
        return np.zeros(0, dtype=np.float32)

    try:
        import av
    except ImportError:
        logger.error("PyAV not installed — cannot decode audio chunks")
        return np.zeros(0, dtype=np.float32)

    try:
        container = av.open(io.BytesIO(audio_bytes))
    except Exception as exc:
        logger.debug("Audio chunk failed to open: %s", exc)
        return np.zeros(0, dtype=np.float32)

    try:
        audio_streams = [s for s in container.streams if s.type == "audio"]
        if not audio_streams:
            return np.zeros(0, dtype=np.float32)

        resampler = av.audio.resampler.AudioResampler(
            format="flt",
            layout="mono",
            rate=TARGET_SAMPLE_RATE,
        )

        chunks: list[np.ndarray] = []
        for frame in container.decode(audio_streams[0]):
            for resampled in resampler.resample(frame):
                # to_ndarray returns shape (channels, samples) — we
                # forced layout="mono" so channels==1.
                arr = resampled.to_ndarray()
                if arr.ndim == 2:
                    arr = arr[0]
                chunks.append(arr.astype(np.float32, copy=False))

        # Flush the resampler — anything still queued comes out here.
        for resampled in resampler.resample(None):
            arr = resampled.to_ndarray()
            if arr.ndim == 2:
                arr = arr[0]
            chunks.append(arr.astype(np.float32, copy=False))

        if not chunks:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(chunks)
    except Exception as exc:
        logger.debug("Audio chunk decode error: %s", exc)
        return np.zeros(0, dtype=np.float32)
    finally:
        container.close()


def pcm_to_wav_bytes(pcm: np.ndarray, sample_rate: int = TARGET_SAMPLE_RATE) -> bytes:
    """Wrap a float32 PCM array in a minimal RIFF/WAV header.

    Whisper engines expect a file path, so we write the rolling buffer
    to a tempfile as 16-bit PCM WAV. This helper builds the bytes; the
    caller writes them to disk.
    """
    if pcm.size == 0:
        return b""
    pcm_clipped = np.clip(pcm, -1.0, 1.0)
    pcm_int16 = (pcm_clipped * 32767.0).astype(np.int16)

    import struct

    num_samples = pcm_int16.shape[0]
    byte_rate = sample_rate * 2  # mono, 16-bit
    block_align = 2  # mono, 16-bit
    data_size = num_samples * 2

    header = b"".join([
        b"RIFF",
        struct.pack("<I", 36 + data_size),
        b"WAVE",
        b"fmt ",
        struct.pack("<I", 16),       # fmt chunk size
        struct.pack("<H", 1),        # PCM
        struct.pack("<H", 1),        # mono
        struct.pack("<I", sample_rate),
        struct.pack("<I", byte_rate),
        struct.pack("<H", block_align),
        struct.pack("<H", 16),       # bits per sample
        b"data",
        struct.pack("<I", data_size),
    ])
    return header + pcm_int16.tobytes()

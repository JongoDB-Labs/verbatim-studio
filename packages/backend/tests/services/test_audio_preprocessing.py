"""Tests for audio preprocessing service (ffmpeg noise reduction & normalization)."""

import math
import os
import shutil
import struct
import wave

import pytest

from services.audio_preprocessing import AudioPreprocessOptions, get_ffmpeg_path, preprocess_audio

# Skip entire module if ffmpeg is not available
_ffmpeg_available = shutil.which("ffmpeg") is not None or os.environ.get("FFMPEG_PATH")
pytestmark = pytest.mark.skipif(not _ffmpeg_available, reason="ffmpeg not found on PATH")


def _create_sine_wav(path: str, duration_s: float = 0.5, sample_rate: int = 44100) -> str:
    """Create a minimal WAV file containing a 440 Hz sine tone."""
    n_samples = int(sample_rate * duration_s)
    with wave.open(path, "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)  # 16-bit
        wf.setframerate(sample_rate)
        for i in range(n_samples):
            sample = int(32767 * math.sin(2 * math.pi * 440 * i / sample_rate))
            wf.writeframes(struct.pack("<h", sample))
    return path


@pytest.fixture()
def wav_file(tmp_path):
    """Provide a temporary WAV file with a sine tone."""
    path = str(tmp_path / "test_input.wav")
    _create_sine_wav(path)
    return path


# ---------------------------------------------------------------------------
# get_ffmpeg_path
# ---------------------------------------------------------------------------


class TestGetFfmpegPath:
    def test_returns_string(self):
        result = get_ffmpeg_path()
        assert isinstance(result, str)
        assert len(result) > 0

    def test_respects_env_var(self, monkeypatch):
        monkeypatch.setenv("FFMPEG_PATH", "/fake/ffmpeg")
        assert get_ffmpeg_path() == "/fake/ffmpeg"

    def test_raises_when_not_found(self, monkeypatch):
        monkeypatch.setenv("FFMPEG_PATH", "")
        monkeypatch.setattr(shutil, "which", lambda _: None)
        with pytest.raises(FileNotFoundError):
            get_ffmpeg_path()


# ---------------------------------------------------------------------------
# preprocess_audio — no-op path
# ---------------------------------------------------------------------------


class TestPreprocessNoOp:
    def test_returns_original_path_when_both_false(self, wav_file):
        opts = AudioPreprocessOptions(noise_reduction=False, normalize=False)
        result = preprocess_audio(wav_file, opts)
        assert result == wav_file

    def test_returns_original_path_with_defaults(self, wav_file):
        opts = AudioPreprocessOptions()
        result = preprocess_audio(wav_file, opts)
        assert result == wav_file


# ---------------------------------------------------------------------------
# preprocess_audio — noise reduction
# ---------------------------------------------------------------------------


class TestPreprocessNoiseReduction:
    def test_returns_new_path(self, wav_file):
        opts = AudioPreprocessOptions(noise_reduction=True)
        result = preprocess_audio(wav_file, opts)
        assert result != wav_file
        assert os.path.exists(result)

    def test_output_has_same_suffix(self, wav_file):
        opts = AudioPreprocessOptions(noise_reduction=True)
        result = preprocess_audio(wav_file, opts)
        assert result.endswith(".wav")

    def test_output_is_valid_wav(self, wav_file):
        opts = AudioPreprocessOptions(noise_reduction=True)
        result = preprocess_audio(wav_file, opts)
        with wave.open(result, "r") as wf:
            assert wf.getnframes() > 0


# ---------------------------------------------------------------------------
# preprocess_audio — normalization
# ---------------------------------------------------------------------------


class TestPreprocessNormalize:
    def test_returns_new_path(self, wav_file):
        opts = AudioPreprocessOptions(normalize=True)
        result = preprocess_audio(wav_file, opts)
        assert result != wav_file
        assert os.path.exists(result)

    def test_output_is_valid_wav(self, wav_file):
        opts = AudioPreprocessOptions(normalize=True)
        result = preprocess_audio(wav_file, opts)
        with wave.open(result, "r") as wf:
            assert wf.getnframes() > 0


# ---------------------------------------------------------------------------
# preprocess_audio — combined
# ---------------------------------------------------------------------------


class TestPreprocessCombined:
    def test_both_filters_returns_new_path(self, wav_file):
        opts = AudioPreprocessOptions(noise_reduction=True, normalize=True)
        result = preprocess_audio(wav_file, opts)
        assert result != wav_file
        assert os.path.exists(result)


# ---------------------------------------------------------------------------
# preprocess_audio — error handling
# ---------------------------------------------------------------------------


class TestPreprocessErrors:
    def test_raises_for_nonexistent_input(self):
        opts = AudioPreprocessOptions(noise_reduction=True)
        with pytest.raises(FileNotFoundError):
            preprocess_audio("/nonexistent/audio.wav", opts)

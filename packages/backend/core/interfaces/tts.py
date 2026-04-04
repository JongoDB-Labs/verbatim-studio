"""TTS service interface definitions.

This module defines the contract for text-to-speech operations,
allowing different implementations (Qwen3-TTS local, cloud TTS, etc.)
to be swapped transparently.
"""

from abc import ABC, abstractmethod
from typing import AsyncIterator


class ITTSService(ABC):
    """Interface for text-to-speech operations.

    Implementations can wrap:
    - Qwen3-TTS (local, via LiveKit Agents)
    - Cloud TTS APIs
    - etc.
    """

    @abstractmethod
    async def synthesize(
        self,
        text: str,
        voice: str | None = None,
    ) -> bytes:
        """Synthesize speech from text.

        Args:
            text: Text to synthesize
            voice: Optional voice identifier

        Returns:
            Full audio data as bytes
        """
        ...

    @abstractmethod
    async def synthesize_stream(
        self,
        text: str,
        voice: str | None = None,
    ) -> AsyncIterator[bytes]:
        """Synthesize speech from text in streaming chunks.

        Args:
            text: Text to synthesize
            voice: Optional voice identifier

        Yields:
            Audio data chunks as bytes
        """
        ...

    @abstractmethod
    async def list_voices(self) -> list[dict]:
        """Get list of available voices.

        Returns:
            List of voice info dicts
        """
        ...

    @abstractmethod
    async def is_available(self) -> bool:
        """Check if the TTS service is available and model is loaded.

        Returns:
            True if service is ready to synthesize
        """
        ...

    @abstractmethod
    async def load(self, model_path: str) -> None:
        """Load a TTS model.

        Args:
            model_path: Path to the model to load
        """
        ...

    @abstractmethod
    async def unload(self) -> None:
        """Unload the current model and free memory."""
        ...

"""TTS model catalog — Qwen3-TTS for Apple Silicon (MLX).

Single recommended model. Uses Ryan voice at 1.3x speed.
"""

TTS_CATALOG: dict[str, dict] = {
    "qwen3-tts": {
        "repo": "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-8bit",
        "label": "Qwen3-TTS 1.7B",
        "description": "Text-to-speech for Max voice chat. ~5 GB RAM.",
        "size_bytes": 2_900_000_000,
        "ram_gb": 5,
        "tier": "basic",
        "platform": "darwin",
    },
}

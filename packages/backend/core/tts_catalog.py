"""TTS model catalog — Qwen3-TTS variants."""

TTS_CATALOG: dict[str, dict] = {
    "qwen3-tts-0.6b": {
        "repo": "Qwen/Qwen3-TTS-0.6B-MLX-8bit",
        "label": "Qwen3-TTS 0.6B (Lite)",
        "description": "Fast, lightweight TTS. ~2-3 GB RAM. Good for most use cases.",
        "size_bytes": 700_000_000,
        "ram_gb": 3,
        "tier": "basic",
        "platform": "darwin",
    },
    "qwen3-tts-1.7b": {
        "repo": "Qwen/Qwen3-TTS-1.7B-MLX-8bit",
        "label": "Qwen3-TTS 1.7B (Pro)",
        "description": "Higher quality, more natural speech. ~6 GB RAM.",
        "size_bytes": 2_000_000_000,
        "ram_gb": 6,
        "tier": "basic",
        "platform": "darwin",
    },
}

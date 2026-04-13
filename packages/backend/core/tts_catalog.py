"""TTS model catalog — Qwen3-TTS MLX variants (Apple Silicon).

Models from mlx-community on HuggingFace. All Apache-2.0 licensed.
Requires mlx-audio library for inference (not mlx-lm).
Tokenizer: Qwen/Qwen3-TTS-Tokenizer-12Hz (auto-downloaded).
"""

TTS_CATALOG: dict[str, dict] = {
    "qwen3-tts-0.6b-base": {
        "repo": "mlx-community/Qwen3-TTS-12Hz-0.6B-Base-8bit",
        "label": "Qwen3-TTS 0.6B Base",
        "description": "Fast, lightweight TTS. ~2 GB RAM. Good for basic voice output.",
        "size_bytes": 700_000_000,
        "ram_gb": 2,
        "tier": "basic",
        "platform": "darwin",
    },
    "qwen3-tts-0.6b-custom": {
        "repo": "mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit",
        "label": "Qwen3-TTS 0.6B Custom Voice",
        "description": "Lightweight TTS with voice cloning from a reference audio clip.",
        "size_bytes": 700_000_000,
        "ram_gb": 2,
        "tier": "basic",
        "platform": "darwin",
    },
    "qwen3-tts-1.7b-base": {
        "repo": "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-8bit",
        "label": "Qwen3-TTS 1.7B Base",
        "description": "Higher quality speech synthesis. ~5 GB RAM.",
        "size_bytes": 2_000_000_000,
        "ram_gb": 5,
        "tier": "basic",
        "platform": "darwin",
    },
    "qwen3-tts-1.7b-custom": {
        "repo": "mlx-community/Qwen3-TTS-12Hz-1.7B-CustomVoice-8bit",
        "label": "Qwen3-TTS 1.7B Custom Voice",
        "description": "Best quality TTS with voice cloning. ~5 GB RAM. Most popular variant.",
        "size_bytes": 2_000_000_000,
        "ram_gb": 5,
        "tier": "basic",
        "platform": "darwin",
    },
}

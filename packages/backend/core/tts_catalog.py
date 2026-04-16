"""TTS model catalog — voice models for Max voice chat.

Two options:
- Kokoro 82M: Ultra-fast (~0.3s/sentence), smaller, good quality
- Qwen3-TTS 1.7B: Higher quality with voice cloning, slower (~10s/sentence)
"""

TTS_CATALOG: dict[str, dict] = {
    # ── macOS (Apple Silicon / MLX) ──────────────────────────────────
    "kokoro-82m": {
        "repo": "mlx-community/Kokoro-82M-bf16",
        "label": "Kokoro 82M (Fast)",
        "description": "Ultra-fast TTS. ~0.3s per sentence. ~1 GB RAM. Recommended for voice chat.",
        "size_bytes": 350_000_000,
        "ram_gb": 1,
        "tier": "basic",
        "platform": "darwin",
        "engine": "kokoro",
    },
    "qwen3-tts": {
        "repo": "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-8bit",
        "label": "Qwen3-TTS 1.7B (Quality)",
        "description": "Higher quality with voice cloning. ~10s per sentence. ~5 GB RAM.",
        "size_bytes": 2_900_000_000,
        "ram_gb": 5,
        "tier": "basic",
        "platform": "darwin",
        "engine": "qwen3",
    },
    # ── Windows / Linux (ONNX Runtime) ───────────────────────────────
    "kokoro-onnx": {
        "repo": "onnx-community/Kokoro-82M-v1.0-ONNX",
        "label": "Kokoro 82M ONNX (Fast)",
        "description": "Ultra-fast TTS via ONNX Runtime. ~0.3s per sentence. ~1 GB RAM. Supports CUDA.",
        "size_bytes": 330_000_000,
        "ram_gb": 1,
        "tier": "basic",
        "platform": "win32",
        "engine": "kokoro-onnx",
    },
}

#!/bin/bash
set -e

# Prepare resources for Electron build
# This copies Python and backend code to the build/resources directory

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Determine architecture
case "$(uname -m)" in
  x86_64) ARCH="x64" ;;
  arm64|aarch64) ARCH="arm64" ;;
  *) echo "Unsupported architecture: $(uname -m)"; exit 1 ;;
esac

# Determine platform
case "$(uname -s)" in
  Darwin) PLATFORM="macos" ;;
  Linux) PLATFORM="linux" ;;
  MINGW*|MSYS*|CYGWIN*) PLATFORM="windows" ;;
  *) echo "Unsupported platform: $(uname -s)"; exit 1 ;;
esac

PYTHON_DIR="$PROJECT_ROOT/build/python-standalone/python-${PLATFORM}-${ARCH}"
RESOURCES_DIR="$PROJECT_ROOT/build/resources"
BACKEND_SRC="$PROJECT_ROOT/packages/backend"

echo "=== Preparing Electron Resources ==="
echo "Platform: $PLATFORM"
echo "Architecture: $ARCH"
echo "Python: $PYTHON_DIR"
echo "Resources: $RESOURCES_DIR"
echo ""

# Verify Python exists
if [ ! -d "$PYTHON_DIR" ]; then
  echo "Error: Python not found at $PYTHON_DIR"
  echo "Run ./scripts/download-python-standalone.sh first"
  exit 1
fi

# Clean and create resources directory (preserve cached resources if they exist)
TEMP_PRESERVE=$(mktemp -d)
PRESERVED_ITEMS=()

# Preserve whisper-models if they exist
if [ -d "$RESOURCES_DIR/whisper-models" ]; then
  echo "Preserving whisper-models directory..."
  mv "$RESOURCES_DIR/whisper-models" "$TEMP_PRESERVE/"
  PRESERVED_ITEMS+=("whisper-models")
fi

# Preserve ffmpeg if it exists
if [ -d "$RESOURCES_DIR/ffmpeg" ]; then
  echo "Preserving ffmpeg directory..."
  mv "$RESOURCES_DIR/ffmpeg" "$TEMP_PRESERVE/"
  PRESERVED_ITEMS+=("ffmpeg")
fi

# Preserve cuda libs if they exist (Windows CUDA DLLs)
if [ -d "$RESOURCES_DIR/cuda" ]; then
  echo "Preserving cuda directory..."
  mv "$RESOURCES_DIR/cuda" "$TEMP_PRESERVE/"
  PRESERVED_ITEMS+=("cuda")
fi

# Preserve embedding-models if they exist
if [ -d "$RESOURCES_DIR/embedding-models" ]; then
  echo "Preserving embedding-models directory..."
  mv "$RESOURCES_DIR/embedding-models" "$TEMP_PRESERVE/"
  PRESERVED_ITEMS+=("embedding-models")
fi

rm -rf "$RESOURCES_DIR"
mkdir -p "$RESOURCES_DIR"

# Restore preserved directories
for item in "${PRESERVED_ITEMS[@]}"; do
  if [ -d "$TEMP_PRESERVE/$item" ]; then
    mv "$TEMP_PRESERVE/$item" "$RESOURCES_DIR/"
    echo "Restored: $item"
  fi
done
rm -rf "$TEMP_PRESERVE"

# Copy Python
echo "Copying Python..."
cp -R "$PYTHON_DIR" "$RESOURCES_DIR/python"

# Copy backend source code (excluding venv, cache, etc.)
echo "Copying backend..."
mkdir -p "$RESOURCES_DIR/backend"
cp -R "$BACKEND_SRC/api" "$RESOURCES_DIR/backend/"
cp -R "$BACKEND_SRC/core" "$RESOURCES_DIR/backend/" 2>/dev/null || true
cp -R "$BACKEND_SRC/services" "$RESOURCES_DIR/backend/" 2>/dev/null || true
cp -R "$BACKEND_SRC/persistence" "$RESOURCES_DIR/backend/" 2>/dev/null || true
cp -R "$BACKEND_SRC/storage" "$RESOURCES_DIR/backend/" 2>/dev/null || true
cp -R "$BACKEND_SRC/jobs" "$RESOURCES_DIR/backend/" 2>/dev/null || true
cp -R "$BACKEND_SRC/adapters" "$RESOURCES_DIR/backend/" 2>/dev/null || true
cp -R "$BACKEND_SRC/migrations" "$RESOURCES_DIR/backend/" 2>/dev/null || true

# Copy pyproject.toml for package metadata
cp "$BACKEND_SRC/pyproject.toml" "$RESOURCES_DIR/backend/"

# Remove any database files that shouldn't be bundled
rm -f "$RESOURCES_DIR/backend/"*.db* 2>/dev/null || true

# Remove __pycache__ directories
find "$RESOURCES_DIR/backend" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true

# Copy ML requirements for on-demand installation and dep reconciliation
for reqfile in requirements-ml.txt requirements-ml-windows.txt; do
  if [ -f "$SCRIPT_DIR/$reqfile" ]; then
    cp "$SCRIPT_DIR/$reqfile" "$RESOURCES_DIR/"
  fi
done

# Download and copy FFmpeg binaries
echo "Setting up FFmpeg..."
FFMPEG_DIR="$PROJECT_ROOT/build/resources/ffmpeg"
if [ ! -f "$FFMPEG_DIR/ffmpeg" ] && [ ! -f "$FFMPEG_DIR/ffmpeg.exe" ]; then
  echo "Downloading FFmpeg..."
  "$SCRIPT_DIR/download-ffmpeg.sh"
fi

if [ -d "$FFMPEG_DIR" ]; then
  echo "Copying FFmpeg binaries..."
  mkdir -p "$RESOURCES_DIR/ffmpeg"
  cp -R "$FFMPEG_DIR/"* "$RESOURCES_DIR/ffmpeg/" 2>/dev/null || true
  ls -la "$RESOURCES_DIR/ffmpeg"
else
  echo "Warning: FFmpeg binaries not found, video processing may not work"
fi

# Download LiveKit server binary (voice chat requires a local LiveKit server)
echo "Setting up LiveKit server..."
LIVEKIT_DIR="$PROJECT_ROOT/build/resources/bin"
if [ ! -f "$LIVEKIT_DIR/livekit-server" ] && [ ! -f "$LIVEKIT_DIR/livekit-server.exe" ]; then
  echo "Downloading LiveKit server..."
  "$SCRIPT_DIR/download-livekit.sh" || echo "Warning: LiveKit download failed — voice chat will not be available"
fi

if [ -d "$LIVEKIT_DIR" ]; then
  echo "Copying LiveKit binary..."
  mkdir -p "$RESOURCES_DIR/bin"
  cp -R "$LIVEKIT_DIR/"* "$RESOURCES_DIR/bin/" 2>/dev/null || true
  ls -la "$RESOURCES_DIR/bin"
else
  echo "Warning: LiveKit binary not found, voice chat will not be available"
fi

# Bundle voice clones — the Max assistant's reference voice for Qwen3 cloning.
# Files in assets/voice_clones/ (excluding README) are copied into the bundle
# and migrated to user data on first launch.
echo "Setting up voice clones..."
VOICE_CLONES_SRC="$PROJECT_ROOT/assets/voice_clones"
if [ -d "$VOICE_CLONES_SRC" ]; then
  mkdir -p "$RESOURCES_DIR/voice_clones"
  # Copy WAV and TXT files only (skip README and other docs)
  find "$VOICE_CLONES_SRC" -maxdepth 1 \( -name "*.wav" -o -name "*.txt" \) \
    -exec cp {} "$RESOURCES_DIR/voice_clones/" \;
  CLONE_COUNT=$(find "$RESOURCES_DIR/voice_clones" -name "*.wav" 2>/dev/null | wc -l | tr -d ' ')
  if [ "$CLONE_COUNT" -gt 0 ]; then
    echo "Bundled $CLONE_COUNT voice clone(s):"
    ls -la "$RESOURCES_DIR/voice_clones"
  else
    echo "Warning: No .wav files in $VOICE_CLONES_SRC — Qwen3 voice will be random until user uploads one"
  fi
else
  echo "Warning: $VOICE_CLONES_SRC does not exist — skipping voice clone bundling"
fi

# Bundle the vocabulary corpus DB.
# Two sources for vocab_bundled.db, in priority order:
#   1. CI environment: download via build-vocab-corpus workflow
#      artifact (preferred — has full Nomic embeddings, ~350 MB).
#   2. Local checkout: assets/vocab_bundled.db built via
#      `python -m scripts.build_vocab_corpus` (typically without
#      embeddings during dev — runtime falls back to BM25-only).
# When neither exists, we don't error — runtime degrades to user-only
# dictionary, which keeps the app shippable while corpus is iterating.
echo "Setting up vocabulary corpus..."
VOCAB_DB_SRC="$PROJECT_ROOT/assets/vocab_bundled.db"
if [ -f "$VOCAB_DB_SRC" ]; then
  cp "$VOCAB_DB_SRC" "$RESOURCES_DIR/vocab_bundled.db"
  VOCAB_SIZE=$(ls -la "$RESOURCES_DIR/vocab_bundled.db" | awk '{print $5}')
  echo "Bundled vocab corpus: $VOCAB_SIZE bytes ($(numfmt --to=iec --suffix=B $VOCAB_SIZE 2>/dev/null || echo "${VOCAB_SIZE}B"))"
else
  echo "Warning: $VOCAB_DB_SRC not found — runtime will use BM25-only fallback"
  echo "  Build via: python -m scripts.build_vocab_corpus"
fi

echo ""
echo "=== Done ==="
echo "Resources prepared at: $RESOURCES_DIR"
echo ""
ls -la "$RESOURCES_DIR"

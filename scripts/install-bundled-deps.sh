#!/bin/bash
set -e

# Install Python dependencies for bundling (core + ML)
# Usage: ./install-bundled-deps.sh [python-dir]
#
# IMPORTANT: This script uses STRICT version pins from requirements-ml.txt
# to prevent pip from pulling incompatible package versions.

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

# Python directory
PYTHON_DIR="${1:-$PROJECT_ROOT/build/python-standalone/python-${PLATFORM}-${ARCH}}"

# Python binary path differs on Windows
if [ "$PLATFORM" = "windows" ]; then
  PYTHON_BIN="$PYTHON_DIR/python.exe"
else
  PYTHON_BIN="$PYTHON_DIR/bin/python3"
fi

if [ ! -f "$PYTHON_BIN" ]; then
  echo "Error: Python not found at $PYTHON_BIN"
  echo "Run ./scripts/download-python-standalone.sh first"
  exit 1
fi

# Site-packages directory (inside the Python installation)
if [ "$PLATFORM" = "windows" ]; then
  SITE_PACKAGES="$PYTHON_DIR/Lib/site-packages"
else
  SITE_PACKAGES="$PYTHON_DIR/lib/python3.12/site-packages"
fi

REQUIREMENTS_CORE="$SCRIPT_DIR/requirements-core.txt"
REQUIREMENTS_ML="$SCRIPT_DIR/requirements-ml.txt"

echo "=== Installing Bundled Dependencies ==="
echo "Python: $PYTHON_BIN"
echo "Site-packages: $SITE_PACKAGES"
echo "Platform: $PLATFORM"
echo "Architecture: $ARCH"
echo ""

# Upgrade pip first, then pin setuptools <72 (82.0 dropped pkg_resources top-level module)
# ctranslate2 needs `import pkg_resources` at import time
echo "Upgrading pip..."
"$PYTHON_BIN" -m pip install --upgrade pip --quiet
echo "Installing setuptools with pkg_resources..."
"$PYTHON_BIN" -m pip install 'setuptools>=69,<72' --quiet

# =============================================================================
# Install core dependencies
# =============================================================================
echo ""
echo "=== Installing Core Dependencies ==="
# On Windows, llama-cpp-python needs pre-built CPU wheels from the custom index.
# Standard PyPI has no pre-built Windows wheels (requires CMake/MSVC to build).
CORE_EXTRA_ARGS=""
if [ "$PLATFORM" = "windows" ]; then
  CORE_EXTRA_ARGS="--extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu"
  echo "  (using pre-built llama-cpp-python CPU wheels for Windows)"
fi
"$PYTHON_BIN" -m pip install \
  --target "$SITE_PACKAGES" \
  --upgrade \
  $CORE_EXTRA_ARGS \
  -r "$REQUIREMENTS_CORE"

# =============================================================================
# Install ML dependencies with STRICT version pins
# Uses requirements-ml.txt which has exact version pins (==)
# =============================================================================
echo ""
echo "=== Installing ML Dependencies (Strict Pins) ==="
echo "Using exact versions from requirements-ml.txt to prevent compatibility issues"

# Install ML dependencies directly from requirements file
# --no-deps first pass to install exact versions without letting pip pull newer deps
# Then a second pass to resolve any missing sub-dependencies

if [ "$PLATFORM" = "macos" ] && [ "$ARCH" = "arm64" ]; then
  echo "Installing for Apple Silicon (includes mlx-whisper)..."

  # Step 1: Install the pinned packages without their dependencies
  # This ensures we get EXACTLY the versions we specify
  "$PYTHON_BIN" -m pip install \
    --target "$SITE_PACKAGES" \
    --no-deps \
    -r "$REQUIREMENTS_ML"

  # Step 2: Install missing sub-dependencies, but use requirements-ml.txt as constraints
  # This prevents pip from upgrading our pinned packages
  "$PYTHON_BIN" -m pip install \
    --target "$SITE_PACKAGES" \
    --constraint "$REQUIREMENTS_ML" \
    -r "$REQUIREMENTS_ML"

  # Step 3: Install mlx-audio separately (requires huggingface_hub>=1.0 which
  # conflicts with whisperx's pin). Using --no-deps to avoid pulling in
  # conflicting transitive deps — the necessary deps are already installed.
  echo "Installing mlx-audio (voice TTS) without conflicting deps..."
  "$PYTHON_BIN" -m pip install \
    --target "$SITE_PACKAGES" \
    --no-deps \
    "mlx-audio>=0.4.0"

  # Install mlx-audio's unique deps that aren't already present
  "$PYTHON_BIN" -m pip install \
    --target "$SITE_PACKAGES" \
    --no-deps \
    "mlx-lm>=0.31.0" \
    "sounddevice>=0.5.0" \
    "miniaudio>=1.61" \
    "pyloudnorm>=0.2.0" \
    "misaki>=0.9.0" \
    "phonemizer-fork>=3.3.0" \
    "espeakng-loader>=0.2.0" 2>/dev/null || true

elif [ "$PLATFORM" = "windows" ]; then
  echo "Installing for Windows x64 (CPU PyTorch + CTranslate2 GPU)..."

  REQUIREMENTS_ML_WIN="$SCRIPT_DIR/requirements-ml-windows.txt"

  # Step 1: Install the pinned packages without their dependencies
  # No --extra-index-url needed: CPU torch comes from standard PyPI
  # GPU transcription uses CTranslate2's native CUDA bindings (not PyTorch CUDA)
  "$PYTHON_BIN" -m pip install \
    --target "$SITE_PACKAGES" \
    --no-deps \
    -r "$REQUIREMENTS_ML_WIN"

  # Step 2: Install missing sub-dependencies with constraints
  "$PYTHON_BIN" -m pip install \
    --target "$SITE_PACKAGES" \
    --constraint "$REQUIREMENTS_ML_WIN" \
    -r "$REQUIREMENTS_ML_WIN"

else
  echo "Installing for ${PLATFORM}-${ARCH} (excludes mlx-whisper)..."

  # Create a temp file without mlx-whisper for non-Apple platforms
  TEMP_REQUIREMENTS=$(mktemp)
  grep -v "mlx-whisper" "$REQUIREMENTS_ML" > "$TEMP_REQUIREMENTS"

  # Step 1: Install the pinned packages without their dependencies
  "$PYTHON_BIN" -m pip install \
    --target "$SITE_PACKAGES" \
    --no-deps \
    -r "$TEMP_REQUIREMENTS"

  # Step 2: Install missing sub-dependencies with constraints
  "$PYTHON_BIN" -m pip install \
    --target "$SITE_PACKAGES" \
    --constraint "$TEMP_REQUIREMENTS" \
    -r "$TEMP_REQUIREMENTS"

  rm -f "$TEMP_REQUIREMENTS"
fi

# =============================================================================
# Restore setuptools <72 if --target installs overwrote it with a newer version
# (torch depends on setuptools, --target may pull 82.0+ which lacks pkg_resources)
# =============================================================================
echo ""
echo "=== Ensuring pkg_resources is available ==="
"$PYTHON_BIN" -c "import pkg_resources; print('pkg_resources OK')" 2>/dev/null || {
  echo "pkg_resources missing, reinstalling setuptools <72..."
  "$PYTHON_BIN" -m pip install 'setuptools>=69,<72' --force-reinstall --quiet
  "$PYTHON_BIN" -c "import pkg_resources; print('pkg_resources restored')" || echo "WARNING: pkg_resources still unavailable"
}

# =============================================================================
# Verify critical version constraints using pip list (reliable with --path)
# =============================================================================
echo ""
echo "=== Verifying Installed Versions ==="

# Get all package versions in one call for efficiency
PACKAGE_LIST=$("$PYTHON_BIN" -m pip list --path "$SITE_PACKAGES" --format=freeze 2>/dev/null)

verify_version() {
  local package=$1
  local expected=$2
  # pip list uses underscores, but package names might use hyphens
  local pattern=$(echo "$package" | sed 's/-/_/g; s/\./_/g')
  local actual=$(echo "$PACKAGE_LIST" | grep -i "^${pattern}==" | cut -d'=' -f3 | head -1)

  if [ -z "$actual" ]; then
    # Try with original name (hyphens)
    pattern=$(echo "$package" | sed 's/_/-/g')
    actual=$(echo "$PACKAGE_LIST" | grep -i "^${pattern}==" | cut -d'=' -f3 | head -1)
  fi

  if [ -z "$actual" ]; then
    actual="NOT INSTALLED"
  fi

  if [ "$actual" = "$expected" ]; then
    echo "✓ $package: $actual"
  else
    echo "✗ $package: $actual (expected $expected)"
    return 1
  fi
}

FAILED=0

# Core package checks (document processing) - just verify installed
check_installed() {
  local package=$1
  local pattern=$(echo "$package" | sed 's/-/_/g; s/\./_/g')
  if echo "$PACKAGE_LIST" | grep -qi "^${pattern}=="; then
    local version=$(echo "$PACKAGE_LIST" | grep -i "^${pattern}==" | cut -d'=' -f3 | head -1)
    echo "✓ $package: $version"
  else
    echo "✗ $package: NOT INSTALLED"
    return 1
  fi
}

check_installed "PyMuPDF" || FAILED=1
check_installed "python_docx" || FAILED=1
check_installed "openpyxl" || FAILED=1
check_installed "python_pptx" || FAILED=1

# Critical ML version checks (CUDA builds have +cu126 suffix)
# CPU PyTorch on all platforms (GPU via CTranslate2 CUDA, not PyTorch CUDA)
verify_version "torch" "2.8.0" || FAILED=1
verify_version "torchaudio" "2.8.0" || FAILED=1
verify_version "huggingface_hub" "0.36.1" || FAILED=1
verify_version "transformers" "4.48.0" || FAILED=1
verify_version "pyannote.audio" "3.3.2" || FAILED=1
verify_version "pyannote.core" "5.0.0" || FAILED=1
verify_version "pyannote.database" "5.1.3" || FAILED=1
verify_version "pyannote.pipeline" "3.0.1" || FAILED=1
verify_version "pyannote.metrics" "3.2.1" || FAILED=1
verify_version "whisperx" "3.3.4" || FAILED=1
verify_version "numpy" "2.0.2" || FAILED=1

# Apple Silicon specific
if [ "$PLATFORM" = "macos" ] && [ "$ARCH" = "arm64" ]; then
  verify_version "mlx-whisper" "0.4.3" || FAILED=1
fi

echo ""

if [ $FAILED -eq 1 ]; then
  echo "=== VERSION MISMATCH DETECTED ==="
  echo "Some packages have incorrect versions. This may cause compatibility issues."
  echo "Check the pip install output above for dependency resolution messages."
  echo ""
  # Don't fail the build, but warn loudly
fi

echo "=== Done ==="
echo "Dependencies installed to: $SITE_PACKAGES"
echo ""

# List key installed packages
echo "Key packages installed:"
"$PYTHON_BIN" -m pip list --path "$SITE_PACKAGES" 2>/dev/null | grep -E "torch|whisper|pyannote|mlx|transformers|huggingface|sentence|numpy" || true

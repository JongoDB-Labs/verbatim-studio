#!/bin/bash
set -e

# Install Python dependencies for bundling (core + ML)
# Usage: ./install-bundled-deps.sh [python-dir]
#
# Installs directly into the standalone Python (no --target) because:
# 1. We own the entire Python installation — no system contamination risk
# 2. pip --target breaks namespace packages (livekit/, google/) by not
#    merging subdirectories from multiple distributions

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

# Site-packages directory (for verification only — pip installs to default location)
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

# Install a `lightning` namespace shim BEFORE the deps step.
# pyannote-audio==3.3.2 declares `lightning>=2.0.1` as a runtime dep, but the
# upstream `lightning` PyPI package was quarantined (security action) — pip
# can no longer find any version. We install pytorch-lightning + lightning-fabric
# (still on PyPI) and create a stub `lightning` package that re-exports the
# pieces pyannote-audio actually imports. With the shim in place, pip's
# constraint check during step 2 sees `lightning` as already installed.
install_lightning_shim() {
  local site=$1
  echo ""
  echo "=== Installing lightning namespace shim ==="
  echo "(workaround for quarantined PyPI 'lightning' package)"

  # pyannote.audio imports BOTH:
  #   - lightning_fabric.utilities.cloud_io (core/model.py)
  #   - pytorch_lightning.utilities.memory (core/inference.py)
  # Both are pure-Python and small (~1.5 MB combined). Install with --no-deps
  # so we don't pull in torchmetrics/typer etc here — those come in via
  # step 2's constrained resolve when actually needed by pyannote-audio.
  "$PYTHON_BIN" -m pip install --no-deps \
    "lightning-fabric>=2.0.1,<3" \
    "pytorch-lightning>=2.0.1,<3" \
    "lightning-utilities" || {
      echo "WARNING: Could not install lightning shim base packages — continuing anyway"
    }

  # Build a stub `lightning` namespace
  local shim_dir="$site/lightning"
  rm -rf "$shim_dir"
  mkdir -p "$shim_dir/pytorch" "$shim_dir/fabric"

  cat > "$shim_dir/__init__.py" <<'EOF'
"""Stub for the quarantined upstream `lightning` umbrella package.

Re-exports lightning_fabric (used by pyannote.audio at runtime) and
provides import-time stubs for lightning.pytorch (training APIs that
we never invoke at runtime).
"""
import lightning_fabric as fabric  # noqa: F401
__version__ = "2.5.5"
EOF

  cat > "$shim_dir/pytorch/__init__.py" <<'EOF'
"""lightning.pytorch -> pytorch_lightning alias."""
from pytorch_lightning import *  # noqa: F401,F403
# Re-export submodules that pyannote.audio imports
from pytorch_lightning import utilities  # noqa: F401
EOF

  cat > "$shim_dir/fabric/__init__.py" <<'EOF'
"""lightning.fabric -> lightning_fabric alias."""
from lightning_fabric import *  # noqa: F401,F403
EOF

  # Plant a dist-info so pip thinks `lightning==2.5.5` is installed.
  # This satisfies pyannote-audio's `lightning>=2.0.1` constraint.
  local dist_info="$site/lightning-2.5.5.dist-info"
  rm -rf "$dist_info"
  mkdir -p "$dist_info"
  cat > "$dist_info/METADATA" <<'EOF'
Metadata-Version: 2.1
Name: lightning
Version: 2.5.5
Summary: Local stub for the quarantined upstream `lightning` package.
EOF
  cat > "$dist_info/WHEEL" <<'EOF'
Wheel-Version: 1.0
Generator: install-bundled-deps.sh
Root-Is-Purelib: true
Tag: py3-none-any
EOF
  echo "lightning-shim" > "$dist_info/INSTALLER"
  : > "$dist_info/RECORD"

  echo "lightning shim installed at $shim_dir"
}

if [ "$PLATFORM" = "macos" ] && [ "$ARCH" = "arm64" ]; then
  echo "Installing for Apple Silicon (includes mlx-whisper)..."

  # Step 1: Install the pinned packages without their dependencies
  # This ensures we get EXACTLY the versions we specify.
  "$PYTHON_BIN" -m pip install \
    --no-deps \
    -r "$REQUIREMENTS_ML"

  # Step 1.5: Install the lightning namespace shim before pip resolves
  # pyannote-audio's transitive deps. See install_lightning_shim().
  install_lightning_shim "$SITE_PACKAGES"

  # Step 2: Install sub-dependencies, constrained to our pinned versions
  "$PYTHON_BIN" -m pip install \
    --constraint "$REQUIREMENTS_ML" \
    -r "$REQUIREMENTS_ML"

  # Step 3: Install mlx-audio separately (requires huggingface_hub>=1.0 which
  # conflicts with whisperx's pin). Using --no-deps to avoid pulling in
  # conflicting transitive deps — the necessary deps are already installed.
  echo "Installing mlx-audio (voice TTS) without conflicting deps..."
  "$PYTHON_BIN" -m pip install \
    --no-deps \
    "mlx-audio>=0.4.0"

  # Install mlx-audio's unique deps that aren't already present
  "$PYTHON_BIN" -m pip install \
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
  "$PYTHON_BIN" -m pip install \
    --no-deps \
    -r "$REQUIREMENTS_ML_WIN"

  # Step 1.5: Install lightning namespace shim (see install_lightning_shim())
  install_lightning_shim "$SITE_PACKAGES"

  # Step 2: Install sub-dependencies with constraints
  "$PYTHON_BIN" -m pip install \
    --constraint "$REQUIREMENTS_ML_WIN" \
    -r "$REQUIREMENTS_ML_WIN"

else
  echo "Installing for ${PLATFORM}-${ARCH} (excludes mlx-whisper)..."

  # Create a temp file without mlx-whisper for non-Apple platforms
  TEMP_REQUIREMENTS=$(mktemp)
  grep -v "mlx-whisper" "$REQUIREMENTS_ML" > "$TEMP_REQUIREMENTS"

  "$PYTHON_BIN" -m pip install \
    --no-deps \
    -r "$TEMP_REQUIREMENTS"

  # Lightning shim (see install_lightning_shim())
  install_lightning_shim "$SITE_PACKAGES"

  "$PYTHON_BIN" -m pip install \
    --constraint "$TEMP_REQUIREMENTS" \
    -r "$TEMP_REQUIREMENTS"

  rm -f "$TEMP_REQUIREMENTS"
fi

# =============================================================================
# Restore setuptools <72 if installs overwrote it with a newer version
# (torch depends on setuptools, pip may pull 82.0+ which lacks pkg_resources)
# =============================================================================
echo ""
echo "=== Ensuring pkg_resources is available ==="
"$PYTHON_BIN" -c "import pkg_resources; print('pkg_resources OK')" 2>/dev/null || {
  echo "pkg_resources missing, reinstalling setuptools <72..."
  "$PYTHON_BIN" -m pip install 'setuptools>=69,<72' --force-reinstall --quiet
  "$PYTHON_BIN" -c "import pkg_resources; print('pkg_resources restored')" || echo "WARNING: pkg_resources still unavailable"
}

# =============================================================================
# Verify critical version constraints
# =============================================================================
echo ""
echo "=== Verifying Installed Versions ==="

PACKAGE_LIST=$("$PYTHON_BIN" -m pip list --format=freeze 2>/dev/null)

verify_version() {
  local package=$1
  local expected=$2
  local pattern=$(echo "$package" | sed 's/-/_/g; s/\./_/g')
  local actual=$(echo "$PACKAGE_LIST" | grep -i "^${pattern}==" | cut -d'=' -f3 | head -1)

  if [ -z "$actual" ]; then
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

# Critical ML version checks
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

# LiveKit (voice chat)
check_installed "livekit_api" || FAILED=1
check_installed "livekit_protocol" || FAILED=1
check_installed "livekit_agents" || FAILED=1

# Apple Silicon specific
if [ "$PLATFORM" = "macos" ] && [ "$ARCH" = "arm64" ]; then
  verify_version "mlx-whisper" "0.4.3" || FAILED=1
fi

echo ""

# Verify namespace packages resolve correctly
echo "=== Verifying Namespace Packages ==="
"$PYTHON_BIN" -c "from livekit.api import AccessToken; print('✓ livekit.api')" 2>/dev/null || { echo "✗ livekit.api"; FAILED=1; }
"$PYTHON_BIN" -c "from livekit.protocol import agent_dispatch; print('✓ livekit.protocol')" 2>/dev/null || { echo "✗ livekit.protocol"; FAILED=1; }
"$PYTHON_BIN" -c "from google.oauth2.credentials import Credentials; print('✓ google.oauth2')" 2>/dev/null || { echo "✗ google.oauth2"; FAILED=1; }
"$PYTHON_BIN" -c "import google.api_core; print('✓ google.api_core')" 2>/dev/null || { echo "✗ google.api_core"; FAILED=1; }

if [ $FAILED -eq 1 ]; then
  echo ""
  echo "=== VERSION MISMATCH OR MISSING PACKAGES ==="
  echo "Some packages have incorrect versions or failed to install."
  echo "Check the pip install output above for dependency resolution messages."
  echo ""
fi

echo ""
echo "=== Done ==="
echo "Dependencies installed to: $SITE_PACKAGES"
echo ""

# List key installed packages
echo "Key packages installed:"
"$PYTHON_BIN" -m pip list 2>/dev/null | grep -E "torch|whisper|pyannote|mlx|transformers|huggingface|sentence|numpy|livekit" || true

#!/bin/bash
# Download/build LiveKit server binary for bundling with Electron
# macOS: builds from source via Go (no pre-built binaries available)
# Windows: downloads pre-built binary from GitHub releases
# Usage: ./scripts/download-livekit.sh [version]

set -euo pipefail

LIVEKIT_VERSION="${1:-1.10.1}"
OUTPUT_DIR="build/resources/bin"

mkdir -p "$OUTPUT_DIR"

case "$(uname -s)" in
    Darwin)
        echo "macOS: Installing LiveKit Server v${LIVEKIT_VERSION} via Homebrew..."
        if command -v livekit-server &>/dev/null; then
            INSTALLED_VERSION=$(livekit-server --version 2>&1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' || echo "unknown")
            echo "LiveKit Server already installed: v${INSTALLED_VERSION}"
        else
            brew install livekit 2>/dev/null || echo "Homebrew install failed — trying Go build"
        fi

        # Copy the binary to the output dir
        LIVEKIT_BIN=$(command -v livekit-server 2>/dev/null || echo "")
        if [ -n "$LIVEKIT_BIN" ]; then
            cp "$LIVEKIT_BIN" "$OUTPUT_DIR/livekit-server"
            echo "Copied livekit-server to ${OUTPUT_DIR}/"
        else
            echo "WARNING: livekit-server not found. Voice assistant will not be available."
            exit 0
        fi
        ;;

    MINGW*|MSYS*|CYGWIN*|Windows_NT)
        echo "Windows: Downloading LiveKit Server v${LIVEKIT_VERSION}..."
        ARCH="amd64"
        if [ "$(uname -m)" = "aarch64" ] || [ "${PROCESSOR_ARCHITECTURE:-}" = "ARM64" ]; then
            ARCH="arm64"
        fi
        FILENAME="livekit_${LIVEKIT_VERSION}_windows_${ARCH}.zip"
        URL="https://github.com/livekit/livekit/releases/download/v${LIVEKIT_VERSION}/${FILENAME}"

        curl -L -o "/tmp/${FILENAME}" "${URL}"
        unzip -o "/tmp/${FILENAME}" livekit-server.exe -d "$OUTPUT_DIR"
        rm -f "/tmp/${FILENAME}"
        ;;

    *)
        echo "Unsupported platform: $(uname -s)"
        exit 0
        ;;
esac

# Verify
if [ -f "$OUTPUT_DIR/livekit-server" ] || [ -f "$OUTPUT_DIR/livekit-server.exe" ]; then
    echo "LiveKit Server ready in ${OUTPUT_DIR}/"
    ls -lh "$OUTPUT_DIR"/livekit-server*
else
    echo "WARNING: LiveKit binary not found — voice assistant will not be available in packaged app."
fi

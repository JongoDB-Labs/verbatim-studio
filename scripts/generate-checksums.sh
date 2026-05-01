#!/usr/bin/env bash
# Generate a SHA256SUMS file for release artifacts.
#
# Usage:   ./scripts/generate-checksums.sh <dist-dir> [<dist-dir2> ...]
# Output:  SHA256SUMS file inside the FIRST dist dir, listing every
#          .dmg/.exe/.zip/.AppImage in all dist dirs.
#
# Format mirrors `shasum -a 256` output (one line per file):
#   <hex hash>  <filename>
#
# The updater (apps/electron/src/main/updater.ts) reads this manifest from
# the GitHub release assets to verify each downloaded installer.

set -euo pipefail

if [ "$#" -lt 1 ]; then
  echo "Usage: $0 <dist-dir> [<dist-dir2> ...]" >&2
  exit 2
fi

OUT_DIR="$1"
OUT_FILE="$OUT_DIR/SHA256SUMS"

# sha256 binary varies by platform: shasum on macOS, sha256sum on Linux/Windows
HASHER=""
if command -v shasum >/dev/null 2>&1; then
  HASHER="shasum -a 256"
elif command -v sha256sum >/dev/null 2>&1; then
  HASHER="sha256sum"
else
  echo "ERROR: neither shasum nor sha256sum available" >&2
  exit 1
fi

> "$OUT_FILE"

found=0
for dir in "$@"; do
  if [ ! -d "$dir" ]; then
    echo "Skipping non-existent dir: $dir" >&2
    continue
  fi
  while IFS= read -r -d '' file; do
    name=$(basename "$file")
    hash=$($HASHER "$file" | awk '{print $1}')
    printf '%s  %s\n' "$hash" "$name" >> "$OUT_FILE"
    found=$((found + 1))
    echo "  $name  $hash"
  done < <(find "$dir" -maxdepth 1 -type f \( \
    -name '*.dmg' -o -name '*.exe' -o -name '*.zip' -o -name '*.AppImage' \
  \) -print0 | sort -z)
done

if [ "$found" -eq 0 ]; then
  echo "ERROR: no installer artifacts found in: $*" >&2
  rm -f "$OUT_FILE"
  exit 1
fi

echo ""
echo "Wrote $OUT_FILE with $found entries"

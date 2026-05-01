import { promises as fs } from 'fs';
import os from 'os';
import path from 'path';

export const UPDATE_DIR = path.join(os.tmpdir(), 'verbatim-update');

/**
 * Generates a shell script for seamless macOS app replacement.
 * The script:
 * 1. Waits for the app to quit (with timeout)
 * 2. Removes old app from /Applications
 * 3. Copies new app from mounted DMG volume
 * 4. Relaunches the app
 * 5. Cleans up (unmounts DMG, removes temp files)
 */
export function generateUpdaterScript(volumePath: string, appName: string): string {
  // Validate inputs to prevent command injection
  if (!appName || !/^[a-zA-Z0-9\s\-_.]+$/.test(appName)) {
    throw new Error(`Invalid app name: ${appName}`);
  }
  if (!volumePath || !/^\/Volumes\/[a-zA-Z0-9\s\-_.\/]+$/.test(volumePath)) {
    throw new Error(`Invalid volume path: ${volumePath}`);
  }

  const appPath = `/Applications/${appName}.app`;
  const sourceAppPath = `${volumePath}/${appName}.app`;
  const maxWaitSeconds = 30;

  return `#!/bin/bash
set -e

APP_NAME="${appName}"
APP_PATH="${appPath}"
SOURCE_APP="${sourceAppPath}"
VOLUME_PATH="${volumePath}"
UPDATE_DIR="${UPDATE_DIR}"
MAX_WAIT=${maxWaitSeconds}
WAIT_COUNT=0

# Function to show macOS notification
notify() {
  osascript -e "display notification \\"$1\\" with title \\"$APP_NAME Update\\""
}

# Function to show error and exit
fail() {
  notify "$1"
  echo "ERROR: $1" >&2
  cleanup
  exit 1
}

# Function to cleanup
cleanup() {
  # Unmount DMG if mounted
  if [ -d "$VOLUME_PATH" ]; then
    hdiutil detach "$VOLUME_PATH" -quiet 2>/dev/null || true
  fi

  # Remove temp update directory
  if [ -d "$UPDATE_DIR" ]; then
    rm -rf "$UPDATE_DIR" 2>/dev/null || true
  fi
}

# Wait for the app to quit
echo "Waiting for $APP_NAME to quit..."
while pgrep -x "$APP_NAME" > /dev/null; do
  sleep 1
  WAIT_COUNT=$((WAIT_COUNT + 1))
  if [ $WAIT_COUNT -ge $MAX_WAIT ]; then
    fail "Timed out waiting for $APP_NAME to quit. Please close the app and try again."
  fi
done

echo "$APP_NAME has quit. Proceeding with update..."

# Verify source app exists
if [ ! -d "$SOURCE_APP" ]; then
  fail "Update source not found at $SOURCE_APP"
fi

# Validate app path ends with .app
if [[ "$APP_PATH" != *.app ]]; then
  fail "Invalid app path - must end with .app"
fi

# Remove old app
echo "Removing old version..."
if [ -d "$APP_PATH" ]; then
  rm -rf "$APP_PATH" || fail "Failed to remove old version. Check permissions."
fi

# Copy new app
echo "Installing new version..."
cp -R "$SOURCE_APP" "$APP_PATH" || fail "Failed to copy new version. Check disk space and permissions."

# Remove quarantine attribute (for unsigned apps)
xattr -rd com.apple.quarantine "$APP_PATH" 2>/dev/null || true

# Verify installation
if [ ! -d "$APP_PATH" ]; then
  fail "Installation verification failed"
fi

echo "Update installed successfully!"

# Relaunch FIRST so the new app is up before we tear down the script's own
# working directory (cleanup() does rm -rf "$UPDATE_DIR" which contains
# this script — bash usually keeps it open mid-execution but it's safer to
# launch the app before pulling the rug).
echo "Relaunching $APP_NAME..."
open "$APP_PATH" || {
  notify "Update installed but relaunch failed — please open $APP_NAME manually."
  cleanup
  exit 1
}

# Cleanup AFTER the new app process has been kicked off by 'open'.
# 'open' returns immediately once LaunchServices has accepted the request,
# so the new app starts in parallel with our cleanup.
cleanup

exit 0
`;
}

/**
 * Writes the updater script to disk and sets executable permissions.
 * @param volumePath - The mounted DMG volume path (e.g., "/Volumes/Verbatim Studio")
 * @param appName - The application name (e.g., "Verbatim Studio")
 * @returns The path to the written script
 */
export async function writeUpdaterScript(volumePath: string, appName: string): Promise<string> {
  // Ensure update directory exists
  await fs.mkdir(UPDATE_DIR, { recursive: true });

  const scriptContent = generateUpdaterScript(volumePath, appName);
  const scriptPath = path.join(UPDATE_DIR, 'update.sh');

  // Write the script
  await fs.writeFile(scriptPath, scriptContent, 'utf-8');

  // Set executable permission (0o755 = rwxr-xr-x)
  await fs.chmod(scriptPath, 0o755);

  return scriptPath;
}

/**
 * Parses hdiutil attach output to extract the volume mount path.
 * Example output format:
 *   /dev/disk4s1  Apple_HFS  /Volumes/Verbatim Studio
 *   /dev/disk4              /Volumes/Verbatim Studio
 * @param hdiutilOutput - The stdout from hdiutil attach command
 * @returns The extracted volume path
 * @throws Error if volume path cannot be found
 */
export function parseVolumePath(hdiutilOutput: string): string {
  // Split into lines and process each
  const lines = hdiutilOutput.trim().split('\n');

  for (const line of lines) {
    // Look for lines containing /Volumes/
    const volumeMatch = line.match(/\/Volumes\/[^\t\n]+/);
    if (volumeMatch) {
      // Trim any trailing whitespace
      return volumeMatch[0].trim();
    }
  }

  // Alternative parsing: some hdiutil outputs use tabs
  for (const line of lines) {
    const parts = line.split('\t');
    for (const part of parts) {
      const trimmed = part.trim();
      if (trimmed.startsWith('/Volumes/')) {
        return trimmed;
      }
    }
  }

  throw new Error('Volume path not found in hdiutil output. Output was: ' + hdiutilOutput);
}

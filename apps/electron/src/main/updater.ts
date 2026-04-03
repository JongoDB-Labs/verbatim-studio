import { app, BrowserWindow } from 'electron';
import { execFile, spawn } from 'child_process';
import { promisify } from 'util';
import { createWriteStream, existsSync, lstatSync, readlinkSync } from 'fs';
import { mkdir, rm } from 'fs/promises';
import path from 'path';
import https from 'https';
import {
  getAutoUpdateEnabled,
  getLastUpdateCheck,
  setLastUpdateCheck,
  getLastSeenVersion,
  setLastSeenVersion,
  getGithubPat,
} from './update-store';
import { writeUpdaterScript, parseVolumePath, UPDATE_DIR } from './update-script';

const execFileAsync = promisify(execFile);

// Constants
const GITHUB_OWNER = 'JongoDB';
const GITHUB_REPO_PUBLIC = 'verbatim-studio-releases'; // Public repo for updates (no auth needed)
const GITHUB_REPO_PRIVATE = 'verbatim-studio';          // Private repo (requires PAT)
const CHECK_INTERVAL_MS = 24 * 60 * 60 * 1000; // 24 hours
const APP_NAME = 'Verbatim Studio';

// Interfaces
interface GitHubRelease {
  tag_name: string;
  name: string;
  body: string;
  published_at: string;
  assets: GitHubAsset[];
}

interface GitHubAsset {
  name: string;
  browser_download_url: string;
  size: number;
}

// Module state
let mainWindow: BrowserWindow | null = null;
let isCheckingForUpdates = false;

/**
 * Checks if the Python environment has been properly migrated to user data.
 * Stripped "update" releases only work if this returns true.
 *
 * Returns false if the Python binary is a symlink into the app bundle,
 * because such symlinks break when a stripped update replaces the app.
 */
function hasMigratedPython(): boolean {
  const userDataDir = app.getPath('userData');
  const pythonBin = process.platform === 'win32'
    ? path.join(userDataDir, 'python', 'python.exe')
    : path.join(userDataDir, 'python', 'bin', 'python3');

  if (!existsSync(pythonBin)) {
    return false;
  }

  // Symlinks pointing into the app bundle will break after a stripped update
  try {
    const stat = lstatSync(pythonBin);
    if (stat.isSymbolicLink()) {
      const target = readlinkSync(pythonBin);
      if (target.includes('/Contents/Resources/python/')) {
        console.log('[Updater] Python binary is symlinked to app bundle — not safely migrated');
        return false;
      }
    }
  } catch {
    // If we can't check, assume it's not safely migrated
    return false;
  }

  return true;
}

/**
 * Safely sends an IPC message to the main window.
 * Checks that the window exists and hasn't been destroyed.
 */
function safeSend(channel: string, ...args: unknown[]): void {
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send(channel, ...args);
  }
}

/**
 * Parses a version string like "0.26.22" into a comparable number.
 * Supports up to 3 segments, each up to 999.
 */
function parseVersion(version: string): number {
  // Remove 'v' prefix if present
  const cleaned = version.replace(/^v/, '');
  const parts = cleaned.split('.').map((p) => parseInt(p, 10) || 0);

  // Pad to 3 parts
  while (parts.length < 3) {
    parts.push(0);
  }

  // Combine: major * 1000000 + minor * 1000 + patch
  return parts[0] * 1000000 + parts[1] * 1000 + parts[2];
}

/**
 * Fetches releases from GitHub API.
 */
function fetchGitHubReleases(): Promise<GitHubRelease[]> {
  return new Promise((resolve, reject) => {
    const MAX_RESPONSE_SIZE = 5 * 1024 * 1024; // 5MB

    const headers: Record<string, string> = {
      'User-Agent': `${APP_NAME}/${app.getVersion()}`,
      Accept: 'application/vnd.github.v3+json',
    };

    // Use private repo with PAT if configured, otherwise public releases repo
    const pat = getGithubPat();
    let repo = GITHUB_REPO_PUBLIC;
    if (pat) {
      headers['Authorization'] = `token ${pat}`;
      repo = GITHUB_REPO_PRIVATE;
    }

    const options = {
      hostname: 'api.github.com',
      path: `/repos/${GITHUB_OWNER}/${repo}/releases`,
      method: 'GET',
      headers,
    };

    const req = https.request(options, (res) => {
      let data = '';

      res.on('data', (chunk) => {
        data += chunk;
        if (data.length > MAX_RESPONSE_SIZE) {
          req.destroy();
          reject(new Error('Response too large'));
        }
      });

      res.on('end', () => {
        if (res.statusCode !== 200) {
          reject(new Error(`GitHub API returned status ${res.statusCode}: ${data}`));
          return;
        }

        try {
          const releases = JSON.parse(data) as GitHubRelease[];
          resolve(releases);
        } catch (err) {
          reject(new Error(`Failed to parse GitHub response: ${err}`));
        }
      });
    });

    req.setTimeout(30000, () => {
      req.destroy();
      reject(new Error('Request timed out'));
    });

    req.on('error', reject);
    req.end();
  });
}

/**
 * Downloads a file from a URL with progress reporting.
 * Handles HTTP redirects (301, 302, 307, 308).
 */
function downloadFile(
  url: string,
  destPath: string,
  onProgress?: (percent: number) => void,
  redirectDepth = 0,
  maxSize = 2 * 1024 * 1024 * 1024 // 2GB
): Promise<void> {
  return new Promise((resolve, reject) => {
    // Check redirect depth
    if (redirectDepth > 5) {
      reject(new Error('Too many redirects'));
      return;
    }

    const urlObj = new URL(url);

    const options = {
      hostname: urlObj.hostname,
      path: urlObj.pathname + urlObj.search,
      method: 'GET',
      headers: {
        'User-Agent': `${APP_NAME}/${app.getVersion()}`,
      },
    };

    const fileStream = createWriteStream(destPath);

    const req = https.request(options, (res) => {
      // Handle redirects
      if (res.statusCode && [301, 302, 307, 308].includes(res.statusCode)) {
        const redirectUrl = res.headers.location;
        if (redirectUrl) {
          fileStream.close();
          downloadFile(redirectUrl, destPath, onProgress, redirectDepth + 1, maxSize)
            .then(resolve)
            .catch(reject);
          return;
        }
        fileStream.close();
        reject(new Error('Redirect without location header'));
        return;
      }

      if (res.statusCode !== 200) {
        fileStream.close();
        reject(new Error(`Download failed with status ${res.statusCode}`));
        return;
      }

      const totalSize = parseInt(res.headers['content-length'] || '0', 10);

      // Check size limit
      if (totalSize > maxSize) {
        req.destroy();
        fileStream.close();
        reject(new Error(`File too large: ${totalSize} bytes (max ${maxSize})`));
        return;
      }

      let downloadedSize = 0;

      res.on('data', (chunk: Buffer) => {
        downloadedSize += chunk.length;

        // Runtime size check
        if (downloadedSize > maxSize) {
          req.destroy();
          fileStream.close();
          reject(new Error('Download exceeded size limit'));
          return;
        }

        if (totalSize > 0 && onProgress) {
          const percent = (downloadedSize / totalSize) * 100;
          onProgress(percent);
        }
      });

      res.pipe(fileStream);

      fileStream.on('finish', () => {
        fileStream.close();
        resolve();
      });

      fileStream.on('error', async (err) => {
        fileStream.close();
        try {
          await rm(destPath, { force: true });
        } catch {
          // Ignore cleanup errors
        }
        reject(err);
      });
    });

    // 30 minute timeout for large downloads (up to 2GB) on slower connections
    // This is a connection/inactivity timeout, not total download time
    req.setTimeout(1800000, () => {
      req.destroy();
      reject(new Error('Download timed out - please check your internet connection'));
    });

    req.on('error', reject);
    req.end();
  });
}

/**
 * Initializes the auto-updater system.
 */
export function initAutoUpdater(window: BrowserWindow): void {
  mainWindow = window;

  // Don't check for updates in development
  if (!app.isPackaged) {
    console.log('[Updater] Skipping updates in development mode');
    return;
  }

  // Check what's new on startup
  checkWhatsNew().catch((err) => {
    console.error('[Updater] Error checking what\'s new:', err);
  });

  // Check for updates after a short delay
  setTimeout(() => {
    if (getAutoUpdateEnabled() && mainWindow && !mainWindow.isDestroyed()) {
      checkForUpdates(false).catch((err) => {
        console.error('[Updater] Error checking for updates:', err);
      });
    }
  }, 5000);

  // Set up periodic check (every hour, but only if 24h has passed)
  setInterval(() => {
    if (!getAutoUpdateEnabled()) {
      return;
    }

    const lastCheck = getLastUpdateCheck();
    const now = Date.now();

    if (now - lastCheck >= CHECK_INTERVAL_MS) {
      checkForUpdates(false).catch((err) => {
        console.error('[Updater] Periodic update check error:', err);
      });
    }
  }, 60 * 60 * 1000); // Check every hour
}

/**
 * Checks for available updates from GitHub.
 * @param manual - Whether this was triggered manually by the user
 */
export async function checkForUpdates(manual = false): Promise<void> {
  if (isCheckingForUpdates) {
    console.log('[Updater] Check already in progress, skipping');
    return;
  }
  isCheckingForUpdates = true;

  try {
    console.log('[Updater] Checking for updates...');

    const releases = await fetchGitHubReleases();

    if (!releases || releases.length === 0) {
      if (manual) {
        safeSend('update-not-available');
      }
      return;
    }

    const latestRelease = releases[0];
    const latestVersion = latestRelease.tag_name.replace(/^v/, '');
    const currentVersion = app.getVersion();

    console.log(`[Updater] Current: ${currentVersion}, Latest: ${latestVersion}`);

    const latestNum = parseVersion(latestVersion);
    const currentNum = parseVersion(currentVersion);

    if (latestNum <= currentNum) {
      console.log('[Updater] No update available');
      if (manual) {
        safeSend('update-not-available');
      }
      setLastUpdateCheck(Date.now());
      return;
    }

    // Find the correct asset for this platform and architecture.
    // Only use stripped "update" variants if the Python environment has already
    // been migrated to user data. Otherwise, use the full installer so migration
    // can bootstrap the environment on first launch.
    const canUseStripped = hasMigratedPython();
    let updateAsset: GitHubAsset | undefined;

    if (process.platform === 'win32') {
      if (canUseStripped) {
        updateAsset = latestRelease.assets.find((asset) => {
          const name = asset.name.toLowerCase();
          return name.endsWith('.exe') && name.includes('update');
        });
      }
      if (!updateAsset) {
        updateAsset = latestRelease.assets.find((asset) => {
          const name = asset.name.toLowerCase();
          return name.endsWith('.exe') && name.includes('setup');
        });
      }
      if (!updateAsset) {
        console.error('[Updater] No Windows installer asset found');
        safeSend('update-error', {
          message: 'No Windows installer available for this release',
        });
        return;
      }
    } else {
      // macOS: Update DMGs omit the arch from the filename so that older
      // updaters (which match .dmg + arch) fall through to the full DMG.
      const arch = process.arch === 'arm64' ? 'arm64' : 'x64';
      if (canUseStripped) {
        updateAsset = latestRelease.assets.find((asset) => {
          const name = asset.name.toLowerCase();
          return name.endsWith('.dmg') && name.includes('update');
        });
      }
      if (!updateAsset) {
        updateAsset = latestRelease.assets.find((asset) => {
          const name = asset.name.toLowerCase();
          return name.endsWith('.dmg') && name.includes(arch);
        });
      }
      if (!updateAsset) {
        console.error('[Updater] No DMG asset found for architecture:', arch);
        safeSend('update-error', {
          message: `No download available for your Mac (${arch})`,
        });
        return;
      }
    }

    console.log(`[Updater] Python migrated: ${canUseStripped}, selected: ${updateAsset.name}`);

    console.log('[Updater] Update available:', latestVersion, updateAsset.name);

    safeSend('update-available', {
      version: latestVersion,
      releaseNotes: latestRelease.body,
      releaseName: latestRelease.name,
      downloadUrl: updateAsset.browser_download_url,
      downloadSize: updateAsset.size,
    });

    setLastUpdateCheck(Date.now());
  } catch (err) {
    console.error('[Updater] Error checking for updates:', err);
    safeSend('update-error', {
      message: err instanceof Error ? err.message : 'Unknown error',
    });
  } finally {
    isCheckingForUpdates = false;
  }
}

/**
 * Downloads and installs an update.
 * @param downloadUrl - The URL to download the DMG from
 * @param version - The version being installed
 */
export async function startUpdate(downloadUrl: string, version: string): Promise<void> {
  console.log(`[Updater] Starting update to ${version}`);

  const fallbackUrl = `https://github.com/${GITHUB_OWNER}/${GITHUB_REPO_PUBLIC}/releases/tag/v${version}`;

  try {
    // Create temp directory
    await mkdir(UPDATE_DIR, { recursive: true });

    if (process.platform === 'win32') {
      // Windows: download NSIS installer and run it
      const installerPath = path.join(UPDATE_DIR, `update-${version}.exe`);

      console.log('[Updater] Downloading Windows installer...');
      await downloadFile(downloadUrl, installerPath, (percent) => {
        safeSend('update-downloading', { percent });
      });

      console.log('[Updater] Download complete, launching installer...');

      // Notify the UI that the update is ready
      safeSend('update-ready', { version });

      // Launch the NSIS installer silently and detached
      const child = spawn(installerPath, ['/S'], {
        detached: true,
        stdio: 'ignore',
      });
      child.unref();

      // Quit the app after a short delay to allow the installer to start
      setTimeout(() => {
        console.log('[Updater] Quitting app for update...');
        app.quit();
      }, 500);
    } else {
      // macOS: download DMG and use updater script
      const dmgPath = path.join(UPDATE_DIR, `update-${version}.dmg`);

      // Download the DMG with progress reporting
      console.log('[Updater] Downloading DMG...');
      await downloadFile(downloadUrl, dmgPath, (percent) => {
        safeSend('update-downloading', { percent });
      });

      console.log('[Updater] Download complete, removing quarantine...');

      // Remove quarantine attribute
      try {
        await execFileAsync('xattr', ['-c', dmgPath]);
      } catch (err) {
        console.warn('[Updater] Failed to remove quarantine (non-fatal):', err);
      }

      // Mount the DMG
      console.log('[Updater] Mounting DMG...');
      const { stdout: mountOutput } = await execFileAsync('hdiutil', [
        'attach',
        '-nobrowse',
        dmgPath,
      ]);

      // Parse the volume path
      const volumePath = parseVolumePath(mountOutput);
      console.log('[Updater] Mounted at:', volumePath);

      // Write the updater script
      console.log('[Updater] Writing updater script...');
      const scriptPath = await writeUpdaterScript(volumePath, APP_NAME);

      // Notify the UI that the update is ready
      safeSend('update-ready', { version, scriptPath });

      // Spawn the updater script detached
      console.log('[Updater] Spawning updater script...');
      const child = spawn(scriptPath, [], {
        detached: true,
        stdio: 'ignore',
      });
      child.unref();

      // Quit the app after a short delay to allow the script to start
      setTimeout(() => {
        console.log('[Updater] Quitting app for update...');
        app.quit();
      }, 500);
    }
  } catch (err) {
    console.error('[Updater] Update failed:', err);

    // Cleanup on error
    try {
      await rm(UPDATE_DIR, { recursive: true, force: true });
    } catch {
      // Ignore cleanup errors
    }

    safeSend('update-error', {
      message: err instanceof Error ? err.message : 'Update failed',
      fallbackUrl,
    });
  }
}

/**
 * Checks if there are new features to show the user since their last seen version.
 */
export async function checkWhatsNew(): Promise<void> {
  const currentVersion = app.getVersion();
  const lastSeenVersion = getLastSeenVersion();

  console.log(`[Updater] Checking what's new: current=${currentVersion}, lastSeen=${lastSeenVersion}`);

  // First run - just set the version and return
  if (!lastSeenVersion) {
    console.log('[Updater] First run, setting last seen version');
    setLastSeenVersion(currentVersion);
    return;
  }

  // Versions match - nothing new to show
  if (lastSeenVersion === currentVersion) {
    console.log('[Updater] Versions match, nothing new');
    return;
  }

  // Fetch release notes for versions between lastSeen and current
  try {
    const releases = await fetchReleaseNotes(lastSeenVersion, currentVersion);

    if (releases.length > 0) {
      safeSend('show-whats-new', { releases });
    }
  } catch (err) {
    console.error('[Updater] Error fetching release notes:', err);
  }
}

/**
 * Fetches release notes for versions between fromVersion (exclusive) and toVersion (inclusive).
 */
export async function fetchReleaseNotes(
  fromVersion: string,
  toVersion: string
): Promise<Array<{ version: string; notes: string }>> {
  console.log(`[Updater] Fetching release notes from ${fromVersion} to ${toVersion}`);

  const releases = await fetchGitHubReleases();
  const fromNum = parseVersion(fromVersion);
  const toNum = parseVersion(toVersion);

  const relevantReleases = releases
    .filter((release) => {
      const version = release.tag_name.replace(/^v/, '');
      const versionNum = parseVersion(version);
      // Include versions > fromVersion and <= toVersion
      return versionNum > fromNum && versionNum <= toNum;
    })
    .map((release) => ({
      version: release.tag_name.replace(/^v/, ''),
      notes: release.body || '',
    }))
    .sort((a, b) => parseVersion(b.version) - parseVersion(a.version)); // Newest first

  console.log(`[Updater] Found ${relevantReleases.length} relevant releases`);
  return relevantReleases;
}

/**
 * Marks the what's new dialog as seen for the given version.
 */
export function markWhatsNewSeen(version: string): void {
  console.log(`[Updater] Marking what's new seen for version ${version}`);
  setLastSeenVersion(version);
}

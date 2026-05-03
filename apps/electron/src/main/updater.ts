import { app, BrowserWindow } from 'electron';
import { execFile, spawn } from 'child_process';
import { promisify } from 'util';
import { createHash } from 'crypto';
import { createReadStream, createWriteStream, existsSync, lstatSync, readlinkSync } from 'fs';
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
 * Compute SHA-256 of a local file as a hex string.
 *
 * Waits for the read stream's underlying file descriptor to fully
 * close before resolving — on Windows there's a short window after
 * 'end' where the OS-level handle is still held, which causes
 * spawn(EBUSY) when the next step is to launch the just-hashed file.
 */
function sha256File(filePath: string): Promise<string> {
  return new Promise((resolve, reject) => {
    const hash = createHash('sha256');
    const stream = createReadStream(filePath);
    let digest: string | null = null;
    let errored = false;

    stream.on('data', (chunk) => hash.update(chunk));
    stream.on('end', () => { digest = hash.digest('hex'); });
    stream.on('error', (err) => {
      errored = true;
      reject(err);
    });
    stream.on('close', () => {
      if (errored) return;
      if (digest) resolve(digest);
      else reject(new Error('sha256File: stream closed before end'));
    });
  });
}

/**
 * Fetch SHA256SUMS from a GitHub release asset and return a map of
 * filename → expected hash. Returns null if the manifest isn't published
 * (older releases). The expected format is one line per file:
 *   <hash>  <filename>
 * (matching `shasum -a 256` output).
 */
function fetchChecksumsManifest(release: GitHubRelease): Promise<Record<string, string> | null> {
  const asset = release.assets.find(
    (a) => a.name === 'SHA256SUMS' || a.name === 'SHA256SUMS.txt'
  );
  if (!asset) return Promise.resolve(null);

  return new Promise((resolve) => {
    const headers: Record<string, string> = {
      'User-Agent': `${APP_NAME}/${app.getVersion()}`,
      Accept: 'application/octet-stream',
    };
    const pat = getGithubPat();
    if (pat) headers['Authorization'] = `token ${pat}`;

    const followRedirects = (url: string, depth = 0): void => {
      if (depth > 5) return resolve(null);
      const urlObj = new URL(url);
      const req = https.request(
        {
          hostname: urlObj.hostname,
          path: urlObj.pathname + urlObj.search,
          method: 'GET',
          headers,
        },
        (res) => {
          if ([301, 302, 307, 308].includes(res.statusCode || 0)) {
            const next = res.headers.location;
            if (next) return followRedirects(next, depth + 1);
            return resolve(null);
          }
          if (res.statusCode !== 200) {
            return resolve(null);
          }
          let body = '';
          res.on('data', (c) => (body += c));
          res.on('end', () => {
            const map: Record<string, string> = {};
            for (const line of body.split('\n')) {
              const m = line.trim().match(/^([0-9a-fA-F]{64})\s+\*?(.+)$/);
              if (m) map[m[2].trim()] = m[1].toLowerCase();
            }
            resolve(Object.keys(map).length > 0 ? map : null);
          });
        },
      );
      req.on('error', () => resolve(null));
      req.setTimeout(15000, () => {
        req.destroy();
        resolve(null);
      });
      req.end();
    };
    followRedirects(asset.browser_download_url);
  });
}

/**
 * Verify a downloaded file against the release's SHA256SUMS manifest.
 *
 * Returns true if the hash matches OR if no manifest is published
 * (legacy release behavior — caller logs a warning). Throws if the
 * manifest exists but the hash doesn't match — in that case the
 * downloaded file MUST NOT be installed.
 */
async function verifyDownloadChecksum(
  release: GitHubRelease,
  assetName: string,
  filePath: string,
): Promise<boolean> {
  const manifest = await fetchChecksumsManifest(release);
  if (!manifest) {
    console.warn(
      '[Updater] No SHA256SUMS published for this release — skipping checksum verification.',
    );
    return true;
  }

  const expected = manifest[assetName];
  if (!expected) {
    console.warn(`[Updater] ${assetName} not listed in SHA256SUMS — skipping verification.`);
    return true;
  }

  const actual = await sha256File(filePath);
  if (actual.toLowerCase() === expected.toLowerCase()) {
    console.log(`[Updater] Checksum verified: ${assetName} (sha256=${actual.slice(0, 12)}…)`);
    return true;
  }

  throw new Error(
    `Checksum mismatch for ${assetName}: expected ${expected}, got ${actual}. ` +
    `The downloaded file may be corrupt or tampered with.`,
  );
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
    //
    // Windows: stripped updates are DISABLED. Observed in the wild that NSIS's
    // "uninstall before install" step deletes icudtl.dat / app.asar, then the
    // extract step crashed mid-way (likely Defender/SAC interference), leaving
    // the install in a non-bootable state — Chromium fails ICU init and the
    // .exe exits silently before our JS even runs. Until that's root-caused,
    // fall back to the full installer (~1.9GB) which is much more robust.
    const canUseStripped = hasMigratedPython() && process.platform !== 'win32';
    let updateAsset: GitHubAsset | undefined;

    if (process.platform === 'win32') {
      // Windows: always full installer for now (stripped disabled, see above).
      updateAsset = latestRelease.assets.find((asset) => {
        const name = asset.name.toLowerCase();
        return name.endsWith('.exe') && name.includes('setup');
      });
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
/**
 * Sanity-check the running install before applying an update on top of it.
 *
 * If the previous update partially extracted (Windows: icudtl.dat / app.asar
 * deleted, then NSIS aborted before extracting new ones), running another
 * install would compound the corruption — the user would be left with a
 * download manager that can't relaunch and no recovery path. Detect that
 * state up front and surface a clear "manual reinstall required" error.
 */
function detectCorruptInstall(): string | null {
  if (process.platform !== 'win32') return null;
  if (!app.isPackaged) return null;

  const exeDir = path.dirname(process.execPath);
  const required: string[] = ['icudtl.dat', path.join('resources', 'app.asar')];
  const missing = required.filter((rel) => {
    try {
      return !existsSync(path.join(exeDir, rel));
    } catch {
      return true;
    }
  });

  if (missing.length === 0) return null;
  return (
    `Your Verbatim Studio install is missing required files (${missing.join(', ')}). ` +
    `A previous update extracted partially. ` +
    `Please reinstall manually from https://github.com/${GITHUB_OWNER}/${GITHUB_REPO_PUBLIC}/releases/latest`
  );
}

export async function startUpdate(downloadUrl: string, version: string): Promise<void> {
  console.log(`[Updater] Starting update to ${version}`);

  const corruption = detectCorruptInstall();
  if (corruption) {
    console.error('[Updater] Refusing to update on top of corrupt install:', corruption);
    safeSend('update-error', { message: corruption });
    return;
  }

  const fallbackUrl = `https://github.com/${GITHUB_OWNER}/${GITHUB_REPO_PUBLIC}/releases/tag/v${version}`;
  const assetName = decodeURIComponent(downloadUrl.split('/').pop() || '');

  // Fetch the matching release ahead of time so we can verify the
  // downloaded artifact's checksum (when the release publishes a
  // SHA256SUMS manifest). Falling back to no-verify keeps existing
  // releases working.
  let matchingRelease: GitHubRelease | null = null;
  try {
    const releases = await fetchGitHubReleases();
    matchingRelease = releases.find((r) => r.tag_name === `v${version}` || r.tag_name === version) || null;
  } catch (err) {
    console.warn('[Updater] Could not fetch release for checksum verification:', err);
  }

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

      // Verify the download against the release's published checksums.
      // Throws if a manifest exists and the hash doesn't match — we then
      // delete the corrupt download and surface an error instead of
      // running a tampered installer.
      if (matchingRelease) {
        try {
          await verifyDownloadChecksum(matchingRelease, assetName, installerPath);
        } catch (err) {
          await rm(installerPath, { force: true });
          throw err;
        }
      }

      console.log('[Updater] Download complete, launching installer...');

      // Notify the UI that the update is ready
      safeSend('update-ready', { version });

      // Launch the NSIS installer silently and detached.
      // --force-run forces the app to relaunch after install completes.
      // Retry on EBUSY: Windows Defender / AV briefly holds open the
      // freshly-downloaded .exe for inspection — first spawn can fail.
      let spawnAttempt = 0;
      let child;
      while (true) {
        try {
          child = spawn(installerPath, ['/S', '--force-run'], {
            detached: true,
            stdio: 'ignore',
          });
          break;
        } catch (err: unknown) {
          spawnAttempt++;
          const code = (err as NodeJS.ErrnoException).code;
          if (code === 'EBUSY' && spawnAttempt < 5) {
            console.warn(`[Updater] spawn EBUSY attempt ${spawnAttempt} — retrying in ${spawnAttempt * 500}ms`);
            await new Promise((r) => setTimeout(r, spawnAttempt * 500));
            continue;
          }
          throw err;
        }
      }
      child.unref();

      // Belt-and-suspenders: queue a relaunch on quit so even if NSIS's
      // --force-run flag is ignored (electron-builder behavior varies in
      // silent mode), the new binary launches when our process exits.
      // The installer replaces files in-place at the same execPath.
      app.relaunch();

      // Quit the app after a short delay to allow the installer to start
      setTimeout(() => {
        console.log('[Updater] Quitting app for update...');
        app.exit(0);
      }, 500);
    } else {
      // macOS: download DMG and use updater script
      const dmgPath = path.join(UPDATE_DIR, `update-${version}.dmg`);

      // Download the DMG with progress reporting
      console.log('[Updater] Downloading DMG...');
      await downloadFile(downloadUrl, dmgPath, (percent) => {
        safeSend('update-downloading', { percent });
      });

      // Verify download against published checksums (see startUpdate header)
      if (matchingRelease) {
        try {
          await verifyDownloadChecksum(matchingRelease, assetName, dmgPath);
        } catch (err) {
          await rm(dmgPath, { force: true });
          throw err;
        }
      }

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

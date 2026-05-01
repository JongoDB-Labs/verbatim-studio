/**
 * Migrates heavy resources (Python env, FFmpeg) from the app bundle to the
 * user data directory on first launch. This allows stripped "update" releases
 * to work without re-bundling the Python environment every time.
 *
 * Flow:
 *   Full install → Python in bundle → copied to user data on first launch
 *   Update install → No Python in bundle → user data copy already exists
 */

import { app } from 'electron';
import * as path from 'path';
import { createHash } from 'crypto';
import { execFile } from 'child_process';
import { promisify } from 'util';
import { existsSync, lstatSync, readFileSync, readlinkSync } from 'fs';
import { chmod, cp, mkdir, readFile, rename, rm, writeFile } from 'fs/promises';

const execFileAsync = promisify(execFile);

const TEMP_MIGRATION_SUFFIX = '.migrating';

/**
 * Migrate bundled Python environment to user data directory.
 * Skips if already migrated or if no bundled Python (update variant).
 *
 * Uses a temp directory + rename strategy for crash recovery:
 * if the app crashes mid-copy, the temp dir is cleaned up on next launch.
 *
 * @returns true if migration happened, false if skipped
 */
export async function migrateResourcesToUserData(
  onProgress?: (message: string) => void
): Promise<boolean> {
  if (!app.isPackaged) {
    return false;
  }

  const userDataDir = app.getPath('userData');
  const userPythonDir = path.join(userDataDir, 'python');
  const tempPythonDir = userPythonDir + TEMP_MIGRATION_SUFFIX;
  const bundledPythonDir = path.join(process.resourcesPath, 'python');

  const pythonBin = process.platform === 'win32'
    ? path.join(userPythonDir, 'python.exe')
    : path.join(userPythonDir, 'bin', 'python3');

  // Clean up any failed previous migration
  if (existsSync(tempPythonDir)) {
    console.log('[Migration] Cleaning up incomplete previous migration');
    await rm(tempPythonDir, { recursive: true, force: true });
  }

  const hasBundledPython = existsSync(bundledPythonDir);
  const hasUserPython = existsSync(pythonBin);

  // Check if the Python binary is a symlink pointing into the app bundle.
  // Earlier migrations used fs.cp without dereference, so symlinks were
  // preserved with absolute paths back to the bundle. When a stripped update
  // replaces the app, these symlinks break. Detect this and force re-migration.
  const hasBrokenSymlink = isPythonSymlinkedToBundle(pythonBin);
  if (hasBrokenSymlink && hasBundledPython) {
    console.log('[Migration] Python binary is symlinked to app bundle — forcing re-migration');
  }

  // No bundled Python — this is an update install
  if (!hasBundledPython) {
    if (hasUserPython && !hasBrokenSymlink) {
      console.log('[Migration] Update variant — using existing Python from user data');

      // Check if requirements changed since last migration. If so, install
      // missing packages into the existing user data Python. This handles
      // the case where a stripped update adds new dependencies (e.g. livekit)
      // that weren't in the original full install.
      const depsChanged = await haveDepsChanged(userDataDir);
      if (depsChanged) {
        console.log('[Migration] Dependencies changed in update — reconciling packages');
        onProgress?.('Updating Python packages\u2026');
        await reconcileDeps(userPythonDir, onProgress);
        await writePythonEnvVersion(userDataDir);
      }

      // Voice clones may have been added/updated in the stripped update too
      await migrateVoiceClones(userDataDir, onProgress);
    } else {
      // This happens when a user upgrades from a pre-migration version using a
      // stripped update. They need the full installer to bootstrap the Python env.
      console.error('[Migration] No Python in bundle or user data!');
      const { dialog } = await import('electron');
      const releaseUrl = `https://github.com/JongoDB/verbatim-studio/releases/latest`;
      const result = await dialog.showMessageBox({
        type: 'error',
        title: 'Python Environment Missing',
        message: 'This update requires a one-time full install to set up the Python environment.',
        detail: 'Please download the full installer from the releases page. Future updates will be much smaller.',
        buttons: ['Open Downloads', 'Quit'],
      });
      if (result.response === 0) {
        const { shell } = await import('electron');
        await shell.openExternal(releaseUrl);
      }
      app.quit();
      // Return false but app is quitting — prevent further startup
      return false;
    }
    return false;
  }

  // Full install with bundled Python — check if we need to (re)migrate
  if (hasUserPython && !hasBrokenSymlink) {
    const depsChanged = await haveDepsChanged(userDataDir);
    if (!depsChanged) {
      console.log('[Migration] Python already in user data and deps unchanged, skipping');
      return false;
    }
    console.log('[Migration] Python deps changed — re-migrating from bundle');
    onProgress?.('Updating Python environment\u2026');
  }

  // Copy Python from bundle to temp dir, then atomically rename
  console.log('[Migration] Copying Python environment to user data...');
  console.log(`[Migration]   From: ${bundledPythonDir}`);
  console.log(`[Migration]   To:   ${tempPythonDir} (staging)`);
  onProgress?.('Migrating Python environment\u2026');

  const startTime = Date.now();
  await mkdir(tempPythonDir, { recursive: true });
  await cp(bundledPythonDir, tempPythonDir, { recursive: true, dereference: true });

  // Ensure Python binary is executable (fs.cp preserves permissions on most
  // systems, but this guarantees it works on all filesystems)
  if (process.platform !== 'win32') {
    const tempBin = path.join(tempPythonDir, 'bin', 'python3');
    if (existsSync(tempBin)) {
      await chmod(tempBin, 0o755);
    }
  }

  // Atomic swap: remove old → rename temp to final
  await rm(userPythonDir, { recursive: true, force: true });
  await rename(tempPythonDir, userPythonDir);

  const elapsed = ((Date.now() - startTime) / 1000).toFixed(1);
  console.log(`[Migration] Python migration complete (${elapsed}s)`);

  // Migrate FFmpeg if bundled
  await migrateDir(
    path.join(process.resourcesPath, 'ffmpeg'),
    path.join(userDataDir, 'ffmpeg'),
    'FFmpeg',
    onProgress,
  );

  // Migrate CUDA libs if bundled (Windows only)
  if (process.platform === 'win32') {
    await migrateDir(
      path.join(process.resourcesPath, 'cuda'),
      path.join(userDataDir, 'cuda'),
      'CUDA libraries',
      onProgress,
    );
  }

  // Migrate bundled voice clones (Max assistant reference voice for Qwen3).
  // File-level merge: never overwrite a user-uploaded clone with the same name.
  await migrateVoiceClones(userDataDir, onProgress);

  // Write version marker for future dependency update detection
  await writePythonEnvVersion(userDataDir);

  return true;
}

/**
 * Copy bundled voice clone files into user data, replacing them when
 * the bundled version differs in size from the user's copy. This lets
 * us ship updated assistant voices to existing installs.
 *
 * To preserve a user-uploaded override, name it differently from the
 * bundled file (e.g. `my_voice.wav` instead of `max.wav`). Files in
 * user data with names not present in the bundle are never touched.
 */
async function migrateVoiceClones(
  userDataDir: string,
  onProgress?: (message: string) => void,
): Promise<void> {
  const bundledDir = path.join(process.resourcesPath, 'voice_clones');
  if (!existsSync(bundledDir)) {
    return;
  }

  const userDir = path.join(userDataDir, 'models', 'tts', 'voice_clones');
  await mkdir(userDir, { recursive: true });

  const { readdir, stat } = await import('fs/promises');
  let files: string[];
  try {
    files = await readdir(bundledDir);
  } catch {
    return;
  }

  let copied = 0;
  for (const name of files) {
    if (!name.endsWith('.wav') && !name.endsWith('.txt')) continue;
    const src = path.join(bundledDir, name);
    const dest = path.join(userDir, name);

    // Skip if dest exists and matches the bundled file's size (cheap proxy
    // for content equality — voice clones are produced once so size is stable).
    if (existsSync(dest)) {
      try {
        const [srcStat, destStat] = await Promise.all([stat(src), stat(dest)]);
        if (srcStat.size === destStat.size) continue;
        console.log(`[Migration] Voice clone ${name} differs (bundled ${srcStat.size}B vs user ${destStat.size}B) — replacing`);
      } catch {
        // Stat failed — fall through and overwrite
      }
    }

    await cp(src, dest, { force: true });
    copied++;
  }

  if (copied > 0) {
    console.log(`[Migration] Migrated ${copied} bundled voice clone(s) to ${userDir}`);
    onProgress?.('Setting up assistant voice…');
  }
}

/**
 * Copy a bundled directory to user data if it doesn't already exist there.
 */
async function migrateDir(
  srcDir: string,
  destDir: string,
  label: string,
  onProgress?: (message: string) => void,
): Promise<void> {
  if (!existsSync(srcDir) || existsSync(destDir)) {
    return;
  }

  console.log(`[Migration] Copying ${label} to user data...`);
  onProgress?.(`Migrating ${label}\u2026`);
  await mkdir(destDir, { recursive: true });
  await cp(srcDir, destDir, { recursive: true });
  console.log(`[Migration] ${label} migration complete`);
}

/**
 * Check if the Python binary at the given path is a symlink whose target
 * lives inside the app bundle. Such symlinks break when a stripped update
 * replaces the app (the target disappears).
 */
function isPythonSymlinkedToBundle(pythonBinPath: string): boolean {
  try {
    const stat = lstatSync(pythonBinPath);
    if (!stat.isSymbolicLink()) {
      return false;
    }
    const target = readlinkSync(pythonBinPath);
    // Absolute symlinks pointing into the app bundle are broken after updates
    return target.includes('/Contents/Resources/python/');
  } catch {
    return false;
  }
}

/**
 * Compute a hash of the bundled requirements files.
 * Used to detect when Python dependencies change across releases.
 *
 * In update variants where requirements files are stripped, falls back
 * to the app version so the hash is still meaningful.
 */
function computeDepsHash(): string {
  const hash = createHash('sha256');

  let foundAny = false;
  const reqFiles = ['requirements-ml.txt', 'requirements-ml-windows.txt'];
  for (const file of reqFiles) {
    const filePath = path.join(process.resourcesPath, file);
    if (existsSync(filePath)) {
      hash.update(readFileSync(filePath, 'utf-8'));
      foundAny = true;
    }
  }

  // Update variants don't have requirements files — use app version
  // so the hash is stable within a version but changes across versions
  if (!foundAny) {
    hash.update(`version:${app.getVersion()}`);
  }

  return hash.digest('hex').slice(0, 16);
}

/**
 * Check if Python dependencies have changed since last migration.
 */
async function haveDepsChanged(userDataDir: string): Promise<boolean> {
  const hashFile = path.join(userDataDir, 'python-deps-hash.txt');

  if (!existsSync(hashFile)) {
    return true; // No hash file means first migration
  }

  const storedHash = (await readFile(hashFile, 'utf-8')).trim();
  const currentHash = computeDepsHash();

  console.log(`[Migration] Deps hash: stored=${storedHash}, current=${currentHash}`);
  return storedHash !== currentHash;
}

/**
 * Write version and deps hash markers alongside the migrated Python env.
 */
async function writePythonEnvVersion(userDataDir: string): Promise<void> {
  const appVersion = app.getVersion();
  const depsHash = computeDepsHash();

  await writeFile(path.join(userDataDir, 'python-env-version.txt'), appVersion, 'utf-8');
  await writeFile(path.join(userDataDir, 'python-deps-hash.txt'), depsHash, 'utf-8');

  console.log(`[Migration] Wrote python-env-version: ${appVersion}, deps-hash: ${depsHash}`);
}

/**
 * Check if the Python env in user data matches the current app's deps.
 * Returns false if no hash file exists (pre-migration installs).
 */
export async function isPythonEnvCurrent(): Promise<boolean> {
  const userDataDir = app.getPath('userData');
  return !(await haveDepsChanged(userDataDir));
}

/**
 * Install specific missing Python packages into the user data Python
 * environment. Used by stripped updates where Python isn't re-bundled
 * but the requirements changed.
 *
 * IMPORTANT: Does NOT pip install the full requirements file — that
 * causes cascading dependency conflicts (e.g. protobuf downgrade
 * breaks google-auth). Instead, installs only the known missing
 * packages with --no-deps to avoid disturbing existing packages.
 */
async function reconcileDeps(
  pythonDir: string,
  onProgress?: (message: string) => void,
): Promise<void> {
  const pythonBin = process.platform === 'win32'
    ? path.join(pythonDir, 'python.exe')
    : path.join(pythonDir, 'bin', 'python3');

  if (!existsSync(pythonBin)) {
    console.error('[Migration] Cannot reconcile deps — Python binary not found:', pythonBin);
    return;
  }

  // Packages that were added after the initial release and need to be
  // installed in existing user data Python environments. Each entry is
  // [import_path, pip_spec] — only installed if the import fails.
  // Namespace packages that pip --target breaks. Check both livekit
  // (voice chat) and google (OAuth/calendar/storage).
  const missingPackages: Array<[string, string]> = [
    ['livekit.protocol', 'livekit-protocol>=1.1.0'],
    ['livekit.rtc', 'livekit>=1.1.0'],
    ['livekit.api', 'livekit-api>=1.1.0'],
    ['livekit.agents', 'livekit-agents>=1.5.0'],
    ['livekit.plugins.silero', 'livekit-plugins-silero>=1.5.0'],
    ['google.api_core', 'google-api-core'],
    ['google.oauth2', 'google-auth'],
    ['googleapiclient', 'google-api-python-client'],
  ];

  // Check which packages are actually missing
  const toInstall: string[] = [];
  for (const [importPath, spec] of missingPackages) {
    try {
      const { stderr } = await execFileAsync(
        pythonBin,
        ['-c', `import ${importPath}`],
        { timeout: 10_000 },
      );
      // Import succeeded — package is present
    } catch {
      toInstall.push(spec);
    }
  }

  if (toInstall.length === 0) {
    console.log('[Migration] All packages present — no reconciliation needed');
    return;
  }

  console.log(`[Migration] Installing ${toInstall.length} missing packages: ${toInstall.join(', ')}`);
  onProgress?.(`Installing ${toInstall.length} missing dependencies\u2026`);

  try {
    // Use --force-reinstall --no-deps: force-reinstall is needed because
    // pip --target may have left stale dist-info metadata that makes pip
    // think packages are installed when the actual namespace dirs are
    // missing. --no-deps avoids disturbing existing package versions.
    const { stdout, stderr } = await execFileAsync(
      pythonBin,
      ['-m', 'pip', 'install', '--force-reinstall', '--no-deps', ...toInstall],
      { timeout: 120_000 }, // 2 minutes
    );
    if (stdout) console.log('[Migration] pip stdout:', stdout.slice(-500));
    if (stderr) console.log('[Migration] pip stderr:', stderr.slice(-500));
    console.log('[Migration] Dependency reconciliation complete');
  } catch (err) {
    console.error('[Migration] Failed to reconcile deps:', err);
    // Non-fatal — the app will still work, just without the new deps
  }
}

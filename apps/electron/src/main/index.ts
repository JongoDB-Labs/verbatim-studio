import { app, BrowserWindow, dialog } from 'electron';
import path from 'path';
import { createMainWindow, navigateToPath } from './windows';
import { backendManager } from './backend';
import { livekitManager } from './livekit';
import { registerIpcHandlers } from './ipc';
import { initAutoUpdater } from './updater';
import { migrateResourcesToUserData } from './resource-migration';
import { bootstrapBundledModels } from './bootstrap-models';
import { createSplashWindow, updateSplashStatus, closeSplashWindow, focusSplashWindow } from './splash';

// Register verbatim:// protocol for deep linking from browser extension
if (process.defaultApp) {
  if (process.argv.length >= 2) {
    app.setAsDefaultProtocolClient('verbatim', process.execPath, [path.resolve(process.argv[1])]);
  }
} else {
  app.setAsDefaultProtocolClient('verbatim');
}

// Single instance lock
const gotTheLock = app.requestSingleInstanceLock();
if (!gotTheLock) {
  app.quit();
}

let mainWindow: BrowserWindow | null = null;
let pendingDeepLink: string | null = null;

/** Extract the path from a verbatim:// URL, e.g. "verbatim://recordings/abc" -> "/recordings/abc" */
function parseDeepLink(url: string): string | null {
  if (!url.startsWith('verbatim://')) return null;
  // verbatim://recordings/abc -> /recordings/abc
  const path = '/' + url.replace('verbatim://', '');
  return path;
}

function handleDeepLink(url: string) {
  const deepPath = parseDeepLink(url);
  if (!deepPath) return;

  if (mainWindow && !mainWindow.isDestroyed()) {
    navigateToPath(mainWindow, deepPath);
    if (mainWindow.isMinimized()) mainWindow.restore();
    mainWindow.focus();
  } else {
    // App is still starting — save for when the window is ready
    pendingDeepLink = deepPath;
  }
}

async function bootstrap(): Promise<void> {
  // Show splash screen immediately so the user sees feedback
  createSplashWindow();

  try {
    console.log('[Main] Bootstrap starting, app.isReady():', app.isReady());

    // Register IPC handlers
    registerIpcHandlers();

    // Migrate Python env from bundle to user data (first launch only)
    updateSplashStatus('Checking resources\u2026');
    await migrateResourcesToUserData((msg) => updateSplashStatus(msg));

    // Bootstrap bundled models (copy from resources to cache if needed)
    updateSplashStatus('Checking bundled models\u2026');
    await bootstrapBundledModels((msg) => updateSplashStatus(msg));

    // Start backend (both development and production)
    console.log('[Main] Starting backend...');
    console.log('[Main] app.isPackaged:', app.isPackaged);
    console.log('[Main] process.resourcesPath:', process.resourcesPath);

    updateSplashStatus('Starting backend\u2026');

    // Forward backend logs to splash screen for progress feedback.
    // On Windows the backend can take 30-60s to start — showing granular
    // progress prevents the splash from looking stuck.
    let startupStep = 0;
    const logListener = ({ message }: { level: string; message: string }) => {
      const msg = message.toLowerCase();
      if (msg.includes('importing') || msg.includes('import ')) {
        startupStep++;
        updateSplashStatus(`Loading Python modules (${startupStep})\u2026`);
      } else if (msg.includes('cache for model') || msg.includes('transformers')) {
        updateSplashStatus('Loading AI libraries\u2026');
      } else if (msg.includes('pyannote') || msg.includes('torchaudio')) {
        updateSplashStatus('Loading audio models\u2026');
      } else if (msg.includes('torch') && !msg.includes('torchaudio')) {
        updateSplashStatus('Loading torch\u2026');
      } else if (msg.includes('database') || msg.includes('init_db') || msg.includes('sqlite')) {
        updateSplashStatus('Initializing database\u2026');
      } else if (msg.includes('file_watcher') || msg.includes('filewatcher')) {
        updateSplashStatus('Starting file watcher\u2026');
      } else if (msg.includes('started server process')) {
        updateSplashStatus('Starting server\u2026');
      } else if (msg.includes('waiting for application startup')) {
        updateSplashStatus('Preparing application\u2026');
      } else if (msg.includes('application startup complete')) {
        updateSplashStatus('Almost ready\u2026');
      } else if (msg.includes('uvicorn') || msg.includes('running on')) {
        updateSplashStatus('Server starting\u2026');
      }
    };
    backendManager.on('log', logListener);

    await backendManager.start();

    backendManager.removeListener('log', logListener);

    console.log('[Main] Backend started successfully, port:', backendManager.port);
    console.log('[Main] Backend API URL:', backendManager.getApiUrl());

    // Start LiveKit server (optional — voice assistant won't be available without it)
    try {
      await livekitManager.start();
      console.log('[Main] LiveKit started, URL:', livekitManager.getUrl());
    } catch (error) {
      console.warn('[Main] LiveKit not available (voice assistant disabled):', error instanceof Error ? error.message : String(error));
    }

    // Double-check app is ready before creating window (should always be true here)
    if (!app.isReady()) {
      console.error('[Main] App not ready after backend start! Waiting...');
      await app.whenReady();
    }

    console.log('[Main] Creating main window...');
    updateSplashStatus('Loading interface\u2026');
    mainWindow = createMainWindow();

    // Close splash once the main window is visible
    mainWindow.once('ready-to-show', () => {
      closeSplashWindow();

      // If we received a deep link while the app was still starting, navigate now
      if (pendingDeepLink) {
        navigateToPath(mainWindow!, pendingDeepLink);
        pendingDeepLink = null;
      }
    });

    // Initialize auto-updater
    initAutoUpdater(mainWindow);
  } catch (error) {
    closeSplashWindow();
    console.error('[Main] Failed to start:', error);
    const logHint = process.platform === 'darwin'
      ? 'Check Console.app for more details.'
      : 'Check the application logs for more details.';
    dialog.showErrorBox(
      'Verbatim Studio - Startup Failed',
      `Failed to start backend:\n\n${error instanceof Error ? error.message : String(error)}\n\n${logHint}`
    );
    app.quit();
  }
}

app.on('ready', bootstrap);

app.on('activate', () => {
  if (app.isReady() && BrowserWindow.getAllWindows().length === 0) {
    if (!backendManager.isRunning()) {
      console.error('[Main] Backend not running on activate');
      dialog.showMessageBox({
        type: 'error',
        title: 'Backend Not Running',
        message: 'The backend process is not running.',
        detail: 'Please restart the application.',
        buttons: ['Restart', 'Quit'],
      }).then((result) => {
        if (result.response === 0) {
          app.relaunch();
        }
        app.quit();
      });
      return;
    }
    mainWindow = createMainWindow();
  }
});

let isQuitting = false;
let cleanupDone = false;

app.on('before-quit', async (event) => {
  if (cleanupDone) return; // Let quit proceed after cleanup

  if (isQuitting) {
    event.preventDefault();
    return; // Cleanup already in progress
  }

  event.preventDefault();
  isQuitting = true;

  console.log('[Main] Shutting down...');
  try {
    await livekitManager.stop();
  } catch (error) {
    console.error('[Main] Error stopping LiveKit:', error);
  }
  try {
    await backendManager.stop();
  } catch (error) {
    console.error('[Main] Error stopping backend:', error);
  }

  cleanupDone = true;
  setImmediate(() => app.quit());
});

app.on('window-all-closed', () => {
  app.quit();
});

app.on('second-instance', (_event, argv) => {
  try {
    // On Windows/Linux, the deep link URL is passed as the last argument
    const deepLinkUrl = argv.find(arg => arg.startsWith('verbatim://'));
    if (deepLinkUrl) {
      handleDeepLink(deepLinkUrl);
    } else if (mainWindow && !mainWindow.isDestroyed()) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.focus();
    } else {
      // Main window not ready yet (bootstrap still in progress) —
      // bring the splash screen to the foreground so the user sees it
      focusSplashWindow();
    }
  } catch (error) {
    console.error('[Main] Error handling second instance:', error);
  }
});

// macOS: handle verbatim:// URLs
app.on('open-url', (event, url) => {
  event.preventDefault();
  handleDeepLink(url);
});

// Handle backend health failures with recovery
let healthFailures = 0;
const MAX_HEALTH_FAILURES = 3;

backendManager.on('unhealthy', () => {
  healthFailures++;
  console.warn(`[Main] Backend unhealthy (${healthFailures}/${MAX_HEALTH_FAILURES})`);

  if (healthFailures >= MAX_HEALTH_FAILURES && mainWindow && !mainWindow.isDestroyed() && !isQuitting) {
    healthFailures = 0; // Reset so we don't show multiple dialogs
    dialog.showMessageBox(mainWindow, {
      type: 'error',
      title: 'Backend Unresponsive',
      message: 'The backend has become unresponsive.',
      detail: 'Please restart the application.',
      buttons: ['Restart', 'Quit'],
    }).then((result) => {
      if (result.response === 0) {
        app.relaunch();
      }
      app.quit();
    });
  }
});

// Reset health failure counter on successful backend output (stdout only —
// stderr may fire during error loops when the backend is actually unresponsive)
backendManager.on('log', (data: { level: string }) => {
  if (data.level === 'info' && healthFailures > 0) healthFailures = 0;
});

backendManager.on('exit', (code: number | null) => {
  console.warn(`[Main] Backend exited with code ${code}`);
  if (code !== 0 && code !== null && !isQuitting) {
    if (mainWindow && !mainWindow.isDestroyed()) {
      dialog.showMessageBox(mainWindow, {
        type: 'error',
        title: 'Backend Stopped',
        message: 'The backend process has stopped unexpectedly.',
        detail: `Exit code: ${code}\n\nPlease restart the application.`,
        buttons: ['Restart', 'Quit'],
      }).then((result) => {
        if (result.response === 0) {
          app.relaunch();
        }
        app.quit();
      });
    }
  }
});

// Safety net: ensure backend and LiveKit stop on process exit
process.on('exit', () => {
  if (livekitManager.isRunning()) {
    console.log('[Main] Process exiting, force-stopping LiveKit');
    try {
      livekitManager.forceKill();
    } catch {
      // Ignore errors during cleanup
    }
  }
  if (backendManager.isRunning()) {
    console.log('[Main] Process exiting, force-stopping backend');
    try {
      backendManager.forceKill();
    } catch {
      // Ignore errors during cleanup
    }
  }
});

// Handle uncaught exceptions
process.on('uncaughtException', async (error) => {
  console.error('[Main] Uncaught exception:', error);
  try {
    await livekitManager.stop();
  } catch {
    // Ignore cleanup errors
  }
  try {
    await backendManager.stop();
  } catch {
    // Ignore cleanup errors
  }
  process.exit(1);
});

// Handle unhandled promise rejections
process.on('unhandledRejection', (reason) => {
  console.error('[Main] Unhandled promise rejection:', reason);
});

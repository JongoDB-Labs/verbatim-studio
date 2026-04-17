import { BrowserWindow, shell, app, session } from 'electron';
import path from 'path';
import { backendManager } from './backend';

export function createMainWindow(): BrowserWindow {
  const preloadPath = path.join(__dirname, '../preload/index.js');
  console.log('[Window] Preload path:', preloadPath);

  // Grant microphone/camera permissions so live transcription and voice
  // chat work without "requested device not found" errors on Windows.
  session.defaultSession.setPermissionRequestHandler((_wc, permission, callback) => {
    const allowed = ['media', 'mediaKeySystem', 'audioCapture'].includes(permission);
    console.log(`[Permission] ${permission}: ${allowed ? 'granted' : 'denied'}`);
    callback(allowed);
  });
  session.defaultSession.setPermissionCheckHandler((_wc, permission) => {
    return ['media', 'mediaKeySystem', 'audioCapture'].includes(permission);
  });

  const mainWindow = new BrowserWindow({
    width: 1400,
    height: 900,
    minWidth: 1024,
    minHeight: 700,
    title: 'Verbatim Studio',
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      preload: preloadPath,
      sandbox: true,
    },
    titleBarStyle: process.platform === 'darwin' ? 'hiddenInset' : 'default',
    trafficLightPosition: { x: 16, y: 16 },
    show: false,
  });

  // Load content
  if (!app.isPackaged) {
    // Development: load from Vite dev server
    mainWindow.loadURL('http://localhost:5173');
    mainWindow.webContents.openDevTools();
  } else {
    // Production: load bundled frontend from extraResources
    const frontendPath = path.join(process.resourcesPath, 'frontend', 'index.html');
    console.log('[Window] Loading frontend from:', frontendPath);
    mainWindow.loadFile(frontendPath);
  }

  // Inject API URL into page early (backup for when preload fails)
  // Use did-start-navigation to inject before any JS runs (handles reloads)
  mainWindow.webContents.on('did-start-navigation', () => {
    const apiUrl = backendManager.getApiUrl();
    console.log('[Window] did-start-navigation - will inject API URL:', apiUrl);
  });

  // Also inject on dom-ready as backup
  mainWindow.webContents.on('dom-ready', () => {
    const apiUrl = backendManager.getApiUrl();
    const port = backendManager.port;
    const isRunning = backendManager.isRunning();
    console.log('[Window] dom-ready - Backend status: running=%s, port=%s, apiUrl=%s', isRunning, port, apiUrl);

    if (apiUrl) {
      const safeUrl = JSON.stringify(apiUrl);
      mainWindow.webContents.executeJavaScript(`
        window.__VERBATIM_API_URL__ = ${safeUrl};
        console.log('[Injected] API URL set to:', window.__VERBATIM_API_URL__);
      `).then(() => {
        console.log('[Window] API URL injection succeeded');
      }).catch((err) => {
        console.error('[Window] API URL injection FAILED:', err);
      });
    } else {
      console.error('[Window] Cannot inject API URL - backend not ready');
    }
  });

  // Handle page reloads - inject URL via preload on every navigation
  mainWindow.webContents.on('did-finish-load', () => {
    const apiUrl = backendManager.getApiUrl();
    if (apiUrl) {
      const safeUrl = JSON.stringify(apiUrl);
      mainWindow.webContents.executeJavaScript(`
        if (!window.__VERBATIM_API_URL__) {
          window.__VERBATIM_API_URL__ = ${safeUrl};
          console.log('[Injected on did-finish-load] API URL set to:', window.__VERBATIM_API_URL__);
        }
      `).catch((err) => {
        console.error('[Window] did-finish-load injection failed:', err);
      });
    }
  });

  // Handle reload properly in production - intercept Cmd+R/Ctrl+R
  mainWindow.webContents.on('before-input-event', (event, input) => {
    // Handle Cmd+R / Ctrl+R reload
    if ((input.meta || input.control) && input.key === 'r' && !input.shift && !input.alt) {
      event.preventDefault();
      console.log('[Window] Intercepted reload shortcut');

      if (!app.isPackaged) {
        mainWindow.loadURL('http://localhost:5173');
      } else {
        const frontendPath = path.join(process.resourcesPath, 'frontend', 'index.html');
        console.log('[Window] Reloading from:', frontendPath);
        mainWindow.loadFile(frontendPath);
      }
      return;
    }

    // Enable dev tools with keyboard shortcut (Cmd+Option+I / Ctrl+Shift+I)
    if ((input.meta && input.alt && input.key === 'i') ||
        (input.control && input.shift && input.key === 'I')) {
      mainWindow.webContents.toggleDevTools();
    }
  });

  // Prevent navigation to invalid URLs in production
  mainWindow.webContents.on('will-navigate', (event, url) => {
    console.log('[Window] will-navigate to:', url);
    if (app.isPackaged && !url.startsWith('file://') && !url.startsWith('http://localhost')) {
      // Allow file:// and localhost URLs only in packaged app
      if (!url.startsWith('https://') && !url.startsWith('http://')) {
        console.log('[Window] Blocking invalid navigation');
        event.preventDefault();
      }
    }
  });

  // Show when ready
  mainWindow.on('ready-to-show', () => {
    mainWindow.show();
  });

  // Open external links in browser (restrict to http/https only)
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    try {
      const parsed = new URL(url);
      if (parsed.protocol === 'http:' || parsed.protocol === 'https:') {
        shell.openExternal(url);
      } else {
        console.warn('[Window] Blocked non-HTTP(S) external URL:', parsed.protocol);
      }
    } catch {
      console.error('[Window] Invalid external URL:', url);
    }
    return { action: 'deny' };
  });

  return mainWindow;
}

/**
 * Navigate an existing main window to a given in-app path (e.g. "/recordings/abc").
 * In development we reload from the Vite dev server URL; in production we reload
 * the bundled index.html and then push the path via History API so the React router
 * picks it up.
 */
export function navigateToPath(win: BrowserWindow, deepPath: string): void {
  if (!app.isPackaged) {
    // Dev: just load the Vite URL with the path
    win.loadURL(`http://localhost:5173${deepPath}`);
  } else {
    // Production: the frontend reads window.location.pathname on mount via
    // pathToNavigation(). We can push the path via executeJavaScript so the
    // React state picks it up without a full page reload.
    win.webContents.executeJavaScript(`
      window.history.pushState(null, '', ${JSON.stringify(deepPath)});
      window.dispatchEvent(new PopStateEvent('popstate'));
    `).catch((err) => {
      console.error('[Window] navigateToPath failed:', err);
    });
  }
}

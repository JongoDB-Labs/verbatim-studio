import { ipcMain, app, BrowserWindow, dialog } from 'electron';
import { backendManager } from './backend';
import { checkForUpdates, startUpdate, markWhatsNewSeen } from './updater';
import { getAutoUpdateEnabled, setAutoUpdateEnabled, getGithubPat, setGithubPat } from './update-store';
import { captureScreenshot } from './screenshot';

export function registerIpcHandlers(): void {
  // App info
  ipcMain.handle('app:getVersion', () => {
    return app.getVersion();
  });

  ipcMain.handle('app:restart', () => {
    app.relaunch();
    app.exit(0);
  });

  // API URL - returns the backend URL for the renderer
  ipcMain.handle('api:getUrl', () => {
    return backendManager.getApiUrl();
  });

  // API Port - returns just the port number
  ipcMain.handle('api:getPort', () => {
    return backendManager.port;
  });

  // Connection mode - for future enterprise support
  ipcMain.handle('api:getConnectionMode', () => {
    // TODO: Read from settings when implementing enterprise mode
    return 'local';
  });

  // Directory picker - returns full path
  ipcMain.handle('dialog:openDirectory', async (event) => {
    const window = BrowserWindow.fromWebContents(event.sender);
    const result = await dialog.showOpenDialog(window!, {
      properties: ['openDirectory', 'createDirectory'],
      title: 'Select Storage Folder',
    });
    if (result.canceled || result.filePaths.length === 0) {
      return null;
    }
    return result.filePaths[0];
  });

  // File picker - returns full path(s)
  ipcMain.handle('dialog:openFile', async (event, options?: {
    filters?: { name: string; extensions: string[] }[];
    multiple?: boolean;
  }) => {
    const window = BrowserWindow.fromWebContents(event.sender);
    const result = await dialog.showOpenDialog(window!, {
      properties: options?.multiple ? ['openFile', 'multiSelections'] : ['openFile'],
      filters: options?.filters,
    });
    if (result.canceled || result.filePaths.length === 0) {
      return null;
    }
    return options?.multiple ? result.filePaths : result.filePaths[0];
  });

  // Screenshot capture
  ipcMain.handle('screenshot:capture', async (event) => {
    const window = BrowserWindow.fromWebContents(event.sender);
    if (!window) return { data: null, width: 0, height: 0 };
    return captureScreenshot(window);
  });

  // Window controls
  ipcMain.on('window:minimize', (event) => {
    const window = BrowserWindow.fromWebContents(event.sender);
    window?.minimize();
  });

  ipcMain.on('window:maximize', (event) => {
    const window = BrowserWindow.fromWebContents(event.sender);
    if (window?.isMaximized()) {
      window.unmaximize();
    } else {
      window?.maximize();
    }
  });

  ipcMain.on('window:close', (event) => {
    const window = BrowserWindow.fromWebContents(event.sender);
    window?.close();
  });

  // Update handlers
  ipcMain.on('update:start', (_event, { downloadUrl, version }) => {
    // Validate URL - restrict to GitHub releases only
    try {
      const parsed = new URL(downloadUrl);
      const allowedHosts = ['github.com', 'api.github.com', 'objects.githubusercontent.com'];
      if (!allowedHosts.some((h) => parsed.hostname === h || parsed.hostname.endsWith('.' + h))) {
        console.error('[IPC] Download URL must be from GitHub:', parsed.hostname);
        return;
      }
    } catch {
      console.error('[IPC] Invalid download URL:', downloadUrl);
      return;
    }
    // Validate version format (semver-like)
    if (!version || typeof version !== 'string' || !/^\d+\.\d+\.\d+/.test(version)) {
      console.error('[IPC] Invalid version format:', version);
      return;
    }
    startUpdate(downloadUrl, version);
  });

  ipcMain.on('update:check', () => {
    checkForUpdates(true); // manual = true
  });

  ipcMain.on('update:whats-new-seen', (_event, version) => {
    if (!version || typeof version !== 'string') {
      console.error('[IPC] Invalid version for whats-new-seen:', version);
      return;
    }
    markWhatsNewSeen(version);
  });

  ipcMain.handle('update:getSettings', () => {
    return { autoUpdateEnabled: getAutoUpdateEnabled() };
  });

  ipcMain.handle('update:setAutoUpdate', (_event, enabled: boolean) => {
    if (typeof enabled !== 'boolean') {
      console.error('[IPC] Invalid enabled value:', enabled);
      return;
    }
    setAutoUpdateEnabled(enabled);
  });

  ipcMain.handle('update:getGithubPat', () => {
    return { pat: getGithubPat() };
  });

  ipcMain.handle('update:setGithubPat', (_event, pat: string) => {
    if (typeof pat !== 'string') {
      console.error('[IPC] Invalid PAT value');
      return;
    }
    setGithubPat(pat);
  });

  ipcMain.handle('update:testGithubPat', async (_event, pat: string) => {
    const https = await import('https');
    return new Promise<{ valid: boolean; error?: string }>((resolve) => {
      const req = https.request(
        {
          hostname: 'api.github.com',
          path: '/repos/JongoDB/verbatim-studio/releases?per_page=1',
          method: 'GET',
          headers: {
            'User-Agent': `Verbatim Studio/${app.getVersion()}`,
            Accept: 'application/vnd.github.v3+json',
            Authorization: `token ${pat}`,
          },
        },
        (res) => {
          let data = '';
          res.on('data', (chunk: Buffer) => { data += chunk; });
          res.on('end', () => {
            if (res.statusCode === 200) {
              resolve({ valid: true });
            } else if (res.statusCode === 401) {
              resolve({ valid: false, error: 'Invalid token' });
            } else if (res.statusCode === 404) {
              resolve({ valid: false, error: 'Token lacks repo scope' });
            } else {
              resolve({ valid: false, error: `GitHub returned ${res.statusCode}` });
            }
          });
        },
      );
      req.on('error', (e: Error) => resolve({ valid: false, error: e.message }));
      req.setTimeout(10000, () => {
        req.destroy();
        resolve({ valid: false, error: 'Connection timed out' });
      });
      req.end();
    });
  });
}

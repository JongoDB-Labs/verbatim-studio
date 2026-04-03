import { contextBridge, ipcRenderer } from 'electron';

console.log('[Preload] Loading preload script...');
console.log('[Preload] contextBridge available:', !!contextBridge);
console.log('[Preload] ipcRenderer available:', !!ipcRenderer);

try {
  // Expose safe APIs to renderer
  contextBridge.exposeInMainWorld('electronAPI', {
  platform: process.platform,

  // App info
  getAppVersion: (): Promise<string> => ipcRenderer.invoke('app:getVersion'),
  restartApp: (): Promise<void> => ipcRenderer.invoke('app:restart'),

  // API connection
  getApiUrl: (): Promise<string | null> => ipcRenderer.invoke('api:getUrl'),
  getApiPort: (): Promise<number | null> => ipcRenderer.invoke('api:getPort'),
  getConnectionMode: (): Promise<'local' | 'connected' | 'hybrid'> =>
    ipcRenderer.invoke('api:getConnectionMode'),

  // Window controls
  minimize: (): void => ipcRenderer.send('window:minimize'),
  maximize: (): void => ipcRenderer.send('window:maximize'),
  close: (): void => ipcRenderer.send('window:close'),

  // Dialogs
  openDirectoryDialog: (): Promise<string | null> =>
    ipcRenderer.invoke('dialog:openDirectory'),
  openFileDialog: (options?: {
    filters?: { name: string; extensions: string[] }[];
    multiple?: boolean;
  }): Promise<string | string[] | null> =>
    ipcRenderer.invoke('dialog:openFile', options),

  // Screenshot capture
  captureScreenshot: (): Promise<{ data: string | null; width: number; height: number }> =>
    ipcRenderer.invoke('screenshot:capture'),

  // Update event listeners (return cleanup function)
  onUpdateAvailable: (
    callback: (data: { version: string; downloadUrl: string }) => void
  ) => {
    const handler = (_event: Electron.IpcRendererEvent, data: { version: string; downloadUrl: string }) => callback(data);
    ipcRenderer.on('update-available', handler);
    return () => ipcRenderer.off('update-available', handler);
  },
  onUpdateNotAvailable: (callback: () => void) => {
    const handler = () => callback();
    ipcRenderer.on('update-not-available', handler);
    return () => ipcRenderer.off('update-not-available', handler);
  },
  onUpdateDownloading: (callback: (data: { percent: number }) => void) => {
    const handler = (_event: Electron.IpcRendererEvent, data: { percent: number }) => callback(data);
    ipcRenderer.on('update-downloading', handler);
    return () => ipcRenderer.off('update-downloading', handler);
  },
  onUpdateReady: (callback: (data: { version: string }) => void) => {
    const handler = (_event: Electron.IpcRendererEvent, data: { version: string }) => callback(data);
    ipcRenderer.on('update-ready', handler);
    return () => ipcRenderer.off('update-ready', handler);
  },
  onUpdateError: (
    callback: (data: { message: string; fallbackUrl?: string }) => void
  ) => {
    const handler = (_event: Electron.IpcRendererEvent, data: { message: string; fallbackUrl?: string }) => callback(data);
    ipcRenderer.on('update-error', handler);
    return () => ipcRenderer.off('update-error', handler);
  },
  onShowWhatsNew: (
    callback: (data: { releases: Array<{ version: string; notes: string }> }) => void
  ) => {
    const handler = (_event: Electron.IpcRendererEvent, data: { releases: Array<{ version: string; notes: string }> }) => callback(data);
    ipcRenderer.on('show-whats-new', handler);
    return () => ipcRenderer.off('show-whats-new', handler);
  },

  // Update actions
  startUpdate: (downloadUrl: string, version: string): void => {
    ipcRenderer.send('update:start', { downloadUrl, version });
  },
  checkForUpdates: (): void => {
    ipcRenderer.send('update:check');
  },
  whatsNewSeen: (version: string): void => {
    ipcRenderer.send('update:whats-new-seen', version);
  },

  // Update async getters
  getUpdateSettings: (): Promise<{ autoUpdateEnabled: boolean }> => {
    return ipcRenderer.invoke('update:getSettings');
  },
  setAutoUpdate: (enabled: boolean): Promise<void> => {
    return ipcRenderer.invoke('update:setAutoUpdate', enabled);
  },

  // GitHub PAT for private repo updates
  getGithubPat: (): Promise<{ pat: string }> => {
    return ipcRenderer.invoke('update:getGithubPat');
  },
  setGithubPat: (pat: string): Promise<void> => {
    return ipcRenderer.invoke('update:setGithubPat', pat);
  },
  testGithubPat: (pat: string): Promise<{ valid: boolean; error?: string }> => {
    return ipcRenderer.invoke('update:testGithubPat', pat);
  },
  });
  console.log('[Preload] contextBridge.exposeInMainWorld succeeded');
} catch (err) {
  console.error('[Preload] contextBridge.exposeInMainWorld FAILED:', err);
}

// Type declaration for renderer
declare global {
  interface Window {
    electronAPI?: {
      platform: NodeJS.Platform;
      getAppVersion: () => Promise<string>;
      restartApp: () => Promise<void>;
      getApiUrl: () => Promise<string | null>;
      getApiPort: () => Promise<number | null>;
      getConnectionMode: () => Promise<'local' | 'connected' | 'hybrid'>;
      minimize: () => void;
      maximize: () => void;
      close: () => void;
      openDirectoryDialog: () => Promise<string | null>;
      openFileDialog: (options?: {
        filters?: { name: string; extensions: string[] }[];
        multiple?: boolean;
      }) => Promise<string | string[] | null>;
      captureScreenshot: () => Promise<{ data: string | null; width: number; height: number }>;
      // Update methods
      onUpdateAvailable: (
        callback: (data: { version: string; downloadUrl: string }) => void
      ) => () => void;
      onUpdateNotAvailable: (callback: () => void) => () => void;
      onUpdateDownloading: (callback: (data: { percent: number }) => void) => () => void;
      onUpdateReady: (callback: (data: { version: string }) => void) => () => void;
      onUpdateError: (
        callback: (data: { message: string; fallbackUrl?: string }) => void
      ) => () => void;
      onShowWhatsNew: (
        callback: (data: { releases: Array<{ version: string; notes: string }> }) => void
      ) => () => void;
      startUpdate: (downloadUrl: string, version: string) => void;
      checkForUpdates: () => void;
      whatsNewSeen: (version: string) => void;
      getUpdateSettings: () => Promise<{ autoUpdateEnabled: boolean }>;
      setAutoUpdate: (enabled: boolean) => Promise<void>;
      getGithubPat: () => Promise<{ pat: string }>;
      setGithubPat: (pat: string) => Promise<void>;
      testGithubPat: (pat: string) => Promise<{ valid: boolean; error?: string }>;
    };
  }
}

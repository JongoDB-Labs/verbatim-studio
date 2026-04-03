/**
 * Type declarations for the Electron API exposed via preload script.
 * This file ensures TypeScript knows about window.electronAPI.
 */

interface ElectronAPI {
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

  // GitHub PAT for private repo updates
  getGithubPat: () => Promise<{ pat: string }>;
  setGithubPat: (pat: string) => Promise<void>;
  testGithubPat: (pat: string) => Promise<{ valid: boolean; error?: string }>;
}

declare global {
  interface Window {
    electronAPI?: ElectronAPI;
  }
}

export {};

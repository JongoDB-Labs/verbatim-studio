/**
 * Global store for tracking model downloads across the app.
 * Uses Zustand for lightweight, persistent state management.
 */
import { create } from 'zustand';

export type ModelType = 'ai' | 'whisper' | 'ocr' | 'diarization' | 'tts';

export interface DownloadProgress {
  modelId: string;
  modelType: ModelType;
  modelName: string;
  status: 'downloading' | 'complete' | 'error';
  downloadedBytes: number;
  totalBytes: number;
  message?: string;
  error?: string;
}

interface DownloadState {
  // Active downloads
  downloads: Map<string, DownloadProgress>;

  // Whether the first-launch prompt has been dismissed
  promptDismissed: boolean;
  promptDismissedPermanently: boolean;

  // Actions
  startDownload: (modelId: string, modelType: ModelType, modelName: string) => void;
  updateProgress: (modelId: string, downloadedBytes: number, totalBytes: number, message?: string) => void;
  completeDownload: (modelId: string) => void;
  failDownload: (modelId: string, error: string) => void;
  removeDownload: (modelId: string) => void;
  dismissPrompt: (permanently: boolean) => void;
  resetPromptForSession: () => void;
}

// Storage key for permanent dismissal
const PROMPT_DISMISSED_KEY = 'verbatim-model-prompt-dismissed';

// Check if prompt was permanently dismissed
const wasPromptDismissedPermanently = (): boolean => {
  try {
    return localStorage.getItem(PROMPT_DISMISSED_KEY) === 'true';
  } catch {
    return false;
  }
};

export const useDownloadStore = create<DownloadState>((set, get) => ({
  downloads: new Map(),
  promptDismissed: wasPromptDismissedPermanently(),
  promptDismissedPermanently: wasPromptDismissedPermanently(),

  startDownload: (modelId, modelType, modelName) => {
    set((state) => {
      const newDownloads = new Map(state.downloads);
      newDownloads.set(modelId, {
        modelId,
        modelType,
        modelName,
        status: 'downloading',
        downloadedBytes: 0,
        totalBytes: 0,
      });
      return { downloads: newDownloads };
    });
  },

  updateProgress: (modelId, downloadedBytes, totalBytes, message) => {
    set((state) => {
      const download = state.downloads.get(modelId);
      if (!download) return state;

      const newDownloads = new Map(state.downloads);
      newDownloads.set(modelId, {
        ...download,
        downloadedBytes,
        totalBytes,
        message,
      });
      return { downloads: newDownloads };
    });
  },

  completeDownload: (modelId) => {
    set((state) => {
      const download = state.downloads.get(modelId);
      if (!download) return state;

      const newDownloads = new Map(state.downloads);
      newDownloads.set(modelId, {
        ...download,
        status: 'complete',
        downloadedBytes: download.totalBytes,
      });
      return { downloads: newDownloads };
    });

    // Auto-remove completed downloads after 3 seconds
    setTimeout(() => {
      get().removeDownload(modelId);
    }, 3000);
  },

  failDownload: (modelId, error) => {
    set((state) => {
      const download = state.downloads.get(modelId);
      if (!download) return state;

      const newDownloads = new Map(state.downloads);
      newDownloads.set(modelId, {
        ...download,
        status: 'error',
        error,
      });
      return { downloads: newDownloads };
    });
  },

  removeDownload: (modelId) => {
    set((state) => {
      const newDownloads = new Map(state.downloads);
      newDownloads.delete(modelId);
      return { downloads: newDownloads };
    });
  },

  dismissPrompt: (permanently) => {
    if (permanently) {
      try {
        localStorage.setItem(PROMPT_DISMISSED_KEY, 'true');
      } catch {
        // Ignore storage errors
      }
    }
    set({
      promptDismissed: true,
      promptDismissedPermanently: permanently,
    });
  },

  resetPromptForSession: () => {
    // Only reset if not permanently dismissed
    const permanent = wasPromptDismissedPermanently();
    set({
      promptDismissed: permanent,
      promptDismissedPermanently: permanent,
    });
  },
}));

// Derived selectors
// Note: useActiveDownloads and useTotalDownloadProgress derive outside the
// selector to avoid returning new array/object references that would cause
// infinite re-renders (Zustand uses Object.is equality by default).
export const useActiveDownloads = () => {
  const downloads = useDownloadStore((state) => state.downloads);
  return Array.from(downloads.values()).filter((d) => d.status === 'downloading');
};

export const useHasActiveDownloads = () => {
  return useDownloadStore((state) =>
    Array.from(state.downloads.values()).some((d) => d.status === 'downloading')
  );
};

export const useTotalDownloadProgress = () => {
  const downloads = useDownloadStore((state) => state.downloads);
  const active = Array.from(downloads.values()).filter((d) => d.status === 'downloading');

  if (active.length === 0) return null;

  const totalBytes = active.reduce((sum, d) => sum + d.totalBytes, 0);
  const downloadedBytes = active.reduce((sum, d) => sum + d.downloadedBytes, 0);

  return {
    count: active.length,
    downloadedBytes,
    totalBytes,
    percent: totalBytes > 0 ? Math.round((downloadedBytes / totalBytes) * 100) : 0,
  };
};

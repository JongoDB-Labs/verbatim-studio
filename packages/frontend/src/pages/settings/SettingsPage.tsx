import { useState, useEffect, useCallback, useRef } from 'react';
import { api, getApiUrl, type ArchiveInfo, type TranscriptionSettings, type AIModel, type AIModelDownloadEvent, type AISettingsResponse, type OCRModel, type OCRModelDownloadEvent, type WhisperModel, type WhisperModelDownloadEvent, type DiarizationModel, type DiarizationModelDownloadEvent, type SystemInfo, type MLStatus, type HardwareInfo, type StorageLocation, type MigrationStatus, type TransferStatus, type SyncResult, type StorageType, type StorageSubtype, type StorageLocationConfig, type OAuthStatusResponse, type CategoryCount, type ClearableCategory, type GpuStatus, type WebSearchSettings, type WebSearchSettingsUpdate } from '@/lib/api';
import { useDownloadStore } from '@/stores/downloadStore';
import { useKeybindingStore, DEFAULT_ACTIONS, formatCombo, type KeyCombo, type ActionCategory } from '@/stores/keybindingStore';
import { StorageTypeSelector } from '@/components/storage/StorageTypeSelector';
import { StorageSubtypeSelector } from '@/components/storage/StorageSubtypeSelector';
import { StorageConfigForm } from '@/components/storage/StorageConfigForm';
import { OAuthCredentialsConfig } from '@/components/storage/OAuthCredentialsConfig';
import { TIMEZONE_OPTIONS, getStoredTimezone, setStoredTimezone, type TimezoneValue } from '@/lib/utils';
import { EnterpriseBadge } from '@/components/ui/EnterpriseBadge';
import { APP_VERSION } from '@/version';

interface SettingsPageProps {
  theme: 'light' | 'dark' | 'system';
  onThemeChange: (theme: 'light' | 'dark' | 'system') => void;
  pluginSettingsTabs?: Array<{ id: string; label: string; icon: string }>;
}

// Languages supported by WhisperX
const LANGUAGES = [
  { code: '', label: 'Auto-detect' },
  { code: 'en', label: 'English' },
  { code: 'es', label: 'Spanish' },
  { code: 'fr', label: 'French' },
  { code: 'de', label: 'German' },
  { code: 'it', label: 'Italian' },
  { code: 'pt', label: 'Portuguese' },
  { code: 'nl', label: 'Dutch' },
  { code: 'ru', label: 'Russian' },
  { code: 'zh', label: 'Chinese' },
  { code: 'ja', label: 'Japanese' },
  { code: 'ko', label: 'Korean' },
];

const PLAYBACK_SPEEDS = [
  { value: 0.5, label: '0.5x' },
  { value: 0.75, label: '0.75x' },
  { value: 1, label: '1x (Normal)' },
  { value: 1.25, label: '1.25x' },
  { value: 1.5, label: '1.5x' },
  { value: 2, label: '2x' },
];

function getStoredSettings() {
  try {
    const stored = localStorage.getItem('verbatim-settings');
    if (stored) {
      return JSON.parse(stored);
    }
  } catch {
    // Ignore parse errors
  }
  return {};
}

function saveSettings(settings: Record<string, unknown>) {
  localStorage.setItem('verbatim-settings', JSON.stringify(settings));
}

function SettingSection({
  title,
  description,
  children,
}: {
  title: string;
  description?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="py-5 border-b border-gray-200 dark:border-gray-700 last:border-b-0">
      <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-4">
        <div className="sm:max-w-md">
          <h3 className="text-sm font-medium text-gray-900 dark:text-gray-100">{title}</h3>
          {description && (
            <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">{description}</p>
          )}
        </div>
        <div className="sm:w-64">{children}</div>
      </div>
    </div>
  );
}

const IGNORED_KEYS = new Set(['Control', 'Shift', 'Alt', 'Meta', 'CapsLock', 'Tab', 'NumLock', 'ScrollLock']);

const CATEGORY_LABELS: Record<ActionCategory, string> = {
  playback: 'Playback Shortcuts',
  live: 'Live Recording Shortcuts',
};

function KeybindingEditor() {
  const { getKey, getDisplayLabel, setKey, resetKey, resetAll, findConflict } = useKeybindingStore();
  // Subscribe to overrides to re-render on changes
  useKeybindingStore(s => s.overrides);
  const [expanded, setExpanded] = useState(false);
  const [capturing, setCapturing] = useState<string | null>(null);
  const [conflict, setConflict] = useState<{ actionId: string; combo: KeyCombo } | null>(null);

  useEffect(() => {
    if (!capturing) return;
    const handler = (e: KeyboardEvent) => {
      // Let ignored keys (bare modifiers, Tab) pass through naturally
      if (IGNORED_KEYS.has(e.key)) return;
      e.preventDefault();
      e.stopPropagation();

      // Escape cancels capture
      if (e.key === 'Escape' && !e.ctrlKey && !e.shiftKey && !e.altKey) {
        setCapturing(null);
        return;
      }

      const combo: KeyCombo = {
        key: e.key,
        ...(e.ctrlKey || e.metaKey ? { ctrl: true } : {}),
        ...(e.shiftKey ? { shift: true } : {}),
        ...(e.altKey ? { alt: true } : {}),
      };

      const conflictId = findConflict(combo, capturing);
      if (conflictId) {
        setConflict({ actionId: conflictId, combo });
      } else {
        setKey(capturing, combo);
        setCapturing(null);
      }
    };
    window.addEventListener('keydown', handler, true);
    return () => window.removeEventListener('keydown', handler, true);
  }, [capturing, findConflict, setKey]);

  const handleSwap = () => {
    if (!conflict || !capturing) return;
    // Swap: give the conflicted action whatever key the capturing action currently has
    const currentKey = getKey(capturing);
    setKey(capturing, conflict.combo);
    setKey(conflict.actionId, currentKey);
    setConflict(null);
    setCapturing(null);
  };

  const handleCancelConflict = () => {
    setConflict(null);
    // Stay in capture mode so user can try another key
  };

  const conflictLabel = conflict
    ? DEFAULT_ACTIONS.find(a => a.id === conflict.actionId)?.label ?? conflict.actionId
    : '';

  const hasOverrides = Object.keys(useKeybindingStore.getState().overrides).length > 0;

  return (
    <div className="mt-6 rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800">
      <div
        className="px-5 py-4 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between cursor-pointer select-none"
        onClick={() => setExpanded(!expanded)}
      >
        <div className="flex items-center gap-2">
          <svg className={`w-4 h-4 text-gray-500 transition-transform ${expanded ? 'rotate-90' : ''}`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
            <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
          </svg>
          <h2 className="text-base font-semibold text-gray-900 dark:text-gray-100">Keyboard Shortcuts</h2>
        </div>
        {hasOverrides && expanded && (
          <button
            onClick={(e) => { e.stopPropagation(); resetAll(); setCapturing(null); setConflict(null); }}
            className="text-xs text-gray-500 dark:text-gray-400 hover:text-red-600 dark:hover:text-red-400 transition-colors"
          >
            Reset all to defaults
          </button>
        )}
      </div>
      {expanded && <div className="px-5 py-4 space-y-6">
        {(['playback', 'live'] as ActionCategory[]).map(category => (
          <div key={category}>
            <h3 className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-3">{CATEGORY_LABELS[category]}</h3>
            <div className="space-y-1">
              {DEFAULT_ACTIONS.filter(a => a.category === category).map(action => {
                const isCapturing = capturing === action.id;
                const isDefault = !useKeybindingStore.getState().overrides[action.id];
                return (
                  <div key={action.id} className={`flex items-center justify-between py-2 px-3 rounded-lg transition-colors ${isCapturing ? 'bg-blue-50 dark:bg-blue-900/20 ring-1 ring-blue-300 dark:ring-blue-700' : 'hover:bg-gray-50 dark:hover:bg-gray-700/50'}`}>
                    <span className="text-sm text-gray-700 dark:text-gray-300">{action.label}</span>
                    <div className="flex items-center gap-2">
                      {/* Conflict warning inline */}
                      {conflict && capturing === action.id && (
                        <div className="flex items-center gap-2 text-xs">
                          <span className="text-amber-600 dark:text-amber-400">
                            {formatCombo(conflict.combo)} used by "{conflictLabel}"
                          </span>
                          <button onClick={handleSwap} className="text-blue-600 dark:text-blue-400 hover:underline font-medium">Swap</button>
                          <button onClick={handleCancelConflict} className="text-gray-500 hover:underline">Cancel</button>
                        </div>
                      )}
                      <kbd
                        onClick={() => { setCapturing(action.id); setConflict(null); }}
                        className={`min-w-[3rem] text-center px-2 py-1 rounded text-xs font-mono cursor-pointer transition-colors ${
                          isCapturing
                            ? 'bg-blue-100 dark:bg-blue-800 text-blue-700 dark:text-blue-300 animate-pulse'
                            : 'bg-gray-100 dark:bg-gray-700 text-gray-700 dark:text-gray-300 hover:bg-gray-200 dark:hover:bg-gray-600'
                        }`}
                      >
                        {isCapturing && !conflict ? 'Press a key\u2026' : getDisplayLabel(action.id)}
                      </kbd>
                      {!isDefault && (
                        <button
                          onClick={() => { resetKey(action.id); if (capturing === action.id) { setCapturing(null); setConflict(null); } }}
                          className="text-xs text-gray-400 hover:text-gray-600 dark:hover:text-gray-300"
                          title="Reset to default"
                        >
                          <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                            <path strokeLinecap="round" strokeLinejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                          </svg>
                        </button>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        ))}
        <p className="text-xs text-gray-500 dark:text-gray-400">
          Click any key to change it. Press Escape to cancel. Shortcuts within the same category must be unique.
        </p>
      </div>}
    </div>
  );
}

export function SettingsPage({ theme, onThemeChange, pluginSettingsTabs }: SettingsPageProps) {
  const [settings, setSettings] = useState(() => getStoredSettings());
  const [saved, setSaved] = useState(false);

  // Backup/restore state
  const [archiveInfo, setArchiveInfo] = useState<ArchiveInfo | null>(null);
  const [isExporting, setIsExporting] = useState(false);
  const [isImporting, setIsImporting] = useState(false);
  const [importResult, setImportResult] = useState<{ success: boolean; message: string } | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Transcription settings
  const [txSettings, setTxSettings] = useState<TranscriptionSettings | null>(null);
  const [txSaving, setTxSaving] = useState(false);
  const [txSaved, setTxSaved] = useState(false);
  const [showHfToken, setShowHfToken] = useState(false);
  const [hfTokenInput, setHfTokenInput] = useState('');

  // AI / LLM model state
  const [aiModels, setAiModels] = useState<AIModel[]>([]);
  const [aiDownloading, setAiDownloading] = useState<string | null>(null);
  const [aiDownloadedBytes, setAiDownloadedBytes] = useState(0);
  const [aiTotalBytes, setAiTotalBytes] = useState(0);
  const [aiError, setAiError] = useState<string | null>(null);
  const downloadAbortRef = useRef<{ abort: () => void } | null>(null);

  // AI context window settings
  const [aiSettings, setAiSettings] = useState<AISettingsResponse | null>(null);
  const [pendingContextSize, setPendingContextSize] = useState<number | null>(null);
  const [savingAiSettings, setSavingAiSettings] = useState(false);

  // OCR model state
  const [ocrModels, setOcrModels] = useState<OCRModel[]>([]);
  const [ocrDownloading, setOcrDownloading] = useState<string | null>(null);
  const [ocrDownloadMessage, setOcrDownloadMessage] = useState<string | null>(null);
  const [ocrDownloadProgress, setOcrDownloadProgress] = useState<{ percent: number; downloaded: number; total: number } | null>(null);
  const [ocrError, setOcrError] = useState<string | null>(null);
  const ocrDownloadAbortRef = useRef<{ abort: () => void } | null>(null);

  // Whisper model state
  const [whisperModels, setWhisperModels] = useState<WhisperModel[]>([]);
  const [whisperDownloading, setWhisperDownloading] = useState<string | null>(null);
  const [whisperDownloadMessage, setWhisperDownloadMessage] = useState<string | null>(null);
  const [whisperDownloadProgress, setWhisperDownloadProgress] = useState<{ percent: number; downloaded: number; total: number } | null>(null);
  const [whisperError, setWhisperError] = useState<string | null>(null);
  const whisperDownloadAbortRef = useRef<{ abort: () => void } | null>(null);

  // Diarization (Pyannote) model state
  const [diarizationModels, setDiarizationModels] = useState<DiarizationModel[]>([]);
  const [diarizationHfTokenSet, setDiarizationHfTokenSet] = useState(false);
  const [diarizationAllDownloaded, setDiarizationAllDownloaded] = useState(false);
  const [diarizationDownloading, setDiarizationDownloading] = useState<string | null>(null);
  const [diarizationDownloadMessage, setDiarizationDownloadMessage] = useState<string | null>(null);
  const [diarizationError, setDiarizationError] = useState<string | null>(null);
  const diarizationDownloadAbortRef = useRef<{ abort: () => void } | null>(null);

  // Hardware info (for memory-aware model recommendations)
  const [hardwareInfo, setHardwareInfo] = useState<HardwareInfo | null>(null);

  // Large download confirmation dialog
  const [confirmDownload, setConfirmDownload] = useState<{ modelId: string; modelLabel: string; sizeBytes: number; ramGb: number; type: 'ai' | 'ocr' } | null>(null);

  // System info state
  const [systemInfo, setSystemInfo] = useState<SystemInfo | null>(null);

  // ML dependencies state
  const [mlStatus, setMlStatus] = useState<MLStatus | null>(null);
  const [mlInstalling, setMlInstalling] = useState(false);
  const [mlInstallProgress, setMlInstallProgress] = useState<string | null>(null);
  const [mlInstallError, setMlInstallError] = useState<string | null>(null);

  // Storage locations state
  const [storageLocations, setStorageLocations] = useState<StorageLocation[]>([]);
  const [showAddLocation, setShowAddLocation] = useState(false);
  const [newLocationName, setNewLocationName] = useState('');
  const [newLocationType, setNewLocationType] = useState<StorageType>('local');
  const [newLocationSubtype, setNewLocationSubtype] = useState<StorageSubtype>(null);
  const [newLocationConfig, setNewLocationConfig] = useState<StorageLocationConfig>({});
  const [storageSaving, setStorageSaving] = useState(false);
  const [storageError, setStorageError] = useState<string | null>(null);
  const [editingLocation, setEditingLocation] = useState<StorageLocation | null>(null);
  const [testingConnection, setTestingConnection] = useState(false);
  const [connectionTestResult, setConnectionTestResult] = useState<{ success: boolean; message: string } | null>(null);
  const [newLocationOAuthTokens, setNewLocationOAuthTokens] = useState<OAuthStatusResponse['tokens'] | undefined>();

  // Migration state
  const [showMigrationDialog, setShowMigrationDialog] = useState(false);
  const [migrationSource, setMigrationSource] = useState('');
  const [migrationDest, setMigrationDest] = useState('');
  const [migrationStatus, setMigrationStatus] = useState<MigrationStatus | null>(null);
  const [pendingLocationUpdate, setPendingLocationUpdate] = useState<StorageLocation | null>(null);

  // Cross-location transfer state (for default switch)
  const [showTransferDialog, setShowTransferDialog] = useState(false);
  const [transferFromLocation, setTransferFromLocation] = useState<StorageLocation | null>(null);
  const [transferToLocationId, setTransferToLocationId] = useState<string | null>(null);
  const [transferFileCount, setTransferFileCount] = useState<{ recordings: number; documents: number } | null>(null);
  const [transferStatus, setTransferStatus] = useState<TransferStatus | null>(null);

  // Sync state
  const [syncing, setSyncing] = useState(false);
  const [syncResult, setSyncResult] = useState<SyncResult | null>(null);

  // Reset database state
  const [showResetDialog, setShowResetDialog] = useState(false);
  const [resetConfirmText, setResetConfirmText] = useState('');
  const [deleteMediaToo, setDeleteMediaToo] = useState(false);
  const [isResetting, setIsResetting] = useState(false);
  const [resetResult, setResetResult] = useState<{ success: boolean; message: string; deleted?: Record<string, number> } | null>(null);

  // Granular clear state
  const [categoryCounts, setCategoryCounts] = useState<CategoryCount[]>([]);
  const [selectedCategories, setSelectedCategories] = useState<Set<ClearableCategory>>(new Set());
  const [showClearDialog, setShowClearDialog] = useState(false);
  const [clearConfirmText, setClearConfirmText] = useState('');
  const [isClearing, setIsClearing] = useState(false);
  const [clearResult, setClearResult] = useState<{ success: boolean; message: string; deleted?: Record<string, number> } | null>(null);

  // Update settings state
  const [autoUpdateEnabled, setAutoUpdateEnabled] = useState(true);
  const [isCheckingForUpdates, setIsCheckingForUpdates] = useState(false);
  const [updateCheckResult, setUpdateCheckResult] = useState<'none' | 'available' | null>(null);

  // GPU acceleration state (Windows only)
  const [gpuStatus, setGpuStatus] = useState<GpuStatus | null>(null);
  const [gpuInstalling, setGpuInstalling] = useState(false);
  const [gpuInstallMessage, setGpuInstallMessage] = useState<string>('');
  const [gpuError, setGpuError] = useState<string>('');

  // Web Search settings
  const [webSearchSettings, setWebSearchSettings] = useState<WebSearchSettings | null>(null);
  const [webSearchProvider, setWebSearchProvider] = useState('tavily');
  const [webSearchApiKey, setWebSearchApiKey] = useState('');
  const [showWebSearchKey, setShowWebSearchKey] = useState(false);
  const [webSearchSaving, setWebSearchSaving] = useState(false);

  const defaultLanguage = settings.defaultLanguage || '';
  const defaultPlaybackSpeed = settings.defaultPlaybackSpeed || 1;
  const autoTranscribe = settings.autoTranscribe ?? false;

  const loadStorageLocations = useCallback(() => {
    api.storageLocations.list()
      .then((r) => setStorageLocations(r.items))
      .catch(console.error);
  }, []);

  // Load archive info, config status, transcription settings, AI models, OCR models, system info, ML status, and storage locations
  useEffect(() => {
    api.archive.info().then(setArchiveInfo).catch(console.error);
    api.config.getTranscription().then(setTxSettings).catch(console.error);
    api.ai.listModels().then((r) => setAiModels(r.models)).catch(console.error);
    api.config.getAI().then(setAiSettings).catch(console.error);
    api.ocr.listModels().then((r) => setOcrModels(r.models)).catch(console.error);
    api.whisper.listModels().then((r) => setWhisperModels(r.models)).catch(console.error);
    api.diarization.listModels().then((r) => {
      setDiarizationModels(r.models);
      setDiarizationHfTokenSet(r.hf_token_set);
      setDiarizationAllDownloaded(r.all_downloaded);
    }).catch(console.error);
    api.system.info().then(setSystemInfo).catch(console.error);
    api.system.mlStatus().then(setMlStatus).catch(console.error);
    api.system.getHardware().then(setHardwareInfo).catch(console.error);
    api.system.getCategoryCounts().then((r) => setCategoryCounts(r.categories)).catch(console.error);
    api.config.getWebSearchSettings().then((ws) => {
      setWebSearchSettings(ws);
      setWebSearchProvider(ws.provider);
    }).catch(console.error);
    // Fetch GPU status on Windows
    if (window.electronAPI?.platform === 'win32') {
      api.system.gpuStatus().then(setGpuStatus).catch(console.error);
    }
    loadStorageLocations();
  }, [loadStorageLocations]);

  // Load update settings on mount
  useEffect(() => {
    if (window.electronAPI) {
      window.electronAPI.getUpdateSettings().then(({ autoUpdateEnabled }) => {
        setAutoUpdateEnabled(autoUpdateEnabled);
      });

      const cleanupNotAvailable = window.electronAPI.onUpdateNotAvailable(() => {
        setIsCheckingForUpdates(false);
        setUpdateCheckResult('none');
        setTimeout(() => setUpdateCheckResult(null), 3000);
      });

      const cleanupAvailable = window.electronAPI.onUpdateAvailable(() => {
        setIsCheckingForUpdates(false);
        setUpdateCheckResult('available');
      });

      return () => {
        cleanupNotAvailable();
        cleanupAvailable();
      };
    }
  }, []);

  // Handle GPU acceleration installation (Windows only)
  const handleEnableGpu = useCallback(async () => {
    const { startDownload, updateProgress, completeDownload, failDownload } = useDownloadStore.getState();
    const downloadId = 'gpu-acceleration';

    setGpuInstalling(true);
    setGpuError('');
    setGpuInstallMessage('Starting...');

    // Track in global store for blue sidebar indicator
    startDownload(downloadId, 'ai', 'GPU Acceleration');

    try {
      for await (const event of api.system.enableGpu()) {
        if (event.status === 'progress') {
          setGpuInstallMessage(event.message);
          updateProgress(downloadId, 0, 0, event.message);
        } else if (event.status === 'complete') {
          setGpuInstallMessage(event.message);
          completeDownload(downloadId);
          if (window.electronAPI?.restartApp) {
            setTimeout(() => window.electronAPI!.restartApp(), 2000);
          } else {
            api.system.gpuStatus().then(setGpuStatus).catch(console.error);
          }
        } else if (event.status === 'error') {
          setGpuError(event.message);
          setGpuInstalling(false);
          failDownload(downloadId, event.message);
          break;
        }
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Installation failed';
      setGpuError(msg);
      failDownload(downloadId, msg);
    } finally {
      setGpuInstalling(false);
    }
  }, []);

  // Handle ML dependencies installation
  const handleInstallMl = useCallback(async () => {
    setMlInstalling(true);
    setMlInstallProgress('Starting installation...');
    setMlInstallError(null);

    try {
      for await (const event of api.system.installMl()) {
        if (event.status === 'progress') {
          setMlInstallProgress(event.message);
        } else if (event.status === 'complete') {
          setMlInstallProgress(event.message);
          // Refresh ML status after installation
          const newStatus = await api.system.mlStatus();
          setMlStatus(newStatus);
          // Also refresh transcription settings which includes available engines
          const newTxSettings = await api.config.getTranscription();
          setTxSettings(newTxSettings);
        } else if (event.status === 'error') {
          setMlInstallError(event.message);
        }
      }
    } catch (err) {
      setMlInstallError(err instanceof Error ? err.message : 'Installation failed');
    } finally {
      setMlInstalling(false);
    }
  }, []);

  const handleAutoUpdateToggle = async (enabled: boolean) => {
    setAutoUpdateEnabled(enabled);
    if (window.electronAPI) {
      await window.electronAPI.setAutoUpdate(enabled);
    }
  };

  const handleCheckForUpdates = () => {
    if (window.electronAPI) {
      setIsCheckingForUpdates(true);
      setUpdateCheckResult(null);
      window.electronAPI.checkForUpdates();
    }
  };

  const handleExport = useCallback(async () => {
    setIsExporting(true);
    try {
      const url = api.archive.exportUrl(true);
      window.location.href = url;
    } finally {
      setTimeout(() => setIsExporting(false), 1000);
    }
  }, []);

  const handleImportClick = useCallback(() => {
    fileInputRef.current?.click();
  }, []);

  const handleFileChange = useCallback(async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    setIsImporting(true);
    setImportResult(null);

    try {
      const result = await api.archive.import(file, true);
      setImportResult({
        success: result.errors.length === 0,
        message: `Imported ${result.recordings_imported} recordings, ${result.transcripts_imported} transcripts, ${result.projects_imported} projects` +
          (result.errors.length > 0 ? `. ${result.errors.length} errors.` : '.'),
      });
      // Refresh archive info
      const info = await api.archive.info();
      setArchiveInfo(info);
    } catch (err) {
      setImportResult({
        success: false,
        message: err instanceof Error ? err.message : 'Import failed',
      });
    } finally {
      setIsImporting(false);
      if (fileInputRef.current) {
        fileInputRef.current.value = '';
      }
    }
  }, []);

  useEffect(() => {
    if (saved) {
      const timer = setTimeout(() => setSaved(false), 2000);
      return () => clearTimeout(timer);
    }
  }, [saved]);

  useEffect(() => {
    if (txSaved) {
      const timer = setTimeout(() => setTxSaved(false), 2000);
      return () => clearTimeout(timer);
    }
  }, [txSaved]);

  const updateTxSetting = useCallback(async (field: string, value: string | number | boolean) => {
    setTxSaving(true);
    try {
      const updated = await api.config.updateTranscription({ [field]: value });
      setTxSettings(updated);
      setTxSaved(true);
    } catch (err) {
      console.error('Failed to save transcription setting:', err);
    } finally {
      setTxSaving(false);
    }
  }, []);

  const applyPreset = useCallback(async (presetKey: string) => {
    if (!txSettings) return;
    const preset = txSettings.presets[presetKey];
    if (!preset) return;
    setTxSaving(true);
    try {
      const updated = await api.config.updateTranscription({
        model: preset.model,
        compute_type: preset.compute_type,
        batch_size: preset.batch_size,
      });
      setTxSettings(updated);
      setTxSaved(true);
    } catch (err) {
      console.error('Failed to apply preset:', err);
    } finally {
      setTxSaving(false);
    }
  }, [txSettings]);

  const saveHfToken = useCallback(async () => {
    setTxSaving(true);
    try {
      const updated = await api.config.updateTranscription({ hf_token: hfTokenInput });
      setTxSettings(updated);
      setHfTokenInput('');
      setTxSaved(true);
      // Refresh diarization models since they depend on HF token
      api.diarization.listModels().then((r) => {
        setDiarizationModels(r.models);
        setDiarizationHfTokenSet(r.hf_token_set);
        setDiarizationAllDownloaded(r.all_downloaded);
      }).catch(console.error);
    } catch (err) {
      console.error('Failed to save HF token:', err);
    } finally {
      setTxSaving(false);
    }
  }, [hfTokenInput]);

  const saveWebSearchSettings = useCallback(async () => {
    setWebSearchSaving(true);
    try {
      const updates: WebSearchSettingsUpdate = {};
      if (webSearchProvider) updates.provider = webSearchProvider;
      if (webSearchApiKey) updates.api_key = webSearchApiKey;
      const updated = await api.config.updateWebSearchSettings(updates);
      setWebSearchSettings(updated);
      setWebSearchApiKey('');
    } catch (error) {
      console.error('Failed to save web search settings:', error);
    } finally {
      setWebSearchSaving(false);
    }
  }, [webSearchProvider, webSearchApiKey]);

  const refreshAiModels = useCallback(() => {
    api.ai.listModels().then((r) => setAiModels(r.models)).catch(console.error);
  }, []);

  const handleDownloadModel = useCallback((modelId: string, modelName?: string) => {
    const { startDownload, updateProgress, completeDownload, failDownload } = useDownloadStore.getState();

    setAiDownloading(modelId);
    setAiDownloadedBytes(0);
    setAiTotalBytes(0);
    setAiError(null);

    // Track in global store
    startDownload(modelId, 'ai', modelName || modelId);

    const handle = api.ai.downloadModel(modelId, (event: AIModelDownloadEvent) => {
      if (event.status === 'progress') {
        const downloaded = event.downloaded_bytes || 0;
        const total = event.total_bytes || 0;
        setAiDownloadedBytes(downloaded);
        setAiTotalBytes(total);
        updateProgress(modelId, downloaded, total);
      } else if (event.status === 'complete' || event.status === 'activated') {
        setAiDownloading(null);
        setAiDownloadedBytes(0);
        setAiTotalBytes(0);
        completeDownload(modelId);
        refreshAiModels();
        // Auto-activation happened on backend — notify all AI components
        if (event.status === 'activated') {
          window.dispatchEvent(new Event('ai-status-changed'));
        }
      } else if (event.status === 'error') {
        setAiError(event.error || 'Download failed');
        setAiDownloading(null);
        failDownload(modelId, event.error || 'Download failed');
      }
    });

    downloadAbortRef.current = handle;
  }, [refreshAiModels]);

  const handleActivateModel = useCallback(async (modelId: string) => {
    try {
      await api.ai.activateModel(modelId);
      refreshAiModels();
      // Refresh AI settings so context slider shows correct RAM estimates for the new model
      api.config.getAI().then(setAiSettings).catch(console.error);
      // Notify other components that AI status has changed
      window.dispatchEvent(new Event('ai-status-changed'));
    } catch (err) {
      setAiError(err instanceof Error ? err.message : 'Activation failed');
    }
  }, [refreshAiModels]);

  const handleDeactivateModel = useCallback(async (modelId: string) => {
    try {
      await api.ai.deactivateModel(modelId);
      refreshAiModels();
      // Refresh AI settings so context slider shows default RAM estimates
      api.config.getAI().then(setAiSettings).catch(console.error);
      window.dispatchEvent(new Event('ai-status-changed'));
    } catch (err) {
      setAiError(err instanceof Error ? err.message : 'Deactivation failed');
    }
  }, [refreshAiModels]);

  const handleDeleteModel = useCallback(async (modelId: string) => {
    try {
      await api.ai.deleteModel(modelId);
      refreshAiModels();
    } catch (err) {
      setAiError(err instanceof Error ? err.message : 'Delete failed');
    }
  }, [refreshAiModels]);

  // OCR model handlers
  const refreshOcrModels = useCallback(() => {
    api.ocr.listModels().then((r) => setOcrModels(r.models)).catch(console.error);
  }, []);

  const handleDownloadOcrModel = useCallback((modelId: string, modelName?: string) => {
    const { startDownload, updateProgress, completeDownload, failDownload } = useDownloadStore.getState();

    setOcrDownloading(modelId);
    setOcrDownloadMessage('Starting download...');
    setOcrDownloadProgress(null);
    setOcrError(null);

    // Track in global store
    startDownload(modelId, 'ocr', modelName || modelId);

    const handle = api.ocr.downloadModel(modelId, (event: OCRModelDownloadEvent) => {
      if (event.status === 'starting') {
        setOcrDownloadMessage('Initializing download...');
      } else if (event.status === 'progress') {
        const percent = event.percent ?? 0;
        const downloaded = event.downloaded_bytes ?? 0;
        const total = event.total_bytes ?? 0;
        setOcrDownloadProgress({ percent, downloaded, total });
        setOcrDownloadMessage(`Downloading... ${percent}%`);
        updateProgress(modelId, downloaded, total, `Downloading... ${percent}%`);
      } else if (event.status === 'complete' || event.status === 'activated') {
        setOcrDownloading(null);
        setOcrDownloadMessage(null);
        setOcrDownloadProgress(null);
        completeDownload(modelId);
        refreshOcrModels();
        // Notify other components that OCR is now available
        window.dispatchEvent(new Event('ocr-status-changed'));
      } else if (event.status === 'error') {
        setOcrError(event.error || 'Download failed');
        setOcrDownloading(null);
        setOcrDownloadMessage(null);
        setOcrDownloadProgress(null);
        failDownload(modelId, event.error || 'Download failed');
      }
    });

    ocrDownloadAbortRef.current = handle;
  }, [refreshOcrModels]);

  const handleActivateOcrModel = useCallback(async (modelId: string) => {
    try {
      await api.ocr.activateModel(modelId);
      refreshOcrModels();
      window.dispatchEvent(new Event('ocr-status-changed'));
    } catch (err) {
      setOcrError(err instanceof Error ? err.message : 'Activation failed');
    }
  }, [refreshOcrModels]);

  const handleDeactivateOcrModel = useCallback(async (modelId: string) => {
    try {
      await api.ocr.deactivateModel(modelId);
      refreshOcrModels();
      window.dispatchEvent(new Event('ocr-status-changed'));
    } catch (err) {
      setOcrError(err instanceof Error ? err.message : 'Deactivation failed');
    }
  }, [refreshOcrModels]);

  const handleDeleteOcrModel = useCallback(async (modelId: string) => {
    try {
      await api.ocr.deleteModel(modelId);
      refreshOcrModels();
    } catch (err) {
      setOcrError(err instanceof Error ? err.message : 'Delete failed');
    }
  }, [refreshOcrModels]);

  // Whisper model handlers
  const refreshWhisperModels = useCallback(() => {
    api.whisper.listModels().then((r) => setWhisperModels(r.models)).catch(console.error);
  }, []);

  const handleDownloadWhisperModel = useCallback((modelId: string, modelName?: string) => {
    const { startDownload, updateProgress, completeDownload, failDownload } = useDownloadStore.getState();

    setWhisperDownloading(modelId);
    setWhisperDownloadMessage('Starting download...');
    setWhisperDownloadProgress(null);
    setWhisperError(null);

    // Track in global store
    startDownload(modelId, 'whisper', modelName || modelId);

    const handle = api.whisper.downloadModel(modelId, (event: WhisperModelDownloadEvent) => {
      if (event.status === 'starting') {
        setWhisperDownloadMessage('Connecting to HuggingFace...');
      } else if (event.status === 'progress') {
        // Update progress if byte data available
        const percent = event.percent ?? (event.downloaded_bytes && event.total_bytes ? Math.round((event.downloaded_bytes / event.total_bytes) * 100) : 0);
        const downloaded = event.downloaded_bytes ?? 0;
        const total = event.total_bytes ?? 0;
        if (downloaded > 0 || total > 0) {
          setWhisperDownloadProgress({ percent, downloaded, total });
          setWhisperDownloadMessage(`Downloading... ${percent}%`);
          updateProgress(modelId, downloaded, total, `Downloading... ${percent}%`);
        } else {
          setWhisperDownloadMessage(event.message || 'Downloading...');
          updateProgress(modelId, 0, 0, event.message || 'Downloading...');
        }
      } else if (event.status === 'complete') {
        setWhisperDownloading(null);
        setWhisperDownloadMessage(null);
        setWhisperDownloadProgress(null);
        completeDownload(modelId);
        refreshWhisperModels();
      } else if (event.status === 'error') {
        setWhisperError(event.error || 'Download failed');
        setWhisperDownloading(null);
        setWhisperDownloadMessage(null);
        setWhisperDownloadProgress(null);
        failDownload(modelId, event.error || 'Download failed');
      }
    });

    whisperDownloadAbortRef.current = handle;
  }, [refreshWhisperModels]);

  const handleActivateWhisperModel = useCallback(async (modelId: string) => {
    try {
      await api.whisper.activateModel(modelId);
      refreshWhisperModels();
    } catch (err) {
      setWhisperError(err instanceof Error ? err.message : 'Activation failed');
    }
  }, [refreshWhisperModels]);

  const handleDeleteWhisperModel = useCallback(async (modelId: string) => {
    try {
      await api.whisper.deleteModel(modelId);
      refreshWhisperModels();
    } catch (err) {
      setWhisperError(err instanceof Error ? err.message : 'Delete failed');
    }
  }, [refreshWhisperModels]);

  // Diarization (Pyannote) model handlers
  const refreshDiarizationModels = useCallback(() => {
    api.diarization.listModels().then((r) => {
      setDiarizationModels(r.models);
      setDiarizationHfTokenSet(r.hf_token_set);
      setDiarizationAllDownloaded(r.all_downloaded);
    }).catch(console.error);
  }, []);

  const handleDownloadDiarizationModel = useCallback((modelId: string, modelName?: string) => {
    const { startDownload, updateProgress, completeDownload, failDownload } = useDownloadStore.getState();

    setDiarizationDownloading(modelId);
    setDiarizationDownloadMessage('Starting download...');
    setDiarizationError(null);

    // Track in global store
    startDownload(modelId, 'diarization', modelName || modelId);

    const handle = api.diarization.downloadModel(modelId, (event: DiarizationModelDownloadEvent) => {
      if (event.status === 'starting') {
        setDiarizationDownloadMessage('Connecting to HuggingFace...');
      } else if (event.status === 'progress') {
        setDiarizationDownloadMessage(event.message || 'Downloading...');
        updateProgress(modelId, 0, 0, event.message || 'Downloading...');
      } else if (event.status === 'complete') {
        setDiarizationDownloading(null);
        setDiarizationDownloadMessage(null);
        completeDownload(modelId);
        refreshDiarizationModels();
      } else if (event.status === 'error') {
        setDiarizationError(event.error || 'Download failed');
        setDiarizationDownloading(null);
        setDiarizationDownloadMessage(null);
        failDownload(modelId, event.error || 'Download failed');
      }
    });

    diarizationDownloadAbortRef.current = handle;
  }, [refreshDiarizationModels]);

  const handleDeleteDiarizationModel = useCallback(async (modelId: string) => {
    try {
      await api.diarization.deleteModel(modelId);
      refreshDiarizationModels();
    } catch (err) {
      setDiarizationError(err instanceof Error ? err.message : 'Delete failed');
    }
  }, [refreshDiarizationModels]);

  // Storage location handlers
  const resetAddLocationForm = useCallback(() => {
    setNewLocationName('');
    setNewLocationType('local');
    setNewLocationSubtype(null);
    setNewLocationConfig({});
    setConnectionTestResult(null);
    setNewLocationOAuthTokens(undefined);
    setStorageError(null);
  }, []);

  const handleTestConnection = useCallback(async () => {
    setTestingConnection(true);
    setConnectionTestResult(null);
    try {
      const result = await api.storageLocations.test({
        type: newLocationType,
        subtype: newLocationSubtype,
        config: newLocationConfig,
      });
      setConnectionTestResult({ success: result.success, message: result.error || (result.success ? 'Connection successful' : 'Connection failed') });
    } catch (err) {
      setConnectionTestResult({ success: false, message: err instanceof Error ? err.message : 'Test failed' });
    } finally {
      setTestingConnection(false);
    }
  }, [newLocationType, newLocationSubtype, newLocationConfig]);

  // OAuth handlers for cloud storage providers
  const handleOAuthSuccess = useCallback((tokens: OAuthStatusResponse['tokens']) => {
    setNewLocationOAuthTokens(tokens);
    setConnectionTestResult({ success: true, message: 'Successfully connected!' });
  }, []);

  const handleOAuthError = useCallback((error: string) => {
    setStorageError(error);
    setConnectionTestResult({ success: false, message: error });
  }, []);

  const handleOAuthDisconnect = useCallback(() => {
    setNewLocationOAuthTokens(undefined);
    setConnectionTestResult(null);
  }, []);

  const handleAddStorageLocation = useCallback(async () => {
    if (!newLocationName.trim()) return;
    // Validate config based on type
    if (newLocationType === 'local' && !newLocationConfig.path?.trim()) return;
    if (newLocationType === 'network' && !newLocationSubtype) return;
    if (newLocationType === 'cloud' && !newLocationSubtype) return;
    // OAuth providers require tokens
    const isOAuthProvider = ['gdrive', 'onedrive', 'dropbox'].includes(newLocationSubtype || '');
    if (isOAuthProvider && !newLocationOAuthTokens) return;

    setStorageSaving(true);
    setStorageError(null);
    try {
      // Include OAuth tokens in config for OAuth providers
      const config = isOAuthProvider
        ? { ...newLocationConfig, oauth_tokens: newLocationOAuthTokens }
        : newLocationConfig;

      await api.storageLocations.create({
        name: newLocationName.trim(),
        type: newLocationType,
        subtype: newLocationSubtype,
        config,
        is_default: storageLocations.length === 0,
      });
      resetAddLocationForm();
      setShowAddLocation(false);
      loadStorageLocations();

      // Automatically sync workspace after adding a new location
      setSyncing(true);
      setSyncResult(null);
      try {
        const result = await api.storageLocations.sync();
        setSyncResult(result);
        window.dispatchEvent(new CustomEvent('storage-synced'));
      } catch (syncErr) {
        // Sync errors are non-critical, just log them
        console.error('Auto-sync after add failed:', syncErr);
      } finally {
        setSyncing(false);
      }
    } catch (err) {
      setStorageError(err instanceof Error ? err.message : 'Failed to add storage location');
    } finally {
      setStorageSaving(false);
    }
  }, [newLocationName, newLocationType, newLocationSubtype, newLocationConfig, newLocationOAuthTokens, storageLocations.length, loadStorageLocations, resetAddLocationForm]);

  const handleUpdateStorageLocation = useCallback(async () => {
    if (!editingLocation) return;

    // Check if path is changing
    const originalLocation = storageLocations.find(l => l.id === editingLocation.id);
    const oldPath = originalLocation?.config?.path;
    const newPath = editingLocation.config?.path;

    if (oldPath && newPath && oldPath !== newPath) {
      // Path is changing - ask about migration
      setMigrationSource(oldPath);
      setMigrationDest(newPath);
      setPendingLocationUpdate(editingLocation);
      setShowMigrationDialog(true);
      return;
    }

    // Path not changing, proceed with update
    await performLocationUpdate(editingLocation);
  }, [editingLocation, storageLocations]);

  const performLocationUpdate = useCallback(async (location: StorageLocation) => {
    setStorageSaving(true);
    setStorageError(null);
    try {
      await api.storageLocations.update(location.id, {
        name: location.name,
        config: location.config,
      });
      setEditingLocation(null);
      loadStorageLocations();
    } catch (err) {
      setStorageError(err instanceof Error ? err.message : 'Failed to update storage location');
    } finally {
      setStorageSaving(false);
    }
  }, [loadStorageLocations]);

  const handleStartMigration = useCallback(async () => {
    if (!pendingLocationUpdate) return;

    setMigrationStatus({
      status: 'running',
      total_files: 0,
      migrated_files: 0,
      total_bytes: 0,
      migrated_bytes: 0,
      current_file: null,
      error: null,
    });

    try {
      // Start the migration
      const status = await api.storageLocations.migrate({
        source_path: migrationSource,
        destination_path: migrationDest,
      });
      setMigrationStatus(status);

      // Poll for status updates
      const pollInterval = setInterval(async () => {
        try {
          const currentStatus = await api.storageLocations.getMigrationStatus();
          setMigrationStatus(currentStatus);

          if (currentStatus.status === 'completed' || currentStatus.status === 'failed') {
            clearInterval(pollInterval);

            if (currentStatus.status === 'completed') {
              // Migration complete, update the storage location
              await performLocationUpdate(pendingLocationUpdate);
              setShowMigrationDialog(false);
              setPendingLocationUpdate(null);
              setMigrationStatus(null);
            }
          }
        } catch (err) {
          console.error('Failed to get migration status:', err);
        }
      }, 500);
    } catch (err) {
      setMigrationStatus({
        status: 'failed',
        total_files: 0,
        migrated_files: 0,
        total_bytes: 0,
        migrated_bytes: 0,
        current_file: null,
        error: err instanceof Error ? err.message : 'Failed to start migration',
      });
    }
  }, [migrationSource, migrationDest, pendingLocationUpdate, performLocationUpdate]);

  const handleSkipMigration = useCallback(async () => {
    if (!pendingLocationUpdate) return;
    // User chose not to migrate - just update the location
    await performLocationUpdate(pendingLocationUpdate);
    setShowMigrationDialog(false);
    setPendingLocationUpdate(null);
  }, [pendingLocationUpdate, performLocationUpdate]);

  const handleCancelMigration = useCallback(() => {
    setShowMigrationDialog(false);
    setPendingLocationUpdate(null);
    setMigrationStatus(null);
  }, []);

  const executeSetDefault = useCallback(async (id: string) => {
    setStorageError(null);
    try {
      await api.storageLocations.update(id, { is_default: true });
      loadStorageLocations();
      window.dispatchEvent(new CustomEvent('storage-location-changed'));

      // Automatically sync workspace after switching default location
      setSyncing(true);
      setSyncResult(null);
      try {
        const result = await api.storageLocations.sync();
        setSyncResult(result);
        window.dispatchEvent(new CustomEvent('storage-synced'));
      } catch (syncErr) {
        console.error('Auto-sync after default change failed:', syncErr);
      } finally {
        setSyncing(false);
      }
    } catch (err) {
      setStorageError(err instanceof Error ? err.message : 'Failed to set default');
    }
  }, [loadStorageLocations]);

  const handleSetDefaultLocation = useCallback(async (id: string) => {
    setStorageError(null);

    // Find the current default location
    const currentDefault = storageLocations.find(l => l.is_default);
    if (!currentDefault) {
      // No current default, just switch directly
      await executeSetDefault(id);
      return;
    }

    try {
      // Check if the current default has files
      const fileCount = await api.storageLocations.getFileCount(currentDefault.id);
      const totalFiles = (fileCount.recordings || 0) + (fileCount.documents || 0);

      if (totalFiles === 0) {
        // No files in current location, switch directly
        await executeSetDefault(id);
        return;
      }

      // Files exist - show transfer dialog
      setTransferFromLocation(currentDefault);
      setTransferToLocationId(id);
      setTransferFileCount(fileCount);
      setTransferStatus(null);
      setShowTransferDialog(true);
    } catch (err) {
      // If file count check fails, fall back to direct switch
      console.error('Failed to check file count:', err);
      await executeSetDefault(id);
    }
  }, [storageLocations, executeSetDefault]);

  const handleTransferFiles = useCallback(async (mode: 'copy' | 'move') => {
    if (!transferFromLocation || !transferToLocationId) return;

    try {
      const result = await api.storageLocations.transfer({
        from_location_id: transferFromLocation.id,
        to_location_id: transferToLocationId,
        mode,
      });
      setTransferStatus(result);

      // Poll for status updates
      const pollInterval = setInterval(async () => {
        try {
          const currentStatus = await api.storageLocations.getTransferStatus();
          setTransferStatus(currentStatus);

          if (currentStatus.status === 'completed' || currentStatus.status === 'failed') {
            clearInterval(pollInterval);

            if (currentStatus.status === 'completed') {
              // Transfer complete, now set the default and sync
              await executeSetDefault(transferToLocationId);
              setTimeout(() => {
                setShowTransferDialog(false);
                setTransferStatus(null);
                setTransferFromLocation(null);
                setTransferToLocationId(null);
                setTransferFileCount(null);
              }, 2000);
            }
          }
        } catch (err) {
          console.error('Failed to get transfer status:', err);
        }
      }, 500);
    } catch (err) {
      setTransferStatus({
        status: 'failed',
        total_files: 0,
        transferred_files: 0,
        current_file: null,
        error: err instanceof Error ? err.message : 'Failed to start transfer',
      });
    }
  }, [transferFromLocation, transferToLocationId, executeSetDefault]);

  const handleTransferSkip = useCallback(async () => {
    if (!transferToLocationId) return;
    setShowTransferDialog(false);
    setTransferFromLocation(null);
    setTransferFileCount(null);
    await executeSetDefault(transferToLocationId);
    setTransferToLocationId(null);
  }, [transferToLocationId, executeSetDefault]);

  const handleTransferCancel = useCallback(() => {
    setShowTransferDialog(false);
    setTransferFromLocation(null);
    setTransferToLocationId(null);
    setTransferFileCount(null);
    setTransferStatus(null);
  }, []);

  const handleDeleteStorageLocation = useCallback(async (id: string) => {
    if (!window.confirm('Are you sure you want to delete this storage location?')) return;
    setStorageError(null);
    try {
      await api.storageLocations.delete(id);
      loadStorageLocations();
    } catch (err) {
      setStorageError(err instanceof Error ? err.message : 'Failed to delete storage location');
    }
  }, [loadStorageLocations]);

  const formatBytes = (bytes: number): string => {
    if (bytes >= 1_073_741_824) return `${(bytes / 1_073_741_824).toFixed(2)} GB`;
    if (bytes >= 1_048_576) return `${(bytes / 1_048_576).toFixed(0)} MB`;
    return `${(bytes / 1024).toFixed(0)} KB`;
  };

  const updateSetting = (key: string, value: unknown) => {
    const newSettings = { ...settings, [key]: value };
    setSettings(newSettings);
    saveSettings(newSettings);
    setSaved(true);
  };

  // Tab state - check URL hash for direct linking
  const [activeTab, setActiveTab] = useState<string>(() => {
    const hash = window.location.hash.slice(1);
    const builtinTabs = ['general', 'transcription', 'ai', 'system'];
    const pluginTabIds = pluginSettingsTabs?.map(t => t.id) || [];
    const validTabs = [...builtinTabs, ...pluginTabIds];
    if (validTabs.includes(hash)) {
      return hash;
    }
    return 'general';
  });

  // Update URL hash when tab changes
  const handleTabChange = (tab: string) => {
    setActiveTab(tab);
    window.history.replaceState(null, '', `#${tab}`);
  };

  // Listen for hash changes (for tour navigation)
  useEffect(() => {
    const handleHashChange = () => {
      const hash = window.location.hash.slice(1);
      const builtinTabs = ['general', 'transcription', 'ai', 'system'];
      const pluginTabIds = pluginSettingsTabs?.map(t => t.id) || [];
      const validTabs = [...builtinTabs, ...pluginTabIds];
      if (validTabs.includes(hash)) {
        setActiveTab(hash);
      }
    };

    window.addEventListener('hashchange', handleHashChange);
    return () => window.removeEventListener('hashchange', handleHashChange);
  }, [pluginSettingsTabs]);

  const TABS = [
    { id: 'general' as const, label: 'General', icon: (
      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
        <path strokeLinecap="round" strokeLinejoin="round" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.066 2.573c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.573 1.066c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.066-2.573c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
        <path strokeLinecap="round" strokeLinejoin="round" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
      </svg>
    )},
    { id: 'transcription' as const, label: 'Transcription', icon: (
      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
        <path strokeLinecap="round" strokeLinejoin="round" d="M19 11a7 7 0 01-7 7m0 0a7 7 0 01-7-7m7 7v4m0 0H8m4 0h4m-4-8a3 3 0 01-3-3V5a3 3 0 116 0v6a3 3 0 01-3 3z" />
      </svg>
    )},
    { id: 'ai' as const, label: 'AI', icon: (
      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
        <path strokeLinecap="round" strokeLinejoin="round" d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
      </svg>
    )},
    { id: 'system' as const, label: 'System', icon: (
      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
        <path strokeLinecap="round" strokeLinejoin="round" d="M9 3v2m6-2v2M9 19v2m6-2v2M5 9H3m2 6H3m18-6h-2m2 6h-2M7 19h10a2 2 0 002-2V7a2 2 0 00-2-2H7a2 2 0 00-2 2v10a2 2 0 002 2zM9 9h6v6H9V9z" />
      </svg>
    )},
  ];

  return (
    <div className="max-w-3xl mx-auto">
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100">Settings</h1>
        <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
          Configure your Verbatim Studio preferences
        </p>
      </div>

      {/* Tab Navigation */}
      <div className="mb-6 border-b border-gray-200 dark:border-gray-700">
        <nav className="flex gap-1 -mb-px overflow-x-auto" aria-label="Settings tabs">
          {TABS.map((tab) => (
            <button
              key={tab.id}
              data-tour={`settings-${tab.id}`}
              onClick={() => handleTabChange(tab.id)}
              className={`flex items-center gap-2 px-4 py-3 text-sm font-medium border-b-2 transition-colors whitespace-nowrap ${
                activeTab === tab.id
                  ? 'border-blue-500 text-blue-600 dark:text-blue-400'
                  : 'border-transparent text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-300 hover:border-gray-300 dark:hover:border-gray-600'
              }`}
            >
              {tab.icon}
              {tab.label}
            </button>
          ))}
          {pluginSettingsTabs?.map((tab) => (
            <button
              key={tab.id}
              onClick={() => handleTabChange(tab.id)}
              className={`flex items-center gap-2 px-4 py-3 text-sm font-medium border-b-2 transition-colors whitespace-nowrap ${
                activeTab === tab.id
                  ? 'border-blue-500 text-blue-600 dark:text-blue-400'
                  : 'border-transparent text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-300 hover:border-gray-300 dark:hover:border-gray-600'
              }`}
            >
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                <path strokeLinecap="round" strokeLinejoin="round" d="M13.5 16.875h3.375m0 0h3.375m-3.375 0V13.5m0 3.375v3.375M6 10.5h2.25a2.25 2.25 0 002.25-2.25V6a2.25 2.25 0 00-2.25-2.25H6A2.25 2.25 0 003.75 6v2.25A2.25 2.25 0 006 10.5zm0 9.75h2.25A2.25 2.25 0 0010.5 18v-2.25a2.25 2.25 0 00-2.25-2.25H6a2.25 2.25 0 00-2.25 2.25V18A2.25 2.25 0 006 20.25zm9.75-9.75H18a2.25 2.25 0 002.25-2.25V6A2.25 2.25 0 0018 3.75h-2.25A2.25 2.25 0 0013.5 6v2.25a2.25 2.25 0 002.25 2.25z" />
              </svg>
              {tab.label}
            </button>
          ))}
        </nav>
      </div>

      {/* Saved indicator */}
      {saved && (
        <div className="mb-4 px-4 py-2 rounded-lg bg-green-50 dark:bg-green-900/20 border border-green-200 dark:border-green-800">
          <p className="text-sm text-green-700 dark:text-green-400 flex items-center gap-2">
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
              <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
            </svg>
            Settings saved
          </p>
        </div>
      )}

      {/* ===== GENERAL TAB ===== */}
      {activeTab === 'general' && (
        <>
      {/* Appearance Section */}
      <div className="rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800">
        <div className="px-5 py-4 border-b border-gray-200 dark:border-gray-700">
          <h2 className="text-base font-semibold text-gray-900 dark:text-gray-100">Appearance</h2>
        </div>
        <div className="px-5">
          <SettingSection
            title="Theme"
            description="Choose your preferred color scheme"
          >
            <div className="flex gap-2">
              {(['light', 'dark', 'system'] as const).map((t) => (
                <button
                  key={t}
                  onClick={() => onThemeChange(t)}
                  className={`flex-1 px-3 py-2 text-sm rounded-lg border transition-colors ${
                    theme === t
                      ? 'border-blue-500 bg-blue-50 dark:bg-blue-900/20 text-blue-700 dark:text-blue-300'
                      : 'border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700'
                  }`}
                >
                  {t === 'light' && (
                    <svg className="w-4 h-4 mx-auto mb-1" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                      <path strokeLinecap="round" strokeLinejoin="round" d="M12 3v1m0 16v1m9-9h-1M4 12H3m15.364 6.364l-.707-.707M6.343 6.343l-.707-.707m12.728 0l-.707.707M6.343 17.657l-.707.707M16 12a4 4 0 11-8 0 4 4 0 018 0z" />
                    </svg>
                  )}
                  {t === 'dark' && (
                    <svg className="w-4 h-4 mx-auto mb-1" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                      <path strokeLinecap="round" strokeLinejoin="round" d="M20.354 15.354A9 9 0 018.646 3.646 9.003 9.003 0 0012 21a9.003 9.003 0 008.354-5.646z" />
                    </svg>
                  )}
                  {t === 'system' && (
                    <svg className="w-4 h-4 mx-auto mb-1" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                      <path strokeLinecap="round" strokeLinejoin="round" d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
                    </svg>
                  )}
                  <span className="capitalize">{t}</span>
                </button>
              ))}
            </div>
          </SettingSection>

          <SettingSection
            title="Timezone"
            description="Display dates and times in your preferred timezone"
          >
            <select
              value={getStoredTimezone()}
              onChange={(e) => {
                setStoredTimezone(e.target.value as TimezoneValue);
                setSaved(true);
                setTimeout(() => setSaved(false), 2000);
              }}
              className="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 py-2 px-3 text-sm text-gray-900 dark:text-gray-100 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
            >
              {TIMEZONE_OPTIONS.map((tz) => (
                <option key={tz.value} value={tz.value}>
                  {tz.label}{tz.offset ? ` (${tz.offset})` : ''}
                </option>
              ))}
            </select>
          </SettingSection>
        </div>
      </div>
        </>
      )}

      {/* ===== TRANSCRIPTION TAB ===== */}
      {activeTab === 'transcription' && (
        <>
      {/* Transcription Section */}
      <div className="rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800">
        <div className="px-5 py-4 border-b border-gray-200 dark:border-gray-700">
          <h2 className="text-base font-semibold text-gray-900 dark:text-gray-100">Transcription</h2>
        </div>
        <div className="px-5">
          <SettingSection
            title="Default Language"
            description="Pre-select language for new transcriptions"
          >
            <select
              value={defaultLanguage}
              onChange={(e) => updateSetting('defaultLanguage', e.target.value)}
              className="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 py-2 px-3 text-sm text-gray-900 dark:text-gray-100 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
            >
              {LANGUAGES.map((lang) => (
                <option key={lang.code} value={lang.code}>
                  {lang.label}
                </option>
              ))}
            </select>
          </SettingSection>

          <SettingSection
            title="Auto-transcribe"
            description="Automatically start transcription after upload"
          >
            <label className="relative inline-flex items-center cursor-pointer">
              <input
                type="checkbox"
                checked={autoTranscribe}
                onChange={(e) => updateSetting('autoTranscribe', e.target.checked)}
                className="sr-only peer"
              />
              <div className="w-11 h-6 bg-gray-200 peer-focus:outline-none peer-focus:ring-2 peer-focus:ring-blue-500 rounded-full peer dark:bg-gray-700 peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all dark:border-gray-600 peer-checked:bg-blue-600" />
            </label>
          </SettingSection>
        </div>
      </div>

      {/* Transcription Engine Configuration */}
      {txSettings && (
        <div className="mt-6 rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800">
          <div className="px-5 py-4 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between">
            <div className="flex items-center gap-3">
              <h2 className="text-base font-semibold text-gray-900 dark:text-gray-100">Transcription Engine</h2>
              <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${
                txSettings.effective_engine === 'mlx-whisper'
                  ? 'bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-300'
                  : txSettings.mode === 'external'
                    ? 'bg-purple-100 text-purple-800 dark:bg-purple-900/30 dark:text-purple-300'
                    : 'bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-300'
              }`}>
                {txSettings.mode === 'external' ? 'External Service' : txSettings.effective_engine === 'mlx-whisper' ? 'MLX Whisper (Apple Silicon)' : 'WhisperX'}
              </span>
            </div>
            {txSaved && (
              <span className="text-xs text-green-600 dark:text-green-400 flex items-center gap-1">
                <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                </svg>
                Saved
              </span>
            )}
            {txSaving && (
              <span className="text-xs text-gray-500 dark:text-gray-400 flex items-center gap-1">
                <svg className="w-3.5 h-3.5 animate-spin" viewBox="0 0 24 24" fill="none">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                </svg>
                Saving...
              </span>
            )}
          </div>
          <div className="px-5 py-4 space-y-5">
            {/* Engine Selection */}
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Engine</label>
              <select
                value={txSettings.engine}
                onChange={(e) => updateTxSetting('engine', e.target.value)}
                disabled={txSaving}
                className="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 py-2 px-3 text-sm text-gray-900 dark:text-gray-100 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500 disabled:opacity-50"
              >
                <option value="auto">Auto (Recommended)</option>
                {txSettings.available_engines.includes('whisperx') && (
                  <option value="whisperx">WhisperX (CPU/CUDA)</option>
                )}
                {txSettings.available_engines.includes('mlx-whisper') && (
                  <option value="mlx-whisper">MLX Whisper (Apple Silicon GPU)</option>
                )}
              </select>
              <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
                {txSettings.engine === 'auto'
                  ? `Auto-detected: ${txSettings.effective_engine === 'mlx-whisper' ? 'MLX Whisper' : 'WhisperX'}`
                  : txSettings.effective_engine === 'mlx-whisper'
                    ? 'MLX Whisper uses Apple Silicon GPU for fast transcription'
                    : 'WhisperX uses CPU or NVIDIA GPU'}
              </p>
            </div>

            {/* ML Dependencies Installation */}
            {txSettings.available_engines.length === 0 && mlStatus && (
              <div className="p-4 rounded-lg bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800">
                <div className="flex items-start gap-3">
                  <svg className="w-5 h-5 text-amber-500 mt-0.5 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
                  </svg>
                  <div className="flex-1">
                    <h4 className="text-sm font-medium text-amber-800 dark:text-amber-200">
                      Local Transcription Not Available
                    </h4>
                    <p className="mt-1 text-sm text-amber-700 dark:text-amber-300">
                      {mlStatus.is_apple_silicon
                        ? 'Install MLX Whisper for fast local transcription using Apple Silicon GPU.'
                        : 'Install WhisperX for local transcription using CPU or NVIDIA GPU.'}
                    </p>
                    {mlInstallError && (
                      <p className="mt-2 text-sm text-red-600 dark:text-red-400">
                        Error: {mlInstallError}
                      </p>
                    )}
                    {mlInstallProgress && mlInstalling && (
                      <p className="mt-2 text-sm text-amber-600 dark:text-amber-400">
                        {mlInstallProgress}
                      </p>
                    )}
                    {!mlInstallProgress?.includes('complete') && (
                      <button
                        onClick={handleInstallMl}
                        disabled={mlInstalling}
                        className="mt-3 inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-amber-600 hover:bg-amber-700 text-white text-sm font-medium disabled:opacity-50 disabled:cursor-not-allowed"
                      >
                        {mlInstalling ? (
                          <>
                            <svg className="w-4 h-4 animate-spin" viewBox="0 0 24 24" fill="none">
                              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                            </svg>
                            Installing...
                          </>
                        ) : (
                          <>
                            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                              <path strokeLinecap="round" strokeLinejoin="round" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
                            </svg>
                            Install {mlStatus.is_apple_silicon ? 'MLX Whisper' : 'WhisperX'}
                          </>
                        )}
                      </button>
                    )}
                    {mlInstallProgress?.includes('complete') && (
                      <p className="mt-2 text-sm text-green-600 dark:text-green-400 font-medium">
                        ✓ {mlInstallProgress}
                      </p>
                    )}
                  </div>
                </div>
              </div>
            )}

            {/* Engine Caveats */}
            {txSettings.engine_caveats.length > 0 && (
              <div className="space-y-2">
                {txSettings.engine_caveats.map((caveat, idx) => (
                  <div
                    key={idx}
                    className={`p-3 rounded-lg text-sm ${
                      caveat.includes('not supported') || caveat.includes('requires')
                        ? 'bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800 text-amber-700 dark:text-amber-400'
                        : 'bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800 text-blue-700 dark:text-blue-400'
                    }`}
                  >
                    {caveat}
                  </div>
                ))}
              </div>
            )}

            {/* Presets */}
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">Presets</label>
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
                {([
                  { key: 'fast', label: 'Fast', desc: 'tiny / int8 / 32' },
                  { key: 'balanced', label: 'Balanced', desc: 'base / int8 / 16' },
                  { key: 'accurate', label: 'Accurate', desc: 'large-v3 / fp16 / 8' },
                  { key: 'cpu_only', label: 'CPU Only', desc: 'base / int8 / 8' },
                ] as const).map((preset) => {
                  const p = txSettings.presets[preset.key];
                  const isActive = p && txSettings.model === p.model && txSettings.compute_type === p.compute_type && txSettings.batch_size === p.batch_size;
                  return (
                    <button
                      key={preset.key}
                      onClick={() => applyPreset(preset.key)}
                      disabled={txSaving}
                      className={`p-3 rounded-lg border text-left transition-all ${
                        isActive
                          ? 'border-blue-500 bg-blue-50 dark:bg-blue-900/20 ring-1 ring-blue-500'
                          : 'border-gray-200 dark:border-gray-600 hover:border-blue-300 dark:hover:border-blue-700 hover:bg-gray-50 dark:hover:bg-gray-700/50'
                      } disabled:opacity-50`}
                    >
                      <div className={`text-sm font-medium ${isActive ? 'text-blue-700 dark:text-blue-300' : 'text-gray-900 dark:text-gray-100'}`}>
                        {preset.label}
                      </div>
                      <div className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">{preset.desc}</div>
                    </button>
                  );
                })}
              </div>
            </div>

            {/* Model, Device, Compute Type, Batch Size dropdowns */}
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Model</label>
                <select
                  value={txSettings.model}
                  onChange={(e) => updateTxSetting('model', e.target.value)}
                  disabled={txSaving}
                  className="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 py-2 px-3 text-sm text-gray-900 dark:text-gray-100 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500 disabled:opacity-50"
                >
                  {txSettings.available_models.map((m) => (
                    <option key={m} value={m}>{m}</option>
                  ))}
                </select>
                <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">Larger models are more accurate but slower</p>
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Device</label>
                <select
                  value={txSettings.device}
                  onChange={(e) => updateTxSetting('device', e.target.value)}
                  disabled={txSaving}
                  className="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 py-2 px-3 text-sm text-gray-900 dark:text-gray-100 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500 disabled:opacity-50"
                >
                  {txSettings.available_devices.map((d) => (
                    <option key={d} value={d}>{d === 'mps' ? 'mps (Apple Silicon)' : d === 'cuda' ? 'cuda (NVIDIA GPU)' : d}</option>
                  ))}
                </select>
                <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">Auto-detected from your hardware</p>
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Compute Type</label>
                <select
                  value={txSettings.compute_type}
                  onChange={(e) => updateTxSetting('compute_type', e.target.value)}
                  disabled={txSaving}
                  className="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 py-2 px-3 text-sm text-gray-900 dark:text-gray-100 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500 disabled:opacity-50"
                >
                  {txSettings.available_compute_types.map((ct) => (
                    <option key={ct} value={ct}>{ct}</option>
                  ))}
                </select>
                <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">int8 is fastest, float16 for GPU, float32 most precise</p>
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Batch Size</label>
                <select
                  value={txSettings.batch_size}
                  onChange={(e) => updateTxSetting('batch_size', parseInt(e.target.value, 10))}
                  disabled={txSaving}
                  className="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 py-2 px-3 text-sm text-gray-900 dark:text-gray-100 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500 disabled:opacity-50"
                >
                  {txSettings.available_batch_sizes.map((bs) => (
                    <option key={bs} value={bs}>{bs}</option>
                  ))}
                </select>
                <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">Higher = faster but uses more memory</p>
              </div>
            </div>

            {/* Speaker Diarization toggle */}
            <div className="flex items-center justify-between py-2">
              <div>
                <div className="text-sm font-medium text-gray-900 dark:text-gray-100">Speaker Diarization</div>
                <div className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">
                  {txSettings.hf_token_set
                    ? 'Identify and label different speakers in the transcript'
                    : 'Requires HuggingFace token (see below)'}
                </div>
              </div>
              <label
                className={`relative inline-flex items-center ${txSettings.hf_token_set ? 'cursor-pointer' : 'cursor-not-allowed'}`}
                title={!txSettings.hf_token_set ? 'Add HuggingFace token below to enable speaker diarization' : undefined}
              >
                <input
                  type="checkbox"
                  checked={txSettings.diarize}
                  onChange={(e) => updateTxSetting('diarize', e.target.checked)}
                  disabled={txSaving || !txSettings.hf_token_set}
                  className="sr-only peer"
                />
                <div className={`w-11 h-6 bg-gray-200 peer-focus:outline-none peer-focus:ring-2 peer-focus:ring-blue-500 rounded-full peer dark:bg-gray-700 peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all dark:border-gray-600 peer-checked:bg-blue-600 ${!txSettings.hf_token_set ? 'opacity-50' : ''}`} />
              </label>
            </div>

            {/* HuggingFace Token */}
            <div className="border-t border-gray-200 dark:border-gray-700 pt-4">
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">HuggingFace Token</label>
              <p className="text-xs text-gray-500 dark:text-gray-400 mb-2">
                Required for speaker diarization. Get a token from{' '}
                <a href="https://huggingface.co/settings/tokens" target="_blank" rel="noopener noreferrer" className="text-blue-600 dark:text-blue-400 hover:underline">
                  huggingface.co/settings/tokens
                </a>
                {' '}and accept the{' '}
                <a href="https://huggingface.co/pyannote/speaker-diarization-3.1" target="_blank" rel="noopener noreferrer" className="text-blue-600 dark:text-blue-400 hover:underline">
                  pyannote model license
                </a>
                .
              </p>
              {txSettings.hf_token_set && !hfTokenInput && (
                <div className="flex items-center gap-2 mb-2">
                  <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md bg-green-50 dark:bg-green-900/20 text-sm text-green-700 dark:text-green-400">
                    <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                      <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                    </svg>
                    Token configured ({txSettings.hf_token_masked})
                  </span>
                  <button
                    onClick={async () => {
                      setTxSaving(true);
                      try {
                        const updated = await api.config.updateTranscription({ hf_token: '' });
                        setTxSettings(updated);
                        api.diarization.listModels().then((r) => {
                          setDiarizationModels(r.models);
                          setDiarizationHfTokenSet(r.hf_token_set);
                        });
                      } finally {
                        setTxSaving(false);
                      }
                    }}
                    disabled={txSaving}
                    className="text-xs text-red-600 dark:text-red-400 hover:text-red-700 dark:hover:text-red-300 hover:underline disabled:opacity-50"
                  >
                    Remove
                  </button>
                </div>
              )}
              <div className="flex gap-2">
                <div className="relative flex-1">
                  <input
                    type={showHfToken ? 'text' : 'password'}
                    value={hfTokenInput}
                    onChange={(e) => setHfTokenInput(e.target.value)}
                    placeholder={txSettings.hf_token_set ? 'Enter new token to replace...' : 'hf_...'}
                    className="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 py-2 px-3 pr-10 text-sm text-gray-900 dark:text-gray-100 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500 font-mono"
                  />
                  <button
                    type="button"
                    onClick={() => setShowHfToken(!showHfToken)}
                    className="absolute inset-y-0 right-0 flex items-center pr-3 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300"
                  >
                    {showHfToken ? (
                      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                        <path strokeLinecap="round" strokeLinejoin="round" d="M13.875 18.825A10.05 10.05 0 0112 19c-4.478 0-8.268-2.943-9.543-7a9.97 9.97 0 011.563-3.029m5.858.908a3 3 0 114.243 4.243M9.878 9.878l4.242 4.242M9.88 9.88l-3.29-3.29m7.532 7.532l3.29 3.29M3 3l3.59 3.59m0 0A9.953 9.953 0 0112 5c4.478 0 8.268 2.943 9.543 7a10.025 10.025 0 01-4.132 5.411m0 0L21 21" />
                      </svg>
                    ) : (
                      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                        <path strokeLinecap="round" strokeLinejoin="round" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
                        <path strokeLinecap="round" strokeLinejoin="round" d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z" />
                      </svg>
                    )}
                  </button>
                </div>
                <button
                  onClick={saveHfToken}
                  disabled={!hfTokenInput.trim() || txSaving}
                  className="px-4 py-2 rounded-lg bg-primary text-primary-foreground text-sm font-medium hover:bg-primary/90 focus:outline-none focus:ring-2 focus:ring-primary disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                >
                  Save
                </button>
              </div>
            </div>

            {/* Footer note */}
            <p className="text-xs text-gray-500 dark:text-gray-400 border-t border-gray-200 dark:border-gray-700 pt-3">
              Changes take effect on the next transcription job. No restart required.
            </p>
          </div>
        </div>
      )}

      {/* Transcription Models Section */}
      <div className="mt-6 rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800">
        <div className="px-5 py-4 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <h2 className="text-base font-semibold text-gray-900 dark:text-gray-100">Transcription Models</h2>
            {whisperModels.some((m) => m.active) ? (
              <span className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-medium bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-300">
                <span className="w-1.5 h-1.5 rounded-full bg-green-500" />
                Ready ({whisperModels.find((m) => m.active)?.label})
              </span>
            ) : (
              <span className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-medium bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-400">
                <span className="w-1.5 h-1.5 rounded-full bg-gray-400" />
                No model ready
              </span>
            )}
          </div>
        </div>

        <div className="px-5 py-4 space-y-4">
          {whisperError && (
            <div className="p-3 rounded-lg bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 text-sm text-red-600 dark:text-red-400">
              {whisperError}
              <button onClick={() => setWhisperError(null)} className="ml-2 underline">Dismiss</button>
            </div>
          )}

          <div>
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">Available Models</label>
            <div className="space-y-3">
              {whisperModels.map((model) => (
                <div
                  key={model.id}
                  className={`p-4 rounded-lg border transition-all ${
                    model.active
                      ? 'border-green-300 dark:border-green-700 bg-green-50/50 dark:bg-green-900/10'
                      : 'border-gray-200 dark:border-gray-600'
                  }`}
                >
                  <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-3">
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="text-sm font-medium text-gray-900 dark:text-gray-100">{model.label}</span>
                        {model.bundled && (
                          <span className="px-1.5 py-0.5 text-xs rounded bg-blue-100 dark:bg-blue-900/30 text-blue-700 dark:text-blue-300">
                            Bundled
                          </span>
                        )}
                        {model.is_default && !model.bundled && (
                          <span className="px-1.5 py-0.5 text-xs rounded bg-blue-100 dark:bg-blue-900/30 text-blue-700 dark:text-blue-300">
                            Recommended
                          </span>
                        )}
                        {model.active && (
                          <span className="inline-flex items-center gap-1 px-1.5 py-0.5 text-xs rounded bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-300">
                            <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                              <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                            </svg>
                            Active
                          </span>
                        )}
                      </div>
                      <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">{model.description}</p>
                      <p className="mt-0.5 text-xs text-gray-400 dark:text-gray-500">{formatBytes(model.size_bytes)}</p>
                    </div>

                    <div className="flex items-center gap-2 flex-wrap sm:shrink-0">
                      {!model.downloaded && whisperDownloading !== model.id && (
                        <button
                          onClick={() => handleDownloadWhisperModel(model.id)}
                          className="px-3 py-1.5 text-xs font-medium rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 transition-colors"
                        >
                          Download
                        </button>
                      )}

                      {model.downloaded && !model.active && whisperDownloading !== model.id && (
                        <button
                          onClick={() => handleActivateWhisperModel(model.id)}
                          className="px-3 py-1.5 text-xs font-medium rounded-lg bg-success text-success-foreground hover:bg-success/90 transition-colors"
                        >
                          Activate
                        </button>
                      )}

                      {model.downloaded && !model.bundled && whisperDownloading !== model.id && (
                        <button
                          onClick={() => handleDeleteWhisperModel(model.id)}
                          className="px-3 py-1.5 text-xs font-medium rounded-lg border border-destructive/50 text-destructive hover:bg-destructive/10 transition-colors"
                        >
                          Delete
                        </button>
                      )}
                    </div>
                  </div>

                  {/* Download progress indicator */}
                  {whisperDownloading === model.id && (
                    <div className="mt-3">
                      <div className="flex items-center justify-between text-xs text-gray-500 dark:text-gray-400 mb-1">
                        <span className="flex items-center gap-2">
                          <svg className="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24">
                            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                          </svg>
                          {whisperDownloadMessage || 'Downloading...'}
                        </span>
                        <button
                          onClick={() => {
                            whisperDownloadAbortRef.current?.abort();
                            setWhisperDownloading(null);
                            setWhisperDownloadMessage(null);
                            setWhisperDownloadProgress(null);
                          }}
                          className="px-2 py-0.5 text-xs font-medium rounded bg-red-100 dark:bg-red-900/30 text-red-600 dark:text-red-400 hover:bg-red-200 dark:hover:bg-red-900/50 transition-colors"
                        >
                          Cancel
                        </button>
                      </div>
                      {/* Progress bar */}
                      {whisperDownloadProgress && (
                        <div className="mt-2">
                          <div className="w-full bg-gray-200 dark:bg-gray-700 rounded-full h-2">
                            <div
                              className="bg-blue-600 h-2 rounded-full transition-all duration-300"
                              style={{ width: `${whisperDownloadProgress.percent}%` }}
                            />
                          </div>
                          <div className="flex justify-between text-xs text-gray-500 dark:text-gray-400 mt-1">
                            <span>{(whisperDownloadProgress.downloaded / 1024 / 1024).toFixed(1)} MB</span>
                            <span>{(whisperDownloadProgress.total / 1024 / 1024).toFixed(1)} MB</span>
                          </div>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              ))}

              {whisperModels.length === 0 && (
                <p className="text-sm text-gray-500 dark:text-gray-400 py-2">
                  Loading model catalog...
                </p>
              )}
            </div>
          </div>

          <p className="text-xs text-gray-500 dark:text-gray-400 border-t border-gray-200 dark:border-gray-700 pt-3">
            Whisper Base is bundled with the app for offline use. Download larger models for improved accuracy on difficult audio.
          </p>
        </div>
      </div>

      {/* Diarization Models Section - only shown when HF token is set */}
      {diarizationHfTokenSet && (
        <div className="mt-6 rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800">
          <div className="px-5 py-4 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between">
            <div className="flex items-center gap-3">
              <h2 className="text-base font-semibold text-gray-900 dark:text-gray-100">Diarization Models</h2>
              {diarizationAllDownloaded ? (
                <span className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-medium bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-300">
                  <span className="w-1.5 h-1.5 rounded-full bg-green-500" />
                  Ready
                </span>
              ) : (
                <span className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-medium bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-300">
                  <span className="w-1.5 h-1.5 rounded-full bg-amber-500" />
                  Models needed
                </span>
              )}
            </div>
          </div>

          <div className="px-5 py-4 space-y-4">
            {diarizationError && (
              <div className="p-3 rounded-lg bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 text-sm text-red-600 dark:text-red-400">
                {diarizationError}
                <button onClick={() => setDiarizationError(null)} className="ml-2 underline">Dismiss</button>
              </div>
            )}

            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">Pyannote Models (Speaker Diarization)</label>
              <p className="text-xs text-gray-500 dark:text-gray-400 mb-3">
                These models are required for speaker identification (diarization). They are gated models that require you to accept the license on HuggingFace.
              </p>
              <div className="space-y-3">
                {diarizationModels.map((model) => (
                  <div
                    key={model.id}
                    className={`p-4 rounded-lg border transition-all ${
                      model.downloaded
                        ? 'border-green-300 dark:border-green-700 bg-green-50/50 dark:bg-green-900/10'
                        : 'border-gray-200 dark:border-gray-600'
                    }`}
                  >
                    <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-3">
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 flex-wrap">
                          <span className="text-sm font-medium text-gray-900 dark:text-gray-100">{model.label}</span>
                          {model.required && (
                            <span className="px-1.5 py-0.5 text-xs rounded bg-amber-100 dark:bg-amber-900/30 text-amber-700 dark:text-amber-300">
                              Required
                            </span>
                          )}
                          {model.downloaded && (
                            <span className="inline-flex items-center gap-1 px-1.5 py-0.5 text-xs rounded bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-300">
                              <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                                <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                              </svg>
                              Downloaded
                            </span>
                          )}
                        </div>
                        <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">{model.description}</p>
                        <p className="mt-0.5 text-xs text-gray-400 dark:text-gray-500">{formatBytes(model.size_bytes)}</p>
                      </div>

                      <div className="flex items-center gap-2 flex-wrap sm:shrink-0">
                        {!model.downloaded && diarizationDownloading !== model.id && (
                          <button
                            onClick={() => handleDownloadDiarizationModel(model.id)}
                            className="px-3 py-1.5 text-xs font-medium rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 transition-colors"
                          >
                            Download
                          </button>
                        )}

                        {model.downloaded && diarizationDownloading !== model.id && (
                          <button
                            onClick={() => handleDeleteDiarizationModel(model.id)}
                            className="px-3 py-1.5 text-xs font-medium rounded-lg border border-destructive/50 text-destructive hover:bg-destructive/10 transition-colors"
                          >
                            Delete
                          </button>
                        )}
                      </div>

                      {/* Download progress indicator */}
                      {diarizationDownloading === model.id && (
                        <div className="mt-3 w-full">
                          <div className="flex items-center justify-between text-xs text-gray-500 dark:text-gray-400 mb-1">
                            <span className="flex items-center gap-2">
                              <svg className="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24">
                                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                              </svg>
                              {diarizationDownloadMessage || 'Downloading...'}
                            </span>
                            <button
                              onClick={() => {
                                diarizationDownloadAbortRef.current?.abort();
                                setDiarizationDownloading(null);
                                setDiarizationDownloadMessage(null);
                              }}
                              className="px-2 py-0.5 text-xs font-medium rounded bg-red-100 dark:bg-red-900/30 text-red-600 dark:text-red-400 hover:bg-red-200 dark:hover:bg-red-900/50 transition-colors"
                            >
                              Cancel
                            </button>
                          </div>
                        </div>
                      )}
                    </div>
                  </div>
                ))}

                {diarizationModels.length === 0 && (
                  <p className="text-sm text-gray-500 dark:text-gray-400 py-2">
                    Loading model catalog...
                  </p>
                )}
              </div>
            </div>

            <div className="text-xs text-gray-500 dark:text-gray-400 border-t border-gray-200 dark:border-gray-700 pt-3 space-y-2">
              <p>
                Speaker diarization identifies who spoke when in your recordings.
              </p>
              <p>
                Before downloading, accept the license agreements for all three models on HuggingFace:
              </p>
              <ul className="list-disc list-inside pl-2 space-y-1">
                <li><a href="https://huggingface.co/pyannote/speaker-diarization-3.1" target="_blank" rel="noopener noreferrer" className="text-primary hover:underline">pyannote/speaker-diarization-3.1</a> (main pipeline)</li>
                <li><a href="https://huggingface.co/pyannote/segmentation-3.0" target="_blank" rel="noopener noreferrer" className="text-primary hover:underline">pyannote/segmentation-3.0</a> (voice detection)</li>
                <li><a href="https://huggingface.co/pyannote/wespeaker-voxceleb-resnet34-LM" target="_blank" rel="noopener noreferrer" className="text-primary hover:underline">pyannote/wespeaker-voxceleb-resnet34-LM</a> (speaker embedding)</li>
              </ul>
            </div>
          </div>
        </div>
      )}

      {/* External and/or Self-Hosted ASR Services Section (Enterprise) */}
      <div className="mt-6 rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 opacity-60 cursor-not-allowed">
        <div className="px-5 py-4 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <h2 className="text-base font-semibold text-gray-900 dark:text-gray-100">External and/or Self-Hosted ASR Services</h2>
            <EnterpriseBadge size="sm" />
          </div>
        </div>

        <div className="px-5 py-4 space-y-4 pointer-events-none">
          <p className="text-sm text-gray-600 dark:text-gray-400">
            Connect to external or self-hosted speech recognition services instead of running WhisperX locally.
          </p>

          {/* WhisperX API Card */}
          <div className="p-4 rounded-lg border border-gray-200 dark:border-gray-600">
            <div className="flex items-start gap-3">
              <input type="radio" name="asr-service" disabled className="mt-1" />
              <div className="flex-1 space-y-4">
                <div>
                  <span className="text-sm font-medium text-gray-900 dark:text-gray-100">WhisperX API</span>
                  <p className="mt-0.5 text-xs text-gray-500 dark:text-gray-400">
                    Connect to a self-hosted WhisperX server or compatible API endpoint.
                  </p>
                </div>

                <div>
                  <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">Service URL</label>
                  <input
                    type="text"
                    disabled
                    placeholder="https://whisperx.example.com/api"
                    className="w-full px-3 py-2 text-sm rounded-lg border border-gray-300 dark:border-gray-600 bg-gray-50 dark:bg-gray-700 text-gray-500 dark:text-gray-400"
                  />
                </div>

                <div>
                  <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">API Key</label>
                  <input
                    type="password"
                    disabled
                    placeholder="sk-••••••••••••••••"
                    className="w-full px-3 py-2 text-sm rounded-lg border border-gray-300 dark:border-gray-600 bg-gray-50 dark:bg-gray-700 text-gray-500 dark:text-gray-400"
                  />
                </div>

                <div className="flex justify-end">
                  <button
                    disabled
                    className="px-3 py-1.5 text-xs font-medium rounded-lg border border-gray-300 dark:border-gray-600 text-gray-400 dark:text-gray-500"
                  >
                    Test Connection
                  </button>
                </div>
              </div>
            </div>
          </div>

          {/* Faster Whisper Server Card */}
          <div className="p-4 rounded-lg border border-gray-200 dark:border-gray-600">
            <div className="flex items-start gap-3">
              <input type="radio" name="asr-service" disabled className="mt-1" />
              <div className="flex-1 space-y-4">
                <div>
                  <span className="text-sm font-medium text-gray-900 dark:text-gray-100">Faster Whisper Server</span>
                  <p className="mt-0.5 text-xs text-gray-500 dark:text-gray-400">
                    Connect to a Faster Whisper server for GPU-accelerated transcription.
                  </p>
                </div>

                <div>
                  <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">Server URL</label>
                  <input
                    type="text"
                    disabled
                    placeholder="http://localhost:52780"
                    className="w-full px-3 py-2 text-sm rounded-lg border border-gray-300 dark:border-gray-600 bg-gray-50 dark:bg-gray-700 text-gray-500 dark:text-gray-400"
                  />
                </div>

                <div className="flex justify-end">
                  <button
                    disabled
                    className="px-3 py-1.5 text-xs font-medium rounded-lg border border-gray-300 dark:border-gray-600 text-gray-400 dark:text-gray-500"
                  >
                    Test Connection
                  </button>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* ASR Service Providers Section (Enterprise) */}
      <div className="mt-6 rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 opacity-60 cursor-not-allowed">
        <div className="px-5 py-4 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <h2 className="text-base font-semibold text-gray-900 dark:text-gray-100">ASR Service Providers</h2>
            <EnterpriseBadge size="sm" />
          </div>
        </div>

        <div className="px-5 py-4 space-y-4 pointer-events-none">
          <p className="text-sm text-gray-600 dark:text-gray-400">
            Connect to cloud-based speech recognition APIs for transcription.
          </p>

          {/* OpenAI Whisper API Card */}
          <div className="p-4 rounded-lg border border-gray-200 dark:border-gray-600">
            <div className="flex items-start gap-3">
              <input type="radio" name="asr-provider" disabled className="mt-1" />
              <div className="flex-1 space-y-4">
                <div>
                  <span className="text-sm font-medium text-gray-900 dark:text-gray-100">OpenAI Whisper</span>
                  <p className="mt-0.5 text-xs text-gray-500 dark:text-gray-400">
                    Use OpenAI's Whisper API for fast, accurate transcription.
                  </p>
                </div>

                <div>
                  <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">API Key</label>
                  <input
                    type="password"
                    disabled
                    placeholder="sk-••••••••••••••••"
                    className="w-full px-3 py-2 text-sm rounded-lg border border-gray-300 dark:border-gray-600 bg-gray-50 dark:bg-gray-700 text-gray-500 dark:text-gray-400"
                  />
                </div>

                <div className="flex justify-end">
                  <button
                    disabled
                    className="px-3 py-1.5 text-xs font-medium rounded-lg border border-gray-300 dark:border-gray-600 text-gray-400 dark:text-gray-500"
                  >
                    Test Connection
                  </button>
                </div>
              </div>
            </div>
          </div>

          {/* Deepgram Card */}
          <div className="p-4 rounded-lg border border-gray-200 dark:border-gray-600">
            <div className="flex items-start gap-3">
              <input type="radio" name="asr-provider" disabled className="mt-1" />
              <div className="flex-1 space-y-4">
                <div>
                  <span className="text-sm font-medium text-gray-900 dark:text-gray-100">Deepgram</span>
                  <p className="mt-0.5 text-xs text-gray-500 dark:text-gray-400">
                    Enterprise-grade speech recognition with real-time streaming support.
                  </p>
                </div>

                <div>
                  <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">API Key</label>
                  <input
                    type="password"
                    disabled
                    placeholder="••••••••••••••••"
                    className="w-full px-3 py-2 text-sm rounded-lg border border-gray-300 dark:border-gray-600 bg-gray-50 dark:bg-gray-700 text-gray-500 dark:text-gray-400"
                  />
                </div>

                <div className="flex justify-end">
                  <button
                    disabled
                    className="px-3 py-1.5 text-xs font-medium rounded-lg border border-gray-300 dark:border-gray-600 text-gray-400 dark:text-gray-500"
                  >
                    Test Connection
                  </button>
                </div>
              </div>
            </div>
          </div>

          {/* AssemblyAI Card */}
          <div className="p-4 rounded-lg border border-gray-200 dark:border-gray-600">
            <div className="flex items-start gap-3">
              <input type="radio" name="asr-provider" disabled className="mt-1" />
              <div className="flex-1 space-y-4">
                <div>
                  <span className="text-sm font-medium text-gray-900 dark:text-gray-100">AssemblyAI</span>
                  <p className="mt-0.5 text-xs text-gray-500 dark:text-gray-400">
                    AI-powered transcription with speaker diarization and content moderation.
                  </p>
                </div>

                <div>
                  <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">API Key</label>
                  <input
                    type="password"
                    disabled
                    placeholder="••••••••••••••••"
                    className="w-full px-3 py-2 text-sm rounded-lg border border-gray-300 dark:border-gray-600 bg-gray-50 dark:bg-gray-700 text-gray-500 dark:text-gray-400"
                  />
                </div>

                <div className="flex justify-end">
                  <button
                    disabled
                    className="px-3 py-1.5 text-xs font-medium rounded-lg border border-gray-300 dark:border-gray-600 text-gray-400 dark:text-gray-500"
                  >
                    Test Connection
                  </button>
                </div>
              </div>
            </div>
          </div>

          {/* Rev.ai Card */}
          <div className="p-4 rounded-lg border border-gray-200 dark:border-gray-600">
            <div className="flex items-start gap-3">
              <input type="radio" name="asr-provider" disabled className="mt-1" />
              <div className="flex-1 space-y-4">
                <div>
                  <span className="text-sm font-medium text-gray-900 dark:text-gray-100">Rev.ai</span>
                  <p className="mt-0.5 text-xs text-gray-500 dark:text-gray-400">
                    High-accuracy speech recognition backed by human-quality transcription.
                  </p>
                </div>

                <div>
                  <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">Access Token</label>
                  <input
                    type="password"
                    disabled
                    placeholder="••••••••••••••••"
                    className="w-full px-3 py-2 text-sm rounded-lg border border-gray-300 dark:border-gray-600 bg-gray-50 dark:bg-gray-700 text-gray-500 dark:text-gray-400"
                  />
                </div>

                <div className="flex justify-end">
                  <button
                    disabled
                    className="px-3 py-1.5 text-xs font-medium rounded-lg border border-gray-300 dark:border-gray-600 text-gray-400 dark:text-gray-500"
                  >
                    Test Connection
                  </button>
                </div>
              </div>
            </div>
          </div>

          {/* Google Cloud Speech-to-Text Card */}
          <div className="p-4 rounded-lg border border-gray-200 dark:border-gray-600">
            <div className="flex items-start gap-3">
              <input type="radio" name="asr-provider" disabled className="mt-1" />
              <div className="flex-1 space-y-4">
                <div>
                  <span className="text-sm font-medium text-gray-900 dark:text-gray-100">Google Cloud Speech-to-Text</span>
                  <p className="mt-0.5 text-xs text-gray-500 dark:text-gray-400">
                    Google's speech recognition with 125+ language support.
                  </p>
                </div>

                <div>
                  <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">Service Account JSON</label>
                  <input
                    type="file"
                    disabled
                    className="w-full px-3 py-2 text-sm rounded-lg border border-gray-300 dark:border-gray-600 bg-gray-50 dark:bg-gray-700 text-gray-500 dark:text-gray-400"
                  />
                </div>

                <div className="flex justify-end">
                  <button
                    disabled
                    className="px-3 py-1.5 text-xs font-medium rounded-lg border border-gray-300 dark:border-gray-600 text-gray-400 dark:text-gray-500"
                  >
                    Test Connection
                  </button>
                </div>
              </div>
            </div>
          </div>

          {/* Amazon Transcribe Card */}
          <div className="p-4 rounded-lg border border-gray-200 dark:border-gray-600">
            <div className="flex items-start gap-3">
              <input type="radio" name="asr-provider" disabled className="mt-1" />
              <div className="flex-1 space-y-4">
                <div>
                  <span className="text-sm font-medium text-gray-900 dark:text-gray-100">Amazon Transcribe</span>
                  <p className="mt-0.5 text-xs text-gray-500 dark:text-gray-400">
                    AWS speech recognition with custom vocabulary and redaction features.
                  </p>
                </div>

                <div>
                  <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">Access Key ID</label>
                  <input
                    type="text"
                    disabled
                    placeholder="AKIA••••••••••••"
                    className="w-full px-3 py-2 text-sm rounded-lg border border-gray-300 dark:border-gray-600 bg-gray-50 dark:bg-gray-700 text-gray-500 dark:text-gray-400"
                  />
                </div>

                <div>
                  <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">Secret Access Key</label>
                  <input
                    type="password"
                    disabled
                    placeholder="••••••••••••••••"
                    className="w-full px-3 py-2 text-sm rounded-lg border border-gray-300 dark:border-gray-600 bg-gray-50 dark:bg-gray-700 text-gray-500 dark:text-gray-400"
                  />
                </div>

                <div>
                  <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">Region</label>
                  <select
                    disabled
                    className="w-full px-3 py-2 text-sm rounded-lg border border-gray-300 dark:border-gray-600 bg-gray-50 dark:bg-gray-700 text-gray-500 dark:text-gray-400"
                  >
                    <option>us-east-1</option>
                    <option>us-west-2</option>
                    <option>eu-west-1</option>
                  </select>
                </div>

                <div className="flex justify-end">
                  <button
                    disabled
                    className="px-3 py-1.5 text-xs font-medium rounded-lg border border-gray-300 dark:border-gray-600 text-gray-400 dark:text-gray-500"
                  >
                    Test Connection
                  </button>
                </div>
              </div>
            </div>
          </div>

          {/* Azure Speech Services Card */}
          <div className="p-4 rounded-lg border border-gray-200 dark:border-gray-600">
            <div className="flex items-start gap-3">
              <input type="radio" name="asr-provider" disabled className="mt-1" />
              <div className="flex-1 space-y-4">
                <div>
                  <span className="text-sm font-medium text-gray-900 dark:text-gray-100">Azure Speech Services</span>
                  <p className="mt-0.5 text-xs text-gray-500 dark:text-gray-400">
                    Microsoft's speech recognition with custom speech models and neural voices.
                  </p>
                </div>

                <div>
                  <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">Subscription Key</label>
                  <input
                    type="password"
                    disabled
                    placeholder="••••••••••••••••"
                    className="w-full px-3 py-2 text-sm rounded-lg border border-gray-300 dark:border-gray-600 bg-gray-50 dark:bg-gray-700 text-gray-500 dark:text-gray-400"
                  />
                </div>

                <div>
                  <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">Region</label>
                  <input
                    type="text"
                    disabled
                    placeholder="eastus"
                    className="w-full px-3 py-2 text-sm rounded-lg border border-gray-300 dark:border-gray-600 bg-gray-50 dark:bg-gray-700 text-gray-500 dark:text-gray-400"
                  />
                </div>

                <div className="flex justify-end">
                  <button
                    disabled
                    className="px-3 py-1.5 text-xs font-medium rounded-lg border border-gray-300 dark:border-gray-600 text-gray-400 dark:text-gray-500"
                  >
                    Test Connection
                  </button>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
        </>
      )}

      {/* ===== AI TAB ===== */}
      {activeTab === 'ai' && (
        <>
      {/* GPU Acceleration Section (Windows only) */}
      {gpuStatus?.platform === 'win32' && (
        <div className="mb-6 rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800">
          <div className="px-5 py-4 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between">
            <div className="flex items-center gap-3">
              <h2 className="text-base font-semibold text-gray-900 dark:text-gray-100">GPU Acceleration</h2>
              {gpuStatus.torch_cuda_available ? (
                <span className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-medium bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-300">
                  <span className="w-1.5 h-1.5 rounded-full bg-green-500" />
                  Full GPU
                </span>
              ) : gpuStatus.cuda_available ? (
                <span className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-medium bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-300">
                  <span className="w-1.5 h-1.5 rounded-full bg-amber-500" />
                  Partial
                </span>
              ) : (
                <span className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-medium bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-400">
                  <span className="w-1.5 h-1.5 rounded-full bg-gray-400" />
                  CPU Only
                </span>
              )}
            </div>
          </div>

          <div className="px-5 py-4 space-y-4">
            {/* GPU name */}
            {gpuStatus.gpu_name && (
              <p className="text-sm text-gray-500 dark:text-gray-400">
                {gpuStatus.gpu_name}
              </p>
            )}

            {/* Feature status table */}
            <div className="space-y-1.5">
              {gpuStatus.features.map((f) => (
                <div key={f.feature} className="flex items-center justify-between text-sm">
                  <span className="text-gray-700 dark:text-gray-300">{f.feature}</span>
                  <span className={f.gpu_accelerated
                    ? 'text-green-600 dark:text-green-400 font-medium'
                    : 'text-gray-400 dark:text-gray-500'
                  }>
                    {f.gpu_accelerated ? `GPU (${f.device.toUpperCase()})` : 'CPU'}
                  </span>
                </div>
              ))}
            </div>

            {/* Upgrade button or status */}
            {gpuStatus.upgrade_available && !gpuStatus.torch_cuda_available && (
              <>
                {gpuInstalling ? (
                  <div className="space-y-2">
                    <div className="w-full bg-gray-200 dark:bg-gray-700 rounded-full h-2">
                      <div className="bg-blue-500 h-2 rounded-full animate-pulse w-full" />
                    </div>
                    <p className="text-sm text-gray-600 dark:text-gray-300">
                      {gpuInstallMessage || 'Installing...'}
                    </p>
                  </div>
                ) : (
                  <div>
                    <button
                      onClick={handleEnableGpu}
                      disabled={gpuInstalling}
                      className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 text-sm font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                    >
                      Enable Full GPU Acceleration
                      {gpuStatus.estimated_download_bytes > 0 && (
                        <span className="ml-2 text-blue-200 text-xs">
                          (~{formatBytes(gpuStatus.estimated_download_bytes)} download)
                        </span>
                      )}
                    </button>
                    <p className="mt-2 text-xs text-gray-500 dark:text-gray-400">
                      Downloads CUDA PyTorch to accelerate all AI features on your GPU. Requires app restart.
                    </p>
                  </div>
                )}
              </>
            )}

            {gpuStatus.torch_cuda_available ? (
              <p className="text-sm text-green-600 dark:text-green-400 font-medium">
                Full GPU acceleration is active — all AI features are using your GPU
              </p>
            ) : !gpuStatus.nvidia_gpu_detected ? (
              <p className="text-sm text-gray-500 dark:text-gray-400">
                No NVIDIA GPU detected. All features running on CPU.
              </p>
            ) : null}

            {gpuError && (
              <div className="p-3 rounded-lg bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 text-sm text-red-600 dark:text-red-400">
                {gpuError}
                <button onClick={() => setGpuError('')} className="ml-2 underline">Dismiss</button>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Large Download Confirmation Dialog */}
      {confirmDownload && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={() => setConfirmDownload(null)}>
          <div className="bg-white dark:bg-gray-800 rounded-xl shadow-xl p-6 max-w-md mx-4" onClick={(e) => e.stopPropagation()}>
            <h3 className="text-lg font-semibold text-gray-900 dark:text-gray-100 mb-2">Large Download</h3>
            <p className="text-sm text-gray-600 dark:text-gray-400 mb-4">
              <strong>{confirmDownload.modelLabel}</strong> is a large model:
            </p>
            <div className="space-y-2 mb-4">
              <div className="flex justify-between text-sm">
                <span className="text-gray-500 dark:text-gray-400">Download size:</span>
                <span className="font-medium text-gray-900 dark:text-gray-100">{formatBytes(confirmDownload.sizeBytes)}</span>
              </div>
              <div className="flex justify-between text-sm">
                <span className="text-gray-500 dark:text-gray-400">RAM/VRAM required:</span>
                <span className="font-medium text-gray-900 dark:text-gray-100">~{confirmDownload.ramGb} GB</span>
              </div>
              {hardwareInfo && (
                <div className="flex justify-between text-sm">
                  <span className="text-gray-500 dark:text-gray-400">Your system:</span>
                  <span className="font-medium text-gray-900 dark:text-gray-100">
                    {hardwareInfo.gpu_vram_gb
                      ? `${hardwareInfo.gpu_vram_gb} GB VRAM + ${hardwareInfo.total_ram_gb} GB RAM`
                      : `${hardwareInfo.total_ram_gb} GB RAM`}
                  </span>
                </div>
              )}
            </div>
            {hardwareInfo && confirmDownload.ramGb > (hardwareInfo.gpu_vram_gb || hardwareInfo.total_ram_gb) && (
              <div className="p-3 rounded-lg bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 text-xs text-red-600 dark:text-red-400 mb-4">
                This model may exceed your available memory. Performance could be severely degraded.
              </div>
            )}
            <div className="flex justify-end gap-3">
              <button
                onClick={() => setConfirmDownload(null)}
                className="px-4 py-2 text-sm font-medium rounded-lg border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={() => {
                  const { modelId, type } = confirmDownload;
                  setConfirmDownload(null);
                  if (type === 'ai') {
                    handleDownloadModel(modelId);
                  } else {
                    handleDownloadOcrModel(modelId);
                  }
                }}
                className="px-4 py-2 text-sm font-medium rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 transition-colors"
              >
                Download Anyway
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Vision Models (OCR) Section */}
      <div className="rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800">
        <div className="px-5 py-4 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <h2 className="text-base font-semibold text-gray-900 dark:text-gray-100">Vision Models (OCR)</h2>
            {ocrModels.some((m) => m.active) ? (
              <span className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-medium bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-300">
                <span className="w-1.5 h-1.5 rounded-full bg-green-500" />
                Active ({ocrModels.find((m) => m.active)?.label})
              </span>
            ) : ocrModels.some((m) => m.downloaded) ? (
              <span className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-medium bg-yellow-100 text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-300">
                <span className="w-1.5 h-1.5 rounded-full bg-yellow-500" />
                Downloaded (not active)
              </span>
            ) : (
              <span className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-medium bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-400">
                <span className="w-1.5 h-1.5 rounded-full bg-gray-400" />
                Not installed
              </span>
            )}
          </div>
        </div>

        <div className="px-5 py-4 space-y-4">
          {ocrError && (
            <div className="p-3 rounded-lg bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 text-sm text-red-600 dark:text-red-400">
              {ocrError}
              <button onClick={() => setOcrError(null)} className="ml-2 underline">Dismiss</button>
            </div>
          )}

          <div>
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">Available Models</label>
            <div className="space-y-3">
              {[...ocrModels].sort((a, b) => {
                const tierOrder: Record<string, number> = { legacy: 0, standard: 1, pro: 2, max: 3 };
                return (tierOrder[a.tier || 'standard'] ?? 9) - (tierOrder[b.tier || 'standard'] ?? 9);
              }).map((model) => {
                const isDownloading = ocrDownloading === model.id || model.downloading;
                const isInstalled = model.downloaded && !model.downloading;
                const tierColors: Record<string, string> = {
                  legacy: 'bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-400',
                  standard: 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300',
                  pro: 'bg-purple-100 text-purple-700 dark:bg-purple-900/30 dark:text-purple-300',
                  max: 'bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300',
                };
                const tierLabel = model.tier ? model.tier.charAt(0).toUpperCase() + model.tier.slice(1) : null;
                const availableMemoryGb = hardwareInfo
                  ? (hardwareInfo.platform === 'windows' && hardwareInfo.cuda_available && hardwareInfo.gpu_vram_gb
                      ? hardwareInfo.gpu_vram_gb
                      : hardwareInfo.total_ram_gb)
                  : null;
                const memoryRatio = model.ram_gb && availableMemoryGb ? model.ram_gb / availableMemoryGb : null;
                const showMemoryWarning = memoryRatio !== null && memoryRatio > 0.6 && model.tier !== 'legacy' && model.tier !== 'standard';

                return (
                <div
                  key={model.id}
                  className={`p-4 rounded-lg border transition-all ${
                    model.active
                      ? 'border-green-300 dark:border-green-700 bg-green-50/50 dark:bg-green-900/10'
                      : isDownloading
                      ? 'border-blue-300 dark:border-blue-700 bg-blue-50/50 dark:bg-blue-900/10'
                      : 'border-gray-200 dark:border-gray-600'
                  }`}
                >
                  <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-3">
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 flex-wrap">
                        {tierLabel && (
                          <span className={`px-1.5 py-0.5 text-xs rounded font-medium ${tierColors[model.tier || ''] || tierColors.standard}`}>
                            {tierLabel}
                          </span>
                        )}
                        <span className="text-sm font-medium text-gray-900 dark:text-gray-100">{model.label}</span>
                        {model.is_default && (
                          <span className="px-1.5 py-0.5 text-xs rounded bg-blue-100 dark:bg-blue-900/30 text-blue-700 dark:text-blue-300">
                            Recommended
                          </span>
                        )}
                        {model.active && (
                          <span className="inline-flex items-center gap-1 px-1.5 py-0.5 text-xs rounded bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-300">
                            <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                              <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                            </svg>
                            Active
                          </span>
                        )}
                        {isDownloading && (
                          <span className="inline-flex items-center gap-1 px-1.5 py-0.5 text-xs rounded bg-blue-100 dark:bg-blue-900/30 text-blue-700 dark:text-blue-300">
                            <svg className="w-3 h-3 animate-spin" fill="none" viewBox="0 0 24 24">
                              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                            </svg>
                            Downloading
                          </span>
                        )}
                        {!isDownloading && isInstalled && !model.active && (
                          <span className="px-1.5 py-0.5 text-xs rounded bg-gray-100 dark:bg-gray-700 text-gray-500 dark:text-gray-400">
                            Installed
                          </span>
                        )}
                        {showMemoryWarning && memoryRatio !== null && (
                          memoryRatio > 1 ? (
                            <span className="px-1.5 py-0.5 text-xs rounded bg-red-100 dark:bg-red-900/30 text-red-600 dark:text-red-400" title={`Requires ~${model.ram_gb} GB but your system has ${availableMemoryGb?.toFixed(0)} GB available`}>
                              Exceeds memory
                            </span>
                          ) : (
                            <span className="px-1.5 py-0.5 text-xs rounded bg-yellow-100 dark:bg-yellow-900/30 text-yellow-700 dark:text-yellow-400" title={`Requires ~${model.ram_gb} GB (${Math.round(memoryRatio * 100)}% of available ${availableMemoryGb?.toFixed(0)} GB)`}>
                              High memory
                            </span>
                          )
                        )}
                        {model.requires_hf_token && (
                          <span className="px-1.5 py-0.5 text-xs rounded bg-orange-100 dark:bg-orange-900/30 text-orange-600 dark:text-orange-400">
                            Requires HF Token
                          </span>
                        )}
                      </div>
                      <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
                        {model.description}
                        {model.requires_hf_token && model.license_url && (
                          <>
                            {' '}
                            <a href={model.license_url} target="_blank" rel="noopener noreferrer" className="text-blue-600 dark:text-blue-400 hover:underline">
                              Accept license on HuggingFace
                            </a>
                          </>
                        )}
                      </p>
                      {model.legacy_note && (
                        <p className="mt-0.5 text-xs text-amber-600 dark:text-amber-400 italic">{model.legacy_note}</p>
                      )}
                      <p className="mt-0.5 text-xs text-gray-400 dark:text-gray-500">
                        {isInstalled && model.size_on_disk
                          ? `${formatBytes(model.size_on_disk)} on disk`
                          : isDownloading && model.size_on_disk
                          ? `${formatBytes(model.size_on_disk)} / ${formatBytes(model.size_bytes)}`
                          : `~${formatBytes(model.size_bytes)} download`}
                        {model.ram_gb ? ` | ~${model.ram_gb} GB RAM` : ''}
                      </p>
                    </div>

                    <div className="flex items-center gap-2 flex-wrap sm:shrink-0">
                      {!isInstalled && !isDownloading && (
                        <button
                          onClick={() => {
                            if (model.size_bytes > 20_000_000_000) {
                              setConfirmDownload({ modelId: model.id, modelLabel: model.label, sizeBytes: model.size_bytes, ramGb: model.ram_gb || 0, type: 'ocr' });
                            } else {
                              handleDownloadOcrModel(model.id, model.label);
                            }
                          }}
                          className="px-3 py-1.5 text-xs font-medium rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 transition-colors"
                        >
                          Download
                        </button>
                      )}

                      {isInstalled && !model.active && (
                        <button
                          onClick={() => handleActivateOcrModel(model.id)}
                          className="px-3 py-1.5 text-xs font-medium rounded-lg bg-success text-success-foreground hover:bg-success/90 transition-colors"
                        >
                          Activate
                        </button>
                      )}

                      {isInstalled && model.active && (
                        <button
                          onClick={() => handleDeactivateOcrModel(model.id)}
                          className="px-3 py-1.5 text-xs font-medium rounded-lg border border-amber-500/50 text-amber-600 dark:text-amber-400 hover:bg-amber-500/10 transition-colors"
                        >
                          Deactivate
                        </button>
                      )}

                      {isInstalled && (
                        <button
                          onClick={() => handleDeleteOcrModel(model.id)}
                          className="px-3 py-1.5 text-xs font-medium rounded-lg border border-destructive/50 text-destructive hover:bg-destructive/10 transition-colors"
                        >
                          Delete
                        </button>
                      )}
                    </div>
                  </div>

                  {/* Download progress */}
                  {isDownloading && (
                    <div className="mt-3">
                      {ocrDownloadProgress && ocrDownloading === model.id ? (
                        <>
                          <div className="w-full h-2 bg-gray-200 dark:bg-gray-700 rounded-full overflow-hidden">
                            <div
                              className="h-full bg-blue-500 dark:bg-blue-400 transition-all duration-300"
                              style={{ width: `${ocrDownloadProgress.percent}%` }}
                            />
                          </div>
                          <div className="flex items-center justify-between mt-1.5 text-xs text-gray-500 dark:text-gray-400">
                            <span>{formatBytes(ocrDownloadProgress.downloaded)} / {formatBytes(ocrDownloadProgress.total)}</span>
                            <div className="flex items-center gap-2">
                              <span>{ocrDownloadProgress.percent}%</span>
                              <button
                                onClick={async () => {
                                  ocrDownloadAbortRef.current?.abort();
                                  setOcrDownloading(null);
                                  setOcrDownloadProgress(null);
                                  setOcrDownloadMessage(null);
                                  await api.ocr.cancelDownload(model.id).catch(() => {});
                                  api.ocr.listModels().then((r) => setOcrModels(r.models)).catch(console.error);
                                }}
                                className="px-2 py-0.5 text-xs font-medium rounded bg-red-100 dark:bg-red-900/30 text-red-600 dark:text-red-400 hover:bg-red-200 dark:hover:bg-red-900/50 transition-colors"
                              >
                                Cancel
                              </button>
                            </div>
                          </div>
                        </>
                      ) : (
                        <div className="flex items-center justify-between text-xs text-gray-500 dark:text-gray-400">
                          <div className="flex items-center gap-2">
                            <svg className="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24">
                              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                            </svg>
                            <span>{ocrDownloadMessage || 'Stale download - click Cancel to reset'}</span>
                          </div>
                          <button
                            onClick={async () => {
                              ocrDownloadAbortRef.current?.abort();
                              setOcrDownloading(null);
                              setOcrDownloadProgress(null);
                              setOcrDownloadMessage(null);
                              await api.ocr.cancelDownload(model.id).catch(() => {});
                              api.ocr.listModels().then((r) => setOcrModels(r.models)).catch(console.error);
                            }}
                            className="px-2 py-0.5 text-xs font-medium rounded bg-red-100 dark:bg-red-900/30 text-red-600 dark:text-red-400 hover:bg-red-200 dark:hover:bg-red-900/50 transition-colors"
                          >
                            Cancel
                          </button>
                        </div>
                      )}
                      <p className="mt-1 text-xs text-gray-400 dark:text-gray-500">
                        This may take several minutes depending on your connection speed.
                      </p>
                    </div>
                  )}
                </div>
              );
              })}

              {ocrModels.length === 0 && (
                <p className="text-sm text-gray-500 dark:text-gray-400 py-2">
                  Loading models...
                </p>
              )}
            </div>
          </div>

          <p className="text-xs text-gray-500 dark:text-gray-400 border-t border-gray-200 dark:border-gray-700 pt-3">
            OCR enables high-quality text extraction from scanned PDFs and images. Models run locally on your machine. Only one vision model can be active at a time.
          </p>
        </div>
      </div>

      {/* Language Models (Summarization + Chat) Section */}
      <div className="mt-6 rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800">
        <div className="px-5 py-4 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <h2 className="text-base font-semibold text-gray-900 dark:text-gray-100">Language Models (Summarization + Chat)</h2>
            {aiModels.some((m) => m.active) ? (
              <span className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-medium bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-300">
                <span className="w-1.5 h-1.5 rounded-full bg-green-500" />
                Active ({aiModels.find((m) => m.active)?.label})
              </span>
            ) : (
              <span className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-medium bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-400">
                <span className="w-1.5 h-1.5 rounded-full bg-gray-400" />
                No model active
              </span>
            )}
          </div>
        </div>

        <div className="px-5 py-4 space-y-4">
          {aiError && (
            <div className="p-3 rounded-lg bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 text-sm text-red-600 dark:text-red-400">
              {aiError}
              <button onClick={() => setAiError(null)} className="ml-2 underline">Dismiss</button>
            </div>
          )}

          <div>
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">Available Models</label>
            <div className="space-y-3">
              {[...aiModels].sort((a, b) => {
                const tierOrder: Record<string, number> = { legacy: 0, standard: 1, pro: 2, max: 3 };
                return (tierOrder[a.tier || 'standard'] ?? 9) - (tierOrder[b.tier || 'standard'] ?? 9);
              }).map((model) => {
                const tierColors: Record<string, string> = {
                  legacy: 'bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-400',
                  standard: 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300',
                  pro: 'bg-purple-100 text-purple-700 dark:bg-purple-900/30 dark:text-purple-300',
                  max: 'bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300',
                };
                const tierLabel = model.tier ? model.tier.charAt(0).toUpperCase() + model.tier.slice(1) : null;
                const availableMemoryGb = hardwareInfo
                  ? (hardwareInfo.platform === 'windows' && hardwareInfo.cuda_available && hardwareInfo.gpu_vram_gb
                      ? hardwareInfo.gpu_vram_gb
                      : hardwareInfo.total_ram_gb)
                  : null;
                const memoryRatio = model.ram_gb && availableMemoryGb ? model.ram_gb / availableMemoryGb : null;
                const showMemoryWarning = memoryRatio !== null && memoryRatio > 0.6 && model.tier !== 'legacy' && model.tier !== 'standard';

                return (
                <div
                  key={model.id}
                  className={`p-4 rounded-lg border transition-all ${
                    model.active
                      ? 'border-green-300 dark:border-green-700 bg-green-50/50 dark:bg-green-900/10'
                      : 'border-gray-200 dark:border-gray-600'
                  }`}
                >
                  <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-3">
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 flex-wrap">
                        {tierLabel && (
                          <span className={`px-1.5 py-0.5 text-xs rounded font-medium ${tierColors[model.tier || ''] || tierColors.standard}`}>
                            {tierLabel}
                          </span>
                        )}
                        <span className="text-sm font-medium text-gray-900 dark:text-gray-100">{model.label}</span>
                        {model.is_default && (
                          <span className="px-1.5 py-0.5 text-xs rounded bg-blue-100 dark:bg-blue-900/30 text-blue-700 dark:text-blue-300">
                            Recommended
                          </span>
                        )}
                        {model.active && (
                          <span className="inline-flex items-center gap-1 px-1.5 py-0.5 text-xs rounded bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-300">
                            <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                              <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                            </svg>
                            Active
                          </span>
                        )}
                        {model.downloaded && !model.active && (
                          <span className="px-1.5 py-0.5 text-xs rounded bg-gray-100 dark:bg-gray-700 text-gray-500 dark:text-gray-400">
                            Downloaded
                          </span>
                        )}
                        {showMemoryWarning && memoryRatio !== null && (
                          memoryRatio > 1 ? (
                            <span className="px-1.5 py-0.5 text-xs rounded bg-red-100 dark:bg-red-900/30 text-red-600 dark:text-red-400" title={`Requires ~${model.ram_gb} GB but your system has ${availableMemoryGb?.toFixed(0)} GB available`}>
                              Exceeds memory
                            </span>
                          ) : (
                            <span className="px-1.5 py-0.5 text-xs rounded bg-yellow-100 dark:bg-yellow-900/30 text-yellow-700 dark:text-yellow-400" title={`Requires ~${model.ram_gb} GB (${Math.round(memoryRatio * 100)}% of available ${availableMemoryGb?.toFixed(0)} GB)`}>
                              High memory
                            </span>
                          )
                        )}
                      </div>
                      <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">{model.description}</p>
                      {model.legacy_note && (
                        <p className="mt-0.5 text-xs text-amber-600 dark:text-amber-400 italic">{model.legacy_note}</p>
                      )}
                      <p className="mt-0.5 text-xs text-gray-400 dark:text-gray-500">
                        ~{formatBytes(model.size_bytes)} download
                        {model.ram_gb ? ` | ~${model.ram_gb} GB RAM` : ''}
                      </p>
                    </div>

                    <div className="flex items-center gap-2 flex-wrap sm:shrink-0">
                      {!model.downloaded && aiDownloading !== model.id && (
                        <button
                          onClick={() => {
                            if (model.size_bytes > 20_000_000_000) {
                              setConfirmDownload({ modelId: model.id, modelLabel: model.label, sizeBytes: model.size_bytes, ramGb: model.ram_gb || 0, type: 'ai' });
                            } else {
                              handleDownloadModel(model.id, model.label);
                            }
                          }}
                          className="px-3 py-1.5 text-xs font-medium rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 transition-colors"
                        >
                          Download
                        </button>
                      )}

                      {model.downloaded && !model.active && aiDownloading !== model.id && (
                        <button
                          onClick={() => handleActivateModel(model.id)}
                          className="px-3 py-1.5 text-xs font-medium rounded-lg bg-success text-success-foreground hover:bg-success/90 transition-colors"
                        >
                          Activate
                        </button>
                      )}

                      {model.downloaded && model.active && aiDownloading !== model.id && (
                        <button
                          onClick={() => handleDeactivateModel(model.id)}
                          className="px-3 py-1.5 text-xs font-medium rounded-lg border border-amber-500/50 text-amber-600 dark:text-amber-400 hover:bg-amber-500/10 transition-colors"
                        >
                          Deactivate
                        </button>
                      )}

                      {model.downloaded && aiDownloading !== model.id && (
                        <button
                          onClick={() => handleDeleteModel(model.id)}
                          className="px-3 py-1.5 text-xs font-medium rounded-lg border border-destructive/50 text-destructive hover:bg-destructive/10 transition-colors"
                        >
                          Delete
                        </button>
                      )}
                    </div>
                  </div>

                  {/* Download progress bar */}
                  {aiDownloading === model.id && (
                    <div className="mt-3">
                      <div className="flex items-center justify-between text-xs text-gray-500 dark:text-gray-400 mb-1">
                        <span>Downloading...</span>
                        <div className="flex items-center gap-3">
                          <span>
                            {aiTotalBytes > 0
                              ? `${formatBytes(aiDownloadedBytes)} / ${formatBytes(aiTotalBytes)}`
                              : 'Starting...'}
                          </span>
                          <button
                            onClick={() => {
                              downloadAbortRef.current?.abort();
                              setAiDownloading(null);
                              setAiDownloadedBytes(0);
                              setAiTotalBytes(0);
                            }}
                            className="px-2 py-0.5 text-xs font-medium rounded bg-red-100 dark:bg-red-900/30 text-red-600 dark:text-red-400 hover:bg-red-200 dark:hover:bg-red-900/50 transition-colors"
                          >
                            Cancel
                          </button>
                        </div>
                      </div>
                      <div className="w-full h-2 bg-gray-200 dark:bg-gray-700 rounded-full overflow-hidden">
                        <div
                          className="h-full bg-blue-600 rounded-full transition-all duration-300"
                          style={{ width: aiTotalBytes > 0 ? `${Math.min(100, (aiDownloadedBytes / aiTotalBytes) * 100)}%` : '0%' }}
                        />
                      </div>
                      {aiTotalBytes > 0 && (
                        <div className="text-right text-xs text-gray-400 dark:text-gray-500 mt-0.5">
                          {Math.round((aiDownloadedBytes / aiTotalBytes) * 100)}%
                        </div>
                      )}
                    </div>
                  )}
                </div>
              );
              })}

              {aiModels.length === 0 && (
                <p className="text-sm text-gray-500 dark:text-gray-400 py-2">
                  Loading model catalog...
                </p>
              )}
            </div>
          </div>

          {/* Context Window Size */}
          {aiSettings && (
            <div className="border-t border-gray-200 dark:border-gray-700 pt-4">
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                Context Window Size
              </label>
              <p className="text-xs text-gray-500 dark:text-gray-400 mb-3">
                Larger context windows let the AI process longer transcripts in a single pass,
                but require more RAM. Transcripts that exceed the context window are automatically
                processed in chunks.
              </p>

              <div className="flex items-center gap-4">
                <input
                  type="range"
                  min={0}
                  max={aiSettings.available_context_sizes.length - 1}
                  value={aiSettings.available_context_sizes.indexOf(
                    pendingContextSize ?? aiSettings.context_size
                  )}
                  onChange={(e) => {
                    const idx = parseInt(e.target.value, 10);
                    const size = aiSettings.available_context_sizes[idx];
                    setPendingContextSize(size !== aiSettings.context_size ? size : null);
                  }}
                  className="flex-1 accent-blue-600"
                  step={1}
                />
                <span className="text-sm font-mono text-gray-700 dark:text-gray-300 w-20 text-right">
                  {((pendingContextSize ?? aiSettings.context_size) / 1024).toFixed(0)}K tokens
                </span>
              </div>

              <div className="flex justify-between text-xs text-gray-400 dark:text-gray-500 mt-1 px-0.5">
                {aiSettings.available_context_sizes.map((size) => (
                  <span key={size}>{(size / 1024).toFixed(0)}K</span>
                ))}
              </div>

              {/* RAM estimate */}
              <p className="text-xs text-gray-500 dark:text-gray-400 mt-2">
                Estimated RAM:{' '}
                <span className="font-medium">
                  {aiSettings.ram_estimates[pendingContextSize ?? aiSettings.context_size] ?? 'Unknown'}
                </span>
              </p>

              {/* Warning for large context sizes */}
              {(pendingContextSize ?? aiSettings.context_size) > 32768 && (
                <p className="text-xs text-amber-600 dark:text-amber-400 mt-1">
                  Context sizes above 32K require significant RAM and may cause slowdowns on systems with less than 16 GB.
                </p>
              )}

              {/* Apply button */}
              {pendingContextSize !== null && (
                <button
                  onClick={async () => {
                    setSavingAiSettings(true);
                    try {
                      const updated = await api.config.updateAI({ context_size: pendingContextSize });
                      setAiSettings(updated);
                      setPendingContextSize(null);
                    } catch (err) {
                      console.error('Failed to update AI settings:', err);
                    } finally {
                      setSavingAiSettings(false);
                    }
                  }}
                  disabled={savingAiSettings}
                  className="mt-3 px-4 py-1.5 text-sm font-medium rounded-md bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  {savingAiSettings ? 'Applying...' : 'Apply'}
                </button>
              )}
            </div>
          )}

          <p className="text-xs text-gray-500 dark:text-gray-400 border-t border-gray-200 dark:border-gray-700 pt-3">
            Models are downloaded from HuggingFace and stored locally. All AI processing happens on your machine. Only one language model can be active at a time.
          </p>
        </div>
      </div>

      {/* External and/or Self-Hosted LLM Services Section (Enterprise) */}
      <div className="mt-6 rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 opacity-60 cursor-not-allowed">
        <div className="px-5 py-4 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <h2 className="text-base font-semibold text-gray-900 dark:text-gray-100">External and/or Self-Hosted LLM Services</h2>
            <EnterpriseBadge size="sm" />
          </div>
        </div>

        <div className="px-5 py-4 space-y-4 pointer-events-none">
          <p className="text-sm text-gray-600 dark:text-gray-400">
            Connect to self-hosted or local LLM services instead of running models directly.
          </p>

          {/* Ollama Card */}
          <div className="p-4 rounded-lg border border-gray-200 dark:border-gray-600">
            <div className="flex items-start gap-3">
              <input type="radio" name="llm-service" disabled className="mt-1" />
              <div className="flex-1 space-y-4">
                <div>
                  <span className="text-sm font-medium text-gray-900 dark:text-gray-100">Ollama</span>
                  <p className="mt-0.5 text-xs text-gray-500 dark:text-gray-400">
                    Run LLMs locally with Ollama's simple API interface.
                  </p>
                </div>

                <div>
                  <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">Server URL</label>
                  <input
                    type="text"
                    disabled
                    placeholder="http://localhost:11434"
                    className="w-full px-3 py-2 text-sm rounded-lg border border-gray-300 dark:border-gray-600 bg-gray-50 dark:bg-gray-700 text-gray-500 dark:text-gray-400"
                  />
                </div>

                <div>
                  <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">Model</label>
                  <input
                    type="text"
                    disabled
                    placeholder="llama3.2"
                    className="w-full px-3 py-2 text-sm rounded-lg border border-gray-300 dark:border-gray-600 bg-gray-50 dark:bg-gray-700 text-gray-500 dark:text-gray-400"
                  />
                </div>

                <div className="flex justify-end">
                  <button
                    disabled
                    className="px-3 py-1.5 text-xs font-medium rounded-lg border border-gray-300 dark:border-gray-600 text-gray-400 dark:text-gray-500"
                  >
                    Test Connection
                  </button>
                </div>
              </div>
            </div>
          </div>

          {/* LM Studio Card */}
          <div className="p-4 rounded-lg border border-gray-200 dark:border-gray-600">
            <div className="flex items-start gap-3">
              <input type="radio" name="llm-service" disabled className="mt-1" />
              <div className="flex-1 space-y-4">
                <div>
                  <span className="text-sm font-medium text-gray-900 dark:text-gray-100">LM Studio</span>
                  <p className="mt-0.5 text-xs text-gray-500 dark:text-gray-400">
                    Run local LLMs with LM Studio's OpenAI-compatible server.
                  </p>
                </div>

                <div>
                  <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">Server URL</label>
                  <input
                    type="text"
                    disabled
                    placeholder="http://localhost:1234/v1"
                    className="w-full px-3 py-2 text-sm rounded-lg border border-gray-300 dark:border-gray-600 bg-gray-50 dark:bg-gray-700 text-gray-500 dark:text-gray-400"
                  />
                </div>

                <div className="flex justify-end">
                  <button
                    disabled
                    className="px-3 py-1.5 text-xs font-medium rounded-lg border border-gray-300 dark:border-gray-600 text-gray-400 dark:text-gray-500"
                  >
                    Test Connection
                  </button>
                </div>
              </div>
            </div>
          </div>

          {/* vLLM Card */}
          <div className="p-4 rounded-lg border border-gray-200 dark:border-gray-600">
            <div className="flex items-start gap-3">
              <input type="radio" name="llm-service" disabled className="mt-1" />
              <div className="flex-1 space-y-4">
                <div>
                  <span className="text-sm font-medium text-gray-900 dark:text-gray-100">vLLM</span>
                  <p className="mt-0.5 text-xs text-gray-500 dark:text-gray-400">
                    High-throughput LLM serving with PagedAttention.
                  </p>
                </div>

                <div>
                  <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">Server URL</label>
                  <input
                    type="text"
                    disabled
                    placeholder="http://localhost:52780/v1"
                    className="w-full px-3 py-2 text-sm rounded-lg border border-gray-300 dark:border-gray-600 bg-gray-50 dark:bg-gray-700 text-gray-500 dark:text-gray-400"
                  />
                </div>

                <div>
                  <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">Model</label>
                  <input
                    type="text"
                    disabled
                    placeholder="meta-llama/Llama-3.2-3B-Instruct"
                    className="w-full px-3 py-2 text-sm rounded-lg border border-gray-300 dark:border-gray-600 bg-gray-50 dark:bg-gray-700 text-gray-500 dark:text-gray-400"
                  />
                </div>

                <div className="flex justify-end">
                  <button
                    disabled
                    className="px-3 py-1.5 text-xs font-medium rounded-lg border border-gray-300 dark:border-gray-600 text-gray-400 dark:text-gray-500"
                  >
                    Test Connection
                  </button>
                </div>
              </div>
            </div>
          </div>

          {/* LocalAI Card */}
          <div className="p-4 rounded-lg border border-gray-200 dark:border-gray-600">
            <div className="flex items-start gap-3">
              <input type="radio" name="llm-service" disabled className="mt-1" />
              <div className="flex-1 space-y-4">
                <div>
                  <span className="text-sm font-medium text-gray-900 dark:text-gray-100">LocalAI</span>
                  <p className="mt-0.5 text-xs text-gray-500 dark:text-gray-400">
                    Self-hosted OpenAI-compatible API for running LLMs locally.
                  </p>
                </div>

                <div>
                  <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">Server URL</label>
                  <input
                    type="text"
                    disabled
                    placeholder="http://localhost:8080/v1"
                    className="w-full px-3 py-2 text-sm rounded-lg border border-gray-300 dark:border-gray-600 bg-gray-50 dark:bg-gray-700 text-gray-500 dark:text-gray-400"
                  />
                </div>

                <div className="flex justify-end">
                  <button
                    disabled
                    className="px-3 py-1.5 text-xs font-medium rounded-lg border border-gray-300 dark:border-gray-600 text-gray-400 dark:text-gray-500"
                  >
                    Test Connection
                  </button>
                </div>
              </div>
            </div>
          </div>

          {/* Text Generation WebUI Card */}
          <div className="p-4 rounded-lg border border-gray-200 dark:border-gray-600">
            <div className="flex items-start gap-3">
              <input type="radio" name="llm-service" disabled className="mt-1" />
              <div className="flex-1 space-y-4">
                <div>
                  <span className="text-sm font-medium text-gray-900 dark:text-gray-100">Text Generation WebUI</span>
                  <p className="mt-0.5 text-xs text-gray-500 dark:text-gray-400">
                    Oobabooga's text-generation-webui with OpenAI-compatible API.
                  </p>
                </div>

                <div>
                  <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">API URL</label>
                  <input
                    type="text"
                    disabled
                    placeholder="http://localhost:5000/v1"
                    className="w-full px-3 py-2 text-sm rounded-lg border border-gray-300 dark:border-gray-600 bg-gray-50 dark:bg-gray-700 text-gray-500 dark:text-gray-400"
                  />
                </div>

                <div className="flex justify-end">
                  <button
                    disabled
                    className="px-3 py-1.5 text-xs font-medium rounded-lg border border-gray-300 dark:border-gray-600 text-gray-400 dark:text-gray-500"
                  >
                    Test Connection
                  </button>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* AI Model Providers Section (Enterprise) */}
      <div className="mt-6 rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 opacity-60 cursor-not-allowed">
        <div className="px-5 py-4 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <h2 className="text-base font-semibold text-gray-900 dark:text-gray-100">AI Model Providers</h2>
            <EnterpriseBadge size="sm" />
          </div>
        </div>

        <div className="px-5 py-4 space-y-4 pointer-events-none">
          <p className="text-sm text-gray-600 dark:text-gray-400">
            Connect to cloud-based AI providers for enhanced capabilities.
          </p>

          {/* OpenAI-compatible Card */}
          <div className="p-4 rounded-lg border border-gray-200 dark:border-gray-600">
            <div className="flex items-start gap-3">
              <input type="radio" name="external-provider" disabled className="mt-1" />
              <div className="flex-1 space-y-4">
                <div>
                  <span className="text-sm font-medium text-gray-900 dark:text-gray-100">OpenAI-compatible</span>
                  <p className="mt-0.5 text-xs text-gray-500 dark:text-gray-400">
                    Connect to any OpenAI-compatible API endpoint.
                  </p>
                </div>

                <div>
                  <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">Base URL</label>
                  <input
                    type="text"
                    disabled
                    placeholder="https://api.openai.com/v1"
                    className="w-full px-3 py-2 text-sm rounded-lg border border-gray-300 dark:border-gray-600 bg-gray-50 dark:bg-gray-700 text-gray-500 dark:text-gray-400"
                  />
                </div>

                <div>
                  <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">API Key</label>
                  <input
                    type="password"
                    disabled
                    placeholder="sk-••••••••••••••••"
                    className="w-full px-3 py-2 text-sm rounded-lg border border-gray-300 dark:border-gray-600 bg-gray-50 dark:bg-gray-700 text-gray-500 dark:text-gray-400"
                  />
                </div>

                <div>
                  <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">Model</label>
                  <select
                    disabled
                    className="w-full px-3 py-2 text-sm rounded-lg border border-gray-300 dark:border-gray-600 bg-gray-50 dark:bg-gray-700 text-gray-500 dark:text-gray-400"
                  >
                    <option>gpt-4o</option>
                    <option>gpt-4o-mini</option>
                    <option>gpt-4-turbo</option>
                  </select>
                </div>

                <div className="flex justify-end">
                  <button
                    disabled
                    className="px-3 py-1.5 text-xs font-medium rounded-lg border border-gray-300 dark:border-gray-600 text-gray-400 dark:text-gray-500"
                  >
                    Test Connection
                  </button>
                </div>
              </div>
            </div>
          </div>

          {/* Anthropic Card */}
          <div className="p-4 rounded-lg border border-gray-200 dark:border-gray-600">
            <div className="flex items-start gap-3">
              <input type="radio" name="external-provider" disabled className="mt-1" />
              <div className="flex-1 space-y-4">
                <div>
                  <span className="text-sm font-medium text-gray-900 dark:text-gray-100">Anthropic</span>
                  <p className="mt-0.5 text-xs text-gray-500 dark:text-gray-400">
                    Access Claude models directly via the Anthropic API.
                  </p>
                </div>

                <div>
                  <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">API Key</label>
                  <input
                    type="password"
                    disabled
                    placeholder="sk-ant-••••••••••••••••"
                    className="w-full px-3 py-2 text-sm rounded-lg border border-gray-300 dark:border-gray-600 bg-gray-50 dark:bg-gray-700 text-gray-500 dark:text-gray-400"
                  />
                </div>

                <div>
                  <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">Model</label>
                  <select
                    disabled
                    className="w-full px-3 py-2 text-sm rounded-lg border border-gray-300 dark:border-gray-600 bg-gray-50 dark:bg-gray-700 text-gray-500 dark:text-gray-400"
                  >
                    <option>claude-sonnet-4-20250514</option>
                    <option>claude-opus-4-20250514</option>
                    <option>claude-3-5-haiku-20241022</option>
                  </select>
                </div>

                <div className="flex justify-end">
                  <button
                    disabled
                    className="px-3 py-1.5 text-xs font-medium rounded-lg border border-gray-300 dark:border-gray-600 text-gray-400 dark:text-gray-500"
                  >
                    Test Connection
                  </button>
                </div>
              </div>
            </div>
          </div>

          {/* Google AI Card */}
          <div className="p-4 rounded-lg border border-gray-200 dark:border-gray-600">
            <div className="flex items-start gap-3">
              <input type="radio" name="external-provider" disabled className="mt-1" />
              <div className="flex-1 space-y-4">
                <div>
                  <span className="text-sm font-medium text-gray-900 dark:text-gray-100">Google AI</span>
                  <p className="mt-0.5 text-xs text-gray-500 dark:text-gray-400">
                    Access Gemini models via the Google AI Studio API.
                  </p>
                </div>

                <div>
                  <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">API Key</label>
                  <input
                    type="password"
                    disabled
                    placeholder="AIza••••••••••••••••"
                    className="w-full px-3 py-2 text-sm rounded-lg border border-gray-300 dark:border-gray-600 bg-gray-50 dark:bg-gray-700 text-gray-500 dark:text-gray-400"
                  />
                </div>

                <div>
                  <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">Model</label>
                  <select
                    disabled
                    className="w-full px-3 py-2 text-sm rounded-lg border border-gray-300 dark:border-gray-600 bg-gray-50 dark:bg-gray-700 text-gray-500 dark:text-gray-400"
                  >
                    <option>gemini-1.5-pro</option>
                    <option>gemini-1.5-flash</option>
                    <option>gemini-2.0-flash</option>
                  </select>
                </div>

                <div className="flex justify-end">
                  <button
                    disabled
                    className="px-3 py-1.5 text-xs font-medium rounded-lg border border-gray-300 dark:border-gray-600 text-gray-400 dark:text-gray-500"
                  >
                    Test Connection
                  </button>
                </div>
              </div>
            </div>
          </div>

          <p className="text-xs text-gray-500 dark:text-gray-400 border-t border-gray-200 dark:border-gray-700 pt-3">
            External providers require an Enterprise license. Contact sales@verbatim.studio for more information.
          </p>
        </div>
      </div>

      {/* Web Search Section */}
      <div className="mt-6 rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800">
        <div className="px-5 py-4 border-b border-gray-200 dark:border-gray-700">
          <h2 className="text-base font-semibold text-gray-900 dark:text-gray-100">Web Search</h2>
          <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
            Configure web search for Max AI assistant. Requires an API key from your chosen provider.
          </p>
        </div>

        <div className="px-5 py-4 space-y-4">
          {/* Provider Selection */}
          <div>
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Search Provider</label>
            <select
              value={webSearchProvider}
              onChange={(e) => setWebSearchProvider(e.target.value)}
              className="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 py-2 px-3 text-sm text-gray-900 dark:text-gray-100 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
            >
              <option value="tavily">Tavily</option>
              <option value="brave">Brave Search</option>
              <option value="searxng">SearXNG</option>
            </select>
          </div>

          {/* API Key */}
          <div>
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">API Key</label>
            <p className="text-xs text-gray-500 dark:text-gray-400 mb-2">
              {webSearchProvider === 'tavily' && (
                <>Get an API key from{' '}
                  <a href="https://tavily.com" target="_blank" rel="noopener noreferrer" className="text-blue-600 dark:text-blue-400 hover:underline">
                    tavily.com
                  </a>
                </>
              )}
              {webSearchProvider === 'brave' && (
                <>Get an API key from{' '}
                  <a href="https://brave.com/search/api/" target="_blank" rel="noopener noreferrer" className="text-blue-600 dark:text-blue-400 hover:underline">
                    brave.com/search/api
                  </a>
                </>
              )}
              {webSearchProvider === 'searxng' && (
                <>Configure your SearXNG instance URL as the API key.</>
              )}
            </p>
            {webSearchSettings?.api_key_set && !webSearchApiKey && (
              <div className="flex items-center gap-2 mb-2">
                <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md bg-green-50 dark:bg-green-900/20 text-sm text-green-700 dark:text-green-400">
                  <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                  </svg>
                  Key configured ({webSearchSettings.api_key_masked})
                </span>
                <button
                  onClick={async () => {
                    setWebSearchSaving(true);
                    try {
                      const updated = await api.config.updateWebSearchSettings({ api_key: '' });
                      setWebSearchSettings(updated);
                    } finally {
                      setWebSearchSaving(false);
                    }
                  }}
                  disabled={webSearchSaving}
                  className="text-xs text-red-600 dark:text-red-400 hover:text-red-700 dark:hover:text-red-300 hover:underline disabled:opacity-50"
                >
                  Remove
                </button>
              </div>
            )}
            <div className="flex gap-2">
              <div className="relative flex-1">
                <input
                  type={showWebSearchKey ? 'text' : 'password'}
                  value={webSearchApiKey}
                  onChange={(e) => setWebSearchApiKey(e.target.value)}
                  placeholder={webSearchSettings?.api_key_set ? 'Enter new key to replace...' : 'Enter API key...'}
                  className="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 py-2 px-3 pr-10 text-sm text-gray-900 dark:text-gray-100 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500 font-mono"
                />
                <button
                  type="button"
                  onClick={() => setShowWebSearchKey(!showWebSearchKey)}
                  className="absolute inset-y-0 right-0 flex items-center pr-3 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300"
                >
                  {showWebSearchKey ? (
                    <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                      <path strokeLinecap="round" strokeLinejoin="round" d="M13.875 18.825A10.05 10.05 0 0112 19c-4.478 0-8.268-2.943-9.543-7a9.97 9.97 0 011.563-3.029m5.858.908a3 3 0 114.243 4.243M9.878 9.878l4.242 4.242M9.88 9.88l-3.29-3.29m7.532 7.532l3.29 3.29M3 3l3.59 3.59m0 0A9.953 9.953 0 0112 5c4.478 0 8.268 2.943 9.543 7a10.025 10.025 0 01-4.132 5.411m0 0L21 21" />
                    </svg>
                  ) : (
                    <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                      <path strokeLinecap="round" strokeLinejoin="round" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
                      <path strokeLinecap="round" strokeLinejoin="round" d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z" />
                    </svg>
                  )}
                </button>
              </div>
              <button
                onClick={saveWebSearchSettings}
                disabled={(!webSearchApiKey.trim() && webSearchProvider === (webSearchSettings?.provider || 'tavily')) || webSearchSaving}
                className="px-4 py-2 rounded-lg bg-primary text-primary-foreground text-sm font-medium hover:bg-primary/90 focus:outline-none focus:ring-2 focus:ring-primary disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
              >
                Save
              </button>
            </div>
          </div>

          <p className="text-xs text-gray-500 dark:text-gray-400 border-t border-gray-200 dark:border-gray-700 pt-3">
            Web search allows Max to find current information from the internet during conversations.
          </p>
        </div>
      </div>
        </>
      )}

      {/* ===== GENERAL TAB (continued) - Playback & Shortcuts ===== */}
      {activeTab === 'general' && (
        <>
      {/* Playback Section */}
      <div className="mt-6 rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800">
        <div className="px-5 py-4 border-b border-gray-200 dark:border-gray-700">
          <h2 className="text-base font-semibold text-gray-900 dark:text-gray-100">Playback</h2>
        </div>
        <div className="px-5">
          <SettingSection
            title="Default Playback Speed"
            description="Set the default speed for audio playback"
          >
            <select
              value={defaultPlaybackSpeed}
              onChange={(e) => updateSetting('defaultPlaybackSpeed', parseFloat(e.target.value))}
              className="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 py-2 px-3 text-sm text-gray-900 dark:text-gray-100 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
            >
              {PLAYBACK_SPEEDS.map((speed) => (
                <option key={speed.value} value={speed.value}>
                  {speed.label}
                </option>
              ))}
            </select>
          </SettingSection>
        </div>
      </div>

      {/* Keyboard Shortcuts Editor */}
      <KeybindingEditor />
        </>
      )}

      {/* ===== SYSTEM TAB ===== */}
      {activeTab === 'system' && (
        <>
      {/* Storage Locations Section */}
      <div className="rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800">
        <div className="px-5 py-4 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between">
          <div>
            <h2 className="text-base font-semibold text-gray-900 dark:text-gray-100">Storage Locations</h2>
            <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">Configure where your files and media are stored on disk</p>
          </div>
          <div className="flex items-center gap-2">
            {!showAddLocation && (
              <button
                onClick={() => setShowAddLocation(true)}
                className="inline-flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 transition-colors"
              >
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M12 4v16m8-8H4" />
                </svg>
                Add Location
              </button>
            )}
          </div>
        </div>
        <div className="px-5 py-4 space-y-4">
          {storageError && (
            <div className="p-3 rounded-lg bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 text-sm text-red-600 dark:text-red-400">
              {storageError}
              <button onClick={() => setStorageError(null)} className="ml-2 underline">Dismiss</button>
            </div>
          )}

          {/* Syncing indicator */}
          {syncing && (
            <div className="p-3 rounded-lg bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800 text-sm text-blue-600 dark:text-blue-400 flex items-center gap-2">
              <svg className="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                <path strokeLinecap="round" strokeLinejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
              </svg>
              Syncing workspace...
            </div>
          )}

          {/* Sync result display */}
          {syncResult && !syncing && (
            <div className="p-4 rounded-lg bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800">
              <div className="flex items-start justify-between">
                <div>
                  <h4 className="text-sm font-medium text-gray-900 dark:text-gray-100 mb-2">
                    Sync Complete: {syncResult.storage_location_name}
                  </h4>
                  <div className="grid grid-cols-2 gap-x-6 gap-y-1 text-xs">
                    <div className="text-gray-600 dark:text-gray-400">
                      <span className="font-medium">Recordings:</span> {syncResult.recordings_in_db}
                    </div>
                    <div className="text-gray-600 dark:text-gray-400">
                      <span className="font-medium">Documents:</span> {syncResult.documents_in_db}
                    </div>
                    <div className="text-gray-600 dark:text-gray-400">
                      <span className="font-medium">Files on storage:</span> {syncResult.recordings_on_disk + syncResult.documents_on_disk}
                    </div>
                    <div className="text-gray-600 dark:text-gray-400">
                      <span className="font-medium">Path:</span> {syncResult.storage_path}
                    </div>
                    {syncResult.projects_created > 0 && (
                      <div className="text-blue-600 dark:text-blue-400 col-span-2">
                        <span className="font-medium">Projects created:</span> {syncResult.projects_created} folders mapped to projects
                      </div>
                    )}
                    {(syncResult.recordings_imported > 0 || syncResult.documents_imported > 0) && (
                      <div className="text-green-600 dark:text-green-400 col-span-2">
                        <span className="font-medium">Imported:</span> {syncResult.recordings_imported + syncResult.documents_imported} new files
                      </div>
                    )}
                    {(syncResult.recordings_removed > 0 || syncResult.documents_removed > 0) && (
                      <div className="text-amber-600 dark:text-amber-400 col-span-2">
                        <span className="font-medium">Removed:</span> {syncResult.recordings_removed + syncResult.documents_removed} files no longer on storage
                      </div>
                    )}
                  </div>
                </div>
                <button
                  onClick={() => setSyncResult(null)}
                  className="text-gray-400 hover:text-gray-600 dark:hover:text-gray-300"
                >
                  <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                  </svg>
                </button>
              </div>
            </div>
          )}

          {/* Add new location form */}
          {showAddLocation && (
            <div className="p-4 rounded-lg border border-blue-200 dark:border-blue-800 bg-blue-50/50 dark:bg-blue-900/10 space-y-4">
              <h3 className="text-sm font-medium text-gray-900 dark:text-gray-100">Add New Storage Location</h3>

              {/* Name input */}
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1.5">Name</label>
                <input
                  type="text"
                  value={newLocationName}
                  onChange={(e) => setNewLocationName(e.target.value)}
                  placeholder="e.g., My Cloud Storage"
                  className="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 py-2 px-3 text-sm text-gray-900 dark:text-gray-100 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
                />
              </div>

              {/* Storage type selector */}
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">Storage Type</label>
                <StorageTypeSelector
                  value={newLocationType}
                  onChange={(type) => {
                    setNewLocationType(type);
                    setNewLocationSubtype(null);
                    setNewLocationConfig({});
                    setConnectionTestResult(null);
                  }}
                />
              </div>

              {/* Subtype selector (for network/cloud) */}
              {newLocationType !== 'local' && (
                <StorageSubtypeSelector
                  storageType={newLocationType}
                  value={newLocationSubtype}
                  onChange={(subtype) => {
                    setNewLocationSubtype(subtype);
                    setNewLocationConfig({});
                    setConnectionTestResult(null);
                    setNewLocationOAuthTokens(undefined);
                  }}
                />
              )}

              {/* Configuration form */}
              {(newLocationType === 'local' || newLocationSubtype) && (
                <StorageConfigForm
                  storageType={newLocationType}
                  subtype={newLocationSubtype}
                  config={newLocationConfig}
                  onChange={setNewLocationConfig}
                  oauthTokens={newLocationOAuthTokens}
                  onOAuthSuccess={handleOAuthSuccess}
                  onOAuthError={handleOAuthError}
                  onOAuthDisconnect={handleOAuthDisconnect}
                />
              )}

              {/* Connection test result */}
              {connectionTestResult && (
                <div className={`p-3 rounded-lg text-sm ${
                  connectionTestResult.success
                    ? 'bg-green-50 dark:bg-green-900/20 border border-green-200 dark:border-green-800 text-green-600 dark:text-green-400'
                    : 'bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 text-red-600 dark:text-red-400'
                }`}>
                  {connectionTestResult.success ? '✓ ' : '✗ '}{connectionTestResult.message}
                </div>
              )}

              {/* Actions */}
              <div className="flex gap-2 justify-end pt-2">
                <button
                  onClick={() => { setShowAddLocation(false); resetAddLocationForm(); }}
                  className="px-3 py-1.5 text-sm font-medium rounded-lg border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700"
                >
                  Cancel
                </button>
                {/* Test Connection button - hide for OAuth providers */}
                {(newLocationType !== 'local' && newLocationSubtype && !['gdrive', 'onedrive', 'dropbox'].includes(newLocationSubtype)) && (
                  <button
                    onClick={handleTestConnection}
                    disabled={testingConnection}
                    className="px-3 py-1.5 text-sm font-medium rounded-lg border border-blue-500 text-blue-600 dark:text-blue-400 hover:bg-blue-50 dark:hover:bg-blue-900/20 disabled:opacity-50"
                  >
                    {testingConnection ? 'Testing...' : 'Test Connection'}
                  </button>
                )}
                <button
                  onClick={handleAddStorageLocation}
                  disabled={
                    storageSaving ||
                    !newLocationName.trim() ||
                    (newLocationType === 'local' && !newLocationConfig.path?.trim()) ||
                    (newLocationType !== 'local' && !newLocationSubtype) ||
                    (['gdrive', 'onedrive', 'dropbox'].includes(newLocationSubtype || '') && !newLocationOAuthTokens)
                  }
                  className="px-3 py-1.5 text-sm font-medium rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
                >
                  {storageSaving ? 'Adding...' : 'Add Location'}
                </button>
              </div>
            </div>
          )}

          {/* Location list */}
          <div className="space-y-3">
            {storageLocations.map((location) => (
              <div
                key={location.id}
                className={`p-4 rounded-lg border transition-all ${
                  location.is_default
                    ? 'border-green-300 dark:border-green-700 bg-green-50/50 dark:bg-green-900/10'
                    : 'border-gray-200 dark:border-gray-600'
                }`}
              >
                {editingLocation?.id === location.id ? (
                  // Edit mode
                  <div className="space-y-3">
                    <div>
                      <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">Name</label>
                      <input
                        type="text"
                        value={editingLocation.name}
                        onChange={(e) => setEditingLocation({ ...editingLocation, name: e.target.value })}
                        className="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 py-2 px-3 text-sm text-gray-900 dark:text-gray-100 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
                      />
                    </div>
                    {/* Show different field based on storage type */}
                    {editingLocation.type === 'cloud' && ['gdrive', 'onedrive', 'dropbox'].includes(editingLocation.subtype || '') ? (
                      <div>
                        <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">Folder Name</label>
                        <input
                          type="text"
                          value={editingLocation.config.folder_path || ''}
                          onChange={(e) => setEditingLocation({ ...editingLocation, config: { ...editingLocation.config, folder_path: e.target.value } })}
                          placeholder="Verbatim Studio"
                          className="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 py-2 px-3 text-sm text-gray-900 dark:text-gray-100 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
                        />
                        <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">Folder in your cloud storage where files will be saved</p>
                      </div>
                    ) : (
                      <div>
                        <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">Path</label>
                        <div className="flex gap-2">
                          <input
                            type="text"
                            value={editingLocation.config.path || ''}
                            onChange={(e) => setEditingLocation({ ...editingLocation, config: { ...editingLocation.config, path: e.target.value } })}
                            className="flex-1 rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 py-2 px-3 text-sm font-mono text-gray-900 dark:text-gray-100 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
                          />
                          {(window.electronAPI?.openDirectoryDialog || 'showDirectoryPicker' in window) && (
                            <button
                              type="button"
                              onClick={async () => {
                                try {
                                  // Prefer Electron's native dialog (returns full path)
                                  if (window.electronAPI?.openDirectoryDialog) {
                                    const fullPath = await window.electronAPI.openDirectoryDialog();
                                    if (fullPath) {
                                      setEditingLocation({ ...editingLocation, config: { ...editingLocation.config, path: fullPath } });
                                    }
                                    return;
                                  }
                                  // Fallback to browser API (only returns folder name)
                                  const dirHandle = await window.showDirectoryPicker({ mode: 'readwrite' });
                                  setEditingLocation({ ...editingLocation, config: { ...editingLocation.config, path: dirHandle.name } });
                                } catch {
                                  // User cancelled
                                }
                              }}
                              className="px-3 py-2 text-sm font-medium rounded-lg border border-gray-300 dark:border-gray-600 bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600 text-gray-700 dark:text-gray-300 transition-colors whitespace-nowrap"
                            >
                              Browse...
                            </button>
                          )}
                        </div>
                      </div>
                    )}
                    <div className="flex gap-2 justify-end">
                      <button
                        onClick={() => setEditingLocation(null)}
                        className="px-3 py-1.5 text-sm font-medium rounded-lg border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700"
                      >
                        Cancel
                      </button>
                      <button
                        onClick={handleUpdateStorageLocation}
                        disabled={storageSaving}
                        className="px-3 py-1.5 text-sm font-medium rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
                      >
                        {storageSaving ? 'Saving...' : 'Save'}
                      </button>
                    </div>
                  </div>
                ) : (
                  // View mode
                  <div className="flex items-start justify-between gap-3">
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <svg className="w-5 h-5 text-gray-400 dark:text-gray-500 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                          <path strokeLinecap="round" strokeLinejoin="round" d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z" />
                        </svg>
                        <span className="text-sm font-medium text-gray-900 dark:text-gray-100">{location.name}</span>
                        {location.is_default && (
                          <span className="inline-flex items-center gap-1 px-1.5 py-0.5 text-xs rounded bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-300">
                            <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                              <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                            </svg>
                            Default
                          </span>
                        )}
                        <span className="px-1.5 py-0.5 text-xs rounded bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-400">
                          {location.type}
                        </span>
                      </div>
                      <p className="mt-1 text-xs font-mono text-gray-500 dark:text-gray-400 truncate" title={location.config.path}>
                        {location.config.path}
                      </p>
                    </div>

                    <div className="flex items-center gap-1 shrink-0">
                      {!location.is_default && (
                        <button
                          onClick={() => handleSetDefaultLocation(location.id)}
                          className="p-1.5 text-gray-400 hover:text-green-600 dark:hover:text-green-400 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700"
                          title="Set as default"
                        >
                          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                            <path strokeLinecap="round" strokeLinejoin="round" d="M11.049 2.927c.3-.921 1.603-.921 1.902 0l1.519 4.674a1 1 0 00.95.69h4.915c.969 0 1.371 1.24.588 1.81l-3.976 2.888a1 1 0 00-.363 1.118l1.518 4.674c.3.922-.755 1.688-1.538 1.118l-3.976-2.888a1 1 0 00-1.176 0l-3.976 2.888c-.783.57-1.838-.197-1.538-1.118l1.518-4.674a1 1 0 00-.363-1.118l-3.976-2.888c-.784-.57-.38-1.81.588-1.81h4.914a1 1 0 00.951-.69l1.519-4.674z" />
                          </svg>
                        </button>
                      )}
                      <button
                        onClick={() => setEditingLocation(location)}
                        className="p-1.5 text-gray-400 hover:text-blue-600 dark:hover:text-blue-400 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700"
                        title="Edit"
                      >
                        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                          <path strokeLinecap="round" strokeLinejoin="round" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
                        </svg>
                      </button>
                      {storageLocations.length > 1 && (
                        <button
                          onClick={() => handleDeleteStorageLocation(location.id)}
                          className="p-1.5 text-gray-400 hover:text-red-600 dark:hover:text-red-400 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700"
                          title="Delete"
                        >
                          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                            <path strokeLinecap="round" strokeLinejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                          </svg>
                        </button>
                      )}
                    </div>
                  </div>
                )}
              </div>
            ))}

            {storageLocations.length === 0 && !showAddLocation && (
              <div className="text-center py-6 text-gray-500 dark:text-gray-400">
                <svg className="w-12 h-12 mx-auto mb-3 text-gray-300 dark:text-gray-600" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="1.5">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z" />
                </svg>
                <p className="text-sm">No storage locations configured</p>
                <p className="text-xs mt-1">Add a location to specify where files are stored</p>
              </div>
            )}
          </div>

          <p className="text-xs text-gray-500 dark:text-gray-400 border-t border-gray-200 dark:border-gray-700 pt-3">
            Storage locations determine where recordings, documents, and other media files are saved. The default location is used for new uploads.
          </p>
        </div>
      </div>

      {/* OAuth Credentials Section */}
      <div className="mt-6 rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800">
        <div className="px-5 py-4 border-b border-gray-200 dark:border-gray-700">
          <h2 className="text-base font-semibold text-gray-900 dark:text-gray-100">Cloud Storage Credentials</h2>
          <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">
            Configure OAuth app credentials for cloud storage providers
          </p>
        </div>
        <div className="px-5 py-4">
          <OAuthCredentialsConfig />
        </div>
      </div>

      {/* Backup & Restore Section */}
      <div className="mt-6 rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800">
        <div className="px-5 py-4 border-b border-gray-200 dark:border-gray-700">
          <h2 className="text-base font-semibold text-gray-900 dark:text-gray-100">Backup & Restore</h2>
        </div>
        <div className="px-5 py-4">
          {/* Archive Info */}
          {archiveInfo && (
            <div className="mb-4 p-3 rounded-lg bg-gray-50 dark:bg-gray-900 text-sm">
              <div className="grid grid-cols-2 gap-2 text-gray-600 dark:text-gray-400">
                <div>Recordings: <span className="font-medium text-gray-900 dark:text-gray-100">{archiveInfo.recordings_count}</span></div>
                <div>Transcripts: <span className="font-medium text-gray-900 dark:text-gray-100">{archiveInfo.transcripts_count}</span></div>
                <div>Projects: <span className="font-medium text-gray-900 dark:text-gray-100">{archiveInfo.projects_count}</span></div>
                <div>Media Size: <span className="font-medium text-gray-900 dark:text-gray-100">{(archiveInfo.media_size_bytes / 1024 / 1024).toFixed(1)} MB</span></div>
              </div>
            </div>
          )}

          {/* Import Result */}
          {importResult && (
            <div className={`mb-4 p-3 rounded-lg text-sm ${
              importResult.success
                ? 'bg-green-50 dark:bg-green-900/20 text-green-700 dark:text-green-400'
                : 'bg-red-50 dark:bg-red-900/20 text-red-700 dark:text-red-400'
            }`}>
              {importResult.message}
            </div>
          )}

          {/* Buttons */}
          <div className="flex gap-3">
            <button
              onClick={handleExport}
              disabled={isExporting}
              className="flex-1 inline-flex items-center justify-center gap-2 px-4 py-2 rounded-lg border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors disabled:opacity-50"
            >
              {isExporting ? (
                <>
                  <svg className="w-4 h-4 animate-spin" viewBox="0 0 24 24" fill="none">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                  </svg>
                  Exporting...
                </>
              ) : (
                <>
                  <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12" />
                  </svg>
                  Export Backup
                </>
              )}
            </button>

            <button
              onClick={handleImportClick}
              disabled={isImporting}
              className="flex-1 inline-flex items-center justify-center gap-2 px-4 py-2 rounded-lg border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors disabled:opacity-50"
            >
              {isImporting ? (
                <>
                  <svg className="w-4 h-4 animate-spin" viewBox="0 0 24 24" fill="none">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                  </svg>
                  Importing...
                </>
              ) : (
                <>
                  <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
                  </svg>
                  Restore Backup
                </>
              )}
            </button>
            <input
              ref={fileInputRef}
              type="file"
              accept=".vbz,.zip"
              onChange={handleFileChange}
              className="hidden"
            />
          </div>
          <p className="mt-3 text-xs text-gray-500 dark:text-gray-400">
            Export creates a .vbz archive with all your recordings, transcripts, and projects.
          </p>
        </div>
      </div>
        </>
      )}

      {/* ===== GENERAL TAB (continued) - Updates ===== */}
      {activeTab === 'general' && window.electronAPI && (
        <>
      {/* Updates Section */}
      <div className="mt-6 rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800">
        <div className="px-5 py-4 border-b border-gray-200 dark:border-gray-700">
          <h2 className="text-base font-semibold text-gray-900 dark:text-gray-100">Updates</h2>
        </div>
        <div className="px-5 py-4 space-y-4">
          {/* Current Version */}
          <div className="flex items-center justify-between">
            <span className="text-sm text-gray-600 dark:text-gray-400">Current Version</span>
            <span className="text-sm font-medium text-gray-900 dark:text-gray-100">{APP_VERSION}</span>
          </div>

          {/* Auto-update toggle */}
          <div className="flex items-center justify-between py-2">
            <div>
              <p className="text-sm font-medium text-gray-900 dark:text-gray-100">Check for updates automatically</p>
              <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">Checks on launch + every 24 hours</p>
            </div>
            <label className="relative inline-flex items-center cursor-pointer">
              <input
                type="checkbox"
                checked={autoUpdateEnabled}
                onChange={(e) => handleAutoUpdateToggle(e.target.checked)}
                className="sr-only peer"
              />
              <div className="w-11 h-6 bg-gray-200 peer-focus:outline-none peer-focus:ring-2 peer-focus:ring-blue-500 rounded-full peer dark:bg-gray-700 peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all dark:border-gray-600 peer-checked:bg-blue-600" />
            </label>
          </div>

          {/* Check for updates button */}
          <div className="pt-2 border-t border-gray-200 dark:border-gray-700">
            <div className="flex items-center gap-3">
              <button
                onClick={handleCheckForUpdates}
                disabled={isCheckingForUpdates}
                className="inline-flex items-center justify-center gap-2 px-4 py-2 rounded-lg bg-gray-100 dark:bg-gray-700 text-gray-700 dark:text-gray-200 hover:bg-gray-200 dark:hover:bg-gray-600 transition-colors disabled:opacity-50"
              >
                {isCheckingForUpdates ? (
                  <>
                    <svg className="w-4 h-4 animate-spin" viewBox="0 0 24 24" fill="none">
                      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                    </svg>
                    Checking...
                  </>
                ) : (
                  'Check for Updates'
                )}
              </button>
              {updateCheckResult === 'none' && (
                <span className="text-sm text-green-600 dark:text-green-400">You're on the latest version!</span>
              )}
              {updateCheckResult === 'available' && (
                <span className="text-sm text-blue-600 dark:text-blue-400">Update available!</span>
              )}
            </div>
          </div>
        </div>
      </div>
        </>
      )}

      {/* ===== GENERAL TAB (continued) - About ===== */}
      {activeTab === 'general' && (
        <>
      {/* About Section */}
      <div className="mt-6 rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800">
        <div className="px-5 py-4 border-b border-gray-200 dark:border-gray-700">
          <h2 className="text-base font-semibold text-gray-900 dark:text-gray-100">About Verbatim Studio</h2>
        </div>
        <div className="px-5 py-4 space-y-4">
          <div>
            <p className="text-sm text-gray-600 dark:text-gray-400">
              <strong className="text-gray-900 dark:text-gray-100">Transcription you can trust.</strong> Verbatim Studio is a privacy-first transcription platform designed for researchers, journalists, legal professionals, and anyone who needs accurate, secure transcription.
            </p>
            <p className="mt-2 text-sm text-gray-600 dark:text-gray-400">
              All processing happens locally on your machine. Your recordings and transcripts never leave your computer.
            </p>
          </div>

          {/* Current Features */}
          <div className="pt-3 border-t border-gray-200 dark:border-gray-700">
            <h3 className="text-sm font-medium text-gray-900 dark:text-gray-100 mb-2">Current Features</h3>
            <ul className="text-sm text-gray-600 dark:text-gray-400 space-y-1.5">
              <li className="flex items-start gap-2">
                <svg className="w-4 h-4 text-green-500 mt-0.5 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                </svg>
                <span><strong>Local AI Transcription</strong> — WhisperX and MLX Whisper engines with speaker diarization</span>
              </li>
              <li className="flex items-start gap-2">
                <svg className="w-4 h-4 text-green-500 mt-0.5 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                </svg>
                <span><strong>Live Transcription</strong> — Real-time transcription from microphone input</span>
              </li>
              <li className="flex items-start gap-2">
                <svg className="w-4 h-4 text-green-500 mt-0.5 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                </svg>
                <span><strong>Documents & OCR</strong> — Extract text from PDFs and images with vision AI</span>
              </li>
              <li className="flex items-start gap-2">
                <svg className="w-4 h-4 text-green-500 mt-0.5 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                </svg>
                <span><strong>Video Support</strong> — Upload and transcribe video files (MP4, WebM, MOV, MKV)</span>
              </li>
              <li className="flex items-start gap-2">
                <svg className="w-4 h-4 text-green-500 mt-0.5 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                </svg>
                <span><strong>AI Assistant (Max)</strong> — Chat with your transcripts, ask questions, get summaries</span>
              </li>
              <li className="flex items-start gap-2">
                <svg className="w-4 h-4 text-green-500 mt-0.5 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                </svg>
                <span><strong>Global Search</strong> — Keyword search across projects, transcripts, chats, notes, and OCR results</span>
              </li>
              <li className="flex items-start gap-2">
                <svg className="w-4 h-4 text-green-500 mt-0.5 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                </svg>
                <span><strong>Semantic Search</strong> — Find content by meaning, not just keywords</span>
              </li>
              <li className="flex items-start gap-2">
                <svg className="w-4 h-4 text-green-500 mt-0.5 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                </svg>
                <span><strong>Project Organization</strong> — Organize into projects with real folders visible on your device</span>
              </li>
              <li className="flex items-start gap-2">
                <svg className="w-4 h-4 text-green-500 mt-0.5 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                </svg>
                <span><strong>Export Options</strong> — TXT, SRT, VTT, JSON, and full backup archives</span>
              </li>
              <li className="flex items-start gap-2">
                <svg className="w-4 h-4 text-green-500 mt-0.5 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                </svg>
                <span><strong>Cloud Storage</strong> — Sync with Google Drive, OneDrive, and Dropbox via OAuth</span>
              </li>
              <li className="flex items-start gap-2">
                <svg className="w-4 h-4 text-green-500 mt-0.5 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                </svg>
                <span><strong>Desktop App</strong> — Native Electron application for macOS and Windows</span>
              </li>
            </ul>
          </div>

          {/* Coming Soon */}
          <div className="pt-3 border-t border-gray-200 dark:border-gray-700">
            <h3 className="text-sm font-medium text-gray-900 dark:text-gray-100 mb-2">Coming Soon</h3>
            <ul className="text-sm text-gray-600 dark:text-gray-400 space-y-1.5">
              <li className="flex items-start gap-2">
                <svg className="w-4 h-4 text-blue-500 mt-0.5 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
                </svg>
                <span><strong>Meeting Bots</strong> — Auto-join Teams, Meet, and Zoom calls to transcribe meetings</span>
              </li>
              <li className="flex items-start gap-2">
                <svg className="w-4 h-4 text-blue-500 mt-0.5 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
                </svg>
                <span><strong>External AI Services</strong> — Connect to Ollama, OpenAI, or self-hosted LLM/WhisperX</span>
              </li>
              <li className="flex items-start gap-2">
                <svg className="w-4 h-4 text-blue-500 mt-0.5 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
                </svg>
                <span><strong>S3 Object Storage</strong> — Store media in AWS S3, MinIO, or compatible services</span>
              </li>
              <li className="flex items-start gap-2">
                <svg className="w-4 h-4 text-blue-500 mt-0.5 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
                </svg>
                <span><strong>Network Storage</strong> — Mount SMB/NFS shares for centralized media access</span>
              </li>
              <li className="flex items-start gap-2">
                <svg className="w-4 h-4 text-blue-500 mt-0.5 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
                </svg>
                <span><strong>Team Collaboration</strong> — Share projects securely with multi-user access control</span>
              </li>
              <li className="flex items-start gap-2">
                <svg className="w-4 h-4 text-blue-500 mt-0.5 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
                </svg>
                <span><strong>Team Communication</strong> — Built-in chat platform and user-tracked comments on transcript segments</span>
              </li>
              <li className="flex items-start gap-2">
                <svg className="w-4 h-4 text-blue-500 mt-0.5 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
                </svg>
                <span><strong>Secure Mobile Access</strong> — Connect to your self-hosted Verbatim server via encrypted tunnels</span>
              </li>
            </ul>
          </div>

          {/* Links */}
          <div className="pt-3 border-t border-gray-200 dark:border-gray-700 flex gap-4 text-sm">
            <a
              href="https://github.com/JongoDB/verbatim-studio"
              target="_blank"
              rel="noopener noreferrer"
              className="text-blue-600 dark:text-blue-400 hover:underline flex items-center gap-1"
            >
              <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 24 24">
                <path fillRule="evenodd" clipRule="evenodd" d="M12 2C6.477 2 2 6.484 2 12.017c0 4.425 2.865 8.18 6.839 9.504.5.092.682-.217.682-.483 0-.237-.008-.868-.013-1.703-2.782.605-3.369-1.343-3.369-1.343-.454-1.158-1.11-1.466-1.11-1.466-.908-.62.069-.608.069-.608 1.003.07 1.531 1.032 1.531 1.032.892 1.53 2.341 1.088 2.91.832.092-.647.35-1.088.636-1.338-2.22-.253-4.555-1.113-4.555-4.951 0-1.093.39-1.988 1.029-2.688-.103-.253-.446-1.272.098-2.65 0 0 .84-.27 2.75 1.026A9.564 9.564 0 0112 6.844c.85.004 1.705.115 2.504.337 1.909-1.296 2.747-1.027 2.747-1.027.546 1.379.202 2.398.1 2.651.64.7 1.028 1.595 1.028 2.688 0 3.848-2.339 4.695-4.566 4.943.359.309.678.92.678 1.855 0 1.338-.012 2.419-.012 2.747 0 .268.18.58.688.482A10.019 10.019 0 0022 12.017C22 6.484 17.522 2 12 2z" />
              </svg>
              GitHub
            </a>
            <a
              href="https://github.com/JongoDB/verbatim-studio/issues"
              target="_blank"
              rel="noopener noreferrer"
              className="text-blue-600 dark:text-blue-400 hover:underline"
            >
              Report an Issue
            </a>
          </div>
        </div>
      </div>
        </>
      )}

      {/* ===== SYSTEM TAB (continued) - System Information ===== */}
      {activeTab === 'system' && systemInfo && (
        <>
        <div className="mt-6 rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800">
          <div className="px-5 py-4 border-b border-gray-200 dark:border-gray-700">
            <h2 className="text-base font-semibold text-gray-900 dark:text-gray-100">System Information</h2>
          </div>
          <div className="px-5 py-4 space-y-6">
            {/* App Info */}
            <div>
              <h3 className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-3">Application</h3>
              <div className="grid grid-cols-2 gap-3 text-sm">
                <div className="flex justify-between">
                  <span className="text-gray-500 dark:text-gray-400">Version</span>
                  <span className="font-mono text-gray-900 dark:text-gray-100">{systemInfo.app_version}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-gray-500 dark:text-gray-400">Python</span>
                  <span className="font-mono text-gray-900 dark:text-gray-100">{systemInfo.python_version}</span>
                </div>
                <div className="flex justify-between col-span-2">
                  <span className="text-gray-500 dark:text-gray-400">Platform</span>
                  <span className="font-mono text-gray-900 dark:text-gray-100 text-right truncate ml-4" title={systemInfo.platform_version}>
                    {systemInfo.platform}
                  </span>
                </div>
                <div className="flex justify-between">
                  <span className="text-gray-500 dark:text-gray-400">Max Upload</span>
                  <span className="font-mono text-gray-900 dark:text-gray-100">{formatBytes(systemInfo.max_upload_bytes)}</span>
                </div>
              </div>
            </div>

            {/* Storage Paths */}
            <div className="border-t border-gray-200 dark:border-gray-700 pt-4">
              <h3 className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-3">Storage Paths</h3>
              <div className="space-y-2 text-sm">
                <div className="flex flex-col">
                  <span className="text-gray-500 dark:text-gray-400">Data Directory</span>
                  <span className="font-mono text-xs text-gray-900 dark:text-gray-100 truncate" title={systemInfo.paths.data_dir}>
                    {systemInfo.paths.data_dir}
                  </span>
                </div>
                <div className="flex flex-col">
                  <span className="text-gray-500 dark:text-gray-400">Media Directory</span>
                  <span className="font-mono text-xs text-gray-900 dark:text-gray-100 truncate" title={systemInfo.paths.media_dir}>
                    {systemInfo.paths.media_dir}
                  </span>
                </div>
                <div className="flex flex-col">
                  <span className="text-gray-500 dark:text-gray-400">Models Directory</span>
                  <span className="font-mono text-xs text-gray-900 dark:text-gray-100 truncate" title={systemInfo.paths.models_dir}>
                    {systemInfo.paths.models_dir}
                  </span>
                </div>
                <div className="flex flex-col">
                  <span className="text-gray-500 dark:text-gray-400">Database</span>
                  <span className="font-mono text-xs text-gray-900 dark:text-gray-100 truncate" title={systemInfo.paths.database_path}>
                    {systemInfo.paths.database_path}
                  </span>
                </div>
              </div>
            </div>

            {/* Disk Usage */}
            <div className="border-t border-gray-200 dark:border-gray-700 pt-4">
              <h3 className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-3">Disk Usage</h3>
              <div className="space-y-2">
                <div className="w-full h-3 bg-gray-200 dark:bg-gray-700 rounded-full overflow-hidden">
                  <div
                    className={`h-full rounded-full transition-all ${
                      systemInfo.disk_usage.percent_used > 90
                        ? 'bg-red-500'
                        : systemInfo.disk_usage.percent_used > 75
                          ? 'bg-yellow-500'
                          : 'bg-blue-500'
                    }`}
                    style={{ width: `${systemInfo.disk_usage.percent_used}%` }}
                  />
                </div>
                <p className="text-sm text-gray-600 dark:text-gray-400">
                  {formatBytes(systemInfo.disk_usage.free_bytes)} free of {formatBytes(systemInfo.disk_usage.total_bytes)} ({systemInfo.disk_usage.percent_used}% used)
                </p>
              </div>
            </div>

            {/* Storage Breakdown */}
            <div className="border-t border-gray-200 dark:border-gray-700 pt-4">
              <h3 className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-3">Verbatim Storage</h3>
              <div className="space-y-2 text-sm">
                <div className="flex justify-between">
                  <span className="text-gray-500 dark:text-gray-400">Media Files</span>
                  <span className="text-gray-900 dark:text-gray-100">
                    {systemInfo.storage_breakdown.media_count} files ({formatBytes(systemInfo.storage_breakdown.media_bytes)})
                  </span>
                </div>
                <div className="flex justify-between">
                  <span className="text-gray-500 dark:text-gray-400">Database</span>
                  <span className="text-gray-900 dark:text-gray-100">
                    {formatBytes(systemInfo.storage_breakdown.database_bytes)}
                  </span>
                </div>
                <div className="flex justify-between">
                  <span className="text-gray-500 dark:text-gray-400">LLM Models</span>
                  <span className="text-gray-900 dark:text-gray-100">
                    {systemInfo.storage_breakdown.models.llm_count} ({formatBytes(systemInfo.storage_breakdown.models.llm_bytes)})
                  </span>
                </div>
                <div className="flex justify-between">
                  <span className="text-gray-500 dark:text-gray-400">ASR Models</span>
                  <span className="text-gray-900 dark:text-gray-100">
                    {systemInfo.storage_breakdown.models.asr_count} ({formatBytes(systemInfo.storage_breakdown.models.asr_bytes)})
                  </span>
                </div>
                <div className="flex justify-between">
                  <span className="text-gray-500 dark:text-gray-400">Diarization</span>
                  <span className="text-gray-900 dark:text-gray-100">
                    {systemInfo.storage_breakdown.models.diarization_count} ({formatBytes(systemInfo.storage_breakdown.models.diarization_bytes)})
                  </span>
                </div>
                <div className="flex justify-between">
                  <span className="text-gray-500 dark:text-gray-400">OCR / VLM</span>
                  <span className="text-gray-900 dark:text-gray-100">
                    {systemInfo.storage_breakdown.models.ocr_count} ({formatBytes(systemInfo.storage_breakdown.models.ocr_bytes)})
                  </span>
                </div>
                <div className="flex justify-between pt-2 border-t border-gray-100 dark:border-gray-700 font-medium">
                  <span className="text-gray-700 dark:text-gray-300">Total</span>
                  <span className="text-gray-900 dark:text-gray-100">
                    {formatBytes(systemInfo.storage_breakdown.total_bytes)}
                  </span>
                </div>
              </div>
            </div>

            {/* Content Counts */}
            <div className="border-t border-gray-200 dark:border-gray-700 pt-4">
              <h3 className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-3">Content</h3>
              <div className="grid grid-cols-3 gap-4 text-center">
                <div className="p-3 rounded-lg bg-gray-50 dark:bg-gray-900">
                  <div className="text-2xl font-bold text-gray-900 dark:text-gray-100">
                    {systemInfo.content_counts.recordings}
                  </div>
                  <div className="text-xs text-gray-500 dark:text-gray-400">Recordings</div>
                </div>
                <div className="p-3 rounded-lg bg-gray-50 dark:bg-gray-900">
                  <div className="text-2xl font-bold text-gray-900 dark:text-gray-100">
                    {systemInfo.content_counts.transcripts}
                  </div>
                  <div className="text-xs text-gray-500 dark:text-gray-400">Transcripts</div>
                </div>
                <div className="p-3 rounded-lg bg-gray-50 dark:bg-gray-900">
                  <div className="text-2xl font-bold text-gray-900 dark:text-gray-100">
                    {systemInfo.content_counts.segments.toLocaleString()}
                  </div>
                  <div className="text-xs text-gray-500 dark:text-gray-400">Segments</div>
                </div>
              </div>
            </div>

            {/* Database Management */}
            <div className="border-t border-gray-200 dark:border-gray-700 pt-4 mt-2">
              <h3 className="text-sm font-medium text-gray-900 dark:text-gray-100 mb-3">Database Management</h3>

              {/* Selective Clear */}
              <div className="p-4 border border-gray-200 dark:border-gray-700 rounded-lg bg-gray-50 dark:bg-gray-900/50 mb-4">
                <h4 className="font-medium text-gray-900 dark:text-gray-100 mb-2">Clear Selected Data</h4>
                <p className="text-sm text-gray-500 dark:text-gray-400 mb-4">
                  Select categories to clear. This allows granular control over what data to delete.
                </p>

                <div className="space-y-2 mb-4">
                  {categoryCounts.map((cat) => (
                    <label
                      key={cat.category}
                      className="flex items-center justify-between p-2 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800 cursor-pointer"
                    >
                      <div className="flex items-center gap-3">
                        <input
                          type="checkbox"
                          checked={selectedCategories.has(cat.category)}
                          onChange={(e) => {
                            const newSet = new Set(selectedCategories);
                            if (e.target.checked) {
                              newSet.add(cat.category);
                            } else {
                              newSet.delete(cat.category);
                            }
                            setSelectedCategories(newSet);
                          }}
                          className="h-4 w-4 rounded border-gray-300 dark:border-gray-600 text-primary focus:ring-primary"
                        />
                        <div>
                          <span className="text-sm font-medium text-gray-700 dark:text-gray-300">{cat.label}</span>
                          <p className="text-xs text-gray-500 dark:text-gray-400">{cat.description}</p>
                        </div>
                      </div>
                      <span className="text-sm font-medium text-gray-500 dark:text-gray-400 tabular-nums">
                        {cat.count.toLocaleString()}
                      </span>
                    </label>
                  ))}
                </div>

                <div className="flex items-center gap-2">
                  <button
                    onClick={() => {
                      if (selectedCategories.size === categoryCounts.length) {
                        setSelectedCategories(new Set());
                      } else {
                        setSelectedCategories(new Set(categoryCounts.map((c) => c.category)));
                      }
                    }}
                    className="px-3 py-1.5 text-xs font-medium text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-gray-200 border border-gray-300 dark:border-gray-600 rounded-lg"
                  >
                    {selectedCategories.size === categoryCounts.length ? 'Deselect All' : 'Select All'}
                  </button>
                  <button
                    onClick={() => {
                      setShowClearDialog(true);
                      setClearConfirmText('');
                      setClearResult(null);
                    }}
                    disabled={selectedCategories.size === 0}
                    className="px-3 py-1.5 text-xs font-medium bg-amber-600 text-white rounded-lg hover:bg-amber-700 disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    Clear Selected ({selectedCategories.size})
                  </button>
                </div>
              </div>

              {/* Full Reset - Danger Zone */}
              <div className="p-4 border border-red-200 dark:border-red-800/50 rounded-lg bg-red-50 dark:bg-red-900/10">
                <div className="flex items-start justify-between gap-4">
                  <div className="flex-1">
                    <h4 className="font-medium text-red-700 dark:text-red-300">Full Database Reset</h4>
                    <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">
                      Delete all recordings, transcripts, projects, and conversations. This cannot be undone.
                    </p>
                  </div>
                  <button
                    onClick={() => {
                      setShowResetDialog(true);
                      setResetConfirmText('');
                      setDeleteMediaToo(false);
                      setResetResult(null);
                    }}
                    className="shrink-0 px-4 py-2 bg-red-600 text-white text-sm font-medium rounded-lg hover:bg-red-700 transition-colors"
                  >
                    Reset All
                  </button>
                </div>
              </div>
            </div>
          </div>
        </div>
        </>
      )}

      {/* Plugin settings tabs */}
      {pluginSettingsTabs?.some((t) => t.id === activeTab) && (
        <div className="h-[calc(100vh-16rem)]">
          <iframe
            src={getApiUrl(`/plugins/settings/${activeTab}`)}
            className="w-full h-full border-0 rounded-lg"
            title={`Plugin settings: ${activeTab}`}
            sandbox="allow-scripts allow-same-origin allow-forms"
          />
        </div>
      )}

      {/* Migration Dialog */}
      {showMigrationDialog && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
          <div className="bg-white dark:bg-gray-800 rounded-lg shadow-xl max-w-md w-full mx-4 p-6">
            <h3 className="text-lg font-semibold text-gray-900 dark:text-gray-100 mb-4">
              Migrate Files to New Location?
            </h3>

            {!migrationStatus && (
              <>
                <p className="text-sm text-gray-600 dark:text-gray-400 mb-4">
                  You're changing the storage location. Would you like to move existing files to the new location?
                </p>

                <div className="text-xs text-gray-500 dark:text-gray-400 mb-4 space-y-1">
                  <div><span className="font-medium">From:</span> {migrationSource}</div>
                  <div><span className="font-medium">To:</span> {migrationDest}</div>
                </div>

                <div className="flex gap-3">
                  <button
                    onClick={handleStartMigration}
                    className="flex-1 px-4 py-2 bg-primary text-primary-foreground rounded-lg hover:bg-primary/90 text-sm font-medium"
                  >
                    Move Files
                  </button>
                  <button
                    onClick={handleSkipMigration}
                    className="flex-1 px-4 py-2 border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-700 text-sm font-medium"
                  >
                    Don't Move
                  </button>
                  <button
                    onClick={handleCancelMigration}
                    className="px-4 py-2 text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 text-sm"
                  >
                    Cancel
                  </button>
                </div>
              </>
            )}

            {migrationStatus?.status === 'running' && (
              <>
                <div className="mb-4">
                  <div className="flex justify-between text-sm text-gray-600 dark:text-gray-400 mb-2">
                    <span>Migrating files...</span>
                    <span>{migrationStatus.migrated_files} / {migrationStatus.total_files}</span>
                  </div>
                  <div className="w-full h-2 bg-gray-200 dark:bg-gray-700 rounded-full overflow-hidden">
                    <div
                      className="h-full bg-blue-500 transition-all"
                      style={{
                        width: migrationStatus.total_files > 0
                          ? `${(migrationStatus.migrated_files / migrationStatus.total_files) * 100}%`
                          : '0%'
                      }}
                    />
                  </div>
                  {migrationStatus.current_file && (
                    <p className="text-xs text-gray-500 dark:text-gray-400 mt-2 truncate">
                      {migrationStatus.current_file}
                    </p>
                  )}
                </div>
              </>
            )}

            {migrationStatus?.status === 'completed' && (
              <div className="text-center py-4">
                <svg className="w-12 h-12 text-green-500 mx-auto mb-2" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                </svg>
                <p className="text-gray-600 dark:text-gray-400">
                  Migration complete! {migrationStatus.migrated_files} files moved.
                </p>
              </div>
            )}

            {migrationStatus?.status === 'failed' && (
              <div className="text-center py-4">
                <svg className="w-12 h-12 text-red-500 mx-auto mb-2" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
                <p className="text-red-600 dark:text-red-400 mb-2">
                  Migration failed
                </p>
                <p className="text-sm text-gray-500 dark:text-gray-400 mb-4">
                  {migrationStatus.error}
                </p>
                <button
                  onClick={handleCancelMigration}
                  className="px-4 py-2 border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-700 text-sm"
                >
                  Close
                </button>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Cross-Location Transfer Dialog */}
      {showTransferDialog && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
          <div className="bg-white dark:bg-gray-800 rounded-lg shadow-xl max-w-md w-full mx-4 p-6">
            <h3 className="text-lg font-semibold text-gray-900 dark:text-gray-100 mb-4">
              Transfer Files to New Location?
            </h3>

            {!transferStatus && transferFileCount && (
              <>
                <p className="text-sm text-gray-600 dark:text-gray-400 mb-4">
                  Your current default location has{' '}
                  <span className="font-medium">
                    {transferFileCount.recordings} recording{transferFileCount.recordings !== 1 ? 's' : ''}
                  </span>
                  {transferFileCount.documents > 0 && (
                    <>
                      {' '}and{' '}
                      <span className="font-medium">
                        {transferFileCount.documents} document{transferFileCount.documents !== 1 ? 's' : ''}
                      </span>
                    </>
                  )}
                  . Would you like to transfer them?
                </p>

                <div className="text-xs text-gray-500 dark:text-gray-400 mb-4 space-y-1">
                  <div><span className="font-medium">From:</span> {transferFromLocation?.name}</div>
                  <div><span className="font-medium">To:</span> {storageLocations.find(l => l.id === transferToLocationId)?.name}</div>
                </div>

                <div className="flex gap-2">
                  <button
                    onClick={() => handleTransferFiles('copy')}
                    className="flex-1 px-4 py-2 bg-primary text-primary-foreground rounded-lg hover:bg-primary/90 text-sm font-medium"
                  >
                    Copy Files
                  </button>
                  <button
                    onClick={() => handleTransferFiles('move')}
                    className="flex-1 px-4 py-2 bg-orange-500 text-white rounded-lg hover:bg-orange-600 text-sm font-medium"
                  >
                    Move Files
                  </button>
                  <button
                    onClick={handleTransferSkip}
                    className="flex-1 px-4 py-2 border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-700 text-sm font-medium"
                  >
                    Just Switch
                  </button>
                </div>
              </>
            )}

            {transferStatus?.status === 'running' && (
              <>
                <div className="mb-4">
                  <div className="flex justify-between text-sm text-gray-600 dark:text-gray-400 mb-2">
                    <span>Transferring files...</span>
                    <span>{transferStatus.transferred_files} / {transferStatus.total_files}</span>
                  </div>
                  <div className="w-full h-2 bg-gray-200 dark:bg-gray-700 rounded-full overflow-hidden">
                    <div
                      className="h-full bg-blue-500 transition-all"
                      style={{
                        width: transferStatus.total_files > 0
                          ? `${(transferStatus.transferred_files / transferStatus.total_files) * 100}%`
                          : '0%'
                      }}
                    />
                  </div>
                  {transferStatus.current_file && (
                    <p className="text-xs text-gray-500 dark:text-gray-400 mt-2 truncate">
                      {transferStatus.current_file}
                    </p>
                  )}
                </div>
              </>
            )}

            {transferStatus?.status === 'completed' && (
              <div className="text-center py-4">
                <svg className="w-12 h-12 text-green-500 mx-auto mb-2" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                </svg>
                <p className="text-gray-600 dark:text-gray-400">
                  Transfer complete! {transferStatus.transferred_files} files transferred.
                </p>
              </div>
            )}

            {transferStatus?.status === 'failed' && (
              <div className="text-center py-4">
                <svg className="w-12 h-12 text-red-500 mx-auto mb-2" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
                <p className="text-red-600 dark:text-red-400 mb-2">
                  Transfer failed
                </p>
                <p className="text-sm text-gray-500 dark:text-gray-400 mb-4">
                  {transferStatus.error}
                </p>
                <button
                  onClick={handleTransferCancel}
                  className="px-4 py-2 border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-700 text-sm"
                >
                  Close
                </button>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Reset Database Dialog */}
      {showResetDialog && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
          <div className="bg-white dark:bg-gray-800 rounded-lg shadow-xl max-w-md w-full mx-4 p-6">
            {!resetResult ? (
              <>
                <div className="flex items-center gap-3 mb-4">
                  <div className="w-10 h-10 rounded-full bg-red-100 dark:bg-red-900/30 flex items-center justify-center">
                    <svg className="w-5 h-5 text-red-600 dark:text-red-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
                    </svg>
                  </div>
                  <h3 className="text-lg font-semibold text-gray-900 dark:text-gray-100">
                    Reset Database
                  </h3>
                </div>

                <p className="text-sm text-gray-600 dark:text-gray-400 mb-4">
                  This will permanently delete all your data:
                </p>

                <ul className="text-sm text-gray-500 dark:text-gray-400 mb-4 space-y-1 list-disc list-inside">
                  <li>{systemInfo?.content_counts.recordings || 0} recordings</li>
                  <li>{systemInfo?.content_counts.transcripts || 0} transcripts</li>
                  <li>All projects, tags, and conversations</li>
                  <li>All search embeddings</li>
                </ul>

                <label className="flex items-center gap-2 mb-4 text-sm">
                  <input
                    type="checkbox"
                    checked={deleteMediaToo}
                    onChange={(e) => setDeleteMediaToo(e.target.checked)}
                    className="rounded border-gray-300 dark:border-gray-600"
                  />
                  <span className="text-gray-700 dark:text-gray-300">
                    Also delete media files ({systemInfo?.storage_breakdown.media_count || 0} files)
                  </span>
                </label>

                <div className="mb-4">
                  <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                    Type <span className="font-mono text-red-600 dark:text-red-400">RESET</span> to confirm
                  </label>
                  <input
                    type="text"
                    value={resetConfirmText}
                    onChange={(e) => setResetConfirmText(e.target.value)}
                    placeholder="RESET"
                    className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 text-sm"
                  />
                </div>

                <div className="flex gap-3">
                  <button
                    onClick={async () => {
                      setIsResetting(true);
                      try {
                        const result = await api.system.resetDatabase(deleteMediaToo);
                        setResetResult({
                          success: result.success,
                          message: result.message,
                          deleted: result.deleted,
                        });
                        if (result.success) {
                          // Refresh system info
                          api.system.info().then(setSystemInfo).catch(console.error);
                        }
                      } catch (err) {
                        setResetResult({
                          success: false,
                          message: err instanceof Error ? err.message : 'Reset failed',
                        });
                      } finally {
                        setIsResetting(false);
                      }
                    }}
                    disabled={resetConfirmText !== 'RESET' || isResetting}
                    className="flex-1 px-4 py-2 bg-red-600 text-white rounded-lg hover:bg-red-700 disabled:opacity-50 disabled:cursor-not-allowed text-sm font-medium transition-colors"
                  >
                    {isResetting ? 'Resetting...' : 'Reset Database'}
                  </button>
                  <button
                    onClick={() => setShowResetDialog(false)}
                    disabled={isResetting}
                    className="px-4 py-2 border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-700 text-sm font-medium"
                  >
                    Cancel
                  </button>
                </div>
              </>
            ) : (
              <div className="text-center">
                {resetResult.success ? (
                  <>
                    <div className="w-12 h-12 rounded-full bg-green-100 dark:bg-green-900/30 flex items-center justify-center mx-auto mb-4">
                      <svg className="w-6 h-6 text-green-600 dark:text-green-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                      </svg>
                    </div>
                    <h3 className="text-lg font-semibold text-gray-900 dark:text-gray-100 mb-2">
                      Database Reset Complete
                    </h3>
                    <p className="text-sm text-gray-500 dark:text-gray-400 mb-4">
                      {resetResult.message}
                    </p>
                  </>
                ) : (
                  <>
                    <div className="w-12 h-12 rounded-full bg-red-100 dark:bg-red-900/30 flex items-center justify-center mx-auto mb-4">
                      <svg className="w-6 h-6 text-red-600 dark:text-red-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                      </svg>
                    </div>
                    <h3 className="text-lg font-semibold text-gray-900 dark:text-gray-100 mb-2">
                      Reset Failed
                    </h3>
                    <p className="text-sm text-red-600 dark:text-red-400 mb-4">
                      {resetResult.message}
                    </p>
                  </>
                )}
                <button
                  onClick={() => {
                    setShowResetDialog(false);
                    setResetResult(null);
                  }}
                  className="px-4 py-2 border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-700 text-sm font-medium"
                >
                  Close
                </button>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Clear Selected Dialog */}
      {showClearDialog && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
          <div className="bg-white dark:bg-gray-800 rounded-lg shadow-xl max-w-md w-full mx-4 p-6">
            {!clearResult ? (
              <>
                <div className="flex items-center gap-3 mb-4">
                  <div className="w-10 h-10 rounded-full bg-amber-100 dark:bg-amber-900/30 flex items-center justify-center">
                    <svg className="w-5 h-5 text-amber-600 dark:text-amber-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                    </svg>
                  </div>
                  <h3 className="text-lg font-semibold text-gray-900 dark:text-gray-100">
                    Clear Selected Data
                  </h3>
                </div>

                <p className="text-sm text-gray-600 dark:text-gray-400 mb-4">
                  You are about to clear the following categories:
                </p>

                <ul className="text-sm text-gray-500 dark:text-gray-400 mb-4 space-y-1 list-disc list-inside">
                  {Array.from(selectedCategories).map((cat) => {
                    const catInfo = categoryCounts.find((c) => c.category === cat);
                    return (
                      <li key={cat}>
                        {catInfo?.label || cat} ({catInfo?.count.toLocaleString() || 0} items)
                      </li>
                    );
                  })}
                </ul>

                <div className="mb-4">
                  <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                    Type <span className="font-mono text-amber-600 dark:text-amber-400">CLEAR</span> to confirm
                  </label>
                  <input
                    type="text"
                    value={clearConfirmText}
                    onChange={(e) => setClearConfirmText(e.target.value)}
                    placeholder="CLEAR"
                    className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 text-sm"
                  />
                </div>

                <div className="flex gap-3">
                  <button
                    onClick={async () => {
                      setIsClearing(true);
                      try {
                        const result = await api.system.clearSelective(Array.from(selectedCategories));
                        setClearResult({
                          success: result.success,
                          message: result.message,
                          deleted: result.deleted,
                        });
                        if (result.success) {
                          // Refresh system info and category counts
                          api.system.info().then(setSystemInfo).catch(console.error);
                          api.system.getCategoryCounts().then((r) => setCategoryCounts(r.categories)).catch(console.error);
                          setSelectedCategories(new Set());
                        }
                      } catch (err) {
                        setClearResult({
                          success: false,
                          message: err instanceof Error ? err.message : 'Clear failed',
                        });
                      } finally {
                        setIsClearing(false);
                      }
                    }}
                    disabled={clearConfirmText !== 'CLEAR' || isClearing}
                    className="flex-1 px-4 py-2 bg-amber-600 text-white rounded-lg hover:bg-amber-700 disabled:opacity-50 disabled:cursor-not-allowed text-sm font-medium transition-colors"
                  >
                    {isClearing ? 'Clearing...' : 'Clear Data'}
                  </button>
                  <button
                    onClick={() => setShowClearDialog(false)}
                    disabled={isClearing}
                    className="px-4 py-2 border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-700 text-sm font-medium"
                  >
                    Cancel
                  </button>
                </div>
              </>
            ) : (
              <div className="text-center">
                {clearResult.success ? (
                  <>
                    <div className="w-12 h-12 rounded-full bg-green-100 dark:bg-green-900/30 flex items-center justify-center mx-auto mb-4">
                      <svg className="w-6 h-6 text-green-600 dark:text-green-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                      </svg>
                    </div>
                    <h3 className="text-lg font-semibold text-gray-900 dark:text-gray-100 mb-2">
                      Data Cleared
                    </h3>
                    <p className="text-sm text-gray-500 dark:text-gray-400 mb-4">
                      {clearResult.message}
                    </p>
                  </>
                ) : (
                  <>
                    <div className="w-12 h-12 rounded-full bg-red-100 dark:bg-red-900/30 flex items-center justify-center mx-auto mb-4">
                      <svg className="w-6 h-6 text-red-600 dark:text-red-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                      </svg>
                    </div>
                    <h3 className="text-lg font-semibold text-gray-900 dark:text-gray-100 mb-2">
                      Clear Failed
                    </h3>
                    <p className="text-sm text-red-600 dark:text-red-400 mb-4">
                      {clearResult.message}
                    </p>
                  </>
                )}
                <button
                  onClick={() => {
                    setShowClearDialog(false);
                    setClearResult(null);
                  }}
                  className="px-4 py-2 border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-700 text-sm font-medium"
                >
                  Close
                </button>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

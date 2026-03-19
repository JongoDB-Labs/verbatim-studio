import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { api, type Recording, type Tag, type Project } from '@/lib/api';
import { useRecordings, useDeleteRecording, useBulkDeleteRecordings, useTranscribeRecording, useCancelRecording, useRetryRecording, useRunningJobs } from '@/hooks';
import { queryKeys, type RecordingFilters as RecordingQueryFilters } from '@/lib/queryKeys';
import { UploadDropzone } from '@/components/recordings/UploadDropzone';
import { RecordingCard } from '@/components/recordings/RecordingCard';
import { RecordingsTable } from '@/components/recordings/RecordingsTable';
import { RecordingFilters, type FilterState, type ViewMode } from '@/components/recordings/RecordingFilters';
import { TranscribeDialog } from '@/components/recordings/TranscribeDialog';
import { EditRecordingDialog } from '@/components/recordings/EditRecordingDialog';
import { BulkActionBar } from '@/components/recordings/BulkActionBar';
import { AudioRecorder } from '@/components/recordings/AudioRecorder';
import { RecordingSetupPanel, type RecordingSettings } from '@/components/recordings/RecordingSetupPanel';
import { ProjectSelector } from '@/components/projects/ProjectSelector';
import { useProjectStore } from '@/stores/projectStore';
import { RecordingTemplateManager } from '@/components/recordings/RecordingTemplateManager';
import { UploadSetupDialog, type UploadOptions } from '@/components/recordings/UploadSetupDialog';

interface RecordingsPageProps {
  onViewTranscript: (recordingId: string) => void;
}

const STORAGE_KEY = 'verbatim-recording-filters';
const VIEW_MODE_KEY = 'verbatim-view-mode';

const DEFAULT_FILTERS: FilterState = {
  search: '',
  status: '',
  sortBy: 'created_at',
  sortOrder: 'desc',
  dateFrom: '',
  dateTo: '',
  tagIds: [],
  speaker: '',
  templateId: '',
};

function filtersFromUrlParams(): Partial<FilterState> {
  const params = new URLSearchParams(window.location.search);
  const result: Partial<FilterState> = {};

  if (params.has('search')) result.search = params.get('search')!;
  if (params.has('status')) result.status = params.get('status')!;
  if (params.has('sortBy')) result.sortBy = params.get('sortBy') as FilterState['sortBy'];
  if (params.has('sortOrder')) result.sortOrder = params.get('sortOrder') as 'asc' | 'desc';
  if (params.has('dateFrom')) result.dateFrom = params.get('dateFrom')!;
  if (params.has('dateTo')) result.dateTo = params.get('dateTo')!;
  if (params.has('tagIds')) result.tagIds = params.get('tagIds')!.split(',').filter(Boolean);
  if (params.has('speaker')) result.speaker = params.get('speaker')!;
  if (params.has('templateId')) result.templateId = params.get('templateId')!;

  return result;
}

function filtersToUrlParams(filters: FilterState): string {
  const params = new URLSearchParams();

  if (filters.search) params.set('search', filters.search);
  if (filters.status) params.set('status', filters.status);
  if (filters.sortBy !== 'created_at') params.set('sortBy', filters.sortBy);
  if (filters.sortOrder !== 'desc') params.set('sortOrder', filters.sortOrder);
  if (filters.dateFrom) params.set('dateFrom', filters.dateFrom);
  if (filters.dateTo) params.set('dateTo', filters.dateTo);
  if (filters.tagIds.length > 0) params.set('tagIds', filters.tagIds.join(','));
  if (filters.speaker) params.set('speaker', filters.speaker);
  if (filters.templateId) params.set('templateId', filters.templateId);

  return params.toString();
}

function loadSavedFilters(): FilterState {
  // URL params take priority, then localStorage, then defaults
  const urlOverrides = filtersFromUrlParams();
  if (Object.keys(urlOverrides).length > 0) {
    return { ...DEFAULT_FILTERS, ...urlOverrides };
  }

  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved) {
      const parsed = JSON.parse(saved);
      return { ...DEFAULT_FILTERS, ...parsed };
    }
  } catch {
    // ignore
  }
  return DEFAULT_FILTERS;
}

function loadSavedViewMode(): ViewMode {
  try {
    const saved = localStorage.getItem(VIEW_MODE_KEY);
    if (saved === 'grid' || saved === 'list') return saved;
  } catch {
    // ignore
  }
  return 'list'; // List view is the default for better information density
}

export function RecordingsPage({ onViewTranscript }: RecordingsPageProps) {
  const queryClient = useQueryClient();
  const { activeProject } = useProjectStore();

  // Local UI state
  const [isUploading, setIsUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [filters, setFilters] = useState<FilterState>(loadSavedFilters);
  const [viewMode, setViewMode] = useState<ViewMode>(loadSavedViewMode);
  const [transcribeDialogRecording, setTranscribeDialogRecording] = useState<Recording | null>(null);
  const [editDialogRecording, setEditDialogRecording] = useState<Recording | null>(null);
  const [recordingPhase, setRecordingPhase] = useState<'none' | 'setup' | 'recording'>('none');
  const [recordingSettings, setRecordingSettings] = useState<RecordingSettings | null>(null);
  const [selectedProjectId, setSelectedProjectId] = useState<string | null>(null);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [templateManagerOpen, setTemplateManagerOpen] = useState(false);
  const [allTags, setAllTags] = useState<Tag[]>([]);
  const [allProjects, setAllProjects] = useState<Project[]>([]);
  const [pendingUploadFile, setPendingUploadFile] = useState<File | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Build query filters from local state
  const queryFilters: RecordingQueryFilters = useMemo(() => ({
    search: filters.search || undefined,
    status: filters.status || undefined,
    sortBy: filters.sortBy,
    sortOrder: filters.sortOrder,
    projectId: selectedProjectId || undefined,
    dateFrom: filters.dateFrom || undefined,
    dateTo: filters.dateTo || undefined,
    tagIds: filters.tagIds.length > 0 ? filters.tagIds : undefined,
    speaker: filters.speaker || undefined,
    templateId: filters.templateId || undefined,
  }), [filters, selectedProjectId]);

  // React Query hooks for data fetching
  const { data: recordingsData, isLoading, error: fetchError } = useRecordings(queryFilters);
  const recordings = recordingsData?.items ?? [];
  const totalRecordings = recordingsData?.total ?? 0;

  // React Query mutations
  const deleteRecording = useDeleteRecording();
  const bulkDeleteRecordings = useBulkDeleteRecordings();
  const transcribeRecording = useTranscribeRecording();
  const cancelRecording = useCancelRecording();
  const retryRecording = useRetryRecording();

  // Job progress from useRunningJobs hook
  const { data: jobsData } = useRunningJobs();
  const jobProgress = useMemo(() => {
    const progressMap: Record<string, number> = {};
    if (jobsData?.items) {
      for (const job of jobsData.items) {
        const recId = (job.payload as Record<string, unknown>)?.recording_id;
        if (typeof recId === 'string') {
          progressMap[recId] = job.progress;
        }
      }
    }
    return progressMap;
  }, [jobsData]);

  // Combine errors for display
  const error = uploadError || (fetchError instanceof Error ? fetchError.message : fetchError ? 'Failed to load recordings' : null);

  // Load tags and projects for display
  useEffect(() => {
    api.tags.list().then((res) => setAllTags(res.items)).catch(() => {});
    api.projects.list().then((res) => setAllProjects(res.items || [])).catch(() => {});
  }, []);

  // Sync filters to URL and localStorage
  useEffect(() => {
    const queryString = filtersToUrlParams(filters);
    const newUrl = queryString
      ? `${window.location.pathname}?${queryString}`
      : window.location.pathname;
    window.history.replaceState(null, '', newUrl);

    // Persist to localStorage (exclude search to avoid stale text)
    const { search: _, ...persistable } = filters;
    localStorage.setItem(STORAGE_KEY, JSON.stringify(persistable));
  }, [filters]);

  // Persist view mode
  useEffect(() => {
    localStorage.setItem(VIEW_MODE_KEY, viewMode);
  }, [viewMode]);

  const handleUpload = useCallback((file: File) => {
    // Open the setup dialog instead of uploading directly
    setPendingUploadFile(file);
  }, []);

  const handleUploadConfirm = useCallback(
    async (options: UploadOptions) => {
      if (!pendingUploadFile) return;

      setPendingUploadFile(null);
      setIsUploading(true);
      setUploadError(null);

      try {
        const result = await api.recordings.upload(pendingUploadFile, {
          title: options.title,
          templateId: options.templateId,
          metadata: options.metadata,
        });

        // Auto-transcribe if option is enabled
        if (options.autoTranscribe && result.id) {
          try {
            await api.recordings.transcribe(result.id, {
              autoGenerateSummary: options.autoGenerateSummary,
            });
          } catch {
            // Don't fail the upload if transcription fails to start
            console.error('Failed to start auto-transcription');
          }
        }
        // Explicitly refresh the list (WebSocket broadcast is also sent but may be delayed)
        queryClient.invalidateQueries({ queryKey: queryKeys.recordings.all });
        queryClient.invalidateQueries({ queryKey: queryKeys.dashboard.stats });
      } catch (err) {
        setUploadError(err instanceof Error ? err.message : 'Failed to upload file');
      } finally {
        setIsUploading(false);
      }
    },
    [pendingUploadFile]
  );

  const handleBatchImport = useCallback(
    async (files: FileList | File[]) => {
      setIsUploading(true);
      setUploadError(null);

      try {
        for (const file of Array.from(files)) {
          await api.recordings.upload(file);
        }
        queryClient.invalidateQueries({ queryKey: queryKeys.recordings.all });
        queryClient.invalidateQueries({ queryKey: queryKeys.dashboard.stats });
      } catch (err) {
        setUploadError(err instanceof Error ? err.message : 'Failed to import files');
      } finally {
        setIsUploading(false);
      }
    },
    [queryClient]
  );

  const handleOpenTranscribeDialog = useCallback((recording: Recording) => {
    setTranscribeDialogRecording(recording);
  }, []);

  const handleCloseTranscribeDialog = useCallback(() => {
    setTranscribeDialogRecording(null);
  }, []);

  const handleTranscribe = useCallback(
    (language: string | undefined) => {
      if (!transcribeDialogRecording) return;
      transcribeRecording.mutate({ id: transcribeDialogRecording.id, language });
    },
    [transcribeDialogRecording, transcribeRecording]
  );

  const handleDelete = useCallback(
    (recordingId: string) => {
      deleteRecording.mutate(recordingId);
    },
    [deleteRecording]
  );

  const handleCancel = useCallback(
    (recordingId: string) => {
      cancelRecording.mutate(recordingId);
    },
    [cancelRecording]
  );

  const handleRetry = useCallback(
    (recordingId: string) => {
      retryRecording.mutate(recordingId);
    },
    [retryRecording]
  );

  const handleView = useCallback((recordingId: string) => {
    onViewTranscript(recordingId);
  }, [onViewTranscript]);

  const handleRecordingComplete = useCallback(
    async (blob: Blob, filename: string) => {
      setRecordingPhase('none');
      setIsUploading(true);
      setUploadError(null);

      try {
        const file = new File([blob], filename, { type: blob.type });
        const meta = recordingSettings?.metadata;
        const result = await api.recordings.upload(file, {
          title: meta?.title || undefined,
          description: meta?.description || undefined,
          tags: meta?.tags?.length ? meta.tags : undefined,
          participants: meta?.participants?.length ? meta.participants : undefined,
          location: meta?.location || undefined,
          recordedDate: meta?.recordedDate || undefined,
          quality: recordingSettings?.quality,
        });

        // Auto-transcribe if option is enabled
        if (recordingSettings?.autoTranscribe && result.id) {
          try {
            await api.recordings.transcribe(result.id);
          } catch {
            console.error('Failed to start auto-transcription');
          }
        }
        queryClient.invalidateQueries({ queryKey: queryKeys.recordings.all });
        queryClient.invalidateQueries({ queryKey: queryKeys.dashboard.stats });
      } catch (err) {
        setUploadError(err instanceof Error ? err.message : 'Failed to upload recording');
      } finally {
        setIsUploading(false);
        setRecordingSettings(null);
      }
    },
    [recordingSettings]
  );

  const handleRecordingCancel = useCallback(() => {
    setRecordingPhase('none');
    setRecordingSettings(null);
  }, []);

  const handleFiltersChange = useCallback((newFilters: FilterState) => {
    setFilters(newFilters);
  }, []);

  const handleViewModeChange = useCallback((mode: ViewMode) => {
    setViewMode(mode);
  }, []);

  const handleSortChange = useCallback(
    (sortBy: 'created_at' | 'title' | 'duration', sortOrder: 'asc' | 'desc') => {
      setFilters(prev => ({ ...prev, sortBy, sortOrder }));
    },
    []
  );

  const handleEdit = useCallback((recording: Recording) => {
    setEditDialogRecording(recording);
  }, []);

  const handleEditSaved = useCallback(() => {
    // React Query will handle cache invalidation via WebSocket
  }, []);

  // Bulk selection handlers
  const handleSelectRecording = useCallback((id: string, selected: boolean) => {
    setSelectedIds(prev => {
      const next = new Set(prev);
      if (selected) {
        next.add(id);
      } else {
        next.delete(id);
      }
      return next;
    });
  }, []);

  const handleSelectAll = useCallback((selected: boolean) => {
    if (selected) {
      setSelectedIds(new Set(recordings.map(r => r.id)));
    } else {
      setSelectedIds(new Set());
    }
  }, [recordings]);

  const handleClearSelection = useCallback(() => {
    setSelectedIds(new Set());
  }, []);

  const handleBulkDelete = useCallback(() => {
    if (selectedIds.size === 0) return;
    bulkDeleteRecordings.mutate(Array.from(selectedIds), {
      onSuccess: () => {
        setSelectedIds(new Set());
      },
    });
  }, [selectedIds, bulkDeleteRecordings]);

  const handleLinkedToProjects = useCallback(() => {
    setSelectedIds(new Set());
    // React Query will handle cache invalidation via WebSocket
  }, []);

  // Build a map of recording ID to its project IDs for the bulk action bar
  const recordingProjectMap = recordings.reduce<Record<string, string[]>>((acc, r) => {
    acc[r.id] = r.project_ids || [];
    return acc;
  }, {});

  // Escape key to clear selection
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && selectedIds.size > 0) {
        setSelectedIds(new Set());
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [selectedIds.size]);

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-12">
        <svg
          className="h-8 w-8 animate-spin text-primary"
          xmlns="http://www.w3.org/2000/svg"
          fill="none"
          viewBox="0 0 24 24"
        >
          <circle
            className="opacity-25"
            cx="12"
            cy="12"
            r="10"
            stroke="currentColor"
            strokeWidth="4"
          />
          <path
            className="opacity-75"
            fill="currentColor"
            d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"
          />
        </svg>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center gap-3">
        <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100">Recordings</h1>
        {activeProject && (
          <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-xs bg-zinc-100 dark:bg-zinc-800 text-zinc-600 dark:text-zinc-400">
            {activeProject.color && (
              <span className="w-2 h-2 rounded-full" style={{ backgroundColor: activeProject.color }} />
            )}
            {activeProject.name}
          </span>
        )}
      </div>

      {/* Upload and Record Section */}
      <div className="grid gap-4 md:grid-cols-2">
        <UploadDropzone onUpload={handleUpload} onUploadMultiple={handleBatchImport} isUploading={isUploading} />

        {/* Record Button, Setup Panel, or Recorder */}
        {recordingPhase === 'recording' ? (
          <AudioRecorder
            onRecordingComplete={handleRecordingComplete}
            onCancel={handleRecordingCancel}
            audioBitsPerSecond={recordingSettings?.audioBitsPerSecond}
            autoStart
          />
        ) : recordingPhase === 'setup' ? (
          <RecordingSetupPanel
            onStartRecording={(settings) => {
              setRecordingSettings(settings);
              setRecordingPhase('recording');
            }}
            onCancel={handleRecordingCancel}
          />
        ) : (
          <button
            onClick={() => setRecordingPhase('setup')}
            disabled={isUploading}
            className="flex flex-col items-center justify-center gap-3 rounded-lg border-2 border-dashed border-gray-300 dark:border-gray-600 bg-gray-50 dark:bg-gray-800/50 p-8 hover:border-red-400 hover:bg-red-50 dark:hover:bg-red-900/10 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            <div className="w-16 h-16 rounded-full bg-red-100 dark:bg-red-900/30 flex items-center justify-center">
              <svg
                className="w-8 h-8 text-red-600 dark:text-red-400"
                xmlns="http://www.w3.org/2000/svg"
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
                strokeWidth="2"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  d="M19 11a7 7 0 01-7 7m0 0a7 7 0 01-7-7m7 7v4m0 0H8m4 0h4m-4-8a3 3 0 01-3-3V5a3 3 0 116 0v6a3 3 0 01-3 3z"
                />
              </svg>
            </div>
            <div className="text-center">
              <p className="text-sm font-medium text-gray-900 dark:text-gray-100">
                Record Audio
              </p>
              <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">
                Click to start recording from your microphone
              </p>
            </div>
          </button>
        )}
      </div>

      {error && (
        <div className="rounded-lg border border-destructive/50 bg-destructive/10 p-4">
          <p className="text-sm text-destructive">{error}</p>
        </div>
      )}

      {/* Project Selector, Filters, and Import */}
      <div className="flex flex-col gap-4">
        <div className="flex flex-col sm:flex-row gap-4 items-start sm:items-center">
          <ProjectSelector
            selectedProjectId={selectedProjectId}
            onProjectChange={setSelectedProjectId}
          />
          {/* Batch Import */}
          <input
            ref={fileInputRef}
            type="file"
            multiple
            accept="audio/*,video/*"
            className="hidden"
            onChange={(e) => {
              if (e.target.files && e.target.files.length > 0) {
                handleBatchImport(e.target.files);
                e.target.value = '';
              }
            }}
          />
          <button
            onClick={() => fileInputRef.current?.click()}
            disabled={isUploading}
            className="flex items-center gap-1.5 px-3 py-2 rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-sm font-medium text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors disabled:opacity-50"
          >
            <svg className="h-4 w-4" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
              <path strokeLinecap="round" strokeLinejoin="round" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12" />
            </svg>
            Import Files
          </button>
          <button
            onClick={() => setTemplateManagerOpen(true)}
            className="flex items-center gap-1.5 px-3 py-2 rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-sm font-medium text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors"
          >
            <svg className="h-4 w-4" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
              <path strokeLinecap="round" strokeLinejoin="round" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
            </svg>
            Templates
          </button>
        </div>
        <RecordingFilters
          filters={filters}
          onFiltersChange={handleFiltersChange}
          totalResults={totalRecordings}
          viewMode={viewMode}
          onViewModeChange={handleViewModeChange}
        />
      </div>

      {recordings.length === 0 ? (
        <div className="text-center py-12">
          <p className="text-muted-foreground">
            {filters.search || filters.status || filters.dateFrom || filters.dateTo || filters.tagIds.length > 0 || filters.speaker
              ? 'No recordings match your filters.'
              : 'No recordings yet. Upload a file to get started.'}
          </p>
        </div>
      ) : viewMode === 'grid' ? (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {recordings.map((recording) => (
            <RecordingCard
              key={recording.id}
              recording={recording}
              onTranscribe={() => handleOpenTranscribeDialog(recording)}
              onDelete={() => handleDelete(recording.id)}
              onView={() => handleView(recording.id)}
              onCancel={() => handleCancel(recording.id)}
              onRetry={() => handleRetry(recording.id)}
              onEdit={() => handleEdit(recording)}
              progress={jobProgress[recording.id]}
              isSelected={selectedIds.has(recording.id)}
              onSelectChange={(selected) => handleSelectRecording(recording.id, selected)}
              allTags={allTags}
              allProjects={allProjects}
            />
          ))}
        </div>
      ) : (
        <RecordingsTable
          recordings={recordings}
          sortBy={filters.sortBy}
          sortOrder={filters.sortOrder}
          onSortChange={handleSortChange}
          onTranscribe={handleOpenTranscribeDialog}
          onDelete={(id) => handleDelete(id)}
          onView={(id) => handleView(id)}
          onCancel={(id) => handleCancel(id)}
          onRetry={(id) => handleRetry(id)}
          onEdit={handleEdit}
          jobProgress={jobProgress}
          selectedIds={selectedIds}
          onSelectRecording={handleSelectRecording}
          onSelectAll={handleSelectAll}
          allTags={allTags}
          allProjects={allProjects}
        />
      )}

      {/* Transcribe Dialog */}
      <TranscribeDialog
        isOpen={transcribeDialogRecording !== null}
        onClose={handleCloseTranscribeDialog}
        onTranscribe={handleTranscribe}
        recordingTitle={transcribeDialogRecording?.title ?? ''}
      />

      {/* Edit Recording Dialog */}
      <EditRecordingDialog
        isOpen={editDialogRecording !== null}
        recording={editDialogRecording}
        onClose={() => setEditDialogRecording(null)}
        onSaved={handleEditSaved}
      />

      {/* Upload Setup Dialog */}
      <UploadSetupDialog
        isOpen={pendingUploadFile !== null}
        file={pendingUploadFile}
        onClose={() => setPendingUploadFile(null)}
        onConfirm={handleUploadConfirm}
      />

      {/* Bulk Action Bar */}
      <BulkActionBar
        selectedCount={selectedIds.size}
        selectedIds={selectedIds}
        recordingProjectMap={recordingProjectMap}
        onDelete={handleBulkDelete}
        onClearSelection={handleClearSelection}
        onLinked={handleLinkedToProjects}
      />

      {/* Recording Template Manager */}
      <RecordingTemplateManager
        open={templateManagerOpen}
        onOpenChange={setTemplateManagerOpen}
      />
    </div>
  );
}

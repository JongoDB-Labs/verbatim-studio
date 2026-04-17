import { useState, useMemo } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api, type Project, type Recording, type Document } from '@/lib/api';
import { queryKeys } from '@/lib/queryKeys';
import { formatDuration } from '@/lib/utils';

function formatMimeType(mime: string): string {
  const map: Record<string, string> = {
    'application/pdf': 'PDF',
    'text/plain': 'Text',
    'text/markdown': 'Markdown',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document': 'DOCX',
    'application/msword': 'DOC',
    'image/png': 'PNG',
    'image/jpeg': 'JPEG',
  };
  return map[mime] || mime.split('/').pop()?.toUpperCase() || mime;
}

function formatTimeAgo(dateStr: string | null | undefined): string {
  if (!dateStr) return 'Unknown';
  const date = new Date(dateStr);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24));

  if (diffDays === 0) return 'Today';
  if (diffDays === 1) return 'Yesterday';
  if (diffDays < 30) return `${diffDays} days ago`;
  if (diffDays < 60) return '1 month ago';
  return `${Math.floor(diffDays / 30)} months ago`;
}

interface CollapsibleSectionProps {
  title: string;
  count: number;
  defaultOpen?: boolean;
  children: React.ReactNode;
}

function CollapsibleSection({ title, count, defaultOpen = true, children }: CollapsibleSectionProps) {
  const [isOpen, setIsOpen] = useState(defaultOpen);

  return (
    <div className="border border-border rounded-lg overflow-hidden">
      <button
        onClick={() => setIsOpen(!isOpen)}
        className="w-full flex items-center justify-between px-4 py-3 bg-muted/50 hover:bg-muted transition-colors text-left"
      >
        <span className="text-sm font-semibold text-foreground">
          {title} ({count})
        </span>
        <svg
          className={`w-4 h-4 text-muted-foreground transition-transform ${isOpen ? 'rotate-180' : ''}`}
          xmlns="http://www.w3.org/2000/svg"
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
          strokeWidth="2"
        >
          <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
        </svg>
      </button>
      {isOpen && (
        <div className="divide-y divide-border">
          {children}
        </div>
      )}
    </div>
  );
}

export function ArchivePage() {
  const queryClient = useQueryClient();

  // Fetch archived projects
  const { data: projectsData, isLoading: projectsLoading } = useQuery({
    queryKey: queryKeys.projects.list({ includeArchived: true }),
    queryFn: () => api.projects.list({ include_archived: true }),
  });

  // Fetch archived recordings
  const { data: recordingsData, isLoading: recordingsLoading } = useQuery({
    queryKey: queryKeys.recordings.archived(),
    queryFn: () => api.recordings.listArchived(),
  });

  // Fetch archived documents
  const { data: documentsData, isLoading: documentsLoading } = useQuery({
    queryKey: queryKeys.documents.archived(),
    queryFn: () => api.documents.listArchived(),
  });

  // Fetch trash settings for auto-purge info
  const { data: trashSettings } = useQuery({
    queryKey: queryKeys.trash.settings,
    queryFn: () => api.config.getTrashSettings(),
  });

  // Filter to only archived projects
  const archivedProjects = (projectsData?.items ?? []).filter(p => p.is_archived);

  // Build a map from project id -> project for display
  const projectMap = useMemo(() => {
    const map = new Map<string, Project>();
    for (const p of projectsData?.items ?? []) {
      map.set(p.id, p);
    }
    return map;
  }, [projectsData]);

  // Track loading states
  const [actionId, setActionId] = useState<string | null>(null);
  const [confirmPermanentId, setConfirmPermanentId] = useState<string | null>(null);
  const [showEmptyConfirm, setShowEmptyConfirm] = useState(false);

  const invalidateAll = () => {
    queryClient.invalidateQueries({ queryKey: queryKeys.recordings.all });
    queryClient.invalidateQueries({ queryKey: queryKeys.recordings.archived() });
    queryClient.invalidateQueries({ queryKey: queryKeys.documents.all });
    queryClient.invalidateQueries({ queryKey: queryKeys.documents.archived() });
    queryClient.invalidateQueries({ queryKey: queryKeys.projects.all });
    queryClient.invalidateQueries({ queryKey: queryKeys.dashboard.stats });
  };

  // Restore mutations
  const restoreProject = useMutation({
    mutationFn: (id: string) => { setActionId(id); return api.projects.unarchive(id); },
    onSettled: () => setActionId(null),
    onSuccess: invalidateAll,
  });

  const restoreRecording = useMutation({
    mutationFn: (id: string) => { setActionId(id); return api.recordings.unarchive(id); },
    onSettled: () => setActionId(null),
    onSuccess: invalidateAll,
  });

  const restoreDocument = useMutation({
    mutationFn: (id: string) => { setActionId(id); return api.documents.unarchive(id); },
    onSettled: () => setActionId(null),
    onSuccess: invalidateAll,
  });

  // Permanent delete mutations
  const permanentDeleteRecording = useMutation({
    mutationFn: (id: string) => { setActionId(id); return api.recordings.permanentDelete(id); },
    onSettled: () => { setActionId(null); setConfirmPermanentId(null); },
    onSuccess: invalidateAll,
  });

  const permanentDeleteDocument = useMutation({
    mutationFn: (id: string) => { setActionId(id); return api.documents.permanentDelete(id); },
    onSettled: () => { setActionId(null); setConfirmPermanentId(null); },
    onSuccess: invalidateAll,
  });

  const permanentDeleteProject = useMutation({
    mutationFn: (id: string) => { setActionId(id); return api.projects.permanentDelete(id); },
    onSettled: () => { setActionId(null); setConfirmPermanentId(null); },
    onSuccess: invalidateAll,
  });

  // Empty trash mutation
  const emptyTrash = useMutation({
    mutationFn: () => api.config.emptyTrash(),
    onSettled: () => setShowEmptyConfirm(false),
    onSuccess: invalidateAll,
  });

  const isLoading = projectsLoading || recordingsLoading || documentsLoading;
  const archivedRecordings = recordingsData?.items ?? [];
  const archivedDocuments = documentsData?.items ?? [];
  const totalItems = archivedProjects.length + archivedRecordings.length + archivedDocuments.length;
  const isEmpty = totalItems === 0;

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-20">
        <svg
          className="h-8 w-8 animate-spin text-muted-foreground"
          xmlns="http://www.w3.org/2000/svg"
          fill="none"
          viewBox="0 0 24 24"
        >
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
          <path
            className="opacity-75"
            fill="currentColor"
            d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"
          />
        </svg>
      </div>
    );
  }

  function getProjectName(projectIds: string[]): string {
    if (!projectIds || projectIds.length === 0) return 'No project';
    const names = projectIds
      .map(id => projectMap.get(id)?.name)
      .filter(Boolean);
    return names.length > 0 ? names.join(', ') : 'No project';
  }

  const autoPurgeDays = trashSettings?.auto_purge_days ?? 30;

  if (isEmpty) {
    return (
      <div className="space-y-2">
        <h1 className="text-2xl font-bold text-foreground">Trash</h1>
        <p className="text-sm text-muted-foreground">Deleted items can be restored or permanently removed.</p>

        <div className="flex flex-col items-center justify-center py-20 text-center">
          <div className="rounded-full bg-muted p-4 mb-4">
            <svg className="w-8 h-8 text-muted-foreground" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
              <path strokeLinecap="round" strokeLinejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
            </svg>
          </div>
          <h2 className="text-lg font-semibold text-foreground mb-1">Trash is empty</h2>
          <p className="text-sm text-muted-foreground max-w-sm">
            When you delete projects, recordings, or documents, they will appear here. You can restore them or permanently remove them.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div className="space-y-1">
          <h1 className="text-2xl font-bold text-foreground">Trash</h1>
          <p className="text-sm text-muted-foreground">Deleted items can be restored or permanently removed.</p>
        </div>

        {/* Empty Trash button */}
        {!showEmptyConfirm ? (
          <button
            onClick={() => setShowEmptyConfirm(true)}
            className="shrink-0 px-3 py-1.5 text-sm font-medium rounded-lg bg-red-500/10 text-red-600 dark:text-red-400 hover:bg-red-500/20 border border-red-500/20 transition-colors"
          >
            Empty Trash
          </button>
        ) : (
          <div className="shrink-0 flex items-center gap-2">
            <span className="text-sm text-red-600 dark:text-red-400 font-medium">
              Delete all {totalItems} items forever?
            </span>
            <button
              onClick={() => emptyTrash.mutate()}
              disabled={emptyTrash.isPending}
              className="px-3 py-1.5 text-sm font-medium rounded-lg bg-red-600 text-white hover:bg-red-700 transition-colors disabled:opacity-50"
            >
              {emptyTrash.isPending ? 'Deleting...' : 'Confirm'}
            </button>
            <button
              onClick={() => setShowEmptyConfirm(false)}
              className="px-3 py-1.5 text-sm font-medium rounded-lg border border-border text-foreground hover:bg-muted transition-colors"
            >
              Cancel
            </button>
          </div>
        )}
      </div>

      {/* Auto-purge warning */}
      {autoPurgeDays > 0 && (
        <div className="flex items-center gap-2 px-3 py-2 rounded-lg bg-amber-500/10 border border-amber-500/20 text-sm text-amber-700 dark:text-amber-400">
          <svg className="w-4 h-4 shrink-0" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
            <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
          </svg>
          Items are automatically removed after {autoPurgeDays} days. You can change this in Settings.
        </div>
      )}

      {/* Trashed Projects */}
      {archivedProjects.length > 0 && (
        <CollapsibleSection title="Projects" count={archivedProjects.length}>
          {archivedProjects.map((project: Project) => (
            <div key={project.id} className="flex items-center justify-between px-4 py-3 bg-card hover:bg-muted/30 transition-colors">
              <div className="flex items-center gap-3 min-w-0">
                {project.color && (
                  <span
                    className="w-3 h-3 rounded-full shrink-0"
                    style={{ backgroundColor: project.color }}
                  />
                )}
                <div className="min-w-0">
                  <p className="text-sm font-medium text-foreground truncate">{project.name}</p>
                  <p className="text-xs text-muted-foreground">
                    {project.recording_count} recording{project.recording_count !== 1 ? 's' : ''}, {project.document_count} document{project.document_count !== 1 ? 's' : ''}
                    {(project as any).deleted_at && (
                      <> &middot; Deleted {formatTimeAgo((project as any).deleted_at)}</>
                    )}
                  </p>
                </div>
              </div>
              <div className="shrink-0 ml-4 flex items-center gap-2">
                <button
                  onClick={() => restoreProject.mutate(project.id)}
                  disabled={actionId === project.id}
                  className="px-3 py-1.5 text-sm font-medium rounded-lg border border-border text-foreground hover:bg-muted transition-colors disabled:opacity-50"
                >
                  {actionId === project.id ? 'Restoring...' : 'Restore'}
                </button>
                {confirmPermanentId === project.id ? (
                  <div className="flex items-center gap-1">
                    <button
                      onClick={() => permanentDeleteProject.mutate(project.id)}
                      disabled={actionId === project.id}
                      className="px-2 py-1.5 text-xs font-medium rounded-lg bg-red-600 text-white hover:bg-red-700 transition-colors disabled:opacity-50"
                    >
                      Confirm
                    </button>
                    <button
                      onClick={() => setConfirmPermanentId(null)}
                      className="px-2 py-1.5 text-xs font-medium rounded-lg border border-border text-foreground hover:bg-muted transition-colors"
                    >
                      Cancel
                    </button>
                  </div>
                ) : (
                  <button
                    onClick={() => setConfirmPermanentId(project.id)}
                    className="px-3 py-1.5 text-sm font-medium rounded-lg text-red-600 dark:text-red-400 hover:bg-red-500/10 transition-colors"
                  >
                    Delete
                  </button>
                )}
              </div>
            </div>
          ))}
        </CollapsibleSection>
      )}

      {/* Trashed Recordings */}
      {archivedRecordings.length > 0 && (
        <CollapsibleSection title="Recordings" count={archivedRecordings.length}>
          {archivedRecordings.map((recording: Recording) => (
            <div key={recording.id} className="flex items-center justify-between px-4 py-3 bg-card hover:bg-muted/30 transition-colors">
              <div className="min-w-0">
                <p className="text-sm font-medium text-foreground truncate">{recording.title}</p>
                <div className="flex items-center gap-2 text-xs text-muted-foreground">
                  <span>{getProjectName(recording.project_ids)}</span>
                  {recording.duration_seconds != null && recording.duration_seconds > 0 && (
                    <>
                      <span className="text-border">|</span>
                      <span>{formatDuration(recording.duration_seconds, false)}</span>
                    </>
                  )}
                  {(recording as any).deleted_at && (
                    <>
                      <span className="text-border">|</span>
                      <span>Deleted {formatTimeAgo((recording as any).deleted_at)}</span>
                    </>
                  )}
                </div>
              </div>
              <div className="shrink-0 ml-4 flex items-center gap-2">
                <button
                  onClick={() => restoreRecording.mutate(recording.id)}
                  disabled={actionId === recording.id}
                  className="px-3 py-1.5 text-sm font-medium rounded-lg border border-border text-foreground hover:bg-muted transition-colors disabled:opacity-50"
                >
                  {actionId === recording.id ? 'Restoring...' : 'Restore'}
                </button>
                {confirmPermanentId === recording.id ? (
                  <div className="flex items-center gap-1">
                    <button
                      onClick={() => permanentDeleteRecording.mutate(recording.id)}
                      disabled={actionId === recording.id}
                      className="px-2 py-1.5 text-xs font-medium rounded-lg bg-red-600 text-white hover:bg-red-700 transition-colors disabled:opacity-50"
                    >
                      Confirm
                    </button>
                    <button
                      onClick={() => setConfirmPermanentId(null)}
                      className="px-2 py-1.5 text-xs font-medium rounded-lg border border-border text-foreground hover:bg-muted transition-colors"
                    >
                      Cancel
                    </button>
                  </div>
                ) : (
                  <button
                    onClick={() => setConfirmPermanentId(recording.id)}
                    className="px-3 py-1.5 text-sm font-medium rounded-lg text-red-600 dark:text-red-400 hover:bg-red-500/10 transition-colors"
                  >
                    Delete
                  </button>
                )}
              </div>
            </div>
          ))}
        </CollapsibleSection>
      )}

      {/* Trashed Documents */}
      {archivedDocuments.length > 0 && (
        <CollapsibleSection title="Documents" count={archivedDocuments.length}>
          {archivedDocuments.map((doc: Document) => (
            <div key={doc.id} className="flex items-center justify-between px-4 py-3 bg-card hover:bg-muted/30 transition-colors">
              <div className="min-w-0">
                <p className="text-sm font-medium text-foreground truncate">{doc.title}</p>
                <div className="flex items-center gap-2 text-xs text-muted-foreground">
                  <span>{getProjectName(doc.project_ids)}</span>
                  {doc.mime_type && (
                    <>
                      <span className="text-border">|</span>
                      <span>{formatMimeType(doc.mime_type)}</span>
                    </>
                  )}
                  {(doc as any).deleted_at && (
                    <>
                      <span className="text-border">|</span>
                      <span>Deleted {formatTimeAgo((doc as any).deleted_at)}</span>
                    </>
                  )}
                </div>
              </div>
              <div className="shrink-0 ml-4 flex items-center gap-2">
                <button
                  onClick={() => restoreDocument.mutate(doc.id)}
                  disabled={actionId === doc.id}
                  className="px-3 py-1.5 text-sm font-medium rounded-lg border border-border text-foreground hover:bg-muted transition-colors disabled:opacity-50"
                >
                  {actionId === doc.id ? 'Restoring...' : 'Restore'}
                </button>
                {confirmPermanentId === doc.id ? (
                  <div className="flex items-center gap-1">
                    <button
                      onClick={() => permanentDeleteDocument.mutate(doc.id)}
                      disabled={actionId === doc.id}
                      className="px-2 py-1.5 text-xs font-medium rounded-lg bg-red-600 text-white hover:bg-red-700 transition-colors disabled:opacity-50"
                    >
                      Confirm
                    </button>
                    <button
                      onClick={() => setConfirmPermanentId(null)}
                      className="px-2 py-1.5 text-xs font-medium rounded-lg border border-border text-foreground hover:bg-muted transition-colors"
                    >
                      Cancel
                    </button>
                  </div>
                ) : (
                  <button
                    onClick={() => setConfirmPermanentId(doc.id)}
                    className="px-3 py-1.5 text-sm font-medium rounded-lg text-red-600 dark:text-red-400 hover:bg-red-500/10 transition-colors"
                  >
                    Delete
                  </button>
                )}
              </div>
            </div>
          ))}
        </CollapsibleSection>
      )}
    </div>
  );
}

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

  // Track which item is currently being unarchived
  const [unarchivingId, setUnarchivingId] = useState<string | null>(null);

  // Unarchive mutations
  const unarchiveProject = useMutation({
    mutationFn: (id: string) => { setUnarchivingId(id); return api.projects.unarchive(id); },
    onSettled: () => setUnarchivingId(null),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.projects.all });
      queryClient.invalidateQueries({ queryKey: queryKeys.dashboard.stats });
    },
  });

  const unarchiveRecording = useMutation({
    mutationFn: (id: string) => { setUnarchivingId(id); return api.recordings.unarchive(id); },
    onSettled: () => setUnarchivingId(null),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.recordings.all });
      queryClient.invalidateQueries({ queryKey: queryKeys.recordings.archived() });
      queryClient.invalidateQueries({ queryKey: queryKeys.dashboard.stats });
    },
  });

  const unarchiveDocument = useMutation({
    mutationFn: (id: string) => { setUnarchivingId(id); return api.documents.unarchive(id); },
    onSettled: () => setUnarchivingId(null),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.documents.all });
      queryClient.invalidateQueries({ queryKey: queryKeys.documents.archived() });
      queryClient.invalidateQueries({ queryKey: queryKeys.dashboard.stats });
    },
  });

  const isLoading = projectsLoading || recordingsLoading || documentsLoading;
  const archivedRecordings = recordingsData?.items ?? [];
  const archivedDocuments = documentsData?.items ?? [];
  const isEmpty = archivedProjects.length === 0 && archivedRecordings.length === 0 && archivedDocuments.length === 0;

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

  if (isEmpty) {
    return (
      <div className="space-y-2">
        <h1 className="text-2xl font-bold text-foreground">Archive</h1>
        <p className="text-sm text-muted-foreground">Archived items can be restored to their original location.</p>

        <div className="flex flex-col items-center justify-center py-20 text-center">
          <div className="rounded-full bg-muted p-4 mb-4">
            <svg className="w-8 h-8 text-muted-foreground" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
              <path strokeLinecap="round" strokeLinejoin="round" d="M20 7l-8-4-8 4m16 0l-8 4m8-4v10l-8 4m0-10L4 7m8 4v10M4 7v10l8 4" />
            </svg>
          </div>
          <h2 className="text-lg font-semibold text-foreground mb-1">Nothing archived</h2>
          <p className="text-sm text-muted-foreground max-w-sm">
            When you archive projects, recordings, or documents, they will appear here. You can restore them at any time.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="space-y-1">
        <h1 className="text-2xl font-bold text-foreground">Archive</h1>
        <p className="text-sm text-muted-foreground">Archived items can be restored to their original location.</p>
      </div>

      {/* Archived Projects */}
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
                  </p>
                </div>
              </div>
              <button
                onClick={() => unarchiveProject.mutate(project.id)}
                disabled={unarchivingId === project.id}
                className="shrink-0 ml-4 px-3 py-1.5 text-sm font-medium rounded-lg border border-border text-foreground hover:bg-muted transition-colors disabled:opacity-50"
              >
                {unarchivingId === project.id ? 'Restoring...' : 'Unarchive'}
              </button>
            </div>
          ))}
        </CollapsibleSection>
      )}

      {/* Archived Recordings */}
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
                </div>
              </div>
              <button
                onClick={() => unarchiveRecording.mutate(recording.id)}
                disabled={unarchivingId === recording.id}
                className="shrink-0 ml-4 px-3 py-1.5 text-sm font-medium rounded-lg border border-border text-foreground hover:bg-muted transition-colors disabled:opacity-50"
              >
                {unarchivingId === recording.id ? 'Restoring...' : 'Unarchive'}
              </button>
            </div>
          ))}
        </CollapsibleSection>
      )}

      {/* Archived Documents */}
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
                </div>
              </div>
              <button
                onClick={() => unarchiveDocument.mutate(doc.id)}
                disabled={unarchivingId === doc.id}
                className="shrink-0 ml-4 px-3 py-1.5 text-sm font-medium rounded-lg border border-border text-foreground hover:bg-muted transition-colors disabled:opacity-50"
              >
                {unarchivingId === doc.id ? 'Restoring...' : 'Unarchive'}
              </button>
            </div>
          ))}
        </CollapsibleSection>
      )}
    </div>
  );
}

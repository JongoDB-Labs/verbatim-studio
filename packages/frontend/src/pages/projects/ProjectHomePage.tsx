import { useMemo, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  FileAudio2, FileText, MessageSquare, Notebook, Mic,
  ArrowRight, Activity, Image as ImageIcon, Sparkles,
} from 'lucide-react';
import { api, type Recording, type Document } from '@/lib/api';
import { queryKeys } from '@/lib/queryKeys';
import { UploadDocumentDialog } from '@/components/documents/UploadDocumentDialog';
import { UploadSetupDialog, type UploadOptions } from '@/components/recordings/UploadSetupDialog';

interface ProjectHomePageProps {
  projectId: string;
  onNavigateToRecordings: () => void;
  onNavigateToDocuments: () => void;
  onViewTranscript: (recordingId: string) => void;
  onViewDocument: (documentId: string) => void;
  onNavigateToLive?: () => void;
  onNavigateToChats?: () => void;
}

// Project home — the workspace landing experience for a single project.
// Replaces the prior "tiles of every class of info" layout with a
// hierarchy that surfaces (a) what's happening in the project right now,
// (b) clear add-to-this-project actions, (c) browseable per-class lists
// with previews. Themed via the design system (bg-card, bg-background)
// instead of hardcoded zinc shades.

export function ProjectHomePage({
  projectId,
  onNavigateToRecordings,
  onNavigateToDocuments,
  onViewTranscript,
  onViewDocument,
  onNavigateToLive,
  onNavigateToChats,
}: ProjectHomePageProps) {
  const queryClient = useQueryClient();
  const [showUploadRecording, setShowUploadRecording] = useState(false);
  const [pendingRecording, setPendingRecording] = useState<File | null>(null);
  const [showUploadDocument, setShowUploadDocument] = useState(false);

  const { data: project } = useQuery({
    queryKey: queryKeys.projects.detail(projectId),
    queryFn: () => api.projects.get(projectId),
  });

  const { data: sections } = useQuery({
    queryKey: queryKeys.projects.sections(projectId),
    queryFn: () => api.projects.getSections(projectId),
  });

  const { data: recordings } = useQuery({
    queryKey: queryKeys.recordings.list({ projectId, sortBy: 'created_at', sortOrder: 'desc' }),
    queryFn: () => api.recordings.list({ projectId, sortBy: 'created_at', sortOrder: 'desc', pageSize: 6 }),
  });

  const { data: documents } = useQuery({
    queryKey: queryKeys.documents.list({ projectId, sortBy: 'created_at', sortOrder: 'desc' }),
    queryFn: () => api.documents.list({ project_id: projectId, sort_by: 'created_at', sort_order: 'desc', page_size: 6 }),
  });

  const { data: conversations } = useQuery({
    queryKey: ['conversations', { projectId }],
    queryFn: () => api.conversations.list(),
  });

  // Filter chats to this project (the list endpoint is project-scoped
  // via X-Active-Project header, but we may have global mixed in).
  const projectChats = useMemo(() => {
    return (conversations?.items ?? []).filter((c) => c.project_id === projectId).slice(0, 5);
  }, [conversations, projectId]);

  // Build a "recent activity" timeline by interleaving the most recent
  // few items across recordings + documents + chats. Sorted by
  // created_at desc, capped at 6.
  const recentActivity = useMemo(() => {
    type Item = {
      kind: 'recording' | 'document' | 'chat';
      id: string;
      title: string;
      timestamp: string;
      onClick: () => void;
      meta?: string;
    };
    const items: Item[] = [];
    for (const r of recordings?.items ?? []) {
      items.push({
        kind: 'recording',
        id: r.id,
        title: r.title,
        timestamp: r.created_at,
        onClick: () => onViewTranscript(r.id),
        meta: r.duration_seconds ? formatDuration(r.duration_seconds) : undefined,
      });
    }
    for (const d of documents?.items ?? []) {
      items.push({
        kind: 'document',
        id: d.id,
        title: d.title,
        timestamp: d.created_at,
        onClick: () => onViewDocument(d.id),
        meta: humanizeMime(d.mime_type),
      });
    }
    for (const c of projectChats) {
      items.push({
        kind: 'chat',
        id: c.id,
        title: c.title || 'Untitled conversation',
        timestamp: c.updated_at || c.created_at,
        onClick: () => onNavigateToChats?.(),
      });
    }
    return items.sort((a, b) => (b.timestamp || '').localeCompare(a.timestamp || '')).slice(0, 6);
  }, [recordings, documents, projectChats, onViewTranscript, onViewDocument, onNavigateToChats]);

  if (!project) {
    return (
      <div className="flex items-center justify-center py-24">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary"></div>
      </div>
    );
  }

  const isEmpty =
    (sections?.recordings ?? 0) === 0 &&
    (sections?.documents ?? 0) === 0 &&
    (sections?.notes ?? 0) === 0;

  // Add-to-this-project handlers. Pre-scoped via state passed into the
  // upload dialogs, so the user doesn't have to re-pick the project.

  const refreshAll = () => {
    queryClient.invalidateQueries({ queryKey: queryKeys.recordings.all });
    queryClient.invalidateQueries({ queryKey: queryKeys.documents.all });
    queryClient.invalidateQueries({ queryKey: queryKeys.projects.sections(projectId) });
  };

  const handleRecordingFileChosen = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setPendingRecording(file);
    setShowUploadRecording(true);
    e.target.value = '';
  };

  const handleConfirmRecordingUpload = async (options: UploadOptions) => {
    if (!pendingRecording) return;
    try {
      const result = await api.recordings.upload(pendingRecording, {
        title: options.title,
        templateId: options.templateId,
        metadata: options.metadata,
      });
      if (result?.id) {
        await api.recordings.update(result.id, { project_id: projectId });
        if (options.autoTranscribe) {
          try {
            await api.recordings.transcribe(result.id, {
              autoGenerateSummary: options.autoGenerateSummary,
            });
          } catch {
            // best-effort: transcription kickoff is non-fatal here
          }
        }
      }
      setShowUploadRecording(false);
      setPendingRecording(null);
      refreshAll();
    } catch (e) {
      console.error('upload recording failed:', e);
    }
  };

  return (
    <div className="max-w-6xl mx-auto p-6 space-y-6">
      {/* ───── Project Hero ─────────────────────────────────────────── */}
      <div className="rounded-xl border border-border bg-card p-6 shadow-sm">
        <div className="flex items-start gap-4">
          <div
            className="flex-shrink-0 w-14 h-14 rounded-xl flex items-center justify-center text-2xl"
            style={{
              backgroundColor: project.color
                ? `${project.color}1a`  // 10% opacity background
                : 'hsl(var(--muted))',
              color: project.color || 'hsl(var(--muted-foreground))',
            }}
          >
            {project.icon ?? '📁'}
          </div>
          <div className="flex-1 min-w-0">
            <h1 className="text-2xl font-bold text-foreground truncate">
              {project.name}
            </h1>
            {project.description ? (
              <p className="mt-1 text-sm text-muted-foreground">{project.description}</p>
            ) : (
              <p className="mt-1 text-sm text-muted-foreground italic">No description yet.</p>
            )}
            <div className="mt-4 grid grid-cols-2 sm:grid-cols-4 gap-3">
              <Stat icon={<FileAudio2 className="w-4 h-4" />} label="Recordings" value={sections?.recordings ?? 0} />
              <Stat icon={<FileText className="w-4 h-4" />} label="Documents" value={sections?.documents ?? 0} />
              <Stat icon={<Notebook className="w-4 h-4" />} label="Notes" value={sections?.notes ?? 0} />
              <Stat icon={<MessageSquare className="w-4 h-4" />} label="Chats" value={projectChats.length} />
            </div>
          </div>
        </div>

        {/* Add-to-this-project quick actions */}
        <div className="mt-5 pt-5 border-t border-border flex flex-wrap items-center gap-2">
          <span className="text-xs font-medium uppercase tracking-wider text-muted-foreground mr-1">
            Add to this project
          </span>
          <label className="inline-flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 cursor-pointer transition-colors">
            <Mic className="w-4 h-4" />
            Upload recording
            <input
              type="file"
              accept="audio/*,video/*"
              className="hidden"
              onChange={handleRecordingFileChosen}
            />
          </label>
          <button
            onClick={() => setShowUploadDocument(true)}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium rounded-lg border border-border hover:bg-muted transition-colors"
          >
            <FileText className="w-4 h-4" />
            Upload document
          </button>
          {onNavigateToLive && (
            <button
              onClick={onNavigateToLive}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium rounded-lg border border-border hover:bg-muted transition-colors"
            >
              <Sparkles className="w-4 h-4" />
              Start live session
            </button>
          )}
        </div>
      </div>

      {/* ───── Empty state ──────────────────────────────────────────── */}
      {isEmpty && (
        <div className="rounded-xl border border-dashed border-border bg-card/40 py-16 text-center">
          <p className="text-base font-medium text-foreground mb-1">This project is empty</p>
          <p className="text-sm text-muted-foreground max-w-md mx-auto">
            Use the buttons above to upload your first recording or document.
            You can also drag files into the project folder on disk and Verbatim
            will pick them up automatically.
          </p>
        </div>
      )}

      {/* ───── Recent Activity ──────────────────────────────────────── */}
      {recentActivity.length > 0 && (
        <Section title="Recent activity" icon={<Activity className="w-4 h-4" />}>
          <ul className="divide-y divide-border">
            {recentActivity.map((item) => (
              <li key={`${item.kind}-${item.id}`}>
                <button
                  onClick={item.onClick}
                  className="w-full flex items-center gap-3 px-4 py-3 hover:bg-muted/50 transition-colors text-left"
                >
                  <span className="flex-shrink-0 w-8 h-8 rounded-md bg-muted flex items-center justify-center text-muted-foreground">
                    {item.kind === 'recording' && <FileAudio2 className="w-4 h-4" />}
                    {item.kind === 'document' && <FileText className="w-4 h-4" />}
                    {item.kind === 'chat' && <MessageSquare className="w-4 h-4" />}
                  </span>
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium text-foreground truncate">
                      {item.title}
                    </p>
                    <p className="text-xs text-muted-foreground">
                      {labelForKind(item.kind)}
                      {item.meta && ` · ${item.meta}`}
                      {item.timestamp && ` · ${formatRelative(item.timestamp)}`}
                    </p>
                  </div>
                  <ArrowRight className="w-4 h-4 text-muted-foreground flex-shrink-0" />
                </button>
              </li>
            ))}
          </ul>
        </Section>
      )}

      {/* ───── Recordings + Documents (two columns) ─────────────────── */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <Section
          title="Recordings"
          icon={<FileAudio2 className="w-4 h-4" />}
          count={sections?.recordings ?? 0}
          actionLabel="View all"
          onAction={onNavigateToRecordings}
        >
          {(recordings?.items?.length ?? 0) === 0 ? (
            <EmptyHint label="No recordings yet" hint="Upload audio/video files via the button above." />
          ) : (
            <ul className="divide-y divide-border">
              {recordings?.items?.slice(0, 5).map((r) => (
                <RecordingRow key={r.id} recording={r} onClick={() => onViewTranscript(r.id)} />
              ))}
            </ul>
          )}
        </Section>

        <Section
          title="Documents"
          icon={<FileText className="w-4 h-4" />}
          count={sections?.documents ?? 0}
          actionLabel="View all"
          onAction={onNavigateToDocuments}
        >
          {(documents?.items?.length ?? 0) === 0 ? (
            <EmptyHint label="No documents yet" hint="Upload PDFs, Office docs, or images via the button above." />
          ) : (
            <ul className="divide-y divide-border">
              {documents?.items?.slice(0, 5).map((d) => (
                <DocumentRow key={d.id} document={d} onClick={() => onViewDocument(d.id)} />
              ))}
            </ul>
          )}
        </Section>
      </div>

      {/* ───── Chats panel ──────────────────────────────────────────── */}
      {projectChats.length > 0 && (
        <Section
          title="Saved chats"
          icon={<MessageSquare className="w-4 h-4" />}
          count={projectChats.length}
          actionLabel="View all"
          onAction={onNavigateToChats}
        >
          <ul className="divide-y divide-border">
            {projectChats.map((c) => (
              <li key={c.id}>
                <button
                  onClick={onNavigateToChats}
                  className="w-full text-left px-4 py-3 hover:bg-muted/50 transition-colors"
                >
                  <p className="text-sm font-medium text-foreground truncate">
                    {c.title || 'Untitled conversation'}
                  </p>
                  {c.last_message_preview && (
                    <p className="mt-0.5 text-xs text-muted-foreground truncate">
                      {c.last_message_preview}
                    </p>
                  )}
                </button>
              </li>
            ))}
          </ul>
        </Section>
      )}

      {/* ───── Dialogs ──────────────────────────────────────────────── */}
      <UploadDocumentDialog
        open={showUploadDocument}
        onClose={() => setShowUploadDocument(false)}
        onUploaded={() => {
          setShowUploadDocument(false);
          refreshAll();
        }}
        projects={project ? [project] : []}
        defaultProjectId={projectId}
      />
      {pendingRecording && (
        <UploadSetupDialog
          isOpen={showUploadRecording}
          onClose={() => {
            setShowUploadRecording(false);
            setPendingRecording(null);
          }}
          onConfirm={handleConfirmRecordingUpload}
          file={pendingRecording}
        />
      )}
    </div>
  );
}

// ── Helper sub-components ─────────────────────────────────────────────

function Stat({ icon, label, value }: { icon: React.ReactNode; label: string; value: number }) {
  return (
    <div className="flex items-center gap-2">
      <span className="text-muted-foreground">{icon}</span>
      <div className="min-w-0">
        <div className="text-base font-semibold text-foreground leading-none">{value}</div>
        <div className="text-xs text-muted-foreground mt-0.5">{label}</div>
      </div>
    </div>
  );
}

function Section({
  title,
  icon,
  count,
  actionLabel,
  onAction,
  children,
}: {
  title: string;
  icon?: React.ReactNode;
  count?: number;
  actionLabel?: string;
  onAction?: () => void;
  children: React.ReactNode;
}) {
  return (
    <div className="rounded-xl border border-border bg-card overflow-hidden">
      <div className="flex items-center justify-between px-4 py-3 border-b border-border">
        <h2 className="flex items-center gap-2 text-sm font-semibold text-foreground">
          {icon}
          <span>{title}</span>
          {typeof count === 'number' && (
            <span className="ml-1 text-muted-foreground font-normal">({count})</span>
          )}
        </h2>
        {actionLabel && onAction && (
          <button
            onClick={onAction}
            className="text-xs font-medium text-primary hover:underline inline-flex items-center gap-1"
          >
            {actionLabel}
            <ArrowRight className="w-3 h-3" />
          </button>
        )}
      </div>
      {children}
    </div>
  );
}

function EmptyHint({ label, hint }: { label: string; hint: string }) {
  return (
    <div className="px-4 py-8 text-center">
      <p className="text-sm font-medium text-foreground">{label}</p>
      <p className="mt-1 text-xs text-muted-foreground max-w-xs mx-auto">{hint}</p>
    </div>
  );
}

function RecordingRow({ recording, onClick }: { recording: Recording; onClick: () => void }) {
  return (
    <li>
      <button
        onClick={onClick}
        className="w-full flex items-center gap-3 px-4 py-3 hover:bg-muted/50 transition-colors text-left"
      >
        <span className="flex-shrink-0 w-9 h-9 rounded-md bg-muted flex items-center justify-center text-muted-foreground">
          <FileAudio2 className="w-4 h-4" />
        </span>
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium text-foreground truncate">{recording.title}</p>
          <p className="text-xs text-muted-foreground">
            {recording.duration_seconds ? formatDuration(recording.duration_seconds) : '—'}
            {recording.created_at && ` · ${formatRelative(recording.created_at)}`}
            {recording.status && recording.status !== 'completed' && ` · ${recording.status}`}
          </p>
        </div>
        <ArrowRight className="w-4 h-4 text-muted-foreground flex-shrink-0" />
      </button>
    </li>
  );
}

function DocumentRow({ document, onClick }: { document: Document; onClick: () => void }) {
  return (
    <li>
      <button
        onClick={onClick}
        className="w-full flex items-center gap-3 px-4 py-3 hover:bg-muted/50 transition-colors text-left"
      >
        <span className="flex-shrink-0 w-9 h-9 rounded-md bg-muted flex items-center justify-center text-muted-foreground">
          {document.mime_type?.startsWith('image/') ? <ImageIcon className="w-4 h-4" /> : <FileText className="w-4 h-4" />}
        </span>
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium text-foreground truncate">{document.title}</p>
          <p className="text-xs text-muted-foreground">
            {humanizeMime(document.mime_type)}
            {document.created_at && ` · ${formatRelative(document.created_at)}`}
          </p>
        </div>
        <ArrowRight className="w-4 h-4 text-muted-foreground flex-shrink-0" />
      </button>
    </li>
  );
}

// ── Formatting helpers ────────────────────────────────────────────────

function formatDuration(seconds: number): string {
  const mins = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60);
  if (mins >= 60) {
    const hours = Math.floor(mins / 60);
    return `${hours}h ${mins % 60}m`;
  }
  return `${mins}:${secs.toString().padStart(2, '0')}`;
}

function humanizeMime(mime: string | null | undefined): string {
  if (!mime) return 'Document';
  if (mime === 'application/pdf') return 'PDF';
  if (mime.includes('wordprocessingml')) return 'Word document';
  if (mime.includes('spreadsheetml')) return 'Spreadsheet';
  if (mime.includes('presentationml')) return 'Presentation';
  if (mime.startsWith('image/')) return 'Image';
  if (mime === 'text/plain') return 'Text';
  if (mime === 'text/markdown') return 'Markdown';
  return mime.split('/').pop() ?? 'Document';
}

function formatRelative(iso: string): string {
  if (!iso) return '';
  const then = new Date(iso).getTime();
  const now = Date.now();
  const diff = Math.max(0, now - then);
  const sec = Math.floor(diff / 1000);
  if (sec < 60) return 'just now';
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const day = Math.floor(hr / 24);
  if (day < 7) return `${day}d ago`;
  if (day < 30) return `${Math.floor(day / 7)}w ago`;
  if (day < 365) return `${Math.floor(day / 30)}mo ago`;
  return `${Math.floor(day / 365)}y ago`;
}

function labelForKind(kind: 'recording' | 'document' | 'chat'): string {
  if (kind === 'recording') return 'Recording';
  if (kind === 'document') return 'Document';
  return 'Chat';
}

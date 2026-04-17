import { useState, useEffect, useCallback } from 'react';
import { api, type Project, type ProjectRecording, type ProjectType } from '@/lib/api';
import { formatDate } from '@/lib/utils';
import { DynamicMetadataForm } from '@/components/shared/DynamicMetadataForm';
import { TagInput } from '@/components/shared/TagInput';
import { AddRecordingDialog } from '@/components/projects/AddRecordingDialog';

interface ProjectDetailPageProps {
  projectId: string;
  onBack: () => void;
  onViewTranscript: (recordingId: string) => void;
  onNavigateToAnalytics: () => void;
}

function formatDuration(seconds: number | null): string {
  if (!seconds) return '--:--';
  const mins = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60);
  return `${mins}:${secs.toString().padStart(2, '0')}`;
}

export function ProjectDetailPage({
  projectId,
  onBack,
  onViewTranscript,
  onNavigateToAnalytics,
}: ProjectDetailPageProps) {
  const [project, setProject] = useState<Project | null>(null);
  const [projectTypes, setProjectTypes] = useState<ProjectType[]>([]);
  const [recordings, setRecordings] = useState<ProjectRecording[]>([]);
  const [loading, setLoading] = useState(true);

  // Dialogs
  const [showEditDialog, setShowEditDialog] = useState(false);
  const [showDeleteDialog, setShowDeleteDialog] = useState(false);
  const [deleteFilesChoice, setDeleteFilesChoice] = useState<boolean | null>(null);
  const [showAddRecordingDialog, setShowAddRecordingDialog] = useState(false);

  // Edit form
  const [form, setForm] = useState({
    name: '',
    description: '',
    project_type_id: '',
    tags: [] as string[],
    metadata: {} as Record<string, unknown>,
  });

  const loadData = useCallback(async () => {
    try {
      setLoading(true);
      const [projectRes, recordingsRes, typesRes] = await Promise.all([
        api.projects.get(projectId),
        api.projects.getRecordings(projectId),
        api.projectTypes.list(),
      ]);
      setProject(projectRes);
      setRecordings(recordingsRes.items);
      setProjectTypes(typesRes.items);
    } catch (error) {
      console.error('Failed to load project:', error);
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  const openEditDialog = () => {
    if (!project) return;
    const { tags, ...otherMetadata } = project.metadata || {};
    setForm({
      name: project.name,
      description: project.description || '',
      project_type_id: project.project_type?.id || '',
      tags: (tags as string[]) || [],
      metadata: otherMetadata || {},
    });
    setShowEditDialog(true);
  };

  const handleUpdateProject = async () => {
    if (!project) return;
    try {
      const metadata = {
        ...form.metadata,
        tags: form.tags,  // Always include tags, even empty array to allow clearing
      };
      await api.projects.update(project.id, {
        name: form.name,
        description: form.description || undefined,
        project_type_id: form.project_type_id || null,
        metadata,
      });
      setShowEditDialog(false);
      loadData();
    } catch (error) {
      console.error('Failed to update project:', error);
    }
  };

  const handleDeleteProject = async (deleteFiles: boolean) => {
    if (!project) return;
    try {
      await api.projects.delete(project.id, { deleteFiles });
      setShowDeleteDialog(false);
      setDeleteFilesChoice(null);
      onBack();
    } catch (error) {
      console.error('Failed to delete project:', error);
    }
  };

  const handleRemoveRecording = async (recordingId: string) => {
    try {
      await api.projects.removeRecording(projectId, recordingId);
      loadData();
    } catch (error) {
      console.error('Failed to remove recording:', error);
    }
  };

  const selectedTypeSchema = projectTypes.find(t => t.id === form.project_type_id)?.metadata_schema || [];

  if (loading) {
    return (
      <div className="flex items-center justify-center py-12">
        <svg
          className="h-8 w-8 animate-spin text-primary"
          xmlns="http://www.w3.org/2000/svg"
          fill="none"
          viewBox="0 0 24 24"
        >
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
        </svg>
      </div>
    );
  }

  if (!project) {
    return (
      <div className="text-center py-12">
        <p className="text-muted-foreground">Project not found</p>
        <button
          onClick={onBack}
          className="mt-4 px-4 py-2 rounded-lg bg-primary text-primary-foreground text-sm font-medium hover:bg-primary/90 transition-colors"
        >
          Go Back
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-start justify-between">
        <div className="flex items-center gap-4">
          <button
            onClick={onBack}
            className="p-2 rounded-lg hover:bg-muted transition-colors text-muted-foreground hover:text-foreground"
          >
            <svg className="h-5 w-5" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
              <path strokeLinecap="round" strokeLinejoin="round" d="M15 19l-7-7 7-7" />
            </svg>
          </button>
          <div>
            <h1 className="text-2xl font-bold text-foreground">{project.name}</h1>
            {project.project_type && (
              <span className="inline-block mt-1 px-2 py-0.5 text-xs font-medium rounded bg-primary/10 text-primary">
                {project.project_type.name}
              </span>
            )}
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setShowAddRecordingDialog(true)}
            className="flex items-center gap-1.5 px-3 py-2 rounded-lg border border-border bg-background text-sm font-medium text-foreground hover:bg-muted transition-colors"
          >
            <svg className="h-4 w-4" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 4v16m8-8H4" />
            </svg>
            Add Recording
          </button>
          <button
            onClick={onNavigateToAnalytics}
            className="flex items-center gap-1.5 px-3 py-2 rounded-lg border border-border bg-background text-sm font-medium text-foreground hover:bg-muted transition-colors"
          >
            <svg className="h-4 w-4" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
              <path strokeLinecap="round" strokeLinejoin="round" d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
            </svg>
            Analytics
          </button>
          <button
            onClick={openEditDialog}
            className="flex items-center gap-1.5 px-3 py-2 rounded-lg border border-border bg-background text-sm font-medium text-foreground hover:bg-muted transition-colors"
          >
            <svg className="h-4 w-4" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
              <path strokeLinecap="round" strokeLinejoin="round" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
            </svg>
            Edit
          </button>
          <button
            onClick={() => setShowDeleteDialog(true)}
            className="flex items-center gap-1.5 px-3 py-2 rounded-lg border border-destructive/50 text-sm font-medium text-destructive hover:bg-destructive/10 transition-colors"
          >
            <svg className="h-4 w-4" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
              <path strokeLinecap="round" strokeLinejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
            </svg>
            Delete
          </button>
        </div>
      </div>

      {/* Project Info */}
      {project.description && (
        <p className="text-muted-foreground">{project.description}</p>
      )}

      {/* Stats */}
      <div className="flex items-center gap-6 text-sm text-muted-foreground">
        <span>{recordings.length} recording{recordings.length !== 1 ? 's' : ''}</span>
        <span>Created {formatDate(project.created_at)}</span>
      </div>

      {/* Project Tags */}
      {((project.metadata?.tags as string[]) || []).length > 0 && (
        <div className="space-y-2">
          <h3 className="text-sm font-medium text-foreground">Tags</h3>
          <div className="flex flex-wrap gap-2">
            {((project.metadata?.tags as string[]) || []).map((tag) => (
              <span
                key={tag}
                className="inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium bg-primary/10 text-primary"
              >
                {tag}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Inherited Tags from Recordings */}
      {project.inherited_tags && project.inherited_tags.length > 0 && (
        <div className="space-y-2">
          <h3 className="text-sm font-medium text-foreground">Tags from recordings</h3>
          <div className="flex flex-wrap gap-2">
            {project.inherited_tags.map((tag) => (
              <span
                key={tag.id}
                className="inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium bg-muted text-muted-foreground border border-border"
              >
                {tag.color && (
                  <span
                    className="w-2 h-2 rounded-full"
                    style={{ backgroundColor: tag.color }}
                  />
                )}
                {tag.name}
                <span className="text-[10px] opacity-70">({tag.recording_count})</span>
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Recordings List */}
      <div className="space-y-4">
        <h2 className="text-lg font-semibold text-foreground">Recordings</h2>
        {recordings.length === 0 ? (
          <div className="text-center py-12 border border-border rounded-lg">
            <svg className="h-12 w-12 mx-auto text-muted-foreground mb-4" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
              <path strokeLinecap="round" strokeLinejoin="round" d="M19 11a7 7 0 01-7 7m0 0a7 7 0 01-7-7m7 7v4m0 0H8m4 0h4m-4-8a3 3 0 01-3-3V5a3 3 0 116 0v6a3 3 0 01-3 3z" />
            </svg>
            <p className="text-muted-foreground">No recordings in this project</p>
            <button
              onClick={() => setShowAddRecordingDialog(true)}
              className="mt-4 px-4 py-2 rounded-lg bg-primary text-primary-foreground text-sm font-medium hover:bg-primary/90 transition-colors"
            >
              Add recordings
            </button>
          </div>
        ) : (
          <div className="border border-border rounded-lg divide-y divide-border">
            {recordings.map((recording) => (
              <div
                key={recording.id}
                className="flex items-center justify-between p-4 hover:bg-muted/50 transition-colors"
              >
                <div
                  className="flex-1 min-w-0 cursor-pointer"
                  onClick={() => onViewTranscript(recording.id)}
                >
                  <h3 className="font-medium text-foreground truncate">{recording.title}</h3>
                  <div className="flex items-center gap-3 text-sm text-muted-foreground mt-1">
                    <span
                      className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-medium ${
                        recording.status === 'completed'
                          ? 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400'
                          : recording.status === 'failed'
                          ? 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400'
                          : recording.status === 'processing'
                          ? 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400'
                          : 'bg-gray-100 text-gray-700 dark:bg-gray-900/30 dark:text-gray-400'
                      }`}
                    >
                      {recording.status}
                    </span>
                    <span>{formatDuration(recording.duration_seconds)}</span>
                    <span>{formatDate(recording.created_at)}</span>
                  </div>
                </div>
                <button
                  onClick={() => handleRemoveRecording(recording.id)}
                  className="p-2 rounded-md hover:bg-destructive/10 transition-colors text-muted-foreground hover:text-destructive"
                  title="Remove from project"
                >
                  <svg className="h-4 w-4" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                  </svg>
                </button>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Edit Project Dialog */}
      {showEditDialog && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
          onClick={(e) => {
            if ((e.target as HTMLElement).classList.contains('bg-black/50')) {
              setShowEditDialog(false);
            }
          }}
        >
          <div className="bg-card border border-border rounded-lg shadow-xl w-full max-w-md mx-4 max-h-[85vh] overflow-y-auto">
            <div className="flex items-center justify-between px-5 py-4 border-b border-border">
              <h2 className="text-lg font-semibold text-foreground">Edit Project</h2>
              <button
                onClick={() => setShowEditDialog(false)}
                className="p-1 rounded-md hover:bg-muted transition-colors text-muted-foreground"
              >
                <svg className="w-5 h-5" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>
            <div className="px-5 py-4 space-y-4">
              <div className="space-y-1.5">
                <label className="text-sm font-medium text-foreground">Name</label>
                <input
                  value={form.name}
                  onChange={(e: React.ChangeEvent<HTMLInputElement>) => setForm({ ...form, name: e.target.value })}
                  className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
                />
              </div>
              <div className="space-y-1.5">
                <label className="text-sm font-medium text-foreground">Description</label>
                <textarea
                  value={form.description}
                  onChange={(e: React.ChangeEvent<HTMLTextAreaElement>) => setForm({ ...form, description: e.target.value })}
                  rows={2}
                  className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary resize-none"
                />
              </div>
              <div className="space-y-1.5">
                <label className="text-sm font-medium text-foreground">Type</label>
                <select
                  value={form.project_type_id}
                  onChange={(e: React.ChangeEvent<HTMLSelectElement>) => setForm({ ...form, project_type_id: e.target.value, metadata: {} })}
                  className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
                >
                  <option value="">No type</option>
                  {projectTypes.map((type) => (
                    <option key={type.id} value={type.id}>{type.name}</option>
                  ))}
                </select>
              </div>
              <div className="space-y-1.5">
                <label className="text-sm font-medium text-foreground">Tags</label>
                <TagInput
                  tags={form.tags}
                  onChange={(tags) => setForm({ ...form, tags })}
                  suggestions={[]}
                  placeholder="Add tags..."
                />
              </div>
              {selectedTypeSchema.length > 0 && (
                <div className="space-y-1.5">
                  <label className="text-sm font-medium text-foreground">Custom Fields</label>
                  <DynamicMetadataForm
                    fields={selectedTypeSchema}
                    values={form.metadata}
                    onChange={(metadata) => setForm({ ...form, metadata })}
                  />
                </div>
              )}
            </div>
            <div className="flex items-center justify-end gap-2 px-5 py-4 border-t border-border">
              <button
                onClick={() => setShowEditDialog(false)}
                className="px-4 py-2 text-sm font-medium rounded-lg border border-border text-foreground hover:bg-muted transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={handleUpdateProject}
                disabled={!form.name.trim()}
                className="px-4 py-2 text-sm font-medium rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 transition-colors disabled:opacity-50"
              >
                Save
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Delete Project Dialog */}
      {showDeleteDialog && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
          onClick={(e) => {
            if ((e.target as HTMLElement).classList.contains('bg-black/50')) {
              setShowDeleteDialog(false);
              setDeleteFilesChoice(null);
            }
          }}
        >
          <div className="bg-card border border-border rounded-lg shadow-xl w-full max-w-md mx-4">
            <div className="px-5 py-4">
              <h2 className="text-lg font-semibold text-foreground">Move Project to Trash</h2>
              <p className="text-sm text-muted-foreground mt-2">
                Move "{project.name}" to trash? You can restore it later.
              </p>
              {recordings.length > 0 && (
                <div className="mt-4 space-y-3">
                  <p className="text-sm font-medium text-foreground">
                    This project contains {recordings.length} recording{recordings.length !== 1 ? 's' : ''}. What would you like to do?
                  </p>
                  <div className="space-y-2">
                    <label className="flex items-start gap-3 p-3 rounded-lg border border-border hover:bg-muted/50 cursor-pointer transition-colors">
                      <input
                        type="radio"
                        name="deleteChoice"
                        checked={deleteFilesChoice === false}
                        onChange={() => setDeleteFilesChoice(false)}
                        className="mt-0.5"
                      />
                      <div>
                        <p className="text-sm font-medium text-foreground">Keep files</p>
                        <p className="text-xs text-muted-foreground">Move recordings to storage root and unassign from project</p>
                      </div>
                    </label>
                    <label className="flex items-start gap-3 p-3 rounded-lg border border-destructive/50 hover:bg-destructive/10 cursor-pointer transition-colors">
                      <input
                        type="radio"
                        name="deleteChoice"
                        checked={deleteFilesChoice === true}
                        onChange={() => setDeleteFilesChoice(true)}
                        className="mt-0.5"
                      />
                      <div>
                        <p className="text-sm font-medium text-destructive">Also trash all files</p>
                        <p className="text-xs text-muted-foreground">Move all recordings and documents to trash along with the project</p>
                      </div>
                    </label>
                  </div>
                </div>
              )}
            </div>
            <div className="flex items-center justify-end gap-2 px-5 py-4 border-t border-border">
              <button
                onClick={() => {
                  setShowDeleteDialog(false);
                  setDeleteFilesChoice(null);
                }}
                className="px-4 py-2 text-sm font-medium rounded-lg border border-border text-foreground hover:bg-muted transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={() => handleDeleteProject(deleteFilesChoice === true)}
                disabled={recordings.length > 0 && deleteFilesChoice === null}
                className="px-4 py-2 text-sm font-medium rounded-lg bg-destructive text-destructive-foreground hover:bg-destructive/90 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              >
                Delete
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Add Recording Dialog */}
      {showAddRecordingDialog && (
        <AddRecordingDialog
          projectId={projectId}
          existingRecordingIds={recordings.map(r => r.id)}
          open={showAddRecordingDialog}
          onClose={() => setShowAddRecordingDialog(false)}
          onRecordingsAdded={loadData}
        />
      )}
    </div>
  );
}

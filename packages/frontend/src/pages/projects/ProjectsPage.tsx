import { useState, useEffect, useRef } from 'react';
import { MetadataFieldEditor } from '@/components/shared/MetadataFieldEditor';
import { DynamicMetadataForm } from '@/components/shared/DynamicMetadataForm';
import { TagInput } from '@/components/shared/TagInput';
import { api, type Project, type ProjectType, type MetadataField } from '@/lib/api';
import { useProjects, useCreateProject, useUpdateProject, useDeleteProject } from '@/hooks';
import type { ProjectFilters } from '@/lib/queryKeys';

interface ProjectsPageProps {
  onNavigateToProject?: (projectId: string) => void;
}

// Debounce hook
function useDebounce<T>(value: T, delay: number): T {
  const [debouncedValue, setDebouncedValue] = useState<T>(value);
  useEffect(() => {
    const timer = setTimeout(() => setDebouncedValue(value), delay);
    return () => clearTimeout(timer);
  }, [value, delay]);
  return debouncedValue;
}

export function ProjectsPage({ onNavigateToProject }: ProjectsPageProps) {
  const [projectTypes, setProjectTypes] = useState<ProjectType[]>([]);
  const [searchQuery, setSearchQuery] = useState('');
  const debouncedSearch = useDebounce(searchQuery, 300);
  const [filterTypeId, setFilterTypeId] = useState('');
  const [filterTag, setFilterTag] = useState('');
  const dialogRef = useRef<HTMLDivElement>(null);

  // React Query hooks for projects
  const queryFilters: ProjectFilters = {
    search: debouncedSearch || undefined,
    projectTypeId: filterTypeId || undefined,
    tag: filterTag || undefined,
  };
  const { data: projectsData, isLoading: loading } = useProjects(queryFilters);
  const projects = projectsData?.items ?? [];

  // Project mutations
  const createProject = useCreateProject();
  const updateProject = useUpdateProject();
  const deleteProject = useDeleteProject();

  // Dialogs
  const [createDialogOpen, setCreateDialogOpen] = useState(false);
  const [editDialogOpen, setEditDialogOpen] = useState(false);
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [deleteFilesChoice, setDeleteFilesChoice] = useState<boolean | null>(null);
  const [typeManagerOpen, setTypeManagerOpen] = useState(false);

  // Form state
  const [selectedProject, setSelectedProject] = useState<Project | null>(null);
  const [form, setForm] = useState({
    name: '',
    description: '',
    project_type_id: '',
    tags: [] as string[],
    metadata: {} as Record<string, unknown>,
  });

  // Type manager form state
  const [typeForm, setTypeForm] = useState({
    name: '',
    description: '',
    metadata_schema: [] as MetadataField[],
  });
  const [editingType, setEditingType] = useState<ProjectType | null>(null);
  const [typeEditDialogOpen, setTypeEditDialogOpen] = useState(false);
  const [typeDeleteDialogOpen, setTypeDeleteDialogOpen] = useState(false);

  // Load project types on mount
  useEffect(() => {
    const loadProjectTypes = async () => {
      try {
        const typesRes = await api.projectTypes.list();
        setProjectTypes(typesRes.items);
      } catch (error) {
        console.error('Failed to load project types:', error);
      }
    };
    loadProjectTypes();
  }, []);

  // Extract unique tags from all projects (both direct and inherited)
  const uniqueTags = Array.from(
    new Set([
      ...projects.flatMap(p => (p.metadata?.tags as string[]) || []),
      ...projects.flatMap(p => p.inherited_tags?.map(t => t.name) || [])
    ])
  ).sort();

  const resetForm = () => {
    setForm({
      name: '',
      description: '',
      project_type_id: '',
      tags: [],
      metadata: {},
    });
  };

  const resetTypeForm = () => {
    setTypeForm({
      name: '',
      description: '',
      metadata_schema: [],
    });
  };

  // Get selected type's schema
  const selectedTypeSchema = projectTypes.find(t => t.id === form.project_type_id)?.metadata_schema || [];

  // Project CRUD
  const handleCreateProject = async () => {
    const metadata = {
      ...form.metadata,
      ...(form.tags.length > 0 ? { tags: form.tags } : {}),
    };
    await createProject.mutateAsync({
      name: form.name,
      description: form.description || undefined,
      project_type_id: form.project_type_id || undefined,
      metadata: Object.keys(metadata).length > 0 ? metadata : undefined,
    });
    resetForm();
    setCreateDialogOpen(false);
    // No need to call loadData() - React Query handles invalidation
  };

  const handleUpdateProject = async () => {
    if (!selectedProject) return;
    const metadata = {
      ...form.metadata,
      tags: form.tags,  // Always include tags, even empty array to allow clearing
    };
    await updateProject.mutateAsync({
      id: selectedProject.id,
      data: {
        name: form.name,
        description: form.description || undefined,
        project_type_id: form.project_type_id || null,
        metadata,
      },
    });
    setEditDialogOpen(false);
    setSelectedProject(null);
    resetForm();
  };

  const handleDeleteProject = async (deleteFiles: boolean) => {
    if (!selectedProject) return;
    await deleteProject.mutateAsync({
      id: selectedProject.id,
      deleteFiles,
    });
    setDeleteDialogOpen(false);
    setDeleteFilesChoice(null);
    setSelectedProject(null);
  };

  const openEditDialog = (project: Project) => {
    setSelectedProject(project);
    const { tags, ...otherMetadata } = project.metadata || {};
    setForm({
      name: project.name,
      description: project.description || '',
      project_type_id: project.project_type?.id || '',
      tags: (tags as string[]) || [],
      metadata: otherMetadata || {},
    });
    setEditDialogOpen(true);
  };

  const openDeleteDialog = (project: Project) => {
    setSelectedProject(project);
    setDeleteDialogOpen(true);
  };

  // Type CRUD
  const handleCreateType = async () => {
    try {
      await api.projectTypes.create({
        name: typeForm.name,
        description: typeForm.description || null,
        metadata_schema: typeForm.metadata_schema,
      });
      resetTypeForm();
      // Manually refetch project types
      const typesRes = await api.projectTypes.list();
      setProjectTypes(typesRes.items);
    } catch (error) {
      console.error('Failed to create type:', error);
    }
  };

  const handleUpdateType = async () => {
    if (!editingType) return;
    try {
      await api.projectTypes.update(editingType.id, {
        name: typeForm.name,
        description: typeForm.description || null,
        metadata_schema: typeForm.metadata_schema,
      });
      setTypeEditDialogOpen(false);
      setEditingType(null);
      resetTypeForm();
      // Manually refetch project types
      const typesRes = await api.projectTypes.list();
      setProjectTypes(typesRes.items);
    } catch (error) {
      console.error('Failed to update type:', error);
    }
  };

  const handleDeleteType = async () => {
    if (!editingType) return;
    try {
      await api.projectTypes.delete(editingType.id);
      setTypeDeleteDialogOpen(false);
      setEditingType(null);
      // Manually refetch project types
      const typesRes = await api.projectTypes.list();
      setProjectTypes(typesRes.items);
    } catch (error) {
      console.error('Failed to delete type:', error);
    }
  };

  const openTypeEditDialog = (type: ProjectType) => {
    setEditingType(type);
    setTypeForm({
      name: type.name,
      description: type.description || '',
      metadata_schema: type.metadata_schema,
    });
    setTypeEditDialogOpen(true);
  };

  const openTypeDeleteDialog = (type: ProjectType) => {
    setEditingType(type);
    setTypeDeleteDialogOpen(true);
  };

  if (loading) {
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
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-foreground">Projects</h1>
          <p className="text-sm text-muted-foreground mt-1">
            Organize your recordings into projects
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setTypeManagerOpen(true)}
            className="flex items-center gap-1.5 px-3 py-2 rounded-lg border border-border bg-background text-sm font-medium text-foreground hover:bg-muted transition-colors"
          >
            <svg className="h-4 w-4" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
              <path strokeLinecap="round" strokeLinejoin="round" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.066 2.573c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.573 1.066c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.066-2.573c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
              <path strokeLinecap="round" strokeLinejoin="round" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
            </svg>
            Manage Types
          </button>
          <button
            onClick={() => setCreateDialogOpen(true)}
            className="flex items-center gap-1.5 px-3 py-2 rounded-lg bg-primary text-primary-foreground text-sm font-medium hover:bg-primary/90 transition-colors"
          >
            <svg className="h-4 w-4" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 4v16m8-8H4" />
            </svg>
            New Project
          </button>
        </div>
      </div>

      {/* Search and Filters */}
      <div className="flex flex-wrap items-center gap-3">
        <div className="relative flex-1 min-w-[200px] max-w-md">
          <svg className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
            <path strokeLinecap="round" strokeLinejoin="round" d="M21 21l-5.197-5.197m0 0A7.5 7.5 0 105.196 5.196a7.5 7.5 0 0010.607 10.607z" />
          </svg>
          <input
            type="text"
            placeholder="Search projects..."
            value={searchQuery}
            onChange={(e: React.ChangeEvent<HTMLInputElement>) => setSearchQuery(e.target.value)}
            className="w-full rounded-lg border border-border bg-background pl-10 pr-3 py-2 text-sm text-foreground focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
          />
        </div>
        <select
          value={filterTypeId}
          onChange={(e: React.ChangeEvent<HTMLSelectElement>) => setFilterTypeId(e.target.value)}
          className="rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
        >
          <option value="">All Types</option>
          {projectTypes.map((type) => (
            <option key={type.id} value={type.id}>{type.name}</option>
          ))}
        </select>
        {uniqueTags.length > 0 && (
          <select
            value={filterTag}
            onChange={(e: React.ChangeEvent<HTMLSelectElement>) => setFilterTag(e.target.value)}
            className="rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
          >
            <option value="">All Tags</option>
            {uniqueTags.map((tag) => (
              <option key={tag} value={tag}>{tag}</option>
            ))}
          </select>
        )}
      </div>

      {/* Projects Grid */}
      {projects.length === 0 ? (
        <div className="text-center py-12">
          <svg className="h-12 w-12 mx-auto text-muted-foreground mb-4" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
            <path strokeLinecap="round" strokeLinejoin="round" d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z" />
          </svg>
          <p className="text-muted-foreground">No projects yet.</p>
          <button
            onClick={() => setCreateDialogOpen(true)}
            className="mt-4 px-4 py-2 rounded-lg bg-primary text-primary-foreground text-sm font-medium hover:bg-primary/90 transition-colors"
          >
            Create your first project
          </button>
        </div>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {projects.map((project) => (
            <div
              key={project.id}
              className="border border-border rounded-lg p-4 bg-card hover:shadow-md transition-shadow cursor-pointer"
              onClick={() => onNavigateToProject?.(project.id)}
            >
              <div className="flex items-start justify-between">
                <div className="flex-1 min-w-0">
                  <h3 className="font-semibold text-foreground truncate">{project.name}</h3>
                  {project.project_type && (
                    <span className="inline-block mt-1 px-2 py-0.5 text-xs font-medium rounded bg-primary/10 text-primary">
                      {project.project_type.name}
                    </span>
                  )}
                </div>
                <div className="flex items-center gap-1 ml-2">
                  <button
                    onClick={(e) => { e.stopPropagation(); openEditDialog(project); }}
                    className="p-1.5 rounded-md hover:bg-muted transition-colors text-muted-foreground hover:text-foreground"
                  >
                    <svg className="h-4 w-4" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                      <path strokeLinecap="round" strokeLinejoin="round" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
                    </svg>
                  </button>
                  <button
                    onClick={(e) => { e.stopPropagation(); openDeleteDialog(project); }}
                    className="p-1.5 rounded-md hover:bg-destructive/10 transition-colors text-destructive"
                  >
                    <svg className="h-4 w-4" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                      <path strokeLinecap="round" strokeLinejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                    </svg>
                  </button>
                </div>
              </div>
              {project.description && (
                <p className="text-sm text-muted-foreground mt-2 line-clamp-2">
                  {project.description}
                </p>
              )}
              {/* Tags */}
              {((project.metadata?.tags as string[]) || []).length > 0 && (
                <div className="flex flex-wrap gap-1 mt-2">
                  {((project.metadata?.tags as string[]) || []).map((tag) => (
                    <span
                      key={tag}
                      className="inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium bg-primary/10 text-primary"
                    >
                      {tag}
                    </span>
                  ))}
                </div>
              )}
              {/* Inherited Tags from Recordings */}
              {project.inherited_tags && project.inherited_tags.length > 0 && (
                <div className="flex flex-wrap gap-1 mt-2">
                  <span className="text-[10px] text-muted-foreground mr-1 self-center">From recordings:</span>
                  {project.inherited_tags.map((tag) => (
                    <span
                      key={tag.id}
                      className="inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium bg-muted text-muted-foreground border border-border"
                      title={`${tag.recording_count} recording${tag.recording_count !== 1 ? 's' : ''}`}
                    >
                      {tag.color && (
                        <span
                          className="w-1.5 h-1.5 rounded-full"
                          style={{ backgroundColor: tag.color }}
                        />
                      )}
                      {tag.name}
                    </span>
                  ))}
                </div>
              )}
              <p className="text-xs text-muted-foreground mt-3">
                {project.recording_count} recording{project.recording_count !== 1 ? 's' : ''}
              </p>
            </div>
          ))}
        </div>
      )}

      {/* Create Project Dialog */}
      {createDialogOpen && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
          onClick={(e) => {
            if ((e.target as HTMLElement).classList.contains('bg-black/50')) {
              setCreateDialogOpen(false);
            }
          }}
        >
          <div className="bg-card border border-border rounded-lg shadow-xl w-full max-w-md mx-4 max-h-[85vh] overflow-y-auto">
            <div className="flex items-center justify-between px-5 py-4 border-b border-border">
              <h2 className="text-lg font-semibold text-foreground">Create Project</h2>
              <button
                onClick={() => setCreateDialogOpen(false)}
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
                  placeholder="Project name"
                  className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
                />
              </div>
              <div className="space-y-1.5">
                <label className="text-sm font-medium text-foreground">Description</label>
                <textarea
                  value={form.description}
                  onChange={(e: React.ChangeEvent<HTMLTextAreaElement>) => setForm({ ...form, description: e.target.value })}
                  placeholder="Optional description"
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
                  suggestions={uniqueTags}
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
                onClick={() => setCreateDialogOpen(false)}
                className="px-4 py-2 text-sm font-medium rounded-lg border border-border text-foreground hover:bg-muted transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={handleCreateProject}
                disabled={!form.name.trim()}
                className="px-4 py-2 text-sm font-medium rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 transition-colors disabled:opacity-50"
              >
                Create
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Edit Project Dialog */}
      {editDialogOpen && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
          onClick={(e) => {
            if ((e.target as HTMLElement).classList.contains('bg-black/50')) {
              setEditDialogOpen(false);
            }
          }}
        >
          <div className="bg-card border border-border rounded-lg shadow-xl w-full max-w-md mx-4 max-h-[85vh] overflow-y-auto">
            <div className="flex items-center justify-between px-5 py-4 border-b border-border">
              <h2 className="text-lg font-semibold text-foreground">Edit Project</h2>
              <button
                onClick={() => setEditDialogOpen(false)}
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
                  suggestions={uniqueTags}
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
                onClick={() => setEditDialogOpen(false)}
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
      {deleteDialogOpen && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
          onClick={(e) => {
            if ((e.target as HTMLElement).classList.contains('bg-black/50')) {
              setDeleteDialogOpen(false);
              setDeleteFilesChoice(null);
            }
          }}
        >
          <div className="bg-card border border-border rounded-lg shadow-xl w-full max-w-md mx-4">
            <div className="px-5 py-4">
              <h2 className="text-lg font-semibold text-foreground">Move Project to Trash</h2>
              <p className="text-sm text-muted-foreground mt-2">
                Are you sure you want to delete "{selectedProject?.name}"?
              </p>
              {selectedProject && selectedProject.recording_count > 0 && (
                <div className="mt-4 space-y-3">
                  <p className="text-sm font-medium text-foreground">
                    This project contains {selectedProject.recording_count} recording{selectedProject.recording_count !== 1 ? 's' : ''}. What would you like to do?
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
                  setDeleteDialogOpen(false);
                  setDeleteFilesChoice(null);
                }}
                className="px-4 py-2 text-sm font-medium rounded-lg border border-border text-foreground hover:bg-muted transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={() => handleDeleteProject(deleteFilesChoice === true)}
                disabled={!!(selectedProject && selectedProject.recording_count > 0 && deleteFilesChoice === null)}
                className="px-4 py-2 text-sm font-medium rounded-lg bg-destructive text-destructive-foreground hover:bg-destructive/90 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              >
                Delete
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Type Manager Dialog */}
      {typeManagerOpen && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
          onClick={(e) => {
            if ((e.target as HTMLElement).classList.contains('bg-black/50')) {
              setTypeManagerOpen(false);
            }
          }}
        >
          <div
            ref={dialogRef}
            className="bg-card border border-border rounded-lg shadow-xl w-full max-w-2xl mx-4 max-h-[85vh] overflow-y-auto"
          >
            <div className="flex items-center justify-between px-5 py-4 border-b border-border sticky top-0 bg-card">
              <div className="flex items-center gap-2">
                <svg className="h-5 w-5 text-foreground" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z" />
                </svg>
                <h2 className="text-lg font-semibold text-foreground">Manage Project Types</h2>
              </div>
              <button
                onClick={() => setTypeManagerOpen(false)}
                className="p-1 rounded-md hover:bg-muted transition-colors text-muted-foreground"
              >
                <svg className="w-5 h-5" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>

            <div className="px-5 py-4 space-y-4">
              <p className="text-sm text-muted-foreground">
                Project types define custom metadata fields for organizing your projects.
              </p>

              {/* Create new type form */}
              <div className="border border-border rounded-lg p-4 space-y-3">
                <h4 className="font-medium text-foreground">Create New Type</h4>
                <div className="grid grid-cols-2 gap-3">
                  <div className="space-y-1.5">
                    <label className="text-xs font-medium text-foreground">Name</label>
                    <input
                      value={typeForm.name}
                      onChange={(e: React.ChangeEvent<HTMLInputElement>) =>
                        setTypeForm({ ...typeForm, name: e.target.value })
                      }
                      placeholder="Type name"
                      className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
                    />
                  </div>
                  <div className="space-y-1.5">
                    <label className="text-xs font-medium text-foreground">Description</label>
                    <input
                      value={typeForm.description}
                      onChange={(e: React.ChangeEvent<HTMLInputElement>) =>
                        setTypeForm({ ...typeForm, description: e.target.value })
                      }
                      placeholder="Optional description"
                      className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
                    />
                  </div>
                </div>
                <div className="space-y-1.5">
                  <label className="text-xs font-medium text-foreground">Custom Fields</label>
                  <MetadataFieldEditor
                    fields={typeForm.metadata_schema}
                    onChange={(metadata_schema) =>
                      setTypeForm({ ...typeForm, metadata_schema })
                    }
                  />
                </div>
                <button
                  onClick={handleCreateType}
                  disabled={!typeForm.name.trim()}
                  className="flex items-center gap-2 px-3 py-2 text-sm font-medium rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 transition-colors disabled:opacity-50"
                >
                  <svg className="h-4 w-4" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M12 4v16m8-8H4" />
                  </svg>
                  Create Type
                </button>
              </div>

              {/* Existing types */}
              <div className="space-y-2">
                <h4 className="font-medium text-foreground">Existing Types</h4>
                {projectTypes.length === 0 ? (
                  <p className="text-sm text-muted-foreground">No project types yet.</p>
                ) : (
                  <div className="space-y-2">
                    {projectTypes.map((type) => (
                      <div
                        key={type.id}
                        className="flex items-center justify-between border border-border rounded-lg p-3"
                      >
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2">
                            <span className="font-medium text-foreground">{type.name}</span>
                            {type.is_system && (
                              <span className="px-1.5 py-0.5 text-xs font-medium rounded bg-muted text-muted-foreground">
                                System
                              </span>
                            )}
                          </div>
                          {type.description && (
                            <p className="text-xs text-muted-foreground truncate">
                              {type.description}
                            </p>
                          )}
                          <p className="text-xs text-muted-foreground mt-1">
                            {type.metadata_schema.length} fields · {type.project_count} projects
                          </p>
                        </div>
                        <div className="flex items-center gap-1 ml-2">
                          <button
                            onClick={() => openTypeEditDialog(type)}
                            className="p-2 rounded-md hover:bg-muted transition-colors text-muted-foreground hover:text-foreground"
                          >
                            <svg className="h-4 w-4" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                              <path strokeLinecap="round" strokeLinejoin="round" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
                            </svg>
                          </button>
                          {!type.is_system && (
                            <button
                              onClick={() => openTypeDeleteDialog(type)}
                              className="p-2 rounded-md hover:bg-destructive/10 transition-colors text-destructive"
                            >
                              <svg className="h-4 w-4" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                                <path strokeLinecap="round" strokeLinejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                              </svg>
                            </button>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>

            <div className="flex items-center justify-end px-5 py-4 border-t border-border sticky bottom-0 bg-card">
              <button
                onClick={() => setTypeManagerOpen(false)}
                className="px-4 py-2 text-sm font-medium rounded-lg border border-border text-foreground hover:bg-muted transition-colors"
              >
                Done
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Edit Type Dialog */}
      {typeEditDialogOpen && (
        <div
          className="fixed inset-0 z-[60] flex items-center justify-center bg-black/50"
          onClick={(e) => {
            if ((e.target as HTMLElement).classList.contains('bg-black/50')) {
              setTypeEditDialogOpen(false);
            }
          }}
        >
          <div className="bg-card border border-border rounded-lg shadow-xl w-full max-w-lg mx-4 max-h-[85vh] overflow-y-auto">
            <div className="flex items-center justify-between px-5 py-4 border-b border-border">
              <h2 className="text-lg font-semibold text-foreground">Edit Project Type</h2>
              <button
                onClick={() => setTypeEditDialogOpen(false)}
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
                  value={typeForm.name}
                  onChange={(e: React.ChangeEvent<HTMLInputElement>) => setTypeForm({ ...typeForm, name: e.target.value })}
                  className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
                />
              </div>
              <div className="space-y-1.5">
                <label className="text-sm font-medium text-foreground">Description</label>
                <input
                  value={typeForm.description}
                  onChange={(e: React.ChangeEvent<HTMLInputElement>) =>
                    setTypeForm({ ...typeForm, description: e.target.value })
                  }
                  className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
                />
              </div>
              <div className="space-y-1.5">
                <label className="text-sm font-medium text-foreground">Custom Fields</label>
                <MetadataFieldEditor
                  fields={typeForm.metadata_schema}
                  onChange={(metadata_schema) =>
                    setTypeForm({ ...typeForm, metadata_schema })
                  }
                />
              </div>
            </div>
            <div className="flex items-center justify-end gap-2 px-5 py-4 border-t border-border">
              <button
                onClick={() => setTypeEditDialogOpen(false)}
                className="px-4 py-2 text-sm font-medium rounded-lg border border-border text-foreground hover:bg-muted transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={handleUpdateType}
                disabled={!typeForm.name.trim()}
                className="px-4 py-2 text-sm font-medium rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 transition-colors disabled:opacity-50"
              >
                Save
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Delete Type Dialog */}
      {typeDeleteDialogOpen && (
        <div
          className="fixed inset-0 z-[60] flex items-center justify-center bg-black/50"
          onClick={(e) => {
            if ((e.target as HTMLElement).classList.contains('bg-black/50')) {
              setTypeDeleteDialogOpen(false);
            }
          }}
        >
          <div className="bg-card border border-border rounded-lg shadow-xl w-full max-w-md mx-4">
            <div className="px-5 py-4">
              <h2 className="text-lg font-semibold text-foreground">Delete Project Type</h2>
              <p className="text-sm text-muted-foreground mt-2">
                Are you sure you want to delete "{editingType?.name}"?
                Projects using this type will have their type unassigned.
              </p>
            </div>
            <div className="flex items-center justify-end gap-2 px-5 py-4 border-t border-border">
              <button
                onClick={() => setTypeDeleteDialogOpen(false)}
                className="px-4 py-2 text-sm font-medium rounded-lg border border-border text-foreground hover:bg-muted transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={handleDeleteType}
                className="px-4 py-2 text-sm font-medium rounded-lg bg-destructive text-destructive-foreground hover:bg-destructive/90 transition-colors"
              >
                Delete
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

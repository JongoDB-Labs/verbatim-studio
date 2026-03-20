import { useCallback, useEffect, useMemo, useState } from 'react';
import { api, type Tag, type Project } from '@/lib/api';
import {
  useDocuments,
  useDeleteDocument,
  useBulkDeleteDocuments,
  useBulkAssignDocuments,
  useRunDocumentOcr,
  useCancelDocumentProcessing,
  useReprocessDocument,
} from '@/hooks';
import type { DocumentFilters as DocumentQueryFilters } from '@/lib/queryKeys';
import { DocumentCard } from '@/components/documents/DocumentCard';
import { DocumentsTable } from '@/components/documents/DocumentsTable';
import { DocumentFilters, type DocumentFilterState, type ViewMode } from '@/components/documents/DocumentFilters';
import { DocumentBulkActionBar } from '@/components/documents/DocumentBulkActionBar';
import { UploadDocumentDialog } from '@/components/documents/UploadDocumentDialog';
import { ProjectSelector } from '@/components/projects/ProjectSelector';
import { useProjectStore } from '@/stores/projectStore';

interface DocumentsPageProps {
  onViewDocument: (documentId: string) => void;
}

const STORAGE_KEY = 'verbatim-document-filters';
const VIEW_MODE_KEY = 'verbatim-document-view-mode';

const DEFAULT_FILTERS: DocumentFilterState = {
  search: '',
  status: '',
  sortBy: 'created_at',
  sortOrder: 'desc',
  dateFrom: '',
  dateTo: '',
  tagIds: [],
  mimeType: '',
};

function filtersFromUrlParams(): Partial<DocumentFilterState> {
  const params = new URLSearchParams(window.location.search);
  const result: Partial<DocumentFilterState> = {};

  if (params.has('search')) result.search = params.get('search')!;
  if (params.has('status')) result.status = params.get('status')!;
  if (params.has('sortBy')) result.sortBy = params.get('sortBy') as DocumentFilterState['sortBy'];
  if (params.has('sortOrder')) result.sortOrder = params.get('sortOrder') as 'asc' | 'desc';
  if (params.has('dateFrom')) result.dateFrom = params.get('dateFrom')!;
  if (params.has('dateTo')) result.dateTo = params.get('dateTo')!;
  if (params.has('tagIds')) result.tagIds = params.get('tagIds')!.split(',').filter(Boolean);
  if (params.has('mimeType')) result.mimeType = params.get('mimeType')!;

  return result;
}

function filtersToUrlParams(filters: DocumentFilterState): string {
  const params = new URLSearchParams();

  if (filters.search) params.set('search', filters.search);
  if (filters.status) params.set('status', filters.status);
  if (filters.sortBy !== 'created_at') params.set('sortBy', filters.sortBy);
  if (filters.sortOrder !== 'desc') params.set('sortOrder', filters.sortOrder);
  if (filters.dateFrom) params.set('dateFrom', filters.dateFrom);
  if (filters.dateTo) params.set('dateTo', filters.dateTo);
  if (filters.tagIds.length > 0) params.set('tagIds', filters.tagIds.join(','));
  if (filters.mimeType) params.set('mimeType', filters.mimeType);

  return params.toString();
}

function loadSavedFilters(): DocumentFilterState {
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
  return 'grid';
}

export function DocumentsPage({ onViewDocument }: DocumentsPageProps) {
  const { selectedProjects } = useProjectStore();

  // Local UI state
  const [filters, setFilters] = useState<DocumentFilterState>(loadSavedFilters);
  const [viewMode, setViewMode] = useState<ViewMode>(loadSavedViewMode);
  const [showUpload, setShowUpload] = useState(false);
  const [selectedProjectId, setSelectedProjectId] = useState<string | null>(null);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [allTags, setAllTags] = useState<Tag[]>([]);
  const [allProjects, setAllProjects] = useState<Project[]>([]);

  // Build query filters from local state
  const queryFilters: DocumentQueryFilters = useMemo(() => ({
    search: filters.search || undefined,
    status: filters.status || undefined,
    sortBy: filters.sortBy,
    sortOrder: filters.sortOrder,
    projectId: selectedProjectId || undefined,
    dateFrom: filters.dateFrom || undefined,
    dateTo: filters.dateTo || undefined,
    tagIds: filters.tagIds.length > 0 ? filters.tagIds : undefined,
    mimeType: filters.mimeType || undefined,
  }), [filters, selectedProjectId]);

  // React Query hooks for data fetching
  const { data: documentsData, isLoading } = useDocuments(queryFilters);
  const documents = documentsData?.items ?? [];
  const totalDocuments = documentsData?.total ?? 0;

  // Clear selection when filters change so bulk actions don't affect hidden documents
  useEffect(() => {
    setSelectedIds(new Set());
  }, [filters, selectedProjectId]);

  // React Query mutations
  const deleteDocument = useDeleteDocument();
  const bulkDeleteDocuments = useBulkDeleteDocuments();
  const bulkAssignDocuments = useBulkAssignDocuments();
  const runOcr = useRunDocumentOcr();
  const cancelProcessing = useCancelDocumentProcessing();
  const reprocessDocument = useReprocessDocument();

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

    const { search: _, ...persistable } = filters;
    localStorage.setItem(STORAGE_KEY, JSON.stringify(persistable));
  }, [filters]);

  // Persist view mode
  useEffect(() => {
    localStorage.setItem(VIEW_MODE_KEY, viewMode);
  }, [viewMode]);

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

  // Refresh when storage location changes
  useEffect(() => {
    const handleStorageLocationChange = () => {
      // React Query will handle this via WebSocket invalidation
    };
    window.addEventListener('storage-location-changed', handleStorageLocationChange);
    window.addEventListener('storage-synced', handleStorageLocationChange);
    return () => {
      window.removeEventListener('storage-location-changed', handleStorageLocationChange);
      window.removeEventListener('storage-synced', handleStorageLocationChange);
    };
  }, []);

  const handleDelete = useCallback(
    (documentId: string) => {
      deleteDocument.mutate(documentId);
    },
    [deleteDocument]
  );

  const handleRunOcr = useCallback(
    (documentId: string) => {
      runOcr.mutate(documentId);
    },
    [runOcr]
  );

  const handleCancel = useCallback(
    (documentId: string) => {
      cancelProcessing.mutate(documentId);
    },
    [cancelProcessing]
  );

  const handleReprocess = useCallback(
    (documentId: string) => {
      reprocessDocument.mutate(documentId);
    },
    [reprocessDocument]
  );

  const handleFiltersChange = useCallback((newFilters: DocumentFilterState) => {
    setFilters(newFilters);
  }, []);

  const handleViewModeChange = useCallback((mode: ViewMode) => {
    setViewMode(mode);
  }, []);

  const handleSortChange = useCallback(
    (sortBy: 'created_at' | 'title' | 'file_size_bytes', sortOrder: 'asc' | 'desc') => {
      setFilters((prev) => ({ ...prev, sortBy, sortOrder }));
    },
    []
  );

  // Bulk selection handlers
  const handleSelectDocument = useCallback((id: string, selected: boolean) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (selected) {
        next.add(id);
      } else {
        next.delete(id);
      }
      return next;
    });
  }, []);

  const handleSelectAll = useCallback(
    (selected: boolean) => {
      if (selected) {
        setSelectedIds(new Set(documents.map((d) => d.id)));
      } else {
        setSelectedIds(new Set());
      }
    },
    [documents]
  );

  const handleClearSelection = useCallback(() => {
    setSelectedIds(new Set());
  }, []);

  const handleBulkDelete = useCallback(() => {
    if (selectedIds.size === 0) return;
    bulkDeleteDocuments.mutate(Array.from(selectedIds), {
      onSuccess: () => {
        setSelectedIds(new Set());
      },
    });
  }, [selectedIds, bulkDeleteDocuments]);

  const handleBulkAssignProject = useCallback(
    (projectId: string | null) => {
      if (selectedIds.size === 0) return;
      bulkAssignDocuments.mutate(
        { ids: Array.from(selectedIds), projectId },
        {
          onSuccess: () => {
            setSelectedIds(new Set());
          },
        }
      );
    },
    [selectedIds, bulkAssignDocuments]
  );

  const handleUploaded = useCallback(() => {
    // React Query will handle cache invalidation via WebSocket
  }, []);

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
      <div className="flex items-center justify-between">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100">Documents</h1>
            {selectedProjects.length > 0 && (
              <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-xs bg-zinc-100 dark:bg-zinc-800 text-zinc-600 dark:text-zinc-400">
                {selectedProjects[0].color && (
                  <span className="w-2 h-2 rounded-full" style={{ backgroundColor: selectedProjects[0].color }} />
                )}
                {selectedProjects[0].name}
                {selectedProjects.length > 1 && (
                  <span className="text-zinc-400 dark:text-zinc-500">+{selectedProjects.length - 1} more</span>
                )}
              </span>
            )}
          </div>
          <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
            Upload and manage your documents
          </p>
        </div>
        <button
          onClick={() => setShowUpload(true)}
          className="inline-flex items-center gap-2 px-4 py-2 bg-primary text-primary-foreground text-sm font-medium rounded-lg hover:bg-primary/90 transition-colors"
        >
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
            <path strokeLinecap="round" strokeLinejoin="round" d="M12 4v16m8-8H4" />
          </svg>
          Upload Document
        </button>
      </div>

      {/* Project Selector + Filters */}
      <div className="flex flex-col gap-4">
        <ProjectSelector
          selectedProjectId={selectedProjectId}
          onProjectChange={setSelectedProjectId}
        />
        <DocumentFilters
          filters={filters}
          onFiltersChange={handleFiltersChange}
          totalResults={totalDocuments}
          viewMode={viewMode}
          onViewModeChange={handleViewModeChange}
        />
      </div>

      {/* Document list */}
      {documents.length === 0 ? (
        <div className="text-center py-12">
          {filters.search || filters.status || filters.dateFrom || filters.dateTo || filters.tagIds.length > 0 || filters.mimeType ? (
            <p className="text-muted-foreground">
              No documents match your filters.
            </p>
          ) : (
            <>
              <svg className="w-12 h-12 mx-auto text-gray-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="1">
                <path strokeLinecap="round" strokeLinejoin="round" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
              </svg>
              <h3 className="mt-4 text-sm font-medium text-gray-900 dark:text-gray-100">No documents</h3>
              <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
                Upload your first document to get started
              </p>
              <button
                onClick={() => setShowUpload(true)}
                className="mt-4 inline-flex items-center gap-2 px-4 py-2 text-sm font-medium text-blue-600 hover:text-blue-700"
              >
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M12 4v16m8-8H4" />
                </svg>
                Upload Document
              </button>
            </>
          )}
        </div>
      ) : viewMode === 'grid' ? (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {documents.map((doc) => (
            <DocumentCard
              key={doc.id}
              document={doc}
              onClick={() => doc.status === 'completed' && onViewDocument(doc.id)}
              onDelete={() => handleDelete(doc.id)}
              isSelected={selectedIds.has(doc.id)}
              onSelectChange={(selected) => handleSelectDocument(doc.id, selected)}
              allTags={allTags}
              allProjects={allProjects}
            />
          ))}
        </div>
      ) : (
        <DocumentsTable
          documents={documents}
          sortBy={filters.sortBy}
          sortOrder={filters.sortOrder}
          onSortChange={handleSortChange}
          onView={(id) => onViewDocument(id)}
          onDelete={(id) => handleDelete(id)}
          onRunOcr={(id) => handleRunOcr(id)}
          onCancel={(id) => handleCancel(id)}
          onReprocess={(id) => handleReprocess(id)}
          selectedIds={selectedIds}
          onSelectDocument={handleSelectDocument}
          onSelectAll={handleSelectAll}
          allTags={allTags}
          allProjects={allProjects}
        />
      )}

      {/* Upload dialog */}
      <UploadDocumentDialog
        open={showUpload}
        onClose={() => setShowUpload(false)}
        onUploaded={handleUploaded}
        projects={allProjects}
      />

      {/* Bulk Action Bar */}
      <DocumentBulkActionBar
        selectedCount={selectedIds.size}
        onDelete={handleBulkDelete}
        onAssignProject={handleBulkAssignProject}
        onClearSelection={handleClearSelection}
      />
    </div>
  );
}

import { cn, formatDateTime } from '@/lib/utils';
import type { Document, Tag, Project } from '@/lib/api';
import { DocumentThumbnail } from './DocumentThumbnail';

type SortKey = 'created_at' | 'title' | 'file_size_bytes';

interface DocumentsTableProps {
  documents: Document[];
  sortBy: SortKey;
  sortOrder: 'asc' | 'desc';
  onSortChange: (sortBy: SortKey, sortOrder: 'asc' | 'desc') => void;
  onView: (documentId: string) => void;
  onDelete: (documentId: string) => void;
  onRunOcr: (documentId: string) => void;
  onCancel: (documentId: string) => void;
  onReprocess: (documentId: string) => void;
  selectedIds: Set<string>;
  onSelectDocument: (id: string, selected: boolean) => void;
  onSelectAll: (selected: boolean) => void;
  allTags?: Tag[];
  allProjects?: Project[];
  showPreview?: boolean;
}

function formatFileSize(bytes: number): string {
  if (bytes >= 1_073_741_824) return `${(bytes / 1_073_741_824).toFixed(1)} GB`;
  if (bytes >= 1_048_576) return `${(bytes / 1_048_576).toFixed(1)} MB`;
  if (bytes >= 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${bytes} B`;
}

const statusConfig: Record<string, { label: string; className: string }> = {
  pending: {
    label: 'Pending',
    className: 'bg-yellow-100 text-yellow-800 dark:bg-yellow-900 dark:text-yellow-200',
  },
  processing: {
    label: 'Processing',
    className: 'bg-blue-100 text-blue-800 dark:bg-blue-900 dark:text-blue-200',
  },
  completed: {
    label: 'Completed',
    className: 'bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-200',
  },
  failed: {
    label: 'Failed',
    className: 'bg-red-100 text-red-800 dark:bg-red-900 dark:text-red-200',
  },
  cancelled: {
    label: 'Cancelled',
    className: 'bg-gray-100 text-gray-800 dark:bg-gray-700 dark:text-gray-200',
  },
};

const COLUMNS: Array<{
  key: string;
  label: string;
  sortKey?: SortKey;
  className: string;
  headerClass?: string;
}> = [
  { key: 'checkbox', label: '', className: 'w-10', headerClass: 'w-10' },
  { key: 'title', label: 'Title', sortKey: 'title', className: '', headerClass: '' },
  { key: 'size', label: 'Size', sortKey: 'file_size_bytes', className: 'hidden md:table-cell w-24', headerClass: 'hidden md:table-cell w-24' },
  { key: 'pages', label: 'Pages', className: 'hidden lg:table-cell w-20', headerClass: 'hidden lg:table-cell w-20' },
  { key: 'status', label: 'Status', className: 'w-28', headerClass: 'w-28' },
  { key: 'created_at', label: 'Date', sortKey: 'created_at', className: 'hidden lg:table-cell w-44', headerClass: 'hidden lg:table-cell w-44' },
  { key: 'actions', label: '', className: '', headerClass: '' },
];

function SortIcon({ direction }: { direction: 'asc' | 'desc' }) {
  return (
    <svg
      className="w-3.5 h-3.5 inline-block ml-1"
      xmlns="http://www.w3.org/2000/svg"
      fill="none"
      viewBox="0 0 24 24"
      stroke="currentColor"
      strokeWidth="2"
    >
      {direction === 'asc' ? (
        <path strokeLinecap="round" strokeLinejoin="round" d="M5 15l7-7 7 7" />
      ) : (
        <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
      )}
    </svg>
  );
}

export function DocumentsTable({
  documents,
  sortBy,
  sortOrder,
  onSortChange,
  onView,
  onDelete,
  onRunOcr,
  onCancel,
  onReprocess,
  selectedIds,
  onSelectDocument,
  onSelectAll,
  allTags = [],
  allProjects = [],
  showPreview = true,
}: DocumentsTableProps) {
  const handleHeaderClick = (key: SortKey) => {
    if (sortBy === key) {
      onSortChange(key, sortOrder === 'asc' ? 'desc' : 'asc');
    } else {
      onSortChange(key, 'asc');
    }
  };

  const allSelected = documents.length > 0 && documents.every((d) => selectedIds.has(d.id));
  const someSelected = documents.some((d) => selectedIds.has(d.id)) && !allSelected;

  return (
    <div className="rounded-lg border border-border overflow-hidden">
      <table className="w-full">
        <thead>
          <tr className="bg-muted/50 border-b border-border">
            {COLUMNS.map((col) => (
              <th
                key={col.key}
                className={cn(
                  'px-3 py-2.5 text-left text-xs font-medium text-muted-foreground uppercase tracking-wider',
                  col.headerClass
                )}
              >
                {col.key === 'checkbox' ? (
                  <input
                    type="checkbox"
                    checked={allSelected}
                    ref={(el) => {
                      if (el) el.indeterminate = someSelected;
                    }}
                    onChange={(e) => onSelectAll(e.target.checked)}
                    className="h-4 w-4 rounded border-gray-300 text-primary focus:ring-primary"
                  />
                ) : col.sortKey ? (
                  <button
                    onClick={() => handleHeaderClick(col.sortKey!)}
                    className="inline-flex items-center hover:text-foreground transition-colors"
                  >
                    {col.label}
                    {sortBy === col.sortKey && <SortIcon direction={sortOrder} />}
                  </button>
                ) : (
                  col.label
                )}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {documents.map((doc) => {
            const status = statusConfig[doc.status] || statusConfig.pending;

            return (
              <DocumentRow
                key={doc.id}
                document={doc}
                status={status}
                onView={onView}
                onDelete={onDelete}
                onRunOcr={onRunOcr}
                onCancel={onCancel}
                onReprocess={onReprocess}
                isSelected={selectedIds.has(doc.id)}
                onSelectChange={(selected) => onSelectDocument(doc.id, selected)}
                allTags={allTags}
                allProjects={allProjects}
                showPreview={showPreview}
              />
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function DocumentRow({
  document,
  status,
  onView,
  onDelete,
  onRunOcr,
  onCancel,
  onReprocess,
  isSelected,
  onSelectChange,
  allTags,
  allProjects,
  showPreview,
}: {
  document: Document;
  status: { label: string; className: string };
  onView: (id: string) => void;
  onDelete: (id: string) => void;
  onRunOcr: (id: string) => void;
  onCancel: (id: string) => void;
  onReprocess: (id: string) => void;
  isSelected: boolean;
  onSelectChange: (selected: boolean) => void;
  allTags: Tag[];
  allProjects: Project[];
  showPreview: boolean;
}) {
  const docTags = allTags.filter((t) => document.tag_ids?.includes(t.id));
  const docProjects = allProjects.filter((p) => document.project_ids?.includes(p.id));
  const isClickable = document.status === 'completed';

  const handleRowClick = (e: React.MouseEvent) => {
    const target = e.target as HTMLElement;
    if (target.closest('button') || target.closest('input') || target.closest('a')) {
      return;
    }
    if (isClickable) {
      onView(document.id);
    }
  };

  return (
    <tr
      className={cn(
        'hover:bg-muted/30 transition-colors border-b border-border',
        isSelected && 'bg-primary/5',
        isClickable && 'cursor-pointer'
      )}
      onClick={handleRowClick}
    >
      {/* Checkbox */}
      <td className="px-3 py-2.5 w-10">
        <input
          type="checkbox"
          checked={isSelected}
          onChange={(e) => onSelectChange(e.target.checked)}
          className="h-4 w-4 rounded border-gray-300 text-primary focus:ring-primary"
        />
      </td>

      {/* Title */}
      <td className="px-3 py-2.5 min-w-0">
        <div className="flex items-center gap-2">
          <DocumentThumbnail document={document} showPreview={showPreview} size="sm" className="shrink-0" />
          <div className="min-w-0 flex-1">
            <h3 className="font-medium text-sm truncate" title={document.title}>
              {document.title}
            </h3>
            <p className="text-xs text-muted-foreground truncate" title={document.filename}>
              {document.filename}
            </p>
            {/* Tags and Projects */}
            {(docTags.length > 0 || docProjects.length > 0) && (
              <div className="flex flex-wrap gap-1 mt-1">
                {docTags.map((tag) => (
                  <span
                    key={tag.id}
                    className="inline-flex items-center gap-0.5 rounded-full px-1.5 py-0.5 text-[10px] font-medium bg-primary/10 text-primary"
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
                {docProjects.map((project) => (
                  <span
                    key={project.id}
                    className="inline-flex items-center gap-0.5 rounded-full px-1.5 py-0.5 text-[10px] font-medium bg-muted text-muted-foreground"
                  >
                    <svg className="w-2.5 h-2.5" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                      <path strokeLinecap="round" strokeLinejoin="round" d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z" />
                    </svg>
                    {project.name}
                  </span>
                ))}
              </div>
            )}
          </div>
        </div>
      </td>

      {/* Size */}
      <td className="hidden md:table-cell px-3 py-2.5 text-xs text-muted-foreground whitespace-nowrap">
        {formatFileSize(document.file_size_bytes)}
      </td>

      {/* Pages */}
      <td className="hidden lg:table-cell px-3 py-2.5 text-xs text-muted-foreground whitespace-nowrap">
        {document.page_count ?? '—'}
      </td>

      {/* Status */}
      <td className="px-3 py-2.5">
        <span
          className={cn(
            'inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium whitespace-nowrap',
            status.className
          )}
        >
          {status.label}
        </span>
      </td>

      {/* Date */}
      <td className="hidden lg:table-cell px-3 py-2.5 text-xs text-muted-foreground whitespace-nowrap">
        {formatDateTime(document.created_at)}
      </td>

      {/* Actions */}
      <td className="px-3 py-2.5">
        <div className="flex items-center gap-1.5 justify-end">
          {document.status === 'completed' && (
            <button
              onClick={() => onView(document.id)}
              className="inline-flex items-center justify-center rounded-md bg-primary px-2.5 py-1 text-xs font-medium text-primary-foreground hover:bg-primary/90 transition-colors"
            >
              View
            </button>
          )}
          {document.status === 'pending' && (
            <button
              onClick={() => onRunOcr(document.id)}
              className="inline-flex items-center justify-center rounded-md bg-primary px-2.5 py-1 text-xs font-medium text-primary-foreground hover:bg-primary/90 transition-colors"
            >
              Process
            </button>
          )}
          {document.status === 'processing' && (
            <button
              onClick={() => onCancel(document.id)}
              className="inline-flex items-center justify-center rounded-md border border-orange-500/50 px-2.5 py-1 text-xs font-medium text-orange-600 dark:text-orange-400 hover:bg-orange-500/10 transition-colors"
            >
              Cancel
            </button>
          )}
          {(document.status === 'failed' || document.status === 'cancelled') && (
            <button
              onClick={() => onReprocess(document.id)}
              className="inline-flex items-center justify-center rounded-md bg-primary px-2.5 py-1 text-xs font-medium text-primary-foreground hover:bg-primary/90 transition-colors"
            >
              Retry
            </button>
          )}
          <button
            onClick={() => onDelete(document.id)}
            className="inline-flex items-center justify-center rounded-md border border-destructive/50 px-2.5 py-1 text-xs font-medium text-destructive hover:bg-destructive/10 transition-colors"
          >
            Delete
          </button>
        </div>
      </td>
    </tr>
  );
}

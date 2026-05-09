import type { Document, Tag, Project } from '@/lib/api';
import { formatRelativeTime } from '@/lib/utils';
import { cn } from '@/lib/utils';
import { DocumentThumbnail } from './DocumentThumbnail';

interface DocumentCardProps {
  document: Document;
  onClick: () => void;
  onDelete?: () => void;
  isSelected?: boolean;
  onSelectChange?: (selected: boolean) => void;
  allTags?: Tag[];
  allProjects?: Project[];
  showPreview?: boolean;
}

const STATUS_STYLES: Record<string, string> = {
  pending: 'bg-yellow-100 text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-300',
  processing: 'bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-300',
  completed: 'bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-300',
  failed: 'bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-300',
};

function formatBytes(bytes: number): string {
  if (bytes >= 1_073_741_824) return `${(bytes / 1_073_741_824).toFixed(1)} GB`;
  if (bytes >= 1_048_576) return `${(bytes / 1_048_576).toFixed(1)} MB`;
  if (bytes >= 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${bytes} B`;
}

export function DocumentCard({
  document,
  onClick,
  onDelete,
  isSelected,
  onSelectChange,
  allTags = [],
  allProjects = [],
  showPreview = true,
}: DocumentCardProps) {
  const statusStyle = STATUS_STYLES[document.status] || STATUS_STYLES.pending;
  const docTags = allTags.filter((t) => document.tag_ids?.includes(t.id));
  const docProjects = allProjects.filter((p) => document.project_ids?.includes(p.id));

  return (
    <div
      onClick={onClick}
      className={cn(
        'group relative p-4 rounded-lg border bg-white dark:bg-gray-800 hover:border-blue-300 dark:hover:border-blue-600 hover:shadow-md transition-all cursor-pointer',
        isSelected
          ? 'border-primary bg-primary/5'
          : 'border-gray-200 dark:border-gray-700'
      )}
    >
      {/* Selection checkbox */}
      {onSelectChange && (
        <div className="absolute top-2 left-2">
          <input
            type="checkbox"
            checked={isSelected ?? false}
            onChange={(e) => {
              e.stopPropagation();
              onSelectChange(e.target.checked);
            }}
            onClick={(e) => e.stopPropagation()}
            className="h-4 w-4 rounded border-gray-300 text-primary focus:ring-primary"
          />
        </div>
      )}

      <div className={cn('flex items-start gap-3', onSelectChange && 'ml-6')}>
        <DocumentThumbnail document={document} showPreview={showPreview} size="md" />
        <div className="flex-1 min-w-0">
          <h3 className="font-medium text-gray-900 dark:text-gray-100 truncate">
            {document.title}
          </h3>
          <p className="text-sm text-gray-500 dark:text-gray-400 truncate">
            {document.filename}
          </p>
          <div className="mt-2 flex items-center gap-2 text-xs">
            <span className={`px-2 py-0.5 rounded-full ${statusStyle}`}>
              {document.status}
            </span>
            <span className="text-gray-400 dark:text-gray-500">
              {formatBytes(document.file_size_bytes)}
            </span>
            {document.page_count && (
              <span className="text-gray-400 dark:text-gray-500">
                {document.page_count} pages
              </span>
            )}
          </div>

          {/* Tags and Projects */}
          {(docTags.length > 0 || docProjects.length > 0) && (
            <div className="flex flex-wrap gap-1 mt-2">
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

          <p className="mt-1 text-xs text-gray-400 dark:text-gray-500">
            {formatRelativeTime(document.created_at)}
          </p>
        </div>
      </div>

      {onDelete && (
        <button
          onClick={(e) => {
            e.stopPropagation();
            onDelete();
          }}
          className="absolute top-2 right-2 p-1.5 rounded-md opacity-0 group-hover:opacity-100 text-gray-400 hover:text-red-500 hover:bg-red-50 dark:hover:bg-red-900/20 transition-all"
          title="Delete document"
        >
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
            <path strokeLinecap="round" strokeLinejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
          </svg>
        </button>
      )}
    </div>
  );
}

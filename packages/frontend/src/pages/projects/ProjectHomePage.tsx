import { useQuery } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { queryKeys } from '@/lib/queryKeys';

interface ProjectHomePageProps {
  projectId: string;
  onNavigateToRecordings: () => void;
  onNavigateToDocuments: () => void;
  onViewTranscript: (recordingId: string) => void;
  onViewDocument: (documentId: string) => void;
}

export function ProjectHomePage({
  projectId,
  onNavigateToRecordings,
  onNavigateToDocuments,
  onViewTranscript,
  onViewDocument,
}: ProjectHomePageProps) {
  const { data: project } = useQuery({
    queryKey: queryKeys.projects.detail(projectId),
    queryFn: () => api.projects.get(projectId),
  });

  const { data: sections } = useQuery({
    queryKey: ['projects', projectId, 'sections'],
    queryFn: () => api.projects.getSections(projectId),
  });

  const { data: recordings } = useQuery({
    queryKey: queryKeys.recordings.list({ projectId, sortBy: 'created_at', sortOrder: 'desc' }),
    queryFn: () => api.recordings.list({ projectId, sortBy: 'created_at', sortOrder: 'desc', pageSize: 4 }),
  });

  const { data: documents } = useQuery({
    queryKey: queryKeys.documents.list({ projectId, sortBy: 'created_at', sortOrder: 'desc' }),
    queryFn: () => api.documents.list({ project_id: projectId, sort_by: 'created_at', sort_order: 'desc', page_size: 4 }),
  });

  if (!project) return null;

  return (
    <div className="max-w-5xl mx-auto p-6 space-y-6">
      {/* Project Header */}
      <div>
        <div className="flex items-center gap-3 mb-2">
          {project.color && (
            <span className="w-4 h-4 rounded-full" style={{ backgroundColor: project.color }} />
          )}
          <h1 className="text-2xl font-bold text-zinc-900 dark:text-zinc-100">
            {project.name}
          </h1>
        </div>
        {project.description && (
          <p className="text-zinc-600 dark:text-zinc-400">{project.description}</p>
        )}
      </div>

      {/* Type Section Cards */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {/* Recordings Section */}
        {(sections?.recordings ?? 0) > 0 && (
          <div className="bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700 rounded-lg p-4">
            <div className="flex items-center justify-between mb-3">
              <h2 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">
                Recordings ({sections?.recordings})
              </h2>
              <button
                onClick={onNavigateToRecordings}
                className="text-xs text-blue-600 dark:text-blue-400 hover:underline"
              >
                View all
              </button>
            </div>
            <div className="space-y-2">
              {recordings?.items?.slice(0, 4).map((rec) => (
                <button
                  key={rec.id}
                  onClick={() => onViewTranscript(rec.id)}
                  className="w-full text-left px-2 py-1.5 rounded text-sm hover:bg-zinc-100 dark:hover:bg-zinc-800 truncate text-zinc-700 dark:text-zinc-300"
                >
                  {rec.title}
                </button>
              ))}
            </div>
          </div>
        )}

        {/* Documents Section */}
        {(sections?.documents ?? 0) > 0 && (
          <div className="bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700 rounded-lg p-4">
            <div className="flex items-center justify-between mb-3">
              <h2 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">
                Documents ({sections?.documents})
              </h2>
              <button
                onClick={onNavigateToDocuments}
                className="text-xs text-blue-600 dark:text-blue-400 hover:underline"
              >
                View all
              </button>
            </div>
            <div className="space-y-2">
              {documents?.items?.slice(0, 4).map((doc) => (
                <button
                  key={doc.id}
                  onClick={() => onViewDocument(doc.id)}
                  className="w-full text-left px-2 py-1.5 rounded text-sm hover:bg-zinc-100 dark:hover:bg-zinc-800 truncate text-zinc-700 dark:text-zinc-300"
                >
                  {doc.title}
                </button>
              ))}
            </div>
          </div>
        )}

        {/* Notes Section */}
        {(sections?.notes ?? 0) > 0 && (
          <div className="bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700 rounded-lg p-4">
            <h2 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100 mb-3">
              Notes ({sections?.notes})
            </h2>
            <p className="text-xs text-zinc-500">Notes are accessible from their parent recordings and documents.</p>
          </div>
        )}
      </div>

      {/* Empty State */}
      {(sections?.recordings ?? 0) === 0 && (sections?.documents ?? 0) === 0 && (
        <div className="text-center py-12 text-zinc-500">
          <p className="text-lg mb-2">This project is empty</p>
          <p className="text-sm">Upload recordings or documents, or move existing content into this project.</p>
        </div>
      )}
    </div>
  );
}

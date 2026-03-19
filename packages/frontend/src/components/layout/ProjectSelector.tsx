import { useState, useRef, useEffect } from 'react';
import { useProjectStore } from '@/stores/projectStore';
import { api } from '@/lib/api';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { queryKeys } from '@/lib/queryKeys';

interface ProjectSelectorProps {
  collapsed: boolean;
}

export function ProjectSelector({ collapsed }: ProjectSelectorProps) {
  const { activeProject, setActiveProject } = useProjectStore();
  const [isOpen, setIsOpen] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);
  const queryClient = useQueryClient();

  const { data: projectsData } = useQuery({
    queryKey: queryKeys.projects.list(),
    queryFn: () => api.projects.list(),
  });

  const { data: archivedData } = useQuery({
    queryKey: queryKeys.projects.list({ search: '__archived__' }),
    queryFn: () => api.projects.list({ include_archived: true }),
  });

  // Close dropdown when clicking outside
  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setIsOpen(false);
      }
    }
    if (isOpen) document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [isOpen]);

  const handleSelect = async (project: { id: string; name: string; color?: string | null; icon?: string | null } | null) => {
    setActiveProject(project);
    setIsOpen(false);
    // Persist to backend
    await api.projects.setActiveProject(project?.id ?? null);
    // Invalidate all scoped queries so they refetch with new scope
    queryClient.invalidateQueries({ queryKey: queryKeys.recordings.all });
    queryClient.invalidateQueries({ queryKey: queryKeys.documents.all });
    queryClient.invalidateQueries({ queryKey: queryKeys.conversations.all });
    queryClient.invalidateQueries({ queryKey: queryKeys.search.history });
    queryClient.invalidateQueries({ queryKey: queryKeys.dashboard.stats });
  };

  const projects = (projectsData?.items ?? []).filter(p => !p.is_archived);
  const archived = (archivedData?.items ?? []).filter(p => p.is_archived);

  return (
    <div className="relative" ref={dropdownRef}>
      <button
        onClick={() => setIsOpen(!isOpen)}
        className="w-full flex items-center gap-2 px-3 py-2 rounded-lg text-sm font-medium
          bg-zinc-100 dark:bg-zinc-800 hover:bg-zinc-200 dark:hover:bg-zinc-700
          transition-colors text-left"
        title={activeProject?.name ?? 'All Projects'}
      >
        {activeProject?.color && (
          <span
            className="w-3 h-3 rounded-full flex-shrink-0"
            style={{ backgroundColor: activeProject.color }}
          />
        )}
        {!activeProject && (
          <svg className="w-4 h-4 flex-shrink-0 text-zinc-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
            <path strokeLinecap="round" strokeLinejoin="round" d="M4 6h16M4 12h16M4 18h16" />
          </svg>
        )}
        {!collapsed && (
          <>
            <span className="truncate flex-1">
              {activeProject?.name ?? 'All Projects'}
            </span>
            <svg className={`w-4 h-4 flex-shrink-0 transition-transform ${isOpen ? 'rotate-180' : ''}`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
              <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
            </svg>
          </>
        )}
      </button>

      {isOpen && (
        <div className="absolute top-full left-0 right-0 mt-1 z-50 bg-white dark:bg-zinc-900
          border border-zinc-200 dark:border-zinc-700 rounded-lg shadow-lg overflow-hidden min-w-[200px]">
          {/* All Projects option */}
          <button
            onClick={() => handleSelect(null)}
            className={`w-full flex items-center gap-2 px-3 py-2 text-sm hover:bg-zinc-100 dark:hover:bg-zinc-800
              ${!activeProject ? 'bg-zinc-100 dark:bg-zinc-800 font-medium' : ''}`}
          >
            <svg className="w-4 h-4 text-zinc-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
              <path strokeLinecap="round" strokeLinejoin="round" d="M4 6h16M4 12h16M4 18h16" />
            </svg>
            All Projects
          </button>

          {projects.length > 0 && (
            <div className="border-t border-zinc-200 dark:border-zinc-700" />
          )}

          {/* Active projects */}
          {projects.map((project) => (
            <button
              key={project.id}
              onClick={() => handleSelect({
                id: project.id,
                name: project.name,
                color: project.color,
                icon: project.icon,
              })}
              className={`w-full flex items-center gap-2 px-3 py-2 text-sm hover:bg-zinc-100 dark:hover:bg-zinc-800
                ${activeProject?.id === project.id ? 'bg-zinc-100 dark:bg-zinc-800 font-medium' : ''}`}
            >
              {project.color ? (
                <span className="w-3 h-3 rounded-full flex-shrink-0" style={{ backgroundColor: project.color }} />
              ) : (
                <span className="w-3 h-3 rounded-full flex-shrink-0 bg-zinc-400" />
              )}
              <span className="truncate">{project.name}</span>
            </button>
          ))}

          {/* Archived section */}
          {archived.length > 0 && (
            <>
              <div className="border-t border-zinc-200 dark:border-zinc-700" />
              <div className="px-3 py-1.5 text-xs text-zinc-500 uppercase tracking-wider">Archived</div>
              {archived.map((project) => (
                <button
                  key={project.id}
                  onClick={() => handleSelect({
                    id: project.id,
                    name: project.name,
                    color: project.color,
                    icon: project.icon,
                  })}
                  className="w-full flex items-center gap-2 px-3 py-2 text-sm text-zinc-500 hover:bg-zinc-100 dark:hover:bg-zinc-800"
                >
                  <span className="w-3 h-3 rounded-full flex-shrink-0 bg-zinc-300 dark:bg-zinc-600" />
                  <span className="truncate">{project.name}</span>
                </button>
              ))}
            </>
          )}
        </div>
      )}
    </div>
  );
}

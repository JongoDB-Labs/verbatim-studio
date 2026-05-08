import { useState, useRef, useEffect } from 'react';
import { useProjectStore, type ActiveProject } from '@/stores/projectStore';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { queryKeys } from '@/lib/queryKeys';

interface ProjectSelectorProps {
  collapsed: boolean;
}

export function ProjectSelector({ collapsed }: ProjectSelectorProps) {
  const { selectedProjects, toggleProject, clearProjects } = useProjectStore();
  const [isOpen, setIsOpen] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);
  const queryClient = useQueryClient();

  const { data: projectsData } = useQuery({
    queryKey: queryKeys.projects.list(),
    queryFn: () => api.projects.list(),
  });

  const { data: archivedData } = useQuery({
    queryKey: queryKeys.projects.list({ includeArchived: true }),
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

  const invalidateQueries = () => {
    queryClient.invalidateQueries({ queryKey: queryKeys.recordings.all });
    queryClient.invalidateQueries({ queryKey: queryKeys.documents.all });
    queryClient.invalidateQueries({ queryKey: queryKeys.conversations.all });
    queryClient.invalidateQueries({ queryKey: queryKeys.search.history });
    queryClient.invalidateQueries({ queryKey: queryKeys.dashboard.stats });
  };

  const handleAllProjectsClick = () => {
    clearProjects();
    invalidateQueries();
    // Do NOT close the dropdown
  };

  const handleToggleProject = (project: ActiveProject) => {
    toggleProject(project);
    invalidateQueries();
    // Do NOT close the dropdown
  };

  const projects = (projectsData?.items ?? []).filter(p => !p.is_archived);
  const archived = (archivedData?.items ?? []).filter(p => p.is_archived);

  const isAllSelected = selectedProjects.length === 0;
  const selectedIds = new Set(selectedProjects.map(p => p.id));

  // Button label
  const renderButtonLabel = () => {
    if (selectedProjects.length === 0) {
      return (
        <>
          <svg className="w-4 h-4 flex-shrink-0 text-zinc-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
            <path strokeLinecap="round" strokeLinejoin="round" d="M4 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2V6zM14 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2V6zM4 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2v-2zM14 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2v-2z" />
          </svg>
          {!collapsed && <span className="truncate flex-1">All Projects</span>}
        </>
      );
    }

    if (selectedProjects.length === 1) {
      const proj = selectedProjects[0];
      return (
        <>
          {proj.color ? (
            <span className="w-3 h-3 rounded-full flex-shrink-0" style={{ backgroundColor: proj.color }} />
          ) : (
            <span className="w-3 h-3 rounded-full flex-shrink-0 bg-zinc-400" />
          )}
          {!collapsed && <span className="truncate flex-1">{proj.name}</span>}
        </>
      );
    }

    // 2+ projects
    return (
      <>
        <span className="inline-flex items-center justify-center w-5 h-5 rounded-full bg-blue-600 text-white text-xs font-medium flex-shrink-0">
          {selectedProjects.length}
        </span>
        {!collapsed && <span className="truncate flex-1">{selectedProjects.length} projects</span>}
      </>
    );
  };

  const buttonTitle = selectedProjects.length === 0
    ? 'All Projects'
    : selectedProjects.length === 1
      ? selectedProjects[0].name
      : `${selectedProjects.length} projects`;

  return (
    <div className="relative" ref={dropdownRef}>
      <button
        data-tour="project-selector"
        onClick={() => setIsOpen(!isOpen)}
        className="w-full flex items-center gap-2 px-3 py-2 rounded-lg text-sm font-medium
          bg-zinc-100 dark:bg-slate-800 hover:bg-zinc-200 dark:hover:bg-slate-700
          transition-colors text-left"
        title={buttonTitle}
      >
        {renderButtonLabel()}
        {!collapsed && (
          <svg className={`w-4 h-4 flex-shrink-0 transition-transform ${isOpen ? 'rotate-180' : ''}`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
            <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
          </svg>
        )}
      </button>

      {isOpen && (
        <div className="absolute top-full left-0 right-0 mt-1 z-50 bg-white dark:bg-slate-900
          border border-zinc-200 dark:border-slate-700 rounded-lg shadow-lg overflow-hidden min-w-[200px]">
          {/* All Projects option */}
          <button
            onClick={handleAllProjectsClick}
            className={`w-full flex items-center gap-2 px-3 py-2 text-sm hover:bg-zinc-100 dark:hover:bg-slate-800
              ${isAllSelected ? 'bg-zinc-100 dark:bg-slate-800 font-medium' : ''}`}
          >
            {/* Checkbox */}
            <span className={`w-4 h-4 flex-shrink-0 rounded border flex items-center justify-center
              ${isAllSelected
                ? 'bg-blue-600 border-blue-600 text-white'
                : 'border-zinc-300 dark:border-slate-600'
              }`}
            >
              {isAllSelected && (
                <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="3">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                </svg>
              )}
            </span>
            <svg className="w-4 h-4 text-zinc-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
              <path strokeLinecap="round" strokeLinejoin="round" d="M4 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2V6zM14 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2V6zM4 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2v-2zM14 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2v-2z" />
            </svg>
            All Projects
          </button>

          {projects.length > 0 && (
            <div className="border-t border-zinc-200 dark:border-slate-700" />
          )}

          {/* Active projects */}
          {projects.map((project) => {
            const isChecked = selectedIds.has(project.id);
            return (
              <button
                key={project.id}
                onClick={() => handleToggleProject({
                  id: project.id,
                  name: project.name,
                  color: project.color,
                  icon: project.icon,
                })}
                className={`w-full flex items-center gap-2 px-3 py-2 text-sm hover:bg-zinc-100 dark:hover:bg-slate-800
                  ${isChecked ? 'bg-zinc-100 dark:bg-slate-800 font-medium' : ''}`}
              >
                {/* Checkbox */}
                <span className={`w-4 h-4 flex-shrink-0 rounded border flex items-center justify-center
                  ${isChecked
                    ? 'bg-blue-600 border-blue-600 text-white'
                    : 'border-zinc-300 dark:border-slate-600'
                  }`}
                >
                  {isChecked && (
                    <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="3">
                      <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                    </svg>
                  )}
                </span>
                {project.color ? (
                  <span className="w-3 h-3 rounded-full flex-shrink-0" style={{ backgroundColor: project.color }} />
                ) : (
                  <span className="w-3 h-3 rounded-full flex-shrink-0 bg-zinc-400" />
                )}
                <span className="truncate">{project.name}</span>
              </button>
            );
          })}

          {/* Archived section */}
          {archived.length > 0 && (
            <>
              <div className="border-t border-zinc-200 dark:border-slate-700" />
              <div className="px-3 py-1.5 text-xs text-zinc-500 uppercase tracking-wider">Archived</div>
              {archived.map((project) => (
                <button
                  key={project.id}
                  className="w-full flex items-center gap-2 px-3 py-2 text-sm text-zinc-500 hover:bg-zinc-100 dark:hover:bg-slate-800"
                >
                  <span className="w-3 h-3 rounded-full flex-shrink-0 bg-zinc-300 dark:bg-slate-600" />
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

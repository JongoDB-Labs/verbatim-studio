import { useEffect, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { ChevronDown, FolderClosed, FolderOpen, Check, FolderPlus } from 'lucide-react';
import { api } from '@/lib/api';
import { queryKeys } from '@/lib/queryKeys';

interface ProjectAssignmentControlProps {
  // Currently-assigned project, null = unassigned.
  currentProjectId: string | null;
  // Called when the user picks a different project (or null to unassign).
  // Should perform the persistence call and resolve when done.
  onAssign: (projectId: string | null) => Promise<void>;
  // Optional label override — defaults to "Project".
  label?: string;
}

// Compact dropdown for assigning a single recording / document to a project
// from inside its detail view. Themed via design tokens (bg-card / muted /
// hsl) to fit either light or dark mode.

export function ProjectAssignmentControl({
  currentProjectId,
  onAssign,
  label = 'Project',
}: ProjectAssignmentControlProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  const { data: projectList } = useQuery({
    queryKey: queryKeys.projects.list(),
    queryFn: () => api.projects.list(),
  });
  const projects = projectList?.items ?? [];
  const current = projects.find((p) => p.id === currentProjectId);

  useEffect(() => {
    if (!isOpen) return;
    const onClick = (e: MouseEvent) => {
      if (!containerRef.current?.contains(e.target as Node)) setIsOpen(false);
    };
    document.addEventListener('mousedown', onClick);
    return () => document.removeEventListener('mousedown', onClick);
  }, [isOpen]);

  const handlePick = async (id: string | null) => {
    if (id === currentProjectId) {
      setIsOpen(false);
      return;
    }
    setIsSaving(true);
    try {
      await onAssign(id);
      setIsOpen(false);
    } catch (err) {
      console.error('Failed to assign project:', err);
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <div className="relative" ref={containerRef}>
      <button
        type="button"
        onClick={() => setIsOpen((o) => !o)}
        disabled={isSaving}
        className="inline-flex items-center gap-2 px-3 py-1.5 text-sm rounded-md border border-border bg-card text-foreground hover:bg-muted transition-colors disabled:opacity-60"
        title={`${label}: ${current?.name ?? 'None'}`}
      >
        {current ? (
          <FolderOpen className="w-4 h-4 text-muted-foreground" />
        ) : (
          <FolderClosed className="w-4 h-4 text-muted-foreground" />
        )}
        <span className="max-w-[180px] truncate">
          {current ? current.name : <span className="text-muted-foreground">No project</span>}
        </span>
        <ChevronDown className={`w-4 h-4 text-muted-foreground transition-transform ${isOpen ? 'rotate-180' : ''}`} />
      </button>

      {isOpen && (
        <div className="absolute right-0 z-30 mt-1 w-64 rounded-lg border border-border bg-card shadow-lg overflow-hidden">
          <div className="px-3 py-2 text-xs font-medium uppercase tracking-wider text-muted-foreground border-b border-border">
            Assign to {label.toLowerCase()}
          </div>
          <div className="max-h-72 overflow-y-auto p-1">
            <button
              onClick={() => handlePick(null)}
              className={`w-full flex items-center justify-between gap-2 px-3 py-2 rounded-md text-sm text-left transition-colors ${
                currentProjectId === null
                  ? 'bg-primary/10 text-primary'
                  : 'text-foreground hover:bg-muted'
              }`}
            >
              <span className="flex items-center gap-2">
                <FolderClosed className="w-4 h-4" />
                No project
              </span>
              {currentProjectId === null && <Check className="w-4 h-4" />}
            </button>
            {projects.length > 0 && <div className="my-1 border-t border-border" />}
            {projects.map((p) => (
              <button
                key={p.id}
                onClick={() => handlePick(p.id)}
                className={`w-full flex items-center justify-between gap-2 px-3 py-2 rounded-md text-sm text-left transition-colors ${
                  currentProjectId === p.id
                    ? 'bg-primary/10 text-primary'
                    : 'text-foreground hover:bg-muted'
                }`}
              >
                <span className="flex items-center gap-2 min-w-0">
                  {p.icon ? (
                    <span className="w-4 h-4 flex items-center justify-center text-base leading-none">
                      {p.icon}
                    </span>
                  ) : (
                    <FolderClosed className="w-4 h-4" />
                  )}
                  <span className="truncate">{p.name}</span>
                </span>
                {currentProjectId === p.id && <Check className="w-4 h-4 flex-shrink-0" />}
              </button>
            ))}
            {projects.length === 0 && (
              <div className="px-3 py-3 text-sm text-muted-foreground flex items-center gap-2">
                <FolderPlus className="w-4 h-4" />
                No projects yet — create one from the Projects page.
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

/**
 * Global store for tracking selected project workspaces.
 * Uses Zustand with localStorage persistence.
 * Empty array = "All Projects" mode (no filter).
 */
import { create } from 'zustand';
import { useShallow } from 'zustand/react/shallow';

const STORAGE_KEY = 'verbatim-active-project';

export interface ActiveProject {
  id: string;
  name: string;
  color?: string | null;
  icon?: string | null;
}

interface ProjectStore {
  selectedProjects: ActiveProject[];
  toggleProject: (project: ActiveProject) => void;
  clearProjects: () => void;
  setSelectedProjects: (projects: ActiveProject[]) => void;

  // Backwards compatibility - TODO: Remove after Task 3 updates all consumers
  activeProject: ActiveProject | null;
  setActiveProject: (project: ActiveProject | null) => void;
}

function persistProjects(projects: ActiveProject[]) {
  if (projects.length > 0) {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(projects));
  } else {
    localStorage.removeItem(STORAGE_KEY);
  }
}

export const useProjectStore = create<ProjectStore>((set, get) => ({
  selectedProjects: [],

  // Backwards compatibility - derived from selectedProjects[0]
  // TODO: Remove after Task 3 updates all consumers
  activeProject: null,

  toggleProject: (project) => {
    const current = get().selectedProjects;
    const exists = current.some((p) => p.id === project.id);
    const next = exists
      ? current.filter((p) => p.id !== project.id)
      : [...current, project];
    set({ selectedProjects: next, activeProject: next[0] ?? null });
    persistProjects(next);
  },

  clearProjects: () => {
    set({ selectedProjects: [], activeProject: null });
    localStorage.removeItem(STORAGE_KEY);
  },

  setSelectedProjects: (projects) => {
    set({ selectedProjects: projects, activeProject: projects[0] ?? null });
    persistProjects(projects);
  },

  // Backwards compatibility shim - sets a single-element array
  // TODO: Remove after Task 3 updates all consumers
  setActiveProject: (project: ActiveProject | null) => {
    if (project) {
      set({ selectedProjects: [project], activeProject: project });
      localStorage.setItem(STORAGE_KEY, JSON.stringify([project]));
    } else {
      set({ selectedProjects: [], activeProject: null });
      localStorage.removeItem(STORAGE_KEY);
    }
  },
}));

/**
 * Convenience hook that returns a sorted array of selected project IDs.
 * Useful for cache keys and dependency comparisons.
 */
export function useSelectedProjectIds(): string[] {
  return useProjectStore(
    useShallow((state) =>
      state.selectedProjects.map((p) => p.id).sort()
    )
  );
}

// Initialize from localStorage on module load.
// Handles both legacy single-object format and new array format.
const stored = localStorage.getItem(STORAGE_KEY);
if (stored) {
  try {
    const parsed = JSON.parse(stored);

    if (Array.isArray(parsed)) {
      // New format: array of projects
      const valid = parsed.filter(
        (item: unknown) =>
          item != null &&
          typeof item === 'object' &&
          typeof (item as Record<string, unknown>).id === 'string' &&
          typeof (item as Record<string, unknown>).name === 'string'
      ) as ActiveProject[];
      if (valid.length > 0) {
        useProjectStore.setState({
          selectedProjects: valid,
          activeProject: valid[0] ?? null,
        });
      } else {
        localStorage.removeItem(STORAGE_KEY);
      }
    } else if (
      parsed &&
      typeof parsed === 'object' &&
      typeof parsed.id === 'string' &&
      typeof parsed.name === 'string'
    ) {
      // Legacy format: single project object - migrate to array
      const migrated = [parsed as ActiveProject];
      useProjectStore.setState({
        selectedProjects: migrated,
        activeProject: migrated[0],
      });
      localStorage.setItem(STORAGE_KEY, JSON.stringify(migrated));
    } else {
      localStorage.removeItem(STORAGE_KEY);
    }
  } catch {
    localStorage.removeItem(STORAGE_KEY);
  }
}

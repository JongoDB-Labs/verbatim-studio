/**
 * Global store for tracking the active project workspace.
 * Uses Zustand with localStorage persistence. Null state means "All Projects" mode.
 */
import { create } from 'zustand';

const STORAGE_KEY = 'verbatim-active-project';

export interface ActiveProject {
  id: string;
  name: string;
  color?: string | null;
  icon?: string | null;
}

interface ProjectStore {
  activeProject: ActiveProject | null; // null = "All Projects" mode
  setActiveProject: (project: ActiveProject | null) => void;
}

export const useProjectStore = create<ProjectStore>((set) => ({
  activeProject: null,
  setActiveProject: (project) => {
    set({ activeProject: project });
    // Persist to localStorage
    if (project) {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(project));
    } else {
      localStorage.removeItem(STORAGE_KEY);
    }
  },
}));

// Initialize from localStorage on module load
const stored = localStorage.getItem(STORAGE_KEY);
if (stored) {
  try {
    const parsed = JSON.parse(stored);
    if (parsed && typeof parsed.id === 'string' && typeof parsed.name === 'string') {
      useProjectStore.setState({ activeProject: parsed });
    } else {
      localStorage.removeItem(STORAGE_KEY);
    }
  } catch {
    localStorage.removeItem(STORAGE_KEY);
  }
}

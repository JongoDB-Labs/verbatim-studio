import { create } from 'zustand';

interface ActiveProject {
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
      localStorage.setItem('active-project', JSON.stringify(project));
    } else {
      localStorage.removeItem('active-project');
    }
  },
}));

// Initialize from localStorage on module load
const stored = localStorage.getItem('active-project');
if (stored) {
  try {
    const parsed = JSON.parse(stored);
    useProjectStore.setState({ activeProject: parsed });
  } catch {
    localStorage.removeItem('active-project');
  }
}

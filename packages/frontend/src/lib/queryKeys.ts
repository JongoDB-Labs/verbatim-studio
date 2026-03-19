// packages/frontend/src/lib/queryKeys.ts

import { useProjectStore } from '@/stores/projectStore';

// Helper to get current active project ID for query keys
export function activeProjectScope(): string | undefined {
  return useProjectStore.getState().activeProject?.id;
}

// Filter types for query keys
export interface RecordingFilters {
  search?: string;
  status?: string;
  sortBy?: string;
  sortOrder?: string;
  projectId?: string;
  dateFrom?: string;
  dateTo?: string;
  tagIds?: string[];
  speaker?: string;
  templateId?: string;
}

export interface ProjectFilters {
  search?: string;
  projectTypeId?: string;
  tag?: string;
}

export interface DocumentFilters {
  search?: string;
  status?: string;
  sortBy?: string;
  sortOrder?: string;
  projectId?: string;
  dateFrom?: string;
  dateTo?: string;
  tagIds?: string[];
  mimeType?: string;
}

export const queryKeys = {
  // Recordings
  recordings: {
    all: ['recordings'] as const,
    list: (filters?: RecordingFilters) => ['recordings', 'list', { ...filters, _scope: activeProjectScope() }] as const,
    detail: (id: string) => ['recordings', 'detail', id] as const,
  },

  // Projects
  projects: {
    all: ['projects'] as const,
    list: (filters?: ProjectFilters) => ['projects', 'list', filters] as const,
    detail: (id: string) => ['projects', 'detail', id] as const,
    recordings: (id: string) => ['projects', id, 'recordings'] as const,
    analytics: (id: string) => ['projects', id, 'analytics'] as const,
  },

  // Conversations (Chats)
  conversations: {
    all: ['conversations'] as const,
    list: () => ['conversations', 'list', { _scope: activeProjectScope() }] as const,
    detail: (id: string) => ['conversations', 'detail', id] as const,
  },

  // Dashboard
  dashboard: {
    stats: ['dashboard', 'stats'] as const,
  },

  // Search
  search: {
    history: ['search', 'history'] as const,
    global: (query: string) => ['search', 'global', query] as const,
  },

  // Documents
  documents: {
    all: ['documents'] as const,
    list: (filters?: DocumentFilters) =>
      ['documents', 'list', { ...filters, _scope: activeProjectScope() }] as const,
    detail: (id: string) => ['documents', 'detail', id] as const,
  },

  // Jobs (for progress tracking)
  jobs: {
    all: ['jobs'] as const,
    running: () => ['jobs', 'running'] as const,
  },

  // Tags
  tags: {
    all: ['tags'] as const,
    list: () => ['tags', 'list'] as const,
  },
};

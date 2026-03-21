// packages/frontend/src/hooks/useDashboard.ts
import { useQuery } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { queryKeys } from '@/lib/queryKeys';

export function useDashboardStats() {
  return useQuery({
    queryKey: queryKeys.dashboard.scopedStats(),
    queryFn: () => api.stats.dashboard(),
    // Poll every 3 seconds when there are active processing jobs
    // This provides a fallback in case WebSocket broadcasts don't deliver
    refetchInterval: (query) => {
      const hasActiveJobs = (query.state.data?.processing?.active_count ?? 0) > 0;
      return hasActiveJobs ? 3000 : false;
    },
  });
}

export function useRecentRecordings(limit = 5) {
  return useQuery({
    queryKey: [...queryKeys.recordings.list({}), 'recent', limit] as const,
    queryFn: async () => {
      const response = await api.recordings.list({
        pageSize: limit,
        sortBy: 'created_at',
        sortOrder: 'desc',
      });
      return response.items;
    },
    // Poll every 3 seconds when any recording is processing
    // This provides a fallback in case WebSocket broadcasts don't deliver
    refetchInterval: (query) => {
      const hasProcessing = query.state.data?.some(
        (r) => r.status === 'processing' || r.status === 'pending'
      );
      return hasProcessing ? 3000 : false;
    },
  });
}

export function useRecentProjects(limit = 5) {
  return useQuery({
    queryKey: [...queryKeys.projects.list({}), 'recent', limit] as const,
    queryFn: async () => {
      const response = await api.projects.list({});
      // Sort by updated_at descending and take first `limit`
      const sorted = [...response.items].sort(
        (a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime()
      );
      return sorted.slice(0, limit);
    },
  });
}

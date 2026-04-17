// packages/frontend/src/hooks/useRecordings.ts
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api, type RecordingListResponse } from '@/lib/api';
import { queryKeys, type RecordingFilters } from '@/lib/queryKeys';

export function useRecordings(filters: RecordingFilters = {}) {
  return useQuery({
    queryKey: queryKeys.recordings.list(filters),
    queryFn: async (): Promise<RecordingListResponse> => {
      return api.recordings.list({
        search: filters.search || undefined,
        status: filters.status || undefined,
        sortBy: filters.sortBy as 'created_at' | 'title' | 'duration' | undefined,
        sortOrder: filters.sortOrder as 'asc' | 'desc' | undefined,
        projectId: filters.projectId || undefined,
        dateFrom: filters.dateFrom || undefined,
        dateTo: filters.dateTo || undefined,
        tagIds: filters.tagIds?.length ? filters.tagIds : undefined,
        speaker: filters.speaker || undefined,
        templateId: filters.templateId || undefined,
      });
    },
  });
}

export function useRecording(id: string | null) {
  return useQuery({
    queryKey: queryKeys.recordings.detail(id ?? ''),
    queryFn: () => api.recordings.get(id!),
    enabled: !!id,
  });
}

export function useDeleteRecording() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (id: string) => api.recordings.delete(id),

    onMutate: async (id) => {
      // Cancel any outgoing refetches
      await queryClient.cancelQueries({ queryKey: queryKeys.recordings.all });

      // Snapshot previous value for rollback
      const previousData = queryClient.getQueriesData<RecordingListResponse>({
        queryKey: queryKeys.recordings.all,
      });

      // Optimistically remove from all recording lists
      // Note: queryKeys.recordings.all matches both list and detail queries,
      // so we need to check for the items array before filtering
      queryClient.setQueriesData<RecordingListResponse>(
        { queryKey: queryKeys.recordings.all },
        (old) => {
          if (!old || !('items' in old)) return old;
          return {
            ...old,
            items: old.items.filter((r) => r.id !== id),
            total: old.total - 1,
          };
        }
      );

      return { previousData };
    },

    onError: (_err, _id, context) => {
      // Rollback on error
      if (context?.previousData) {
        context.previousData.forEach(([key, data]) => {
          queryClient.setQueryData(key, data);
        });
      }
    },

    onSettled: () => {
      // Refetch to ensure consistency (WebSocket will also trigger this)
      queryClient.invalidateQueries({ queryKey: queryKeys.recordings.all });
      queryClient.invalidateQueries({ queryKey: queryKeys.recordings.archived() });
      queryClient.invalidateQueries({ queryKey: queryKeys.dashboard.stats });
    },
  });
}

export function useBulkDeleteRecordings() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (ids: string[]) => api.recordings.bulkDelete(ids),

    onMutate: async (ids) => {
      await queryClient.cancelQueries({ queryKey: queryKeys.recordings.all });

      const previousData = queryClient.getQueriesData<RecordingListResponse>({
        queryKey: queryKeys.recordings.all,
      });

      const idsSet = new Set(ids);
      queryClient.setQueriesData<RecordingListResponse>(
        { queryKey: queryKeys.recordings.all },
        (old) => {
          if (!old || !('items' in old)) return old;
          const remaining = old.items.filter((r) => !idsSet.has(r.id));
          return {
            ...old,
            items: remaining,
            total: old.total - (old.items.length - remaining.length),
          };
        }
      );

      return { previousData };
    },

    onError: (_err, _ids, context) => {
      if (context?.previousData) {
        context.previousData.forEach(([key, data]) => {
          queryClient.setQueryData(key, data);
        });
      }
    },

    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.recordings.all });
      queryClient.invalidateQueries({ queryKey: queryKeys.recordings.archived() });
      queryClient.invalidateQueries({ queryKey: queryKeys.dashboard.stats });
    },
  });
}

export function useTranscribeRecording() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ id, language }: { id: string; language?: string }) =>
      api.recordings.transcribe(id, { language }),

    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.recordings.all });
      queryClient.invalidateQueries({ queryKey: queryKeys.jobs.all });
    },
  });
}

export function useCancelRecording() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (id: string) => api.recordings.cancel(id),

    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.recordings.all });
      queryClient.invalidateQueries({ queryKey: queryKeys.jobs.all });
    },
  });
}

export function useRetryRecording() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (id: string) => api.recordings.retry(id),

    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.recordings.all });
      queryClient.invalidateQueries({ queryKey: queryKeys.jobs.all });
    },
  });
}

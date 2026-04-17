// packages/frontend/src/hooks/useDataSync.tsx
import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from 'react';
import { useQueryClient, type QueryClient } from '@tanstack/react-query';
import { getWebSocketUrl } from '@/lib/api';
import { queryKeys } from '@/lib/queryKeys';
import { useTaskStore, type TaskType } from '@/stores/taskStore';

interface DataSyncContextValue {
  connected: boolean;
}

const DataSyncContext = createContext<DataSyncContextValue>({ connected: false });

export function useDataSyncStatus() {
  return useContext(DataSyncContext);
}

interface InvalidationMessage {
  type: 'invalidate';
  resource: string;
  action: string;
  id?: string;
}

interface JobProgressMessage {
  type: 'job_progress';
  job_id: string;
  job_type: TaskType;
  status: 'running' | 'completed' | 'failed';
  progress: number;
  task_name?: string;
  recording_id?: string;
  transcript_id?: string;
  error?: string;
}

type WebSocketMessage = InvalidationMessage | JobProgressMessage;

function handleInvalidation(
  queryClient: QueryClient,
  resource: string,
  action: string,
  id?: string
) {
  console.log(`[DataSync] Invalidating ${resource} (${action})${id ? ` id=${id}` : ''}`);

  // Use refetchType: 'active' to force immediate refetch of active queries
  // This overrides staleTime and ensures UI updates immediately
  const refetchOptions = { refetchType: 'active' as const };

  switch (resource) {
    case 'recordings':
      queryClient.invalidateQueries({ queryKey: queryKeys.recordings.all, ...refetchOptions });
      queryClient.invalidateQueries({ queryKey: queryKeys.recordings.archived(), ...refetchOptions });
      queryClient.invalidateQueries({ queryKey: queryKeys.dashboard.stats, ...refetchOptions });
      queryClient.invalidateQueries({ queryKey: queryKeys.jobs.all, ...refetchOptions });
      break;

    case 'projects':
      queryClient.invalidateQueries({ queryKey: queryKeys.projects.all, ...refetchOptions });
      queryClient.invalidateQueries({ queryKey: queryKeys.dashboard.stats, ...refetchOptions });
      break;

    case 'conversations':
      queryClient.invalidateQueries({ queryKey: queryKeys.conversations.all, ...refetchOptions });
      break;

    case 'search_history':
      queryClient.invalidateQueries({ queryKey: queryKeys.search.history, ...refetchOptions });
      break;

    case 'documents':
      queryClient.invalidateQueries({ queryKey: queryKeys.documents.all, ...refetchOptions });
      queryClient.invalidateQueries({ queryKey: queryKeys.dashboard.stats, ...refetchOptions });
      break;

    case 'tags':
      queryClient.invalidateQueries({ queryKey: queryKeys.tags.all, ...refetchOptions });
      // Also invalidate recordings and documents since they display tags
      queryClient.invalidateQueries({ queryKey: queryKeys.recordings.all, ...refetchOptions });
      queryClient.invalidateQueries({ queryKey: queryKeys.documents.all, ...refetchOptions });
      break;

    case 'transcripts':
      // Invalidate transcript queries so quality review results refresh
      queryClient.invalidateQueries({ queryKey: ['transcript'], ...refetchOptions });
      queryClient.invalidateQueries({ queryKey: queryKeys.recordings.all, ...refetchOptions });
      break;

    default:
      console.warn(`[DataSync] Unknown resource: ${resource}`);
  }
}

function getTaskName(jobType: TaskType, message: JobProgressMessage): string {
  switch (jobType) {
    case 'summarize':
      return 'AI Summary';
    case 'transcribe':
      return 'Transcription';
    case 'embed':
      return 'Embeddings';
    case 'process_document':
      return 'Document Processing';
    case 'quality_review':
      return 'AI Quality Review';
    default:
      return message.task_name || 'Processing';
  }
}

function handleJobProgress(message: JobProgressMessage) {
  const { startTask, updateProgress, completeTask, failTask } = useTaskStore.getState();

  console.log(`[DataSync] Job progress: ${message.job_type} ${message.status} ${message.progress}%`);

  const taskName = getTaskName(message.job_type, message);

  if (message.status === 'running') {
    // Start or update task
    const tasks = useTaskStore.getState().tasks;
    if (!tasks.has(message.job_id)) {
      startTask(message.job_id, message.job_type, taskName, {
        recordingId: message.recording_id,
        transcriptId: message.transcript_id,
      });
    }
    updateProgress(message.job_id, message.progress);
  } else if (message.status === 'completed') {
    // Ensure task exists before completing
    const tasks = useTaskStore.getState().tasks;
    if (!tasks.has(message.job_id)) {
      startTask(message.job_id, message.job_type, taskName);
    }
    completeTask(message.job_id);
  } else if (message.status === 'failed') {
    const tasks = useTaskStore.getState().tasks;
    if (!tasks.has(message.job_id)) {
      startTask(message.job_id, message.job_type, taskName);
    }
    failTask(message.job_id, message.error || 'Task failed');
  }
}

interface DataSyncProviderProps {
  children: ReactNode;
}

export function DataSyncProvider({ children }: DataSyncProviderProps) {
  const queryClient = useQueryClient();
  const wsRef = useRef<WebSocket | null>(null);
  const [connected, setConnected] = useState(false);
  const reconnectAttempts = useRef(0);
  const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    function connect() {
      // Clear any pending reconnect
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current);
        reconnectTimeoutRef.current = null;
      }

      const wsUrl = getWebSocketUrl('/api/ws/sync');
      console.log('[DataSync] Connecting to', wsUrl);

      const ws = new WebSocket(wsUrl);
      wsRef.current = ws;

      ws.onopen = () => {
        console.log('[DataSync] Connected');
        setConnected(true);

        // Invalidate core data queries to ensure fresh data.
        // On initial connect this catches any stale/empty results from queries
        // that fired before the WebSocket was ready (common in Electron where
        // backend starts asynchronously). On reconnect it syncs missed updates.
        queryClient.invalidateQueries({ queryKey: queryKeys.recordings.all });
        queryClient.invalidateQueries({ queryKey: queryKeys.projects.all });
        queryClient.invalidateQueries({ queryKey: queryKeys.documents.all });
        queryClient.invalidateQueries({ queryKey: queryKeys.jobs.all });
        queryClient.invalidateQueries({ queryKey: queryKeys.dashboard.stats });

        reconnectAttempts.current = 0;
      };

      ws.onclose = (event) => {
        console.log('[DataSync] Disconnected', event.code, event.reason);
        setConnected(false);
        wsRef.current = null;

        // Reconnect with exponential backoff, capped at 30s.
        // After maxReconnectAttempts fast retries, keep trying at 30s intervals
        // so the connection recovers if the backend restarts.
        const delay = Math.min(1000 * Math.pow(2, reconnectAttempts.current), 30000);
        console.log(`[DataSync] Reconnecting in ${delay}ms (attempt ${reconnectAttempts.current + 1})`);
        reconnectAttempts.current++;
        reconnectTimeoutRef.current = setTimeout(connect, delay);
      };

      ws.onerror = (error) => {
        console.error('[DataSync] WebSocket error', error);
      };

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data) as WebSocketMessage;
          if (data.type === 'invalidate') {
            handleInvalidation(queryClient, data.resource, data.action, data.id);
          } else if (data.type === 'job_progress') {
            handleJobProgress(data);
          }
        } catch (err) {
          console.error('[DataSync] Failed to parse message', err);
        }
      };
    }

    connect();

    return () => {
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current);
      }
      if (wsRef.current) {
        wsRef.current.close();
      }
    };
  }, [queryClient]);

  return (
    <DataSyncContext.Provider value={{ connected }}>
      {children}
    </DataSyncContext.Provider>
  );
}

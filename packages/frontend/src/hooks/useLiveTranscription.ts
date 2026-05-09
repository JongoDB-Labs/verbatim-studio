// packages/frontend/src/hooks/useLiveTranscription.ts
//
// Maintains the live transcription state for the page. The backend uses
// a rolling-buffer pipeline (services/live_transcription_service.py) and
// emits two kinds of segment messages:
//
//   - "segments_replace" — the authoritative current state; carries the
//     full confirmed list (append-only on the client) and the full
//     tentative list (replaceable wholesale). The hook stores them
//     separately so the UI can render tentative segments with a
//     subtle "may revise" cue.
//
//   - "speaker_update" — pyannote results land out-of-band on a slower
//     cadence than transcription. We patch the speaker label in place
//     for any matching segment id (confirmed or tentative).
//
// `segments` (the public API) is the merged confirmed + tentative list,
// always sorted by start time. Edits hit the REST endpoint so the
// authoritative state stays on the server.

import { useState, useRef, useCallback, useEffect } from 'react';
import { getWebSocketUrl, getApiUrl } from '@/lib/api';

export type ConnectionState = 'disconnected' | 'connecting' | 'connected' | 'recording' | 'paused';

export interface WordData {
  word: string;
  start: number;
  end: number;
  confidence: number | null;
}

export interface TranscriptSegment {
  id: string;
  text: string;
  start: number;
  end: number;
  speaker?: string | null;
  confidence?: number | null;
  words?: WordData[] | null;
  edited_by?: 'human' | 'ai' | null;
  /** Tentative segments are inside the live buffer and may revise. */
  tentative?: boolean;
}

export interface LiveError {
  type: string;
  message: string;
  retryable: boolean;
}

export interface UseLiveTranscriptionReturn {
  connectionState: ConnectionState;
  sessionId: string | null;
  segments: TranscriptSegment[];
  duration: number;
  error: LiveError | null;
  lastAutoSave: Date | null;
  fullText: string;
  wordCount: number;
  isMuted: boolean;
  highDetailMode: boolean;
  stream: MediaStream | null;
  /** Number of tentative segments currently in flight (may revise). */
  tentativeCount: number;
  connect: () => Promise<void>;
  disconnect: () => void;
  startRecording: (language: string, highDetail?: boolean) => void;
  stopRecording: () => void;
  pauseRecording: () => void;
  resumeRecording: () => void;
  toggleMute: () => void;
  updateSegmentText: (id: string, newText: string) => void;
  deleteSegment: (id: string) => void;
  clearTranscript: () => Promise<void>;
  dismissError: () => void;
}

// Chunk interval in milliseconds. The backend re-transcribes the full
// rolling buffer on each chunk arrival, so smaller chunks = lower
// perceived latency at the cost of more frequent re-runs.
const CHUNK_INTERVAL_MS = 1500;

const AUTOSAVE_INTERVAL_MS = 30_000;
const MAX_RECONNECT_ATTEMPTS = 5;
const BASE_RECONNECT_DELAY_MS = 1000;
const FINAL_CHUNK_WAIT_MS = CHUNK_INTERVAL_MS + 1000;

interface WireSegment {
  id: string;
  start: number;
  end: number;
  text: string;
  speaker?: string | null;
  confidence?: number | null;
  words?: WordData[] | null;
  edited_by?: 'human' | 'ai' | null;
  tentative?: boolean;
}

function wireToSegment(w: WireSegment): TranscriptSegment {
  return {
    id: w.id,
    start: w.start,
    end: w.end,
    text: w.text,
    speaker: w.speaker ?? null,
    confidence: w.confidence ?? null,
    words: w.words ?? null,
    edited_by: w.edited_by ?? null,
    tentative: w.tentative ?? false,
  };
}

export function useLiveTranscription(): UseLiveTranscriptionReturn {
  const [connectionState, setConnectionState] = useState<ConnectionState>('disconnected');
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [confirmed, setConfirmed] = useState<TranscriptSegment[]>([]);
  const [tentative, setTentative] = useState<TranscriptSegment[]>([]);
  const [duration, setDuration] = useState(0);
  const [error, setError] = useState<LiveError | null>(null);
  const [lastAutoSave, setLastAutoSave] = useState<Date | null>(null);
  const [isMuted, setIsMuted] = useState(false);
  const [highDetailMode, setHighDetailMode] = useState(false);

  const wsRef = useRef<WebSocket | null>(null);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const timerRef = useRef<number | null>(null);
  const chunkIntervalRef = useRef<number | null>(null);
  const autosaveIntervalRef = useRef<number | null>(null);
  const isRecordingRef = useRef(false);
  const reconnectAttemptsRef = useRef(0);
  const reconnectTimeoutRef = useRef<number | null>(null);
  const userDisconnectedRef = useRef(false);
  const sessionIdRef = useRef<string | null>(null);

  useEffect(() => {
    sessionIdRef.current = sessionId;
  }, [sessionId]);

  const cleanup = useCallback((sendDisconnect = false) => {
    isRecordingRef.current = false;

    if (timerRef.current) { clearInterval(timerRef.current); timerRef.current = null; }
    if (chunkIntervalRef.current) { clearInterval(chunkIntervalRef.current); chunkIntervalRef.current = null; }
    if (autosaveIntervalRef.current) { clearInterval(autosaveIntervalRef.current); autosaveIntervalRef.current = null; }
    if (reconnectTimeoutRef.current) { clearTimeout(reconnectTimeoutRef.current); reconnectTimeoutRef.current = null; }

    if (mediaRecorderRef.current?.state === 'recording') {
      mediaRecorderRef.current.stop();
    }
    if (streamRef.current) {
      streamRef.current.getTracks().forEach(track => track.stop());
      streamRef.current = null;
    }
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      if (sendDisconnect) {
        wsRef.current.send(JSON.stringify({ type: 'disconnect' }));
      }
      wsRef.current.close();
    }
    setIsMuted(false);
  }, []);

  useEffect(() => () => cleanup(), [cleanup]);

  const startAutosave = useCallback(() => {
    if (autosaveIntervalRef.current) {
      clearInterval(autosaveIntervalRef.current);
      autosaveIntervalRef.current = null;
    }
    autosaveIntervalRef.current = window.setInterval(async () => {
      const sid = sessionIdRef.current;
      if (!sid) return;
      try {
        await fetch(getApiUrl('/api/live/autosave'), {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ session_id: sid }),
        });
        setLastAutoSave(new Date());
      } catch {
        // Autosave is best-effort
      }
    }, AUTOSAVE_INTERVAL_MS);
  }, []);

  const stopAutosave = useCallback(() => {
    if (autosaveIntervalRef.current) {
      clearInterval(autosaveIntervalRef.current);
      autosaveIntervalRef.current = null;
    }
  }, []);

  const handleWebSocketMessage = useCallback((event: MessageEvent) => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    let data: any;
    try {
      data = JSON.parse(event.data);
    } catch {
      return;
    }

    switch (data.type) {
      case 'ready':
        reconnectAttemptsRef.current = 0;
        break;

      case 'session_start':
        setSessionId(data.session_id as string);
        break;

      case 'segments_replace': {
        // Authoritative state from server.
        const wireConfirmed = (data.confirmed as WireSegment[]) ?? [];
        const wireTentative = (data.tentative as WireSegment[]) ?? [];

        // Confirmed list is append-only on the wire — server can be
        // trusted, but we still merge by id to preserve any local
        // edits made via the REST endpoints (edited_by stays sticky).
        setConfirmed(prev => {
          const byId = new Map(prev.map(s => [s.id, s]));
          for (const w of wireConfirmed) {
            const existing = byId.get(w.id);
            byId.set(w.id, {
              ...wireToSegment(w),
              // Preserve client edits if any
              text: existing?.edited_by === 'human' ? existing.text : w.text,
              edited_by: existing?.edited_by ?? w.edited_by ?? null,
            });
          }
          // Order by start time
          return [...byId.values()].sort((a, b) => a.start - b.start);
        });

        setTentative(wireTentative.map(wireToSegment).sort((a, b) => a.start - b.start));

        if (typeof data.duration === 'number') {
          // Server-authoritative duration tracks the buffer clock; only
          // update if it's larger than the local timer so we don't jump
          // backwards mid-pause.
          setDuration(prev => Math.max(prev, Math.floor(data.duration as number)));
        }
        break;
      }

      case 'speaker_update': {
        // Patch speaker labels for matching segment ids in either list.
        const updates = (data.updates as Array<{ id: string; speaker: string }>) ?? [];
        if (updates.length === 0) break;
        const map = new Map(updates.map(u => [u.id, u.speaker]));
        setConfirmed(prev =>
          prev.map(s => map.has(s.id) ? { ...s, speaker: map.get(s.id)! } : s),
        );
        setTentative(prev =>
          prev.map(s => map.has(s.id) ? { ...s, speaker: map.get(s.id)! } : s),
        );
        break;
      }

      case 'session_end':
        break;

      case 'warning':
        setError({
          type: 'warning',
          message: data.message as string,
          retryable: false,
        });
        break;

      case 'error':
        setError({
          type: (data.error_type as string) || 'unknown',
          message: data.message as string,
          retryable: (data.retryable as boolean) ?? false,
        });
        break;

      case 'pong':
        break;
    }
  }, []);

  const createWebSocket = useCallback((onInitialError?: () => void) => {
    if (reconnectTimeoutRef.current) {
      clearTimeout(reconnectTimeoutRef.current);
      reconnectTimeoutRef.current = null;
    }

    const wsUrl = getWebSocketUrl('/api/live/transcribe');
    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;

    ws.onopen = () => {
      setConnectionState('connected');
      reconnectAttemptsRef.current = 0;
    };

    ws.onmessage = handleWebSocketMessage;

    ws.onerror = () => {
      if (onInitialError) {
        onInitialError();
      }
    };

    ws.onclose = () => {
      if (userDisconnectedRef.current) {
        userDisconnectedRef.current = false;
        setConnectionState('disconnected');
      } else if (isRecordingRef.current && reconnectAttemptsRef.current < MAX_RECONNECT_ATTEMPTS) {
        const delay = Math.min(
          BASE_RECONNECT_DELAY_MS * Math.pow(2, reconnectAttemptsRef.current),
          30_000,
        );
        reconnectAttemptsRef.current += 1;
        reconnectTimeoutRef.current = window.setTimeout(() => createWebSocket(), delay);
      } else if (reconnectAttemptsRef.current >= MAX_RECONNECT_ATTEMPTS) {
        setError({
          type: 'connection',
          message: 'Connection lost. Please reconnect manually.',
          retryable: false,
        });
        setConnectionState('disconnected');
      } else {
        setConnectionState('disconnected');
      }
    };

    return ws;
  }, [handleWebSocketMessage]);

  const connect = useCallback(async () => {
    setError(null);
    userDisconnectedRef.current = false;
    setConnectionState('connecting');

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;

      createWebSocket(() => {
        setError({
          type: 'connection',
          message: 'Failed to connect to transcription server.',
          retryable: true,
        });
        setConnectionState('disconnected');
      });
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Failed to connect';
      const isMicError = msg.includes('Permission') || msg.includes('NotAllowed') || msg.includes('getUserMedia');
      setError({
        type: isMicError ? 'microphone' : 'connection',
        message: isMicError
          ? 'Microphone access denied. Please allow microphone access and try again.'
          : msg,
        retryable: !isMicError,
      });
      setConnectionState('disconnected');
    }
  }, [createWebSocket]);

  const disconnect = useCallback(() => {
    userDisconnectedRef.current = true;
    reconnectAttemptsRef.current = MAX_RECONNECT_ATTEMPTS;
    cleanup(true);
    setConnectionState('disconnected');
  }, [cleanup]);

  const startNewChunk = useCallback(() => {
    if (!streamRef.current || !wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) {
      return;
    }

    const mediaRecorder = new MediaRecorder(streamRef.current, {
      mimeType: 'audio/webm;codecs=opus',
    });
    mediaRecorderRef.current = mediaRecorder;

    const chunks: Blob[] = [];
    mediaRecorder.ondataavailable = (event) => {
      if (event.data.size > 0) {
        chunks.push(event.data);
      }
    };

    mediaRecorder.onstop = () => {
      if (chunks.length > 0 && wsRef.current?.readyState === WebSocket.OPEN) {
        const blob = new Blob(chunks, { type: 'audio/webm;codecs=opus' });
        wsRef.current.send(blob);
      }
    };

    mediaRecorder.start();
  }, []);

  const startRecordingTimers = useCallback(() => {
    startNewChunk();

    chunkIntervalRef.current = window.setInterval(() => {
      if (!isRecordingRef.current) return;
      if (mediaRecorderRef.current?.state === 'recording') {
        mediaRecorderRef.current.stop();
      }
      startNewChunk();
    }, CHUNK_INTERVAL_MS);

    timerRef.current = window.setInterval(() => {
      setDuration(prev => prev + 1);
    }, 1000);
  }, [startNewChunk]);

  const stopRecordingTimers = useCallback(() => {
    if (chunkIntervalRef.current) {
      clearInterval(chunkIntervalRef.current);
      chunkIntervalRef.current = null;
    }
    if (mediaRecorderRef.current?.state === 'recording') {
      mediaRecorderRef.current.stop();
    }
    if (timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  const startRecording = useCallback((language: string, highDetail = false) => {
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) {
      setError({ type: 'connection', message: 'Not connected to server.', retryable: true });
      return;
    }
    if (!streamRef.current) {
      setError({ type: 'microphone', message: 'No microphone stream available.', retryable: false });
      return;
    }

    setHighDetailMode(highDetail);
    wsRef.current.send(JSON.stringify({ type: 'start', language, high_detail_mode: highDetail }));
    isRecordingRef.current = true;

    startRecordingTimers();
    setConnectionState('recording');
    setDuration(0);

    startAutosave();
  }, [startRecordingTimers, startAutosave]);

  const stopRecording = useCallback(() => {
    isRecordingRef.current = false;

    stopRecordingTimers();
    stopAutosave();

    setTimeout(() => {
      if (wsRef.current?.readyState === WebSocket.OPEN) {
        wsRef.current.send(JSON.stringify({ type: 'stop' }));
      }
    }, FINAL_CHUNK_WAIT_MS);

    setConnectionState('connected');
  }, [stopRecordingTimers, stopAutosave]);

  const pauseRecording = useCallback(() => {
    stopRecordingTimers();
    setConnectionState('paused');
  }, [stopRecordingTimers]);

  const resumeRecording = useCallback(() => {
    if (!isRecordingRef.current) return;
    if (!streamRef.current || !wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) {
      return;
    }
    startRecordingTimers();
    setConnectionState('recording');
  }, [startRecordingTimers]);

  const toggleMute = useCallback(() => {
    if (!streamRef.current) return;
    const audioTracks = streamRef.current.getAudioTracks();
    const newMuted = !isMuted;
    audioTracks.forEach(track => { track.enabled = !newMuted; });
    setIsMuted(newMuted);
  }, [isMuted]);

  const updateSegmentText = useCallback(async (id: string, newText: string) => {
    const sid = sessionIdRef.current;
    // Optimistic update
    setConfirmed(prev =>
      prev.map(s => s.id === id ? { ...s, text: newText, edited_by: 'human' } : s),
    );
    setTentative(prev =>
      prev.map(s => s.id === id ? { ...s, text: newText, edited_by: 'human' } : s),
    );
    if (!sid) return;
    try {
      await fetch(getApiUrl(`/api/live/session/${sid}/segment/${id}`), {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: newText }),
      });
    } catch {
      // Optimistic — surface errors silently for now
    }
  }, []);

  const deleteSegment = useCallback(async (id: string) => {
    const sid = sessionIdRef.current;
    setConfirmed(prev => prev.filter(s => s.id !== id));
    setTentative(prev => prev.filter(s => s.id !== id));
    if (!sid) return;
    try {
      await fetch(getApiUrl(`/api/live/session/${sid}/segment/${id}`), {
        method: 'DELETE',
      });
    } catch {
      // Optimistic
    }
  }, []);

  const clearTranscript = useCallback(async () => {
    if (isRecordingRef.current) return;

    if (sessionId) {
      try {
        await fetch(getApiUrl(`/api/live/session/${sessionId}`), {
          method: 'DELETE',
        });
      } catch {
        // Ignore
      }
    }
    setConfirmed([]);
    setTentative([]);
    setSessionId(null);
    setDuration(0);
    setLastAutoSave(null);
  }, [sessionId]);

  const dismissError = useCallback(() => {
    setError(null);
  }, []);

  // Merged + sorted segments for the page to render.
  const segments = (() => {
    const merged = [...confirmed, ...tentative];
    merged.sort((a, b) => a.start - b.start);
    return merged;
  })();

  const fullText = segments.map(s => s.text).join(' ');
  const wordCount = fullText.split(/\s+/).filter(Boolean).length;

  return {
    connectionState,
    sessionId,
    segments,
    duration,
    error,
    lastAutoSave,
    fullText,
    wordCount,
    isMuted,
    highDetailMode,
    stream: streamRef.current,
    tentativeCount: tentative.length,
    connect,
    disconnect,
    startRecording,
    stopRecording,
    pauseRecording,
    resumeRecording,
    toggleMute,
    updateSegmentText,
    deleteSegment,
    clearTranscript,
    dismissError,
  };
}

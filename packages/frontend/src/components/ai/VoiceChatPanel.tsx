import { useState, useRef, useCallback, useEffect } from 'react';
import {
  Room,
  RoomEvent,
  Track,
  RemoteTrack,
  RemoteTrackPublication,
  RemoteParticipant,
} from 'livekit-client';
import { api } from '@/lib/api';

type VoiceState = 'idle' | 'connecting' | 'listening' | 'thinking' | 'speaking';

interface VoiceInfo {
  id: string;
  name: string;
  description: string;
}

export interface VoiceTranscriptMessage {
  role: 'user' | 'assistant';
  content: string;
}

interface VoiceChatPanelProps {
  onClose: (messages?: VoiceTranscriptMessage[]) => void;
  recordingIds?: string[];
  documentIds?: string[];
  webSearchEnabled?: boolean;
  fileContext?: string;
}


export function VoiceChatPanel({ onClose, recordingIds, documentIds, webSearchEnabled, fileContext }: VoiceChatPanelProps) {
  const [state, setState] = useState<VoiceState>('idle');
  const [error, setError] = useState<string | null>(null);
  const [transcript, setTranscript] = useState<string[]>([]);
  const [ttsAvailable, setTtsAvailable] = useState<boolean | null>(null);
  const [_voices, setVoices] = useState<VoiceInfo[]>([]);
  const [selectedVoice] = useState<string>('');

  const roomRef = useRef<Room | null>(null);
  const audioElementsRef = useRef<HTMLAudioElement[]>([]);
  const transcriptEndRef = useRef<HTMLDivElement>(null);
  const streamingAssistantRef = useRef<string>('');
  const voiceMessagesRef = useRef<VoiceTranscriptMessage[]>([]);

  const [missingDeps, setMissingDeps] = useState<string[]>([]);

  // Check TTS and dependency availability on mount
  useEffect(() => {
    api.voice.status()
      .then((status) => {
        setTtsAvailable(status.tts_available && status.livekit_available);
        setMissingDeps(status.missing_deps || []);
        if (status.voices && status.voices.length > 0) {
          setVoices(status.voices);
          // If no voice selected yet, default to the first voice or saved preference
          // voices loaded for future use
        }
      })
      .catch(() => setTtsAvailable(false));
  }, []);

  // Auto-scroll transcript
  useEffect(() => {
    transcriptEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [transcript]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (roomRef.current) {
        roomRef.current.disconnect();
        roomRef.current = null;
      }
      audioElementsRef.current.forEach((el) => {
        el.pause();
        el.srcObject = null;
      });
      audioElementsRef.current = [];
    };
  }, []);

  const handleTrackSubscribed = useCallback(
    (
      track: RemoteTrack,
      _publication: RemoteTrackPublication,
      _participant: RemoteParticipant,
    ) => {
      if (track.kind === Track.Kind.Audio) {
        const audioEl = track.attach();
        document.body.appendChild(audioEl);
        audioElementsRef.current.push(audioEl);
        setState('speaking');
      }
    },
    [],
  );

  const handleTrackUnsubscribed = useCallback(
    (
      track: RemoteTrack,
      _publication: RemoteTrackPublication,
      _participant: RemoteParticipant,
    ) => {
      if (track.kind === Track.Kind.Audio) {
        const detachedElements = track.detach();
        detachedElements.forEach((el) => {
          el.pause();
          el.srcObject = null;
          el.remove();
        });
        audioElementsRef.current = audioElementsRef.current.filter(
          (el) => !detachedElements.includes(el),
        );
        setState('listening');
      }
    },
    [],
  );

  const connect = useCallback(async () => {
    setError(null);
    setState('connecting');

    try {
      const session = await api.voice.createSession(
        selectedVoice || undefined,
        recordingIds,
        documentIds,
        webSearchEnabled,
        fileContext,
      );

      const room = new Room({
        // Audio-only, local network — disable video codecs and adaptive streaming
        adaptiveStream: false,
        dynacast: false,
      });
      roomRef.current = room;

      // Wire up events before connecting
      room.on(RoomEvent.TrackSubscribed, handleTrackSubscribed);
      room.on(RoomEvent.TrackUnsubscribed, handleTrackUnsubscribed);

      room.on(RoomEvent.DataReceived, (payload: Uint8Array) => {
        try {
          const text = new TextDecoder().decode(payload);
          const data = JSON.parse(text);

          if (data.type === 'transcript') {
            if (data.role === 'user') {
              voiceMessagesRef.current.push({ role: 'user', content: data.content });
              setTranscript((prev) => [...prev, `You: ${data.content}`]);
              streamingAssistantRef.current = '';
            } else if (data.role === 'assistant_token') {
              streamingAssistantRef.current += data.content;
              const currentText = streamingAssistantRef.current;
              setTranscript((prev) => {
                const updated = [...prev];
                const lastIdx = updated.length - 1;
                if (lastIdx >= 0 && updated[lastIdx].startsWith('Max: ')) {
                  updated[lastIdx] = `Max: ${currentText}`;
                } else {
                  updated.push(`Max: ${currentText}`);
                }
                return updated;
              });
            } else if (data.role === 'assistant_done') {
              if (streamingAssistantRef.current) {
                voiceMessagesRef.current.push({ role: 'assistant', content: streamingAssistantRef.current });
              }
              streamingAssistantRef.current = '';
            } else if (data.role === 'assistant') {
              voiceMessagesRef.current.push({ role: 'assistant', content: data.content });
              setTranscript((prev) => [...prev, `Max: ${data.content}`]);
            }
          }
        } catch {
          // Non-JSON data, ignore
        }
      });

      room.on(RoomEvent.Disconnected, () => {
        setState('idle');
      });

      await room.connect(session.url, session.token);

      // Publish microphone
      await room.localParticipant.setMicrophoneEnabled(true);

      setState('listening');
    } catch (err) {
      console.error('Voice connection error:', err);
      setError(err instanceof Error ? err.message : 'Failed to connect');
      setState('idle');
      roomRef.current = null;
    }
  }, [handleTrackSubscribed, handleTrackUnsubscribed, selectedVoice]);

  const disconnect = useCallback((passMessages = true) => {
    if (roomRef.current) {
      roomRef.current.disconnect();
      roomRef.current = null;
    }
    audioElementsRef.current.forEach((el) => {
      el.pause();
      el.srcObject = null;
      el.remove();
    });
    audioElementsRef.current = [];
    setState('idle');

    // Save any in-progress streaming assistant text
    if (streamingAssistantRef.current) {
      voiceMessagesRef.current.push({ role: 'assistant', content: streamingAssistantRef.current });
      streamingAssistantRef.current = '';
    }

    // Pass accumulated voice messages to ChatPanel on close
    if (passMessages && voiceMessagesRef.current.length > 0) {
      onClose([...voiceMessagesRef.current]);
      voiceMessagesRef.current = [];
    } else {
      onClose();
    }
  }, [onClose]);

  // TTS not available — show setup message
  if (ttsAvailable === false) {
    return (
      <div className="flex flex-col flex-1 min-h-0">
        <div className="flex-1 flex flex-col items-center justify-center gap-4 px-4 py-6">
          <div className="relative">
            <div className="w-24 h-24 rounded-full flex items-center justify-center bg-gray-200 dark:bg-gray-700">
              <svg
                className="w-10 h-10 text-gray-400 dark:text-gray-500 opacity-50"
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
                strokeWidth="2"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  d="M19 11a7 7 0 01-7 7m0 0a7 7 0 01-7-7m7 7v4m0 0H8m4 0h4m-4-8a3 3 0 01-3-3V5a3 3 0 116 0v6a3 3 0 01-3 3z"
                />
              </svg>
            </div>
          </div>

          <p className="text-sm font-medium text-gray-700 dark:text-gray-300">
            Voice chat setup required
          </p>
          <p className="text-xs text-gray-500 dark:text-gray-400 text-center max-w-[280px]">
            {missingDeps.length > 0
              ? `Missing dependencies: ${missingDeps.join(', ')}. Install them and download a TTS model in Settings.`
              : 'Download a text-to-speech model in Settings \u2192 AI Models to enable voice chat.'}
          </p>

          <button
            onClick={() => onClose()}
            className="px-4 py-2 text-sm font-medium text-gray-600 dark:text-gray-300 bg-gray-100 dark:bg-gray-700 rounded-full hover:bg-gray-200 dark:hover:bg-gray-600 transition-colors"
          >
            Back to Chat
          </button>
        </div>
      </div>
    );
  }

  // Still loading TTS status
  if (ttsAvailable === null) {
    return (
      <div className="flex flex-col flex-1 min-h-0">
        <div className="flex-1 flex flex-col items-center justify-center gap-4 px-4 py-6">
          <div className="w-24 h-24 rounded-full flex items-center justify-center bg-gray-200 dark:bg-gray-700">
            <svg
              className="w-10 h-10 text-gray-400 dark:text-gray-500 animate-spin"
              fill="none"
              viewBox="0 0 24 24"
            >
              <circle
                className="opacity-25"
                cx="12"
                cy="12"
                r="10"
                stroke="currentColor"
                strokeWidth="4"
              />
              <path
                className="opacity-75"
                fill="currentColor"
                d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z"
              />
            </svg>
          </div>
          <p className="text-sm font-medium text-gray-500 dark:text-gray-400">
            Checking voice availability...
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col flex-1 min-h-0">
      {/* State indicator area */}
      <div className="flex-1 flex flex-col items-center justify-center gap-4 px-4 py-6">
        {/* Large circular indicator */}
        <div className="relative">
          <div
            className={`w-24 h-24 rounded-full flex items-center justify-center transition-colors duration-300 ${
              state === 'idle'
                ? 'bg-gray-200 dark:bg-gray-700'
                : state === 'connecting'
                  ? 'bg-yellow-100 dark:bg-yellow-900/40'
                  : state === 'listening'
                    ? 'bg-green-100 dark:bg-green-900/40'
                    : state === 'thinking'
                      ? 'bg-blue-100 dark:bg-blue-900/40'
                      : 'bg-purple-100 dark:bg-purple-900/40'
            }`}
          >
            {/* Pulse ring for listening */}
            {state === 'listening' && (
              <div className="absolute inset-0 rounded-full bg-green-400/30 dark:bg-green-500/20 animate-ping" />
            )}

            {/* Idle: mic icon */}
            {state === 'idle' && (
              <svg
                className="w-10 h-10 text-gray-400 dark:text-gray-500"
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
                strokeWidth="2"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  d="M19 11a7 7 0 01-7 7m0 0a7 7 0 01-7-7m7 7v4m0 0H8m4 0h4m-4-8a3 3 0 01-3-3V5a3 3 0 116 0v6a3 3 0 01-3 3z"
                />
              </svg>
            )}

            {/* Connecting: yellow spinner */}
            {state === 'connecting' && (
              <svg
                className="w-10 h-10 text-yellow-500 dark:text-yellow-400 animate-spin"
                fill="none"
                viewBox="0 0 24 24"
              >
                <circle
                  className="opacity-25"
                  cx="12"
                  cy="12"
                  r="10"
                  stroke="currentColor"
                  strokeWidth="4"
                />
                <path
                  className="opacity-75"
                  fill="currentColor"
                  d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z"
                />
              </svg>
            )}

            {/* Listening: green mic */}
            {state === 'listening' && (
              <svg
                className="w-10 h-10 text-green-600 dark:text-green-400 relative z-10"
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
                strokeWidth="2"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  d="M19 11a7 7 0 01-7 7m0 0a7 7 0 01-7-7m7 7v4m0 0H8m4 0h4m-4-8a3 3 0 01-3-3V5a3 3 0 116 0v6a3 3 0 01-3 3z"
                />
              </svg>
            )}

            {/* Thinking: blue spinner */}
            {state === 'thinking' && (
              <svg
                className="w-10 h-10 text-blue-500 dark:text-blue-400 animate-spin"
                fill="none"
                viewBox="0 0 24 24"
              >
                <circle
                  className="opacity-25"
                  cx="12"
                  cy="12"
                  r="10"
                  stroke="currentColor"
                  strokeWidth="4"
                />
                <path
                  className="opacity-75"
                  fill="currentColor"
                  d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z"
                />
              </svg>
            )}

            {/* Speaking: purple audio bars */}
            {state === 'speaking' && (
              <div className="flex items-center gap-1">
                <span className="w-1.5 h-6 bg-purple-500 dark:bg-purple-400 rounded-full animate-pulse" />
                <span
                  className="w-1.5 h-9 bg-purple-500 dark:bg-purple-400 rounded-full animate-pulse"
                  style={{ animationDelay: '0.15s' }}
                />
                <span
                  className="w-1.5 h-5 bg-purple-500 dark:bg-purple-400 rounded-full animate-pulse"
                  style={{ animationDelay: '0.3s' }}
                />
                <span
                  className="w-1.5 h-8 bg-purple-500 dark:bg-purple-400 rounded-full animate-pulse"
                  style={{ animationDelay: '0.45s' }}
                />
                <span
                  className="w-1.5 h-4 bg-purple-500 dark:bg-purple-400 rounded-full animate-pulse"
                  style={{ animationDelay: '0.6s' }}
                />
              </div>
            )}
          </div>
        </div>

        {/* State label */}
        <p
          className={`text-sm font-medium ${
            state === 'idle'
              ? 'text-gray-500 dark:text-gray-400'
              : state === 'connecting'
                ? 'text-yellow-600 dark:text-yellow-400'
                : state === 'listening'
                  ? 'text-green-600 dark:text-green-400'
                  : state === 'thinking'
                    ? 'text-blue-600 dark:text-blue-400'
                    : 'text-purple-600 dark:text-purple-400'
          }`}
        >
          {state === 'idle' && 'Ready to start'}
          {state === 'connecting' && 'Connecting...'}
          {state === 'listening' && 'Listening...'}
          {state === 'thinking' && 'Thinking...'}
          {state === 'speaking' && 'Speaking...'}
        </p>

        {/* Error message */}
        {error && (
          <p className="text-xs text-red-500 dark:text-red-400 text-center max-w-[280px]">
            {error}
          </p>
        )}

        {/* Action button */}
        {state === 'idle' ? (
          <button
            onClick={connect}
            className="px-5 py-2 text-sm font-medium text-white bg-purple-600 rounded-full hover:bg-purple-700 transition-colors focus:outline-none focus:ring-2 focus:ring-purple-500 focus:ring-offset-2 dark:focus:ring-offset-gray-800"
          >
            Start Voice Chat
          </button>
        ) : (
          <button
            onClick={() => disconnect()}
            disabled={state === 'connecting'}
            className="px-5 py-2 text-sm font-medium text-white bg-red-600 rounded-full hover:bg-red-700 transition-colors focus:outline-none focus:ring-2 focus:ring-red-500 focus:ring-offset-2 dark:focus:ring-offset-gray-800 disabled:opacity-50"
          >
            End Voice Chat
          </button>
        )}
      </div>

      {/* Live transcript area */}
      {transcript.length > 0 && (
        <div className="border-t border-gray-200 dark:border-gray-700 max-h-40 overflow-y-auto px-4 py-3">
          <p className="text-xs font-medium text-gray-500 dark:text-gray-400 mb-2">
            Transcript
          </p>
          <div className="space-y-1.5">
            {transcript.map((line, i) => (
              <p
                key={i}
                className="text-sm text-gray-700 dark:text-gray-300 leading-relaxed"
              >
                {line}
              </p>
            ))}
            <div ref={transcriptEndRef} />
          </div>
        </div>
      )}
    </div>
  );
}

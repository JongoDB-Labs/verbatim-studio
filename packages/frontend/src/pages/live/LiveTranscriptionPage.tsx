import { useState, useCallback, useEffect, useRef } from 'react';
import { getApiUrl } from '@/lib/api';
import { formatDuration } from '@/lib/utils';
import { useLiveTranscription } from '@/hooks/useLiveTranscription';
import { useLiveShortcuts, getLiveShortcuts } from '@/hooks/useLiveShortcuts';
import { useKeybindingStore } from '@/stores/keybindingStore';
import { AudioLevelMeter } from '@/components/audio/AudioLevelMeter';
import { LiveSegment } from '@/components/live/LiveSegment';
import { MetadataPanel, type LiveMetadata } from '@/components/live/MetadataPanel';

interface LiveTranscriptionPageProps {
  onNavigateToRecordings: () => void;
  onViewRecording: (recordingId: string) => void;
}

const LANGUAGES = [
  { code: 'en', label: 'English' },
  { code: 'es', label: 'Spanish' },
  { code: 'fr', label: 'French' },
  { code: 'de', label: 'German' },
  { code: 'it', label: 'Italian' },
  { code: 'pt', label: 'Portuguese' },
  { code: 'zh', label: 'Chinese' },
  { code: 'ja', label: 'Japanese' },
];

const DEFAULT_METADATA: LiveMetadata = {
  title: '',
  projectId: null,
  tags: [],
  description: '',
  saveAudio: true,
};

// Distance from the bottom (in px) within which we consider the user to
// be "tailing" the transcript. New segments only auto-scroll when within
// this band; otherwise the user is reviewing earlier text and should be
// left alone.
const AUTOSCROLL_TAIL_PX = 80;

export function LiveTranscriptionPage({ onNavigateToRecordings: _onNavigateToRecordings, onViewRecording }: LiveTranscriptionPageProps) {
  const getDisplayLabel = useKeybindingStore(s => s.getDisplayLabel);
  const {
    connectionState,
    sessionId,
    segments,
    duration,
    error,
    lastAutoSave,
    wordCount,
    isMuted,
    highDetailMode,
    stream,
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
  } = useLiveTranscription();

  const [language, setLanguage] = useState('en');
  const [highDetail, setHighDetail] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [showSaveConfirm, setShowSaveConfirm] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [metadata, setMetadata] = useState<LiveMetadata>(DEFAULT_METADATA);
  const [showShortcuts, setShowShortcuts] = useState(false);
  const [isEditingSegment, setIsEditingSegment] = useState(false);
  const [isAtBottom, setIsAtBottom] = useState(true);

  const transcriptScrollRef = useRef<HTMLDivElement>(null);
  const transcriptEndRef = useRef<HTMLDivElement>(null);

  const isActive = connectionState === 'recording' || connectionState === 'paused';
  const isRecording = connectionState === 'recording';

  // Smart auto-scroll: only follow new segments if the user was already
  // at (or near) the bottom — avoids yanking them out of earlier content
  // they were reviewing.
  useEffect(() => {
    if (isEditingSegment) return;
    if (!isAtBottom) return;
    transcriptEndRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }, [segments, isAtBottom, isEditingSegment]);

  // Track whether the transcript scroll position is at the tail.
  const handleTranscriptScroll = useCallback(() => {
    const el = transcriptScrollRef.current;
    if (!el) return;
    const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    setIsAtBottom(distanceFromBottom <= AUTOSCROLL_TAIL_PX);
  }, []);

  const handleStartRecording = useCallback(() => {
    startRecording(language, highDetail);
  }, [startRecording, language, highDetail]);

  // One-tap "Connect & Record" — if disconnected, connect first then start.
  const connectThenRecordRef = useRef<{ pending: boolean }>({ pending: false });
  const handleConnectAndRecord = useCallback(async () => {
    if (connectionState === 'connected') {
      handleStartRecording();
      return;
    }
    if (connectionState === 'disconnected') {
      connectThenRecordRef.current.pending = true;
      await connect();
    }
  }, [connectionState, connect, handleStartRecording]);

  // When connection completes after a "connect & record" intent, kick off
  // recording automatically.
  useEffect(() => {
    if (connectionState === 'connected' && connectThenRecordRef.current.pending) {
      connectThenRecordRef.current.pending = false;
      handleStartRecording();
    }
  }, [connectionState, handleStartRecording]);

  const handleToggleRecording = useCallback(() => {
    if (isRecording || connectionState === 'paused') {
      stopRecording();
    } else if (connectionState === 'connected') {
      handleStartRecording();
    } else if (connectionState === 'disconnected') {
      handleConnectAndRecord();
    }
  }, [connectionState, isRecording, stopRecording, handleStartRecording, handleConnectAndRecord]);

  const handlePauseResume = useCallback(() => {
    if (connectionState === 'recording') {
      pauseRecording();
    } else if (connectionState === 'paused') {
      resumeRecording();
    }
  }, [connectionState, pauseRecording, resumeRecording]);

  const downloadTranscript = useCallback(() => {
    const text = segments.map(s => {
      const prefix = s.speaker ? `[${s.speaker}] ` : '';
      return `${prefix}${s.text}`;
    }).join('\n\n');
    const blob = new Blob([text], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `live-transcript-${new Date().toISOString().slice(0, 10)}.txt`;
    a.click();
    URL.revokeObjectURL(url);
  }, [segments]);

  const handleSave = useCallback(async () => {
    if (!sessionId) return;

    const title = metadata.title.trim() || `Live Recording ${new Date().toLocaleDateString()}`;

    setIsSaving(true);
    setSaveError(null);

    try {
      const response = await fetch(getApiUrl('/api/live/save'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          session_id: sessionId,
          title,
          save_audio: metadata.saveAudio,
          project_id: metadata.projectId,
          tags: metadata.tags,
          description: metadata.description || null,
        }),
      });

      if (!response.ok) {
        throw new Error('Failed to save session');
      }

      const data = await response.json();
      setShowSaveConfirm(false);
      setMetadata(DEFAULT_METADATA);

      onViewRecording(data.recording_id);
      clearTranscript();
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : 'Save failed');
    } finally {
      setIsSaving(false);
    }
  }, [sessionId, metadata, clearTranscript, onViewRecording]);

  const handleSaveClick = useCallback(() => {
    if (segments.length === 0 || !sessionId) return;
    setShowSaveConfirm(true);
  }, [segments, sessionId]);

  useLiveShortcuts({
    onToggleRecording: handleToggleRecording,
    onPauseResume: handlePauseResume,
    onSave: handleSaveClick,
    onToggleMute: toggleMute,
    onDiscard: () => {
      if (segments.length > 0 && sessionId) clearTranscript();
    },
    onClear: () => {
      if (segments.length > 0) clearTranscript();
    },
    onDisconnect: () => {
      if (connectionState !== 'disconnected') disconnect();
    },
    enabled: connectionState !== 'disconnected' && !showSaveConfirm,
  });

  const statusLabel = (() => {
    switch (connectionState) {
      case 'recording': return 'Recording';
      case 'paused': return 'Paused';
      case 'connected': return 'Ready';
      case 'connecting': return 'Connecting…';
      default: return 'Disconnected';
    }
  })();

  const statusDotClass = (() => {
    switch (connectionState) {
      case 'recording': return 'bg-red-500 animate-pulse';
      case 'paused': return 'bg-amber-400';
      case 'connected': return 'bg-emerald-400';
      case 'connecting': return 'bg-amber-400 animate-pulse';
      default: return 'bg-gray-400';
    }
  })();

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-purple-500 to-purple-700 flex items-center justify-center shadow-sm">
            <svg className="w-5 h-5 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2.5">
              <path strokeLinecap="round" strokeLinejoin="round" d="M19 11a7 7 0 01-7 7m0 0a7 7 0 01-7-7m7 7v4m0 0H8m4 0h4m-4-8a3 3 0 01-3-3V5a3 3 0 116 0v6a3 3 0 01-3 3z" />
            </svg>
          </div>
          <div>
            <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100 leading-tight">Live Transcription</h1>
            <p className="text-sm text-gray-500 dark:text-gray-400">
              Real-time speech-to-text with low-latency processing
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <span className={`inline-flex items-center gap-2 px-2.5 py-1 rounded-full text-xs font-medium border ${
            isRecording
              ? 'bg-red-50 dark:bg-red-900/20 text-red-700 dark:text-red-300 border-red-200 dark:border-red-800'
              : connectionState === 'paused'
                ? 'bg-amber-50 dark:bg-amber-900/20 text-amber-700 dark:text-amber-300 border-amber-200 dark:border-amber-800'
                : connectionState === 'connected'
                  ? 'bg-emerald-50 dark:bg-emerald-900/20 text-emerald-700 dark:text-emerald-300 border-emerald-200 dark:border-emerald-800'
                  : 'bg-gray-50 dark:bg-gray-800 text-gray-600 dark:text-gray-300 border-gray-200 dark:border-gray-700'
          }`}>
            <span className={`w-1.5 h-1.5 rounded-full ${statusDotClass}`} />
            {statusLabel}
            {isMuted && isActive && <span className="ml-1 text-red-500">· Muted</span>}
          </span>
          <button
            onClick={() => setShowShortcuts(!showShortcuts)}
            className={`inline-flex items-center gap-1.5 text-xs px-2 py-1 rounded transition-colors ${
              showShortcuts
                ? 'text-purple-600 dark:text-purple-400 bg-purple-50 dark:bg-purple-900/20'
                : 'text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-800'
            }`}
            title="Keyboard shortcuts"
          >
            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253" />
            </svg>
            Shortcuts
          </button>
        </div>
      </div>

      {/* Keyboard Shortcuts Panel */}
      {showShortcuts && (
        <div className="rounded-lg border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800/50 p-4">
          <h3 className="text-sm font-semibold text-gray-900 dark:text-gray-100 mb-2">Keyboard Shortcuts</h3>
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
            {getLiveShortcuts().map(s => (
              <div key={s.description} className="flex items-center gap-2 text-xs">
                <kbd className="px-1.5 py-0.5 rounded bg-gray-200 dark:bg-gray-700 text-gray-700 dark:text-gray-300 font-mono">{s.key}</kbd>
                <span className="text-gray-600 dark:text-gray-400">{s.description}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Error / Warning Banner */}
      {error && (
        <div className={`p-4 rounded-lg border ${
          error.type === 'warning'
            ? 'bg-blue-50 dark:bg-blue-900/20 border-blue-200 dark:border-blue-800'
            : error.retryable
              ? 'bg-yellow-50 dark:bg-yellow-900/20 border-yellow-200 dark:border-yellow-800'
              : 'bg-red-50 dark:bg-red-900/20 border-red-200 dark:border-red-800'
        }`}>
          <div className={`flex items-center gap-2 ${
            error.type === 'warning'
              ? 'text-blue-700 dark:text-blue-400'
              : error.retryable
                ? 'text-yellow-700 dark:text-yellow-400'
                : 'text-red-700 dark:text-red-400'
          }`}>
            <svg className="w-5 h-5 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
              {error.type === 'warning' ? (
                <path strokeLinecap="round" strokeLinejoin="round" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
              ) : (
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
              )}
            </svg>
            <span className="text-sm">{error.message}</span>
            <button onClick={dismissError} className="ml-auto text-sm underline shrink-0">Dismiss</button>
          </div>
        </div>
      )}

      {/* Main Content */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Left Panel - Recording controls */}
        <div className="space-y-4">
          {/* Recording Card (hero when active) */}
          <div className={`rounded-xl border p-5 transition-colors ${
            isRecording
              ? 'border-red-200 dark:border-red-800 bg-gradient-to-b from-red-50 to-white dark:from-red-900/10 dark:to-gray-800'
              : 'border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800'
          }`}>
            {/* Timer */}
            <div className="flex items-baseline justify-center gap-2 mb-3">
              <div className="text-5xl font-mono font-bold tabular-nums text-gray-900 dark:text-gray-100">
                {formatDuration(duration)}
              </div>
            </div>

            {/* Audio Level Meter */}
            <div className="mb-4 h-2">
              {isActive ? (
                <AudioLevelMeter stream={stream} isActive={isActive && !isMuted} />
              ) : (
                <div className="flex-1 h-2 bg-gray-100 dark:bg-gray-700/50 rounded-full" />
              )}
            </div>

            {/* Primary action */}
            {!isActive ? (
              <button
                onClick={handleConnectAndRecord}
                disabled={connectionState === 'connecting'}
                className="w-full py-3 rounded-lg bg-red-600 text-white font-semibold hover:bg-red-700 transition-colors disabled:opacity-60 disabled:cursor-not-allowed flex items-center justify-center gap-2 shadow-sm"
              >
                <span className="w-2.5 h-2.5 rounded-full bg-white" />
                {connectionState === 'connecting' ? 'Connecting…' : 'Start Recording'}
              </button>
            ) : (
              <div className="grid grid-cols-3 gap-2">
                <button
                  onClick={handlePauseResume}
                  className="py-3 rounded-lg border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-200 font-medium hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors flex items-center justify-center gap-1.5"
                  title={connectionState === 'paused' ? 'Resume' : 'Pause'}
                >
                  {connectionState === 'paused' ? (
                    <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 24 24">
                      <path d="M8 5v14l11-7z" />
                    </svg>
                  ) : (
                    <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 24 24">
                      <path d="M6 19h4V5H6v14zm8-14v14h4V5h-4z" />
                    </svg>
                  )}
                  <span className="text-sm">{connectionState === 'paused' ? 'Resume' : 'Pause'}</span>
                </button>

                <button
                  onClick={toggleMute}
                  className={`py-3 rounded-lg border transition-colors flex items-center justify-center gap-1.5 ${
                    isMuted
                      ? 'border-red-300 dark:border-red-700 text-red-600 dark:text-red-400 bg-red-50 dark:bg-red-900/20'
                      : 'border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-700'
                  }`}
                  title={isMuted ? 'Unmute' : 'Mute'}
                >
                  {isMuted ? (
                    <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                      <path strokeLinecap="round" strokeLinejoin="round" d="M5.586 15H4a1 1 0 01-1-1v-4a1 1 0 011-1h1.586l4.707-4.707C10.923 3.663 12 4.109 12 5v14c0 .891-1.077 1.337-1.707.707L5.586 15z" />
                      <path strokeLinecap="round" strokeLinejoin="round" d="M17 14l2-2m0 0l2-2m-2 2l-2-2m2 2l2 2" />
                    </svg>
                  ) : (
                    <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                      <path strokeLinecap="round" strokeLinejoin="round" d="M19 11a7 7 0 01-7 7m0 0a7 7 0 01-7-7m7 7v4m0 0H8m4 0h4m-4-8a3 3 0 01-3-3V5a3 3 0 116 0v6a3 3 0 01-3 3z" />
                    </svg>
                  )}
                  <span className="text-sm">{isMuted ? 'Unmute' : 'Mute'}</span>
                </button>

                <button
                  onClick={stopRecording}
                  className="py-3 rounded-lg bg-red-600 text-white font-medium hover:bg-red-700 transition-colors flex items-center justify-center gap-1.5"
                  title="Stop recording"
                >
                  <span className="w-3 h-3 rounded-sm bg-white" />
                  <span className="text-sm">Stop</span>
                </button>
              </div>
            )}

            {/* Footer hints */}
            <div className="mt-4 pt-3 border-t border-gray-100 dark:border-gray-700">
              {isActive && lastAutoSave ? (
                <p className="text-xs text-center text-emerald-600 dark:text-emerald-400 flex items-center justify-center gap-1">
                  <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2.5">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                  </svg>
                  Auto-saved {lastAutoSave.toLocaleTimeString()}
                </p>
              ) : (
                <div className="flex items-center justify-between text-[11px] text-gray-400 dark:text-gray-500">
                  <span>
                    <kbd className="font-mono px-1 py-0.5 rounded bg-gray-100 dark:bg-gray-700">{getDisplayLabel('live.toggleRecording')}</kbd> Start/Stop
                  </span>
                  <span>
                    <kbd className="font-mono px-1 py-0.5 rounded bg-gray-100 dark:bg-gray-700">{getDisplayLabel('live.save')}</kbd> Save
                  </span>
                </div>
              )}
            </div>
          </div>

          {/* Settings Card */}
          <div className="rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 p-5">
            <h2 className="text-sm font-semibold text-gray-900 dark:text-gray-100 mb-3">Settings</h2>

            <div className="mb-3">
              <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">
                Language
              </label>
              <select
                value={language}
                onChange={(e) => setLanguage(e.target.value)}
                disabled={isActive}
                className="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 py-1.5 px-2.5 text-sm text-gray-900 dark:text-gray-100 focus:border-purple-500 focus:outline-none focus:ring-1 focus:ring-purple-500 disabled:opacity-50"
              >
                {LANGUAGES.map(lang => (
                  <option key={lang.code} value={lang.code}>{lang.label}</option>
                ))}
              </select>
            </div>

            <label className="flex items-start gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={highDetail}
                onChange={(e) => setHighDetail(e.target.checked)}
                disabled={isActive}
                className="mt-0.5 rounded border-gray-300 text-purple-600 focus:ring-purple-500"
              />
              <span className="flex-1">
                <span className="block text-xs font-medium text-gray-700 dark:text-gray-300">
                  High detail mode
                </span>
                <span className="block text-[11px] text-gray-500 dark:text-gray-400 leading-snug">
                  Speaker labels + word-level confidence. Slower per chunk.
                </span>
              </span>
            </label>

            {connectionState !== 'disconnected' && !isActive && (
              <button
                onClick={disconnect}
                className="mt-3 w-full py-1.5 text-xs font-medium rounded-lg border border-gray-300 dark:border-gray-600 text-gray-600 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors"
              >
                Disconnect
              </button>
            )}
          </div>

          {/* Metadata */}
          <MetadataPanel
            metadata={metadata}
            onChange={setMetadata}
            disabled={isSaving}
          />
        </div>

        {/* Right Panel - Transcript */}
        <div className="lg:col-span-2">
          <div className="rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 h-full flex flex-col min-h-[500px]">
            {/* Header */}
            <div className="px-5 py-3 border-b border-gray-200 dark:border-gray-700 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 sticky top-0 bg-white dark:bg-gray-800 rounded-t-xl z-10">
              <div className="flex items-center gap-3">
                <h2 className="text-base font-semibold text-gray-900 dark:text-gray-100">Live Transcript</h2>
                {segments.length > 0 && (
                  <span className="text-xs text-gray-500 dark:text-gray-400 tabular-nums">
                    {segments.length} segment{segments.length === 1 ? '' : 's'} · {wordCount} word{wordCount === 1 ? '' : 's'}
                  </span>
                )}
              </div>
              <div className="flex items-center gap-2 flex-wrap">
                <button
                  onClick={handleSaveClick}
                  disabled={segments.length === 0 || !sessionId}
                  className="inline-flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium rounded-lg bg-purple-600 text-white hover:bg-purple-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                >
                  <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M8 7H5a2 2 0 00-2 2v9a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-3m-1 4l-3 3m0 0l-3-3m3 3V4" />
                  </svg>
                  Save
                </button>
                <button
                  onClick={downloadTranscript}
                  disabled={segments.length === 0}
                  className="inline-flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium rounded-lg border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                >
                  <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
                  </svg>
                  Download
                </button>
                <button
                  onClick={() => clearTranscript()}
                  disabled={segments.length === 0}
                  className="px-3 py-1.5 text-sm font-medium rounded-lg border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                >
                  Clear
                </button>
              </div>
            </div>

            {/* Transcript Content */}
            <div
              ref={transcriptScrollRef}
              onScroll={handleTranscriptScroll}
              className="flex-1 p-3 overflow-y-auto min-h-[480px] max-h-[680px]"
              onFocusCapture={() => setIsEditingSegment(true)}
              onBlurCapture={() => setIsEditingSegment(false)}
            >
              {segments.length === 0 ? (
                <div className="h-full flex flex-col items-center justify-center text-gray-400 dark:text-gray-500 px-6 py-12">
                  <div className="w-16 h-16 rounded-2xl bg-gradient-to-br from-purple-100 to-purple-200 dark:from-purple-900/30 dark:to-purple-800/30 flex items-center justify-center mb-4">
                    <svg className="w-8 h-8 text-purple-500 dark:text-purple-300" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="1.5">
                      <path strokeLinecap="round" strokeLinejoin="round" d="M19 11a7 7 0 01-7 7m0 0a7 7 0 01-7-7m7 7v4m0 0H8m4 0h4m-4-8a3 3 0 01-3-3V5a3 3 0 116 0v6a3 3 0 01-3 3z" />
                    </svg>
                  </div>
                  <p className="text-base font-semibold text-gray-700 dark:text-gray-200">Ready when you are</p>
                  <p className="text-sm mt-1 text-center max-w-sm">
                    {connectionState === 'disconnected'
                      ? 'Click Start Recording to connect your microphone and begin transcribing in real time.'
                      : connectionState === 'connecting'
                        ? 'Connecting to the transcription server…'
                        : 'Microphone is ready. Press Start Recording to begin.'}
                  </p>
                </div>
              ) : (
                <div className="space-y-1">
                  {segments.map((seg, i) => {
                    const prev = i > 0 ? segments[i - 1] : null;
                    const sameSpeakerAsPrev = !!(prev && prev.speaker && prev.speaker === seg.speaker);
                    return (
                      <LiveSegment
                        key={`${i}-${seg.start}`}
                        segment={seg}
                        index={i}
                        onEditText={updateSegmentText}
                        onDelete={deleteSegment}
                        showTimestamps={true}
                        showConfidence={highDetailMode}
                        hideSpeaker={sameSpeakerAsPrev}
                      />
                    );
                  })}

                  {/* "Listening" pulse — shown while recording, just below the
                       latest segment, so the user knows more is coming. */}
                  {isRecording && (
                    <div className="flex items-center gap-2 px-3 py-1.5 text-xs text-gray-400 dark:text-gray-500">
                      <span className="flex items-center gap-1">
                        <span className="w-1 h-1 rounded-full bg-purple-400 animate-pulse [animation-delay:0ms]" />
                        <span className="w-1 h-1 rounded-full bg-purple-400 animate-pulse [animation-delay:150ms]" />
                        <span className="w-1 h-1 rounded-full bg-purple-400 animate-pulse [animation-delay:300ms]" />
                      </span>
                      <span>Listening…</span>
                    </div>
                  )}
                </div>
              )}
              <div ref={transcriptEndRef} />
            </div>

            {/* "Jump to bottom" pill — appears when user scrolled up while
                  new segments are arriving. */}
            {!isAtBottom && segments.length > 0 && (
              <div className="absolute bottom-20 right-8 pointer-events-auto">
                <button
                  onClick={() => {
                    transcriptEndRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
                    setIsAtBottom(true);
                  }}
                  className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full bg-gray-900 dark:bg-gray-700 text-white text-xs shadow-lg hover:bg-gray-800 dark:hover:bg-gray-600 transition-colors"
                >
                  <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2.5">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M19 14l-7 7m0 0l-7-7m7 7V3" />
                  </svg>
                  Jump to latest
                </button>
              </div>
            )}

            {/* Footer Stats */}
            {segments.length > 0 && highDetailMode && (
              <div className="px-5 py-2.5 border-t border-gray-200 dark:border-gray-700 flex items-center justify-end text-xs text-gray-500 dark:text-gray-400 gap-3">
                <span className="text-gray-400">Confidence:</span>
                <span className="inline-flex items-center gap-1">
                  <span className="w-3 h-0.5 bg-amber-400 rounded" /> Uncertain
                </span>
                <span className="inline-flex items-center gap-1">
                  <span className="w-3 h-0.5 bg-red-400 rounded" /> Low
                </span>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Save Confirmation Dialog */}
      {showSaveConfirm && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
          <div className="bg-white dark:bg-gray-800 rounded-lg shadow-xl w-full max-w-md mx-4 p-6">
            <h3 className="text-lg font-semibold text-gray-900 dark:text-gray-100 mb-4">
              Save Recording
            </h3>

            <div className="space-y-3">
              <p className="text-sm text-gray-600 dark:text-gray-400">
                Save <strong>{segments.length} segments</strong> ({wordCount} words) as a recording?
              </p>

              {!metadata.title.trim() && (
                <p className="text-xs text-gray-500 dark:text-gray-400">
                  No title set — will be saved as "Live Recording {new Date().toLocaleDateString()}"
                </p>
              )}

              {metadata.title.trim() && (
                <p className="text-sm text-gray-700 dark:text-gray-300">
                  Title: <strong>{metadata.title}</strong>
                </p>
              )}

              {metadata.tags.length > 0 && (
                <div className="flex flex-wrap gap-1">
                  {metadata.tags.map(tag => (
                    <span key={tag} className="inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium bg-purple-100 dark:bg-purple-900/30 text-purple-700 dark:text-purple-300">
                      {tag}
                    </span>
                  ))}
                </div>
              )}

              {saveError && (
                <p className="text-xs text-red-600 dark:text-red-400">{saveError}</p>
              )}
            </div>

            <div className="flex justify-end gap-3 mt-6">
              <button
                onClick={() => { setShowSaveConfirm(false); setSaveError(null); }}
                className="px-4 py-2 text-sm font-medium rounded-lg border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={handleSave}
                disabled={isSaving}
                className="px-4 py-2 text-sm font-medium rounded-lg bg-purple-600 text-white hover:bg-purple-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
              >
                {isSaving ? 'Saving...' : 'Save Recording'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

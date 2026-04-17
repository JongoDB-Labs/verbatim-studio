import { useCallback, useEffect, useRef, useState } from 'react';
import { api, type TranscriptWithSegments, type Segment, type Speaker, type Recording, type HighlightColor, type QualityReviewRecord } from '@/lib/api';
import { WaveformPlayer, type WaveformPlayerRef } from '@/components/audio/WaveformPlayer';
import { EditableSegment } from '@/components/transcript/EditableSegment';
import { ExportButton } from '@/components/transcript/ExportButton';
import { SpeakerPanel } from '@/components/transcript/SpeakerPanel';
import { AIAnalysisPanel } from '@/components/ai/AIAnalysisPanel';
import { EntityPanel } from '@/components/entities/EntityPanel';
import { BulkHighlightToolbar } from '@/components/transcript/BulkHighlightToolbar';
import { TranscriptSearch, highlightSearchMatches } from '@/components/transcript/TranscriptSearch';
import { QualityReviewButton } from '@/components/transcript/QualityReviewButton';
import { QualityReviewPanel } from '@/components/transcript/QualityReviewPanel';
import { FillerDetectionPanel } from '@/components/transcript/FillerDetectionPanel';
import { TranslateButton } from '@/components/transcript/TranslateButton';
import { useKeyboardShortcuts, getPlaybackShortcuts } from '@/hooks/useKeyboardShortcuts';
import { useKeybindingStore } from '@/stores/keybindingStore';
import { useTaskStore } from '@/stores/taskStore';

interface SearchMatch {
  segmentId: string;
  segmentIndex: number;
  startIndex: number;
  endIndex: number;
}

interface TranscriptPageProps {
  recordingId: string;
  onBack: () => void;
  initialSeekTime?: number;
}

export function TranscriptPage({ recordingId, onBack, initialSeekTime }: TranscriptPageProps) {
  // Subscribe to keybinding changes so shortcut labels update reactively
  useKeybindingStore(s => s.overrides);
  const [transcript, setTranscript] = useState<TranscriptWithSegments | null>(null);
  const [recording, setRecording] = useState<Recording | null>(null);
  const [speakers, setSpeakers] = useState<Speaker[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [currentTime, setCurrentTime] = useState(0);
  const audioRef = useRef<WaveformPlayerRef>(null);

  // Quality review state
  const [qualityReviewJobId, setQualityReviewJobId] = useState<string | null>(null);
  const [qualityReviewRecord, setQualityReviewRecord] = useState<QualityReviewRecord | null>(null);

  const loadData = useCallback(async (silent = false) => {
    try {
      if (!silent) {
        setIsLoading(true);
        setError(null);
      }

      // Load transcript and recording data in parallel
      const [transcriptData, recordingData] = await Promise.all([
        api.transcripts.byRecording(recordingId),
        api.recordings.get(recordingId),
      ]);

      setTranscript(transcriptData);
      setRecording(recordingData);

      // Load speakers for this transcript
      try {
        const speakersData = await api.speakers.byTranscript(transcriptData.id);
        setSpeakers(speakersData.items);
      } catch {
        // Speakers might not exist (no diarization)
        setSpeakers([]);
      }
    } catch (err) {
      if (!silent) {
        setError(err instanceof Error ? err.message : 'Failed to load transcript');
      }
    } finally {
      if (!silent) {
        setIsLoading(false);
      }
    }
  }, [recordingId]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  // Seek to initial time (from search result click) once data is loaded
  const hasAppliedInitialSeek = useRef(false);
  useEffect(() => {
    if (
      initialSeekTime !== undefined &&
      !hasAppliedInitialSeek.current &&
      !isLoading &&
      transcript &&
      audioRef.current
    ) {
      hasAppliedInitialSeek.current = true;
      // Small delay to let the waveform initialize
      setTimeout(() => {
        audioRef.current?.seekTo(initialSeekTime);
      }, 500);
    }
  }, [initialSeekTime, isLoading, transcript]);

  // Watch for quality review completion or cancellation via task store
  useEffect(() => {
    if (!qualityReviewJobId || !transcript) return;

    const unsubscribe = useTaskStore.subscribe((state) => {
      const task = state.tasks.get(qualityReviewJobId);

      if (task?.status === 'complete') {
        // Job completed — fetch the result
        api.qualityReview.getResult(transcript.id, qualityReviewJobId)
          .then((result) => {
            if (result) {
              setQualityReviewRecord(result);
            }
          })
          .catch(console.error)
          .finally(() => setQualityReviewJobId(null));
      }
    });

    // Also check if task was removed (cancelled) — poll for that since
    // Zustand subscribe only fires on changes, not on initial missing state
    const interval = setInterval(() => {
      const tasks = useTaskStore.getState().tasks;
      if (!tasks.has(qualityReviewJobId)) {
        setQualityReviewJobId(null);
      }
    }, 2000);

    return () => {
      unsubscribe();
      clearInterval(interval);
    };
  }, [qualityReviewJobId, transcript]);

  // Handle segment text/speaker updates
  const handleSegmentUpdate = (updated: Segment) => {
    setTranscript((prev) => {
      if (!prev) return prev;
      return {
        ...prev,
        segments: prev.segments.map((s) => (s.id === updated.id ? updated : s)),
      };
    });
  };

  // Handle speaker name updates
  const handleSpeakerUpdate = (updated: Speaker) => {
    setSpeakers((prev) => prev.map((s) => (s.id === updated.id ? updated : s)));
  };

  // Handle speaker merge
  const handleSpeakerMerge = useCallback((removedSpeaker: Speaker, targetSpeaker: Speaker, _segmentsMoved: number) => {
    // Remove the merged speaker from the list
    setSpeakers((prev) => prev.filter((s) => s.id !== removedSpeaker.id));
    // Update all segments that had the removed speaker's label to the target's label
    setTranscript((prev) => {
      if (!prev) return prev;
      return {
        ...prev,
        segments: prev.segments.map((s) =>
          s.speaker === removedSpeaker.speaker_label
            ? { ...s, speaker: targetSpeaker.speaker_label }
            : s
        ),
      };
    });
  }, []);

  // Handle per-segment speaker reassignment
  const handleSpeakerReassign = useCallback(async (segmentId: string, speakerName: string) => {
    if (!transcript) return;
    try {
      const result = await api.speakers.reassignSegment(transcript.id, segmentId, speakerName);
      // Update the segment in transcript state
      setTranscript((prev) => {
        if (!prev) return prev;
        return {
          ...prev,
          segments: prev.segments.map((s) =>
            s.id === result.segment.id ? result.segment : s
          ),
        };
      });
      // Replace entire speakers list (handles additions and orphan cleanup)
      setSpeakers(result.speakers);
    } catch (error) {
      console.error('Failed to reassign segment speaker:', error);
    }
  }, [transcript]);

  // --- Selection & annotation state ---
  const [selectedSegmentIds, setSelectedSegmentIds] = useState<Set<string>>(new Set());

  // --- In-transcript search state ---
  const [showSearch, setShowSearch] = useState(false);
  const [searchMatches, setSearchMatches] = useState<SearchMatch[]>([]);
  const [currentMatchIndex, setCurrentMatchIndex] = useState(-1);
  const segmentRefs = useRef<Map<number, HTMLDivElement>>(new Map());

  const handleToggleSelect = useCallback((segmentId: string) => {
    setSelectedSegmentIds((prev) => {
      const next = new Set(prev);
      if (next.has(segmentId)) next.delete(segmentId);
      else next.add(segmentId);
      return next;
    });
  }, []);

  const handleHighlightChange = useCallback(async (segmentId: string, color: HighlightColor | null) => {
    try {
      if (color) {
        await api.highlights.set(segmentId, color);
      } else {
        await api.highlights.remove(segmentId);
      }
      setTranscript((prev) => {
        if (!prev) return prev;
        return {
          ...prev,
          segments: prev.segments.map((s) =>
            s.id === segmentId ? { ...s, highlight_color: color } : s
          ),
        };
      });
    } catch (err) {
      console.error('Failed to update highlight:', err);
    }
  }, []);

  const handleBulkHighlight = useCallback(async (color: HighlightColor) => {
    if (!transcript || selectedSegmentIds.size === 0) return;
    try {
      const ids = Array.from(selectedSegmentIds);
      await api.highlights.bulkSet(transcript.id, ids, color);
      setTranscript((prev) => {
        if (!prev) return prev;
        return {
          ...prev,
          segments: prev.segments.map((s) =>
            selectedSegmentIds.has(s.id) ? { ...s, highlight_color: color } : s
          ),
        };
      });
      setSelectedSegmentIds(new Set());
    } catch (err) {
      console.error('Failed to bulk highlight:', err);
    }
  }, [transcript, selectedSegmentIds]);

  const handleBulkRemoveHighlight = useCallback(async () => {
    if (!transcript || selectedSegmentIds.size === 0) return;
    try {
      const ids = Array.from(selectedSegmentIds);
      await api.highlights.bulkRemove(transcript.id, ids);
      setTranscript((prev) => {
        if (!prev) return prev;
        return {
          ...prev,
          segments: prev.segments.map((s) =>
            selectedSegmentIds.has(s.id) ? { ...s, highlight_color: null } : s
          ),
        };
      });
      setSelectedSegmentIds(new Set());
    } catch (err) {
      console.error('Failed to remove highlights:', err);
    }
  }, [transcript, selectedSegmentIds]);

  const handleDeleteSegment = useCallback(async (segmentId: string) => {
    if (!transcript) return;
    try {
      await api.transcripts.deleteSegment(transcript.id, segmentId);
      setTranscript((prev) => {
        if (!prev) return prev;
        return {
          ...prev,
          segments: prev.segments.filter((s) => s.id !== segmentId),
        };
      });
    } catch (err) {
      console.error('Failed to delete segment:', err);
    }
  }, [transcript]);

  const handleBulkDelete = useCallback(async () => {
    if (!transcript || selectedSegmentIds.size === 0) return;
    try {
      const ids = Array.from(selectedSegmentIds);
      await api.transcripts.bulkDeleteSegments(transcript.id, ids);
      setTranscript((prev) => {
        if (!prev) return prev;
        return {
          ...prev,
          segments: prev.segments.filter((s) => !selectedSegmentIds.has(s.id)),
        };
      });
      setSelectedSegmentIds(new Set());
    } catch (err) {
      console.error('Failed to bulk delete segments:', err);
    }
  }, [transcript, selectedSegmentIds]);

  const handleCommentCountChange = useCallback((segmentId: string, delta: number) => {
    setTranscript((prev) => {
      if (!prev) return prev;
      return {
        ...prev,
        segments: prev.segments.map((s) =>
          s.id === segmentId ? { ...s, comment_count: s.comment_count + delta } : s
        ),
      };
    });
  }, []);

  // Seek audio and start playing
  const seekTo = (time: number) => {
    if (audioRef.current) {
      audioRef.current.seekTo(time);
      audioRef.current.play();
    }
  };

  // Find active segment based on current playback time
  const activeSegmentIndex = transcript?.segments.findIndex(
    (s) => currentTime >= s.start_time && currentTime < s.end_time
  ) ?? -1;
  const activeSegmentId = activeSegmentIndex >= 0 ? transcript?.segments[activeSegmentIndex]?.id : undefined;

  // Keyboard shortcut handlers
  const handleSkipBack = useCallback((seconds = 5) => {
    if (audioRef.current) {
      const newTime = Math.max(0, currentTime - seconds);
      audioRef.current.seekTo(newTime);
    }
  }, [currentTime]);

  const handleSkipForward = useCallback((seconds = 5) => {
    if (audioRef.current) {
      audioRef.current.seekTo(currentTime + seconds);
    }
  }, [currentTime]);

  const handleNextSegment = useCallback(() => {
    if (!transcript || activeSegmentIndex < 0) return;
    const nextIndex = activeSegmentIndex + 1;
    if (nextIndex < transcript.segments.length) {
      seekTo(transcript.segments[nextIndex].start_time);
    }
  }, [transcript, activeSegmentIndex]);

  const handlePrevSegment = useCallback(() => {
    if (!transcript || activeSegmentIndex < 0) return;
    const prevIndex = activeSegmentIndex - 1;
    if (prevIndex >= 0) {
      seekTo(transcript.segments[prevIndex].start_time);
    }
  }, [transcript, activeSegmentIndex]);

  // Register keyboard shortcuts
  useKeyboardShortcuts({
    onPlayPause: () => audioRef.current?.toggle(),
    onSkipBack: handleSkipBack,
    onSkipForward: handleSkipForward,
    onEscape: showSearch ? () => setShowSearch(false) : onBack,
    onNextSegment: handleNextSegment,
    onPrevSegment: handlePrevSegment,
    enabled: !isLoading && !!transcript,
  });

  // Ctrl+F to open in-transcript search
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 'f') {
        e.preventDefault();
        setShowSearch(true);
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, []);

  // Search handlers
  const handleSearchMatchChange = useCallback((matches: SearchMatch[], currentIndex: number) => {
    setSearchMatches(matches);
    setCurrentMatchIndex(currentIndex);
  }, []);

  const handleScrollToSegment = useCallback((segmentIndex: number) => {
    const el = segmentRefs.current.get(segmentIndex);
    if (el) {
      el.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
  }, []);

  const handleScrollToSegmentById = useCallback((segmentId: string) => {
    if (!transcript) return;
    const idx = transcript.segments.findIndex(s => s.id === segmentId);
    if (idx >= 0) {
      handleScrollToSegment(idx);
    }
  }, [transcript, handleScrollToSegment]);

  const handleTranslate = useCallback((language: string) => {
    // Store prefill prompt for the chat page to pick up
    sessionStorage.setItem('verbatim-chat-prefill', JSON.stringify({
      prompt: `Translate this transcript to ${language}`,
      recording_ids: [recordingId],
    }));
    // Navigate to chats page via pushState + popstate (App.tsx listens for popstate)
    window.history.pushState(null, '', '/chats');
    window.dispatchEvent(new PopStateEvent('popstate'));
  }, [recordingId]);

  const handleCloseSearch = useCallback(() => {
    setShowSearch(false);
    setSearchMatches([]);
    setCurrentMatchIndex(-1);
  }, []);

  const [showShortcuts, setShowShortcuts] = useState(false);
  const [isEditingTitle, setIsEditingTitle] = useState(false);
  const [editedTitle, setEditedTitle] = useState('');

  // Handle title edit
  const handleStartEditTitle = useCallback(() => {
    if (recording) {
      setEditedTitle(recording.title);
      setIsEditingTitle(true);
    }
  }, [recording]);

  const handleSaveTitle = useCallback(async () => {
    if (!recording || !editedTitle.trim()) return;
    try {
      await api.recordings.update(recording.id, { title: editedTitle.trim() });
      setRecording((prev) => prev ? { ...prev, title: editedTitle.trim() } : prev);
      setIsEditingTitle(false);
    } catch (err) {
      console.error('Failed to update title:', err);
    }
  }, [recording, editedTitle]);

  const handleCancelEditTitle = useCallback(() => {
    setIsEditingTitle(false);
    setEditedTitle('');
  }, []);

  // Create speaker lookup maps
  const speakerMap = new Map<string, Speaker>();
  const speakerIndexMap = new Map<string, number>();
  speakers.forEach((s, idx) => {
    speakerMap.set(s.speaker_label, s);
    speakerIndexMap.set(s.speaker_label, idx);
  });

  // Back button component (reused across states)
  const BackButton = () => (
    <button
      onClick={onBack}
      className="inline-flex items-center gap-2 text-sm text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200 transition-colors"
    >
      <svg
        xmlns="http://www.w3.org/2000/svg"
        className="h-4 w-4"
        fill="none"
        viewBox="0 0 24 24"
        stroke="currentColor"
        strokeWidth="2"
      >
        <path
          strokeLinecap="round"
          strokeLinejoin="round"
          d="M10 19l-7-7m0 0l7-7m-7 7h18"
        />
      </svg>
      Back to Recordings
    </button>
  );

  // Loading state
  if (isLoading) {
    return (
      <div className="space-y-6">
        <BackButton />
        <div className="flex items-center justify-center py-12">
          <svg
            className="h-8 w-8 animate-spin text-blue-600"
            xmlns="http://www.w3.org/2000/svg"
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
              d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"
            />
          </svg>
        </div>
      </div>
    );
  }

  // Error state
  if (error) {
    return (
      <div className="space-y-6">
        <BackButton />
        <div className="rounded-lg border border-red-300 dark:border-red-800 bg-red-50 dark:bg-red-900/20 p-6 text-center">
          <svg
            className="h-12 w-12 text-red-600 dark:text-red-400 mx-auto mb-4"
            xmlns="http://www.w3.org/2000/svg"
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
            strokeWidth="2"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"
            />
          </svg>
          <h3 className="text-lg font-semibold text-red-600 dark:text-red-400 mb-2">
            Failed to Load Transcript
          </h3>
          <p className="text-sm text-red-600/80 dark:text-red-400/80">{error}</p>
          <button
            onClick={() => loadData()}
            className="mt-4 inline-flex items-center gap-2 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 transition-colors"
          >
            Try Again
          </button>
        </div>
      </div>
    );
  }

  // No transcript found
  if (!transcript || !recording) {
    return (
      <div className="space-y-6">
        <BackButton />
        <div className="text-center py-12">
          <p className="text-gray-500 dark:text-gray-400">No transcript found for this recording.</p>
        </div>
      </div>
    );
  }

  // Transcript loaded successfully
  return (
    <div className="space-y-6">
      <BackButton />

      {/* Transcript info header - now at top */}
      <div className="rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 p-4">
        <div className="flex items-start justify-between mb-3">
          {/* Editable title */}
          {isEditingTitle ? (
            <div className="flex items-center gap-2 flex-1 mr-4">
              <input
                type="text"
                value={editedTitle}
                onChange={(e) => setEditedTitle(e.target.value)}
                className="flex-1 text-lg font-semibold rounded-md border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 px-3 py-1 text-gray-900 dark:text-gray-100 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
                autoFocus
                onKeyDown={(e) => {
                  if (e.key === 'Enter') handleSaveTitle();
                  if (e.key === 'Escape') handleCancelEditTitle();
                }}
              />
              <button
                onClick={handleSaveTitle}
                className="p-1.5 rounded-md text-green-600 hover:bg-green-50 dark:hover:bg-green-900/30"
                title="Save"
              >
                <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                </svg>
              </button>
              <button
                onClick={handleCancelEditTitle}
                className="p-1.5 rounded-md text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-700"
                title="Cancel"
              >
                <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>
          ) : (
            <div className="flex items-center gap-2">
              <h2 className="text-lg font-semibold text-gray-900 dark:text-gray-100">
                {recording.title}
              </h2>
              <button
                onClick={handleStartEditTitle}
                className="p-1 rounded-md text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700"
                title="Edit title"
              >
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z" />
                </svg>
              </button>
            </div>
          )}
          <div className="flex items-center gap-2">
            <button
              onClick={() => setShowSearch(true)}
              className="inline-flex items-center gap-1.5 px-3 py-2 text-sm rounded-md border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors"
              title="Find in transcript (Ctrl+F)"
            >
              <svg className="w-4 h-4" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                <path strokeLinecap="round" strokeLinejoin="round" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
              </svg>
              <span className="hidden sm:inline">Find</span>
            </button>
            <button
              onClick={() => setShowShortcuts(!showShortcuts)}
              className="inline-flex items-center gap-1.5 px-3 py-2 text-sm rounded-md border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors"
              title="Keyboard shortcuts"
            >
              <svg className="w-4 h-4" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 6V4m0 2a2 2 0 100 4m0-4a2 2 0 110 4m-6 8a2 2 0 100-4m0 4a2 2 0 110-4m0 4v2m0-6V4m6 6v10m6-2a2 2 0 100-4m0 4a2 2 0 110-4m0 4v2m0-6V4" />
              </svg>
              <span className="hidden sm:inline">Shortcuts</span>
            </button>
            <QualityReviewButton
              transcriptId={transcript.id}
              onJobStarted={(jobId) => setQualityReviewJobId(jobId)}
            />
            <TranslateButton
              transcriptId={transcript.id}
              onTranslate={handleTranslate}
            />
            <ExportButton transcriptId={transcript.id} title={recording.title} />
          </div>
        </div>

        {/* Keyboard Shortcuts Panel */}
        {showShortcuts && (
          <div className="mt-3 p-3 rounded-lg bg-gray-50 dark:bg-gray-900 border border-gray-200 dark:border-gray-700">
            <div className="flex items-center justify-between mb-2">
              <h4 className="text-sm font-medium text-gray-900 dark:text-gray-100">Keyboard Shortcuts</h4>
              <button
                onClick={() => setShowShortcuts(false)}
                className="text-gray-400 hover:text-gray-600 dark:hover:text-gray-200"
              >
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>
            <div className="grid grid-cols-2 sm:grid-cols-3 gap-2 text-xs">
              {getPlaybackShortcuts().map(({ key, description }) => (
                <div key={description} className="flex items-center gap-2">
                  <kbd className="px-1.5 py-0.5 bg-gray-200 dark:bg-gray-700 rounded text-gray-700 dark:text-gray-300 font-mono">
                    {key}
                  </kbd>
                  <span className="text-gray-600 dark:text-gray-400">{description}</span>
                </div>
              ))}
            </div>
          </div>
        )}
        <div className="flex flex-wrap gap-4 text-sm">
          {transcript.language && (
            <div className="flex items-center gap-2">
              <span className="text-gray-500 dark:text-gray-400">Language:</span>
              <span className="font-medium text-gray-900 dark:text-gray-100">
                {transcript.language.toUpperCase()}
              </span>
            </div>
          )}
          {transcript.word_count !== null && (
            <div className="flex items-center gap-2">
              <span className="text-gray-500 dark:text-gray-400">Words:</span>
              <span className="font-medium text-gray-900 dark:text-gray-100">
                {transcript.word_count.toLocaleString()}
              </span>
            </div>
          )}
          {transcript.model_used && (
            <div className="flex items-center gap-2">
              <span className="text-gray-500 dark:text-gray-400">Model:</span>
              <span className="font-mono text-xs bg-gray-100 dark:bg-gray-700 px-2 py-0.5 rounded text-gray-700 dark:text-gray-300">
                {transcript.model_used}
              </span>
            </div>
          )}
          {speakers.length > 0 && (
            <div className="flex items-center gap-2">
              <span className="text-gray-500 dark:text-gray-400">Speakers:</span>
              <span className="font-medium text-gray-900 dark:text-gray-100">
                {speakers.length}
              </span>
            </div>
          )}
        </div>

        {/* Recording Template & Metadata */}
        {(recording.template || (recording.metadata && Object.keys(recording.metadata).length > 0)) && (
          <div className="mt-4 pt-4 border-t border-gray-200 dark:border-gray-700">
            {recording.template && (
              <div className="mb-3">
                <span className="inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium bg-violet-100 text-violet-700 dark:bg-violet-900/30 dark:text-violet-300">
                  <svg className="w-3.5 h-3.5" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                  </svg>
                  {recording.template.name}
                </span>
              </div>
            )}
            {recording.metadata && Object.keys(recording.metadata).length > 0 && (
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3 text-sm">
                {Object.entries(recording.metadata).map(([key, value]) => {
                  // Skip empty values
                  if (value === null || value === undefined || value === '') return null;
                  // Format the key for display (convert snake_case to Title Case)
                  const formattedKey = key
                    .replace(/_/g, ' ')
                    .replace(/\b\w/g, (c) => c.toUpperCase());
                  // Format the value
                  const formattedValue = Array.isArray(value)
                    ? value.join(', ')
                    : typeof value === 'object'
                    ? JSON.stringify(value)
                    : String(value);
                  return (
                    <div key={key} className="flex flex-col">
                      <span className="text-xs text-gray-500 dark:text-gray-400">{formattedKey}</span>
                      <span className="text-gray-900 dark:text-gray-100 break-words">{formattedValue}</span>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        )}
      </div>

      {/* Waveform Player - sticky when scrolling (hidden for transcript-only live recordings) */}
      <div className="sticky top-0 z-10 bg-gray-50 dark:bg-gray-900 pb-4 -mx-4 px-4 pt-2 -mt-2 rounded-lg">
        {recording.file_path.startsWith('live://') ? (
          <div className="py-3 px-4 rounded-lg bg-gray-100 dark:bg-gray-800 text-sm text-gray-500 dark:text-gray-400 text-center">
            Transcript-only recording (no audio saved)
          </div>
        ) : (
          <WaveformPlayer
            ref={audioRef}
            src={api.recordings.getAudioUrl(recordingId)}
            onTimeUpdate={setCurrentTime}
          />
        )}
        {/* In-transcript search bar */}
        {showSearch && (
          <div className="mt-3">
            <TranscriptSearch
              segments={transcript.segments}
              onClose={handleCloseSearch}
              onMatchChange={handleSearchMatchChange}
              onScrollToSegment={handleScrollToSegment}
            />
          </div>
        )}
      </div>

      {/* Speaker Statistics Panel */}
      {speakers.length > 0 && (
        <SpeakerPanel
          speakers={speakers}
          segments={transcript.segments}
          onSpeakerUpdate={handleSpeakerUpdate}
          onSpeakerMerge={handleSpeakerMerge}
        />
      )}

      {/* AI Analysis Panel */}
      <AIAnalysisPanel transcriptId={transcript.id} existingSummary={transcript.ai_summary} onSummaryComplete={loadData} />

      {/* Entity Extraction Panel */}
      <EntityPanel transcriptId={transcript.id} onScrollToSegment={handleScrollToSegmentById} />

      {/* Filler Detection Panel */}
      <FillerDetectionPanel
        transcriptId={transcript.id}
        onScrollToSegment={handleScrollToSegmentById}
      />

      {/* Quality Review Panel */}
      {qualityReviewRecord && (
        <QualityReviewPanel
          record={qualityReviewRecord}
          transcriptId={transcript.id}
          onApplied={() => loadData(true)}
          onDismiss={() => setQualityReviewRecord(null)}
        />
      )}

      {/* Editable Segments */}
      <div>
        <h3 className="text-sm font-medium text-gray-500 dark:text-gray-400 mb-3">
          {transcript.segments.length} segment{transcript.segments.length !== 1 ? 's' : ''}
        </h3>
        <div className="space-y-2">
          {transcript.segments.map((segment, index) => {
            const speaker = segment.speaker ? speakerMap.get(segment.speaker) ?? null : null;
            const speakerIndex = segment.speaker ? (speakerIndexMap.get(segment.speaker) ?? 0) : 0;
            const highlighted = searchMatches.length > 0
              ? highlightSearchMatches(segment.text, segment.id, searchMatches, currentMatchIndex)
              : undefined;

            return (
              <div
                key={segment.id}
                ref={(el) => {
                  if (el) segmentRefs.current.set(index, el);
                  else segmentRefs.current.delete(index);
                }}
              >
                <EditableSegment
                  segment={segment}
                  transcriptId={transcript.id}
                  speaker={speaker}
                  speakerIndex={speakerIndex}
                  isActive={segment.id === activeSegmentId}
                  isSelected={selectedSegmentIds.has(segment.id)}
                  onSegmentUpdate={handleSegmentUpdate}
                  onSpeakerUpdate={handleSpeakerUpdate}
                  onSeek={seekTo}
                  onToggleSelect={handleToggleSelect}
                  onHighlightChange={handleHighlightChange}
                  onCommentCountChange={handleCommentCountChange}
                  onDelete={handleDeleteSegment}
                  onSpeakerReassign={handleSpeakerReassign}
                  highlightedText={highlighted}
                />
              </div>
            );
          })}
        </div>
      </div>

      {/* Bulk highlight toolbar */}
      {selectedSegmentIds.size > 0 && (
        <BulkHighlightToolbar
          selectedCount={selectedSegmentIds.size}
          onHighlight={handleBulkHighlight}
          onRemoveHighlight={handleBulkRemoveHighlight}
          onDelete={handleBulkDelete}
          onClearSelection={() => setSelectedSegmentIds(new Set())}
        />
      )}
    </div>
  );
}

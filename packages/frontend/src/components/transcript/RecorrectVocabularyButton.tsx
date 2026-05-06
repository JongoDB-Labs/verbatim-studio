import { useState } from 'react';
import { api } from '@/lib/api';

/**
 * Re-runs Phase 2 phonetic vocabulary correction over an already-completed
 * transcript. Useful for users who add domain terms to their dictionary
 * AFTER the recording has been transcribed — the existing transcript
 * doesn't get the benefit of those terms unless we re-correct.
 *
 * Idempotent on the backend: replaying produces no further changes once
 * canonical spellings are in place.
 */
export function RecorrectVocabularyButton({
  transcriptId,
  onCompleted,
}: {
  transcriptId: string;
  onCompleted?: (correctionsApplied: number) => void;
}) {
  const [running, setRunning] = useState(false);
  const [lastResult, setLastResult] = useState<{ applied: number; segments: number } | null>(null);

  const handleClick = async () => {
    setRunning(true);
    setLastResult(null);
    try {
      const result = await api.transcripts.recorrect(transcriptId);
      setLastResult({ applied: result.corrections_applied, segments: result.segments_updated });
      onCompleted?.(result.corrections_applied);
      // Auto-clear the result after a few seconds.
      setTimeout(() => setLastResult(null), 4000);
    } catch (err) {
      console.error('Re-correct failed:', err);
      alert(`Re-correct failed: ${err instanceof Error ? err.message : err}`);
    } finally {
      setRunning(false);
    }
  };

  const labelText = (() => {
    if (running) return 'Re-correcting…';
    if (lastResult) {
      if (lastResult.applied === 0) return 'No new corrections';
      return `${lastResult.applied} corrected`;
    }
    return 'Re-correct';
  })();

  return (
    <button
      onClick={handleClick}
      disabled={running}
      className="inline-flex items-center gap-1.5 px-3 py-2 text-sm rounded-md border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-700 disabled:opacity-50 transition-colors"
      title="Apply your custom vocabulary to this transcript. Useful after adding new dictionary terms."
    >
      {running ? (
        <svg className="w-4 h-4 animate-spin" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" />
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z" />
        </svg>
      ) : (
        <svg className="w-4 h-4" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
          <path strokeLinecap="round" strokeLinejoin="round" d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253" />
        </svg>
      )}
      <span className="hidden sm:inline">{labelText}</span>
    </button>
  );
}

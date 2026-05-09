import { useState, useRef, useEffect } from 'react';
import type { TranscriptSegment } from '@/hooks/useLiveTranscription';
import { formatDuration } from '@/lib/utils';
import { getSpeakerPalette } from '@/lib/speakerColors';

interface LiveSegmentProps {
  segment: TranscriptSegment;
  onEditText: (index: number, newText: string) => void;
  onDelete: (index: number) => void;
  index: number;
  showTimestamps: boolean;
  showConfidence: boolean;
  /**
   * When true, hide the speaker badge because the previous segment was
   * spoken by the same person — keeps the transcript visually quiet
   * during long monologues.
   */
  hideSpeaker?: boolean;
}

export function LiveSegment({
  segment,
  onEditText,
  onDelete,
  index,
  showTimestamps,
  showConfidence,
  hideSpeaker = false,
}: LiveSegmentProps) {
  const [isEditing, setIsEditing] = useState(false);
  const [editValue, setEditValue] = useState(segment.text);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (isEditing && inputRef.current) {
      inputRef.current.focus();
      inputRef.current.select();
    }
  }, [isEditing]);

  useEffect(() => {
    if (!isEditing) {
      setEditValue(segment.text);
    }
  }, [segment.text, isEditing]);

  const handleSave = () => {
    const trimmed = editValue.trim();
    if (trimmed && trimmed !== segment.text) {
      onEditText(index, trimmed);
    } else {
      setEditValue(segment.text);
    }
    setIsEditing(false);
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSave();
    } else if (e.key === 'Escape') {
      setEditValue(segment.text);
      setIsEditing(false);
    }
  };

  const palette = getSpeakerPalette(segment.speaker);

  const getConfidenceClass = (confidence: number | null | undefined) => {
    if (confidence == null) return '';
    if (confidence >= 0.85) return '';
    if (confidence >= 0.7) return 'underline decoration-amber-400 decoration-dotted underline-offset-2';
    return 'underline decoration-red-400 decoration-wavy underline-offset-2';
  };

  return (
    <div
      className={`group relative flex gap-3 py-2 px-3 rounded-lg transition-colors ${
        isEditing
          ? 'bg-purple-50 dark:bg-purple-900/20 ring-1 ring-purple-300 dark:ring-purple-700'
          : 'hover:bg-gray-50 dark:hover:bg-gray-800/60'
      } ${segment.edited_by ? `border-l-2 ${palette.border}` : ''}`}
    >
      {/* Timestamp */}
      {showTimestamps && (
        <span className="text-[11px] text-gray-400 dark:text-gray-500 font-mono shrink-0 pt-1 w-10 tabular-nums">
          {formatDuration(segment.start, false)}
        </span>
      )}

      {/* Speaker badge */}
      {segment.speaker && !hideSpeaker && (
        <span
          className={`shrink-0 inline-flex items-center gap-1.5 self-start mt-0.5 px-2 py-0.5 rounded-full text-[11px] font-semibold ${palette.bg} ${palette.text}`}
        >
          <span className={`w-1.5 h-1.5 rounded-full ${palette.accent}`} />
          {segment.speaker}
        </span>
      )}
      {segment.speaker && hideSpeaker && showTimestamps && (
        <span className="shrink-0 w-2" aria-hidden />
      )}

      {/* Text / Edit area */}
      <div className="flex-1 min-w-0">
        {isEditing ? (
          <textarea
            ref={inputRef}
            value={editValue}
            onChange={(e) => setEditValue(e.target.value)}
            onBlur={handleSave}
            onKeyDown={handleKeyDown}
            rows={2}
            className="w-full text-sm bg-white dark:bg-gray-900 border border-purple-300 dark:border-purple-700 rounded-md px-2 py-1 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-1 focus:ring-purple-500 resize-none"
          />
        ) : (
          <p
            onClick={() => setIsEditing(true)}
            className="text-sm text-gray-900 dark:text-gray-100 cursor-text leading-relaxed"
            title="Click to edit"
          >
            {showConfidence && segment.words ? (
              segment.words.map((w, wi) => (
                <span
                  key={wi}
                  className={getConfidenceClass(w.confidence)}
                  title={w.confidence != null ? `Confidence: ${Math.round(w.confidence * 100)}%` : undefined}
                >
                  {w.word}
                </span>
              ))
            ) : (
              segment.text
            )}
          </p>
        )}
      </div>

      {/* Action buttons on hover */}
      {!isEditing && (
        <div className="flex items-center gap-0.5 opacity-0 group-hover:opacity-100 focus-within:opacity-100 transition-opacity shrink-0 pt-0.5">
          <button
            onClick={() => setIsEditing(true)}
            className="text-gray-400 hover:text-gray-700 dark:hover:text-gray-200 p-1 rounded hover:bg-gray-100 dark:hover:bg-gray-700"
            title="Edit segment"
            aria-label="Edit segment"
          >
            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
              <path strokeLinecap="round" strokeLinejoin="round" d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z" />
            </svg>
          </button>
          <button
            onClick={() => onDelete(index)}
            className="text-gray-400 hover:text-red-500 dark:hover:text-red-400 p-1 rounded hover:bg-gray-100 dark:hover:bg-gray-700"
            title="Delete segment"
            aria-label="Delete segment"
          >
            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>
      )}
    </div>
  );
}

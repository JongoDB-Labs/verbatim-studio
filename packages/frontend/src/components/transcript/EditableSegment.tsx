import { useState, useRef, useEffect } from 'react';
import { type Segment, type Speaker, type HighlightColor, api } from '../../lib/api';
import { SpeakerBadge } from './SpeakerBadge';
import { HighlightPicker } from './HighlightPicker';
import { CommentsPanel } from './CommentsPanel';

interface EditableSegmentProps {
  segment: Segment;
  transcriptId: string;
  speaker: Speaker | null;
  speakerIndex: number;
  isActive: boolean;
  isSelected: boolean;
  onSegmentUpdate: (segment: Segment) => void;
  onSpeakerUpdate: (speaker: Speaker) => void;
  onSeek: (time: number) => void;
  onToggleSelect: (segmentId: string) => void;
  onHighlightChange: (segmentId: string, color: HighlightColor | null) => void;
  onCommentCountChange: (segmentId: string, delta: number) => void;
  onDelete?: (segmentId: string) => void;
  onSpeakerReassign?: (segmentId: string, speakerName: string) => void;
  highlightedText?: React.ReactNode;
}

function formatTime(seconds: number): string {
  const mins = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60);
  return `${mins}:${secs.toString().padStart(2, '0')}`;
}

const HIGHLIGHT_BG: Record<string, string> = {
  yellow: 'bg-yellow-50 dark:bg-yellow-900/20 border-l-yellow-400',
  green: 'bg-green-50 dark:bg-green-900/20 border-l-green-400',
  blue: 'bg-blue-50 dark:bg-blue-900/20 border-l-blue-400',
  red: 'bg-red-50 dark:bg-red-900/20 border-l-red-400',
  purple: 'bg-purple-50 dark:bg-purple-900/20 border-l-purple-400',
  orange: 'bg-orange-50 dark:bg-orange-900/20 border-l-orange-400',
};

export function EditableSegment({
  segment,
  transcriptId,
  speaker,
  speakerIndex,
  isActive,
  isSelected,
  onSegmentUpdate,
  onSpeakerUpdate,
  onSeek,
  onToggleSelect,
  onHighlightChange,
  onCommentCountChange,
  onDelete,
  onSpeakerReassign,
  highlightedText,
}: EditableSegmentProps) {
  const [isEditing, setIsEditing] = useState(false);
  const [text, setText] = useState(segment.text);
  const [isSaving, setIsSaving] = useState(false);
  const [showHighlightPicker, setShowHighlightPicker] = useState(false);
  const [showCorrections, setShowCorrections] = useState(false);
  const [showComments, setShowComments] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const segmentRef = useRef<HTMLDivElement>(null);

  // Update local text when segment changes externally
  useEffect(() => {
    setText(segment.text);
  }, [segment.text]);

  // Focus textarea when entering edit mode
  useEffect(() => {
    if (isEditing && textareaRef.current) {
      textareaRef.current.focus();
      textareaRef.current.setSelectionRange(text.length, text.length);
    }
  }, [isEditing, text.length]);

  // Scroll active segment into view
  useEffect(() => {
    if (isActive && segmentRef.current) {
      segmentRef.current.scrollIntoView({
        behavior: 'smooth',
        block: 'nearest',
      });
    }
  }, [isActive]);

  const handleSave = async () => {
    if (text === segment.text) {
      setIsEditing(false);
      return;
    }

    setIsSaving(true);
    try {
      const updated = await api.transcripts.updateSegment(transcriptId, segment.id, { text });
      onSegmentUpdate(updated);
      setIsEditing(false);
    } catch (error) {
      console.error('Failed to save segment:', error);
      setText(segment.text); // Revert on error
    } finally {
      setIsSaving(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') {
      setText(segment.text);
      setIsEditing(false);
    } else if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSave();
    }
    // Shift+Enter allows multiline editing
  };

  // Build class names for highlighting
  const highlightColor = segment.highlight_color;
  const hasHighlight = !!highlightColor;

  let containerClasses: string;
  if (isActive) {
    containerClasses = 'bg-blue-50 dark:bg-blue-900/20 border-blue-300 dark:border-blue-700 shadow-sm';
  } else if (hasHighlight) {
    containerClasses = `${HIGHLIGHT_BG[highlightColor]} border-l-4 border-gray-200 dark:border-gray-700`;
  } else if (isSelected) {
    containerClasses = 'bg-purple-50 dark:bg-purple-900/20 border-purple-300 dark:border-purple-700';
  } else {
    containerClasses = 'bg-white dark:bg-gray-800 border-gray-200 dark:border-gray-700 hover:border-blue-200 dark:hover:border-blue-800';
  }

  return (
    <div ref={segmentRef}>
      <div
        className={`flex gap-3 p-3 rounded-lg border transition-all ${containerClasses}`}
      >
        {/* Selection checkbox */}
        <div className="flex-shrink-0 flex items-start pt-0.5">
          <input
            type="checkbox"
            checked={isSelected}
            onChange={() => onToggleSelect(segment.id)}
            className="w-3.5 h-3.5 rounded border-gray-300 dark:border-gray-600 text-purple-600 focus:ring-purple-500 cursor-pointer"
          />
        </div>

        {/* Timestamp - clickable to seek */}
        <button
          onClick={() => onSeek(segment.start_time)}
          className="flex-shrink-0 w-14 text-sm font-mono text-blue-600 dark:text-blue-400 hover:underline focus:outline-none focus:ring-2 focus:ring-blue-500 rounded"
          title="Click to seek to this time"
        >
          {formatTime(segment.start_time)}
        </button>

        {/* Speaker badge */}
        {speaker && (
          <div className="flex-shrink-0">
            <SpeakerBadge
              speaker={speaker}
              speakerIndex={speakerIndex}
              onUpdate={onSpeakerUpdate}
              onReassign={
                onSpeakerReassign
                  ? (newName) => onSpeakerReassign(segment.id, newName)
                  : undefined
              }
            />
          </div>
        )}

        {/* Segment text */}
        <div className="flex-1 min-w-0">
          {isEditing ? (
            <textarea
              ref={textareaRef}
              value={text}
              onChange={(e) => setText(e.target.value)}
              onBlur={handleSave}
              onKeyDown={handleKeyDown}
              disabled={isSaving}
              className="w-full p-2 text-sm bg-white dark:bg-gray-700 border border-gray-300 dark:border-gray-600 rounded resize-none focus:outline-none focus:ring-2 focus:ring-blue-500 text-gray-900 dark:text-gray-100"
              rows={Math.max(2, text.split('\n').length)}
            />
          ) : (
            <>
            <p
              onClick={() => setIsEditing(true)}
              className={`text-sm leading-relaxed cursor-text hover:bg-gray-100 dark:hover:bg-gray-700/50 rounded p-1 -m-1 ${
                segment.edited_by ? 'text-gray-600 dark:text-gray-400' : 'text-gray-900 dark:text-gray-100'
              }`}
              title="Click to edit"
            >
              {highlightedText ?? segment.text}
              {segment.edited_by === 'human' && (
                <span className="ml-2 text-xs text-gray-500 dark:text-gray-500">(edited)</span>
              )}
              {segment.edited_by === 'ai' && (
                <span
                  className="ml-2 text-xs text-purple-500 dark:text-purple-400"
                  title={segment.original_text ? `Original: ${segment.original_text}` : undefined}
                >
                  AI corrected
                </span>
              )}
              {segment.corrections && segment.corrections.length > 0 && (
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    setShowCorrections((v) => !v);
                  }}
                  className="ml-2 inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-xs font-medium bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300 hover:bg-emerald-200 dark:hover:bg-emerald-900/50 transition-colors"
                  title={`${segment.corrections.length} vocabulary ${segment.corrections.length === 1 ? 'correction' : 'corrections'} applied. Click to review.`}
                >
                  <span aria-hidden>✏️</span>
                  {segment.corrections.length}
                </button>
              )}
            </p>
            {showCorrections && segment.corrections && segment.corrections.length > 0 && (
              <div className="mt-2 rounded-md border border-emerald-200 dark:border-emerald-800 bg-emerald-50/50 dark:bg-emerald-900/20 p-2">
                <div className="flex items-center justify-between mb-2">
                  <span className="text-xs font-medium text-emerald-700 dark:text-emerald-300">
                    Auto-applied vocabulary corrections
                  </span>
                  <button
                    onClick={() => setShowCorrections(false)}
                    className="text-xs text-gray-500 hover:text-gray-700"
                  >
                    Close
                  </button>
                </div>
                <ul className="space-y-1">
                  {segment.corrections.map((c, idx) => (
                    <li key={idx} className="flex items-center justify-between gap-2 text-xs">
                      <span className="text-gray-700 dark:text-gray-300 truncate">
                        <span className="line-through text-gray-400">{c.original}</span>
                        {' → '}
                        <span className="font-medium text-emerald-700 dark:text-emerald-300">{c.replacement}</span>
                        {c.type === 'llm_vocabulary' && (
                          <span className="ml-1 text-purple-500">AI</span>
                        )}
                      </span>
                      <button
                        onClick={async () => {
                          try {
                            const updated = await api.transcripts.revertCorrection(transcriptId, segment.id, idx);
                            onSegmentUpdate(updated);
                          } catch (err) {
                            console.error('Revert failed:', err);
                            alert('Failed to revert correction');
                          }
                        }}
                        className="text-xs text-gray-500 hover:text-red-600 dark:text-gray-400 dark:hover:text-red-400 flex-shrink-0"
                        title="Revert this correction"
                      >
                        Revert
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            )}
            </>
          )}
        </div>

        {/* Action buttons */}
        <div className="flex-shrink-0 flex items-start gap-1 relative">
          {/* Highlight button */}
          <button
            onClick={() => setShowHighlightPicker(!showHighlightPicker)}
            className={`p-1 rounded transition-colors ${
              hasHighlight
                ? 'text-amber-500 hover:text-amber-600'
                : 'text-gray-400 hover:text-amber-500 dark:hover:text-amber-400'
            }`}
            title="Highlight"
          >
            <svg className="w-4 h-4" fill={hasHighlight ? 'currentColor' : 'none'} viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
              <path strokeLinecap="round" strokeLinejoin="round" d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z" />
            </svg>
          </button>

          {/* Comment button */}
          <button
            onClick={() => setShowComments(!showComments)}
            className={`p-1 rounded transition-colors relative ${
              showComments
                ? 'text-blue-600 dark:text-blue-400'
                : 'text-gray-400 hover:text-blue-600 dark:hover:text-blue-400'
            }`}
            title="Comments"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
              <path strokeLinecap="round" strokeLinejoin="round" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
            </svg>
            {segment.comment_count > 0 && (
              <span className="absolute -top-1 -right-1 min-w-[14px] h-[14px] flex items-center justify-center text-[9px] font-bold bg-blue-600 text-white rounded-full leading-none px-0.5">
                {segment.comment_count}
              </span>
            )}
          </button>

          {/* Delete button */}
          {onDelete && (
            <button
              onClick={() => onDelete(segment.id)}
              className="p-1 rounded transition-colors text-gray-400 hover:text-red-500 dark:hover:text-red-400"
              title="Delete segment"
            >
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                <path strokeLinecap="round" strokeLinejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
              </svg>
            </button>
          )}

          {/* Highlight picker popover */}
          {showHighlightPicker && (
            <HighlightPicker
              currentColor={segment.highlight_color}
              onSelect={(color) => onHighlightChange(segment.id, color)}
              onRemove={() => onHighlightChange(segment.id, null)}
              onClose={() => setShowHighlightPicker(false)}
            />
          )}
        </div>
      </div>

      {/* Inline comments panel */}
      {showComments && (
        <CommentsPanel
          segmentId={segment.id}
          onCommentCountChange={onCommentCountChange}
        />
      )}
    </div>
  );
}

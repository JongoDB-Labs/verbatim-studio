import { useState, useCallback, useEffect, useRef } from 'react';
import { api, type SummarizationResponse } from '@/lib/api';
import { useSummarizeTaskForTranscript } from '@/stores/taskStore';

interface AIAnalysisPanelProps {
  transcriptId: string;
  existingSummary?: SummarizationResponse | null;
  onSummaryComplete?: () => void;
}

interface DraftSummary {
  summary: string;
  key_points: string[];
  action_items: string[];
  topics: string[];
  named_entities: string[];
}

function summaryToDraft(s: SummarizationResponse): DraftSummary {
  return {
    summary: s.summary ?? '',
    key_points: s.key_points ?? [],
    action_items: s.action_items ?? [],
    topics: s.topics ?? [],
    named_entities: s.named_entities ?? [],
  };
}

export function AIAnalysisPanel({ transcriptId, existingSummary, onSummaryComplete }: AIAnalysisPanelProps) {
  const [aiAvailable, setAiAvailable] = useState<boolean | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const checkAiStatus = useCallback(() => {
    api.ai.status()
      .then((s) => setAiAvailable(s.available))
      .catch(() => setAiAvailable(false));
  }, []);

  useEffect(() => {
    checkAiStatus();
  }, [checkAiStatus]);

  useEffect(() => {
    const handleVisibilityChange = () => {
      if (document.visibilityState === 'visible') checkAiStatus();
    };
    document.addEventListener('visibilitychange', handleVisibilityChange);
    return () => document.removeEventListener('visibilitychange', handleVisibilityChange);
  }, [checkAiStatus]);

  useEffect(() => {
    const handleAiStatusChange = () => checkAiStatus();
    window.addEventListener('ai-status-changed', handleAiStatusChange);
    return () => window.removeEventListener('ai-status-changed', handleAiStatusChange);
  }, [checkAiStatus]);

  const backgroundSummaryTask = useSummarizeTaskForTranscript(transcriptId);
  const prevBackgroundTask = useRef(backgroundSummaryTask);

  useEffect(() => {
    if (prevBackgroundTask.current && !backgroundSummaryTask) {
      onSummaryComplete?.();
    }
    prevBackgroundTask.current = backgroundSummaryTask;
  }, [backgroundSummaryTask, onSummaryComplete]);

  const [summary, setSummary] = useState<SummarizationResponse | null>(existingSummary ?? null);
  const [temperature, setTemperature] = useState(0.3);
  const [isEditing, setIsEditing] = useState(false);
  const [draft, setDraft] = useState<DraftSummary | null>(null);
  const [isSavingEdit, setIsSavingEdit] = useState(false);

  useEffect(() => {
    if (existingSummary && !summary) {
      setSummary(existingSummary);
    }
  }, [existingSummary, summary]);

  const handleSummarize = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const result = await api.ai.summarize(transcriptId, temperature);
      setSummary(result);
      setIsEditing(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to generate summary');
    } finally {
      setIsLoading(false);
    }
  }, [transcriptId, temperature]);

  const startEditing = useCallback(() => {
    if (!summary) return;
    setDraft(summaryToDraft(summary));
    setError(null);
    setIsEditing(true);
  }, [summary]);

  const cancelEditing = useCallback(() => {
    setDraft(null);
    setIsEditing(false);
    setError(null);
  }, []);

  const saveEdits = useCallback(async () => {
    if (!draft) return;
    setIsSavingEdit(true);
    setError(null);
    try {
      const updated = await api.ai.updateSummary(transcriptId, {
        summary: draft.summary,
        key_points: draft.key_points,
        action_items: draft.action_items,
        topics: draft.topics,
        named_entities: draft.named_entities,
      });
      setSummary(updated);
      setIsEditing(false);
      setDraft(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save edits');
    } finally {
      setIsSavingEdit(false);
    }
  }, [draft, transcriptId]);

  const updateDraft = useCallback(<K extends keyof DraftSummary>(key: K, value: DraftSummary[K]) => {
    setDraft(prev => prev ? { ...prev, [key]: value } : prev);
  }, []);

  return (
    <div className="rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800">
      {/* Header */}
      <div className="px-4 py-3 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <svg
            className="w-5 h-5 text-purple-600 dark:text-purple-400"
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
            strokeWidth="2"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z"
            />
          </svg>
          <h3 className="text-sm font-semibold text-gray-900 dark:text-gray-100">
            AI Analysis
          </h3>
        </div>
        {summary && !isEditing && aiAvailable && (
          <button
            onClick={startEditing}
            className="inline-flex items-center gap-1 text-xs text-gray-500 hover:text-purple-600 dark:text-gray-400 dark:hover:text-purple-300 transition-colors"
            title="Edit summary"
          >
            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
              <path strokeLinecap="round" strokeLinejoin="round" d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z" />
            </svg>
            Edit
          </button>
        )}
      </div>

      {/* No-model state */}
      {aiAvailable === false && (
        <div className="p-6 text-center">
          <svg className="w-10 h-10 mx-auto text-gray-300 dark:text-gray-600 mb-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="1.5">
            <path strokeLinecap="round" strokeLinejoin="round" d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z" />
          </svg>
          <p className="text-sm text-gray-600 dark:text-gray-400 mb-1">No AI model configured</p>
          <p className="text-xs text-gray-500 dark:text-gray-500 mb-3">
            Download a language model in Settings to enable AI-powered analysis.
          </p>
          <button
            onClick={() => window.location.href = '/settings'}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-lg bg-purple-600 text-white hover:bg-purple-700 transition-colors"
          >
            Go to Settings
          </button>
        </div>
      )}

      {aiAvailable === null && (
        <div className="p-6 text-center">
          <svg className="w-5 h-5 mx-auto animate-spin text-gray-400" viewBox="0 0 24 24" fill="none">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
          </svg>
          <p className="mt-2 text-xs text-gray-500 dark:text-gray-400">Checking AI availability...</p>
        </div>
      )}

      {/* Summary content (only when AI is available) */}
      {aiAvailable && (
        <div className="p-4">
          {error && (
            <div className="mb-4 p-3 rounded-lg bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 text-sm text-red-600 dark:text-red-400">
              {error}
            </div>
          )}

          {!summary ? (
            <div className="text-center py-6">
              {backgroundSummaryTask ? (
                <>
                  <svg className="w-6 h-6 mx-auto animate-spin text-purple-500 mb-3" viewBox="0 0 24 24" fill="none">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                  </svg>
                  <p className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                    AI summary in progress...
                  </p>
                  <p className="text-xs text-gray-500 dark:text-gray-400">
                    {backgroundSummaryTask.progress > 0
                      ? `${backgroundSummaryTask.progress}% complete`
                      : 'Starting...'}
                  </p>
                </>
              ) : (
                <>
                  <p className="text-sm text-gray-500 dark:text-gray-400 mb-4">
                    Generate an AI-powered summary of this transcript including key points, action items, and topics.
                  </p>
                  <button
                    onClick={handleSummarize}
                    disabled={isLoading}
                    className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-purple-600 hover:bg-purple-700 text-white text-sm font-medium disabled:opacity-50 transition-colors"
                  >
                    {isLoading ? (
                      <>
                        <svg className="w-4 h-4 animate-spin" viewBox="0 0 24 24" fill="none">
                          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                        </svg>
                        Generating...
                      </>
                    ) : (
                      'Generate Summary'
                    )}
                  </button>
                </>
              )}
            </div>
          ) : isEditing && draft ? (
            <SummaryEditor
              draft={draft}
              isSaving={isSavingEdit}
              onChange={updateDraft}
              onCancel={cancelEditing}
              onSave={saveEdits}
            />
          ) : (
            <div className="space-y-4">
              <p className="text-sm text-gray-600 dark:text-gray-300 whitespace-pre-wrap">{summary.summary}</p>

              {summary.key_points && summary.key_points.length > 0 && (
                <div>
                  <h4 className="text-sm font-medium text-gray-900 dark:text-gray-100 mb-2">Key Points</h4>
                  <ul className="list-disc list-inside space-y-1">
                    {summary.key_points.map((point, i) => (
                      <li key={i} className="text-sm text-gray-600 dark:text-gray-300">{point}</li>
                    ))}
                  </ul>
                </div>
              )}

              {summary.action_items && summary.action_items.length > 0 && (
                <div>
                  <h4 className="text-sm font-medium text-gray-900 dark:text-gray-100 mb-2">Action Items</h4>
                  <ul className="list-disc list-inside space-y-1">
                    {summary.action_items.map((item, i) => (
                      <li key={i} className="text-sm text-gray-600 dark:text-gray-300">{item}</li>
                    ))}
                  </ul>
                </div>
              )}

              {summary.topics && summary.topics.length > 0 && (
                <div>
                  <h4 className="text-sm font-medium text-gray-900 dark:text-gray-100 mb-2">Topics</h4>
                  <div className="flex flex-wrap gap-2">
                    {summary.topics.map((topic, i) => (
                      <span
                        key={i}
                        className="px-2 py-1 text-xs rounded-full bg-gray-100 dark:bg-gray-700 text-gray-700 dark:text-gray-300"
                      >
                        {topic}
                      </span>
                    ))}
                  </div>
                </div>
              )}

              {summary.named_entities && summary.named_entities.length > 0 && (
                <div>
                  <h4 className="text-sm font-medium text-gray-900 dark:text-gray-100 mb-2">Entities</h4>
                  <div className="flex flex-wrap gap-2">
                    {summary.named_entities.map((entity, i) => (
                      <span
                        key={i}
                        className="px-2 py-1 text-xs rounded-full bg-purple-100 dark:bg-purple-900/30 text-purple-700 dark:text-purple-300"
                      >
                        {entity}
                      </span>
                    ))}
                  </div>
                </div>
              )}

              <div className="flex items-center justify-between pt-2 border-t border-gray-100 dark:border-gray-700">
                <button
                  onClick={handleSummarize}
                  disabled={isLoading}
                  className="inline-flex items-center gap-2 text-sm text-purple-600 dark:text-purple-400 hover:underline disabled:opacity-50"
                >
                  {isLoading && (
                    <svg className="w-3.5 h-3.5 animate-spin" viewBox="0 0 24 24" fill="none">
                      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                    </svg>
                  )}
                  {isLoading ? 'Regenerating...' : 'Regenerate'}
                </button>
                <div className="flex items-center gap-2">
                  <label className="text-xs text-gray-500 dark:text-gray-400">Temperature</label>
                  <input
                    type="range"
                    min="0"
                    max="1"
                    step="0.1"
                    value={temperature}
                    onChange={(e) => setTemperature(parseFloat(e.target.value))}
                    className="w-20 h-1 accent-purple-600"
                  />
                  <span className="text-xs text-gray-500 dark:text-gray-400 w-6 text-right">{temperature.toFixed(1)}</span>
                </div>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

interface SummaryEditorProps {
  draft: DraftSummary;
  isSaving: boolean;
  onChange: <K extends keyof DraftSummary>(key: K, value: DraftSummary[K]) => void;
  onCancel: () => void;
  onSave: () => void;
}

function SummaryEditor({ draft, isSaving, onChange, onCancel, onSave }: SummaryEditorProps) {
  return (
    <div className="space-y-4">
      <div>
        <label className="block text-sm font-medium text-gray-900 dark:text-gray-100 mb-1.5">Summary</label>
        <textarea
          value={draft.summary}
          onChange={(e) => onChange('summary', e.target.value)}
          rows={4}
          className="w-full text-sm rounded-md border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-900 px-3 py-2 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-1 focus:ring-purple-500 focus:border-purple-500 resize-y leading-relaxed"
        />
      </div>

      <ListEditor
        label="Key Points"
        items={draft.key_points}
        onChange={(items) => onChange('key_points', items)}
        placeholder="Add a key point…"
      />

      <ListEditor
        label="Action Items"
        items={draft.action_items}
        onChange={(items) => onChange('action_items', items)}
        placeholder="Add an action item…"
      />

      <ChipEditor
        label="Topics"
        items={draft.topics}
        onChange={(items) => onChange('topics', items)}
        placeholder="Add topic and press Enter"
      />

      <ChipEditor
        label="Entities"
        items={draft.named_entities}
        onChange={(items) => onChange('named_entities', items)}
        placeholder="Add entity and press Enter"
      />

      <div className="flex items-center justify-end gap-2 pt-3 border-t border-gray-100 dark:border-gray-700">
        <button
          onClick={onCancel}
          disabled={isSaving}
          className="px-3 py-1.5 text-sm font-medium rounded-lg border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-700 disabled:opacity-50 transition-colors"
        >
          Cancel
        </button>
        <button
          onClick={onSave}
          disabled={isSaving}
          className="inline-flex items-center gap-2 px-4 py-1.5 text-sm font-medium rounded-lg bg-purple-600 text-white hover:bg-purple-700 disabled:opacity-50 transition-colors"
        >
          {isSaving && (
            <svg className="w-3.5 h-3.5 animate-spin" viewBox="0 0 24 24" fill="none">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
            </svg>
          )}
          {isSaving ? 'Saving…' : 'Save Changes'}
        </button>
      </div>
    </div>
  );
}

interface ListEditorProps {
  label: string;
  items: string[];
  onChange: (items: string[]) => void;
  placeholder: string;
}

function ListEditor({ label, items, onChange, placeholder }: ListEditorProps) {
  const updateItem = (i: number, value: string) => {
    onChange(items.map((item, idx) => (idx === i ? value : item)));
  };
  const removeItem = (i: number) => {
    onChange(items.filter((_, idx) => idx !== i));
  };
  const addItem = () => {
    onChange([...items, '']);
  };

  return (
    <div>
      <div className="flex items-center justify-between mb-1.5">
        <label className="text-sm font-medium text-gray-900 dark:text-gray-100">{label}</label>
        <button
          onClick={addItem}
          className="text-xs text-purple-600 dark:text-purple-400 hover:underline"
        >
          + Add
        </button>
      </div>
      {items.length === 0 ? (
        <p className="text-xs text-gray-400 dark:text-gray-500 italic">No {label.toLowerCase()}.</p>
      ) : (
        <div className="space-y-1.5">
          {items.map((item, i) => (
            <div key={i} className="flex items-center gap-2">
              <input
                type="text"
                value={item}
                onChange={(e) => updateItem(i, e.target.value)}
                placeholder={placeholder}
                className="flex-1 text-sm rounded-md border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-900 px-2.5 py-1 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-1 focus:ring-purple-500 focus:border-purple-500"
              />
              <button
                onClick={() => removeItem(i)}
                className="text-gray-400 hover:text-red-500 dark:hover:text-red-400 p-1"
                title="Remove"
                aria-label="Remove item"
              >
                <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

interface ChipEditorProps {
  label: string;
  items: string[];
  onChange: (items: string[]) => void;
  placeholder: string;
}

function ChipEditor({ label, items, onChange, placeholder }: ChipEditorProps) {
  const [draftValue, setDraftValue] = useState('');

  const commit = () => {
    const v = draftValue.trim();
    if (!v) return;
    if (items.includes(v)) {
      setDraftValue('');
      return;
    }
    onChange([...items, v]);
    setDraftValue('');
  };

  const removeAt = (i: number) => {
    onChange(items.filter((_, idx) => idx !== i));
  };

  return (
    <div>
      <label className="block text-sm font-medium text-gray-900 dark:text-gray-100 mb-1.5">{label}</label>
      <div className="flex flex-wrap gap-1.5 mb-1.5">
        {items.map((item, i) => (
          <span
            key={`${item}-${i}`}
            className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs bg-purple-100 dark:bg-purple-900/30 text-purple-700 dark:text-purple-300"
          >
            <input
              type="text"
              value={item}
              onChange={(e) => onChange(items.map((it, idx) => (idx === i ? e.target.value : it)))}
              className="bg-transparent border-none focus:outline-none text-xs min-w-[40px]"
              style={{ width: `${Math.max(item.length, 4)}ch` }}
            />
            <button
              onClick={() => removeAt(i)}
              className="text-purple-500 hover:text-red-500"
              title="Remove"
              aria-label="Remove"
            >
              <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2.5">
                <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </span>
        ))}
      </div>
      <input
        type="text"
        value={draftValue}
        onChange={(e) => setDraftValue(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ',') {
            e.preventDefault();
            commit();
          }
        }}
        onBlur={commit}
        placeholder={placeholder}
        className="w-full text-xs rounded-md border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-900 px-2 py-1 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-1 focus:ring-purple-500 focus:border-purple-500"
      />
    </div>
  );
}

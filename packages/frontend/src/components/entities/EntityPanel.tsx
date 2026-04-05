import { useState, useEffect, useCallback } from 'react';
import { api, type ExtractionEntity, type ExtractionTemplate } from '@/lib/api';

interface EntityPanelProps {
  transcriptId: string;
  onScrollToSegment?: (segmentId: string) => void;
}

const ENTITY_TYPE_COLORS: Record<string, { bg: string; text: string; darkBg: string; darkText: string }> = {
  action_item: { bg: 'bg-green-100', text: 'text-green-700', darkBg: 'dark:bg-green-900/30', darkText: 'dark:text-green-300' },
  decision: { bg: 'bg-blue-100', text: 'text-blue-700', darkBg: 'dark:bg-blue-900/30', darkText: 'dark:text-blue-300' },
  medication: { bg: 'bg-purple-100', text: 'text-purple-700', darkBg: 'dark:bg-purple-900/30', darkText: 'dark:text-purple-300' },
  diagnosis: { bg: 'bg-red-100', text: 'text-red-700', darkBg: 'dark:bg-red-900/30', darkText: 'dark:text-red-300' },
  party: { bg: 'bg-indigo-100', text: 'text-indigo-700', darkBg: 'dark:bg-indigo-900/30', darkText: 'dark:text-indigo-300' },
  ruling: { bg: 'bg-amber-100', text: 'text-amber-700', darkBg: 'dark:bg-amber-900/30', darkText: 'dark:text-amber-300' },
  date: { bg: 'bg-gray-100', text: 'text-gray-700', darkBg: 'dark:bg-gray-700', darkText: 'dark:text-gray-300' },
};

const DEFAULT_COLORS = { bg: 'bg-slate-100', text: 'text-slate-700', darkBg: 'dark:bg-slate-800', darkText: 'dark:text-slate-300' };

function getEntityColors(entityType: string) {
  return ENTITY_TYPE_COLORS[entityType] ?? DEFAULT_COLORS;
}

function formatEntityType(type: string): string {
  return type.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
}

function formatTimestamp(seconds: number): string {
  const mins = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60);
  return `${mins}:${secs.toString().padStart(2, '0')}`;
}

export function EntityPanel({ transcriptId, onScrollToSegment }: EntityPanelProps) {
  const [entities, setEntities] = useState<ExtractionEntity[]>([]);
  const [templates, setTemplates] = useState<ExtractionTemplate[]>([]);
  const [selectedTemplate, setSelectedTemplate] = useState<string>('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [groundedCount, setGroundedCount] = useState(0);
  const [totalCount, setTotalCount] = useState(0);
  const [templateUsed, setTemplateUsed] = useState<string | null>(null);
  const [isCollapsed, setIsCollapsed] = useState(false);

  // Load available templates on mount
  useEffect(() => {
    api.ai.extractionTemplates()
      .then((tpls) => {
        setTemplates(tpls);
        if (tpls.length > 0) {
          setSelectedTemplate(tpls[0].id);
        }
      })
      .catch(() => {
        // Templates may not be available; that's OK
      });
  }, []);

  const handleExtract = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await api.ai.extractEntities({
        transcript_id: transcriptId,
        template: selectedTemplate || undefined,
      });
      setEntities(result.entities);
      setGroundedCount(result.grounded_count);
      setTotalCount(result.total_count);
      setTemplateUsed(result.template_used);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to extract entities');
    } finally {
      setLoading(false);
    }
  }, [transcriptId, selectedTemplate]);

  const handleEntityClick = useCallback((entity: ExtractionEntity) => {
    if (onScrollToSegment && entity.segment_ids.length > 0) {
      onScrollToSegment(entity.segment_ids[0]);
    }
  }, [onScrollToSegment]);

  // Group entities by type
  const groupedEntities = entities.reduce<Record<string, ExtractionEntity[]>>((acc, entity) => {
    if (!acc[entity.entity_type]) {
      acc[entity.entity_type] = [];
    }
    acc[entity.entity_type].push(entity);
    return acc;
  }, {});

  return (
    <div className="rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800">
      {/* Header */}
      <div className="px-4 py-3 border-b border-gray-200 dark:border-gray-700">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <svg
              className="w-5 h-5 text-emerald-600 dark:text-emerald-400"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
              strokeWidth="2"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                d="M7 7h.01M7 3h5c.512 0 1.024.195 1.414.586l7 7a2 2 0 010 2.828l-7 7a2 2 0 01-2.828 0l-7-7A2 2 0 013 12V7a4 4 0 014-4z"
              />
            </svg>
            <h3 className="text-sm font-semibold text-gray-900 dark:text-gray-100">
              Entity Extraction
            </h3>
            <span className="px-2 py-0.5 text-xs rounded-full bg-emerald-100 dark:bg-emerald-900/30 text-emerald-700 dark:text-emerald-300">
              Beta
            </span>
          </div>
          <button
            onClick={() => setIsCollapsed(!isCollapsed)}
            className="p-1 rounded-md text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700"
          >
            <svg
              className={`w-4 h-4 transition-transform ${isCollapsed ? '' : 'rotate-180'}`}
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
              strokeWidth="2"
            >
              <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
            </svg>
          </button>
        </div>
      </div>

      {!isCollapsed && (
        <div className="p-4">
          {/* Controls */}
          <div className="flex items-end gap-3 mb-4">
            <div className="flex-1">
              <label className="block text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">
                Template
              </label>
              <select
                value={selectedTemplate}
                onChange={(e) => setSelectedTemplate(e.target.value)}
                disabled={loading}
                className="w-full px-3 py-2 text-sm rounded-md border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100 disabled:opacity-50"
              >
                {templates.map((tpl) => (
                  <option key={tpl.id} value={tpl.id}>
                    {tpl.label}
                  </option>
                ))}
                {templates.length === 0 && (
                  <option value="">Loading templates...</option>
                )}
              </select>
            </div>
            <button
              onClick={handleExtract}
              disabled={loading}
              className="inline-flex items-center gap-2 px-4 py-2 rounded-md bg-emerald-600 hover:bg-emerald-700 text-white text-sm font-medium disabled:opacity-50 transition-colors"
            >
              {loading ? (
                <>
                  <svg className="w-4 h-4 animate-spin" viewBox="0 0 24 24" fill="none">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                  </svg>
                  Extracting...
                </>
              ) : (
                <>
                  <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
                  </svg>
                  Extract
                </>
              )}
            </button>
          </div>

          {/* Loading state */}
          {loading && (
            <div className="text-center py-8">
              <svg className="w-8 h-8 mx-auto animate-spin text-emerald-500 mb-3" viewBox="0 0 24 24" fill="none">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
              </svg>
              <p className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                Extracting entities...
              </p>
              <p className="text-xs text-gray-500 dark:text-gray-400">
                This may take several seconds depending on transcript length.
              </p>
            </div>
          )}

          {/* Error state */}
          {error && (
            <div className="mb-4 p-3 rounded-lg bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 text-sm text-red-600 dark:text-red-400">
              {error}
            </div>
          )}

          {/* Results */}
          {!loading && entities.length > 0 && (
            <div className="space-y-4">
              {/* Summary line */}
              <div className="flex items-center justify-between text-sm">
                <span className="text-gray-600 dark:text-gray-400">
                  Found <span className="font-semibold text-gray-900 dark:text-gray-100">{totalCount}</span> entities
                  {' '}(<span className="font-semibold text-emerald-600 dark:text-emerald-400">{groundedCount}</span> grounded)
                </span>
                {templateUsed && (
                  <span className="text-xs text-gray-500 dark:text-gray-400">
                    Template: {templateUsed}
                  </span>
                )}
              </div>

              {/* Grouped entities */}
              {Object.entries(groupedEntities).map(([type, typeEntities]) => {
                const colors = getEntityColors(type);
                return (
                  <div key={type}>
                    <div className="flex items-center gap-2 mb-2">
                      <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium ${colors.bg} ${colors.text} ${colors.darkBg} ${colors.darkText}`}>
                        {formatEntityType(type)}
                      </span>
                      <span className="text-xs text-gray-400 dark:text-gray-500">
                        {typeEntities.length}
                      </span>
                    </div>
                    <div className="space-y-1.5">
                      {typeEntities.map((entity, idx) => (
                        <button
                          key={`${type}-${idx}`}
                          onClick={() => handleEntityClick(entity)}
                          disabled={entity.segment_ids.length === 0}
                          className="w-full text-left p-2.5 rounded-md border border-gray-100 dark:border-gray-700 hover:bg-gray-50 dark:hover:bg-gray-750 hover:border-gray-200 dark:hover:border-gray-600 transition-colors disabled:cursor-default disabled:hover:bg-transparent disabled:hover:border-gray-100 dark:disabled:hover:bg-transparent dark:disabled:hover:border-gray-700"
                        >
                          <div className="flex items-start justify-between gap-2">
                            <div className="flex-1 min-w-0">
                              <div className="flex items-center gap-1.5">
                                {/* Grounded indicator */}
                                {entity.grounded ? (
                                  <svg className="w-3.5 h-3.5 text-emerald-500 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2.5">
                                    <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                                  </svg>
                                ) : (
                                  <svg className="w-3.5 h-3.5 text-amber-500 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2.5">
                                    <path strokeLinecap="round" strokeLinejoin="round" d="M8.228 9c.549-1.165 2.03-2 3.772-2 2.21 0 4 1.343 4 3 0 1.4-1.278 2.575-3.006 2.907-.542.104-.994.54-.994 1.093m0 3h.01" />
                                  </svg>
                                )}
                                <span className="text-sm text-gray-900 dark:text-gray-100 truncate">
                                  &ldquo;{entity.text}&rdquo;
                                </span>
                              </div>
                              {/* Attributes */}
                              {Object.keys(entity.attributes).length > 0 && (
                                <div className="mt-1 flex flex-wrap gap-1.5 ml-5">
                                  {Object.entries(entity.attributes).map(([key, value]) => (
                                    <span
                                      key={key}
                                      className="inline-flex items-center text-xs text-gray-500 dark:text-gray-400"
                                    >
                                      <span className="font-medium text-gray-600 dark:text-gray-300">{key}:</span>
                                      <span className="ml-1">{value}</span>
                                    </span>
                                  ))}
                                </div>
                              )}
                            </div>
                            {/* Timestamp */}
                            {entity.timestamp !== null && (
                              <span className="text-xs font-mono text-gray-400 dark:text-gray-500 flex-shrink-0">
                                {formatTimestamp(entity.timestamp)}
                              </span>
                            )}
                          </div>
                        </button>
                      ))}
                    </div>
                  </div>
                );
              })}
            </div>
          )}

          {/* Empty state (only after first extraction) */}
          {!loading && !error && entities.length === 0 && templateUsed && (
            <div className="text-center py-6">
              <svg className="w-10 h-10 mx-auto text-gray-300 dark:text-gray-600 mb-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="1.5">
                <path strokeLinecap="round" strokeLinejoin="round" d="M7 7h.01M7 3h5c.512 0 1.024.195 1.414.586l7 7a2 2 0 010 2.828l-7 7a2 2 0 01-2.828 0l-7-7A2 2 0 013 12V7a4 4 0 014-4z" />
              </svg>
              <p className="text-sm text-gray-500 dark:text-gray-400">
                No entities found. Try a different template or a transcript with more content.
              </p>
            </div>
          )}

          {/* Initial state - no extraction yet */}
          {!loading && !error && entities.length === 0 && !templateUsed && (
            <div className="text-center py-4">
              <p className="text-sm text-gray-500 dark:text-gray-400">
                Select a template and click Extract to identify entities in this transcript.
              </p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

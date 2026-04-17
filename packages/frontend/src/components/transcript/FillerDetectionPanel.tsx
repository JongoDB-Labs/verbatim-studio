import { useState } from 'react';
import { api, type FillerDetectionResponse } from '@/lib/api';

interface FillerDetectionPanelProps {
  transcriptId: string;
  onScrollToSegment?: (segmentId: string) => void;
}

export function FillerDetectionPanel({ transcriptId, onScrollToSegment }: FillerDetectionPanelProps) {
  const [result, setResult] = useState<FillerDetectionResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isCollapsed, setIsCollapsed] = useState(false);

  const handleAnalyze = async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await api.fillerDetection.analyze(transcriptId);
      setResult(data);
    } catch (err) {
      setError('Failed to analyze fillers');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800">
      <div className="px-5 py-4 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <h2 className="text-base font-semibold text-gray-900 dark:text-gray-100">Filler Words</h2>
          {result && (
            <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-300">
              {result.summary.total_fillers} found
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          {result && (
            <button
              onClick={() => setIsCollapsed(!isCollapsed)}
              className="p-1 text-gray-400 hover:text-gray-600 dark:hover:text-gray-200"
            >
              <svg className={`w-4 h-4 transition-transform ${isCollapsed ? '-rotate-90' : ''}`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
              </svg>
            </button>
          )}
          <button
            onClick={handleAnalyze}
            disabled={loading}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md bg-amber-50 text-amber-700 hover:bg-amber-100 dark:bg-amber-900/20 dark:text-amber-300 dark:hover:bg-amber-900/30 disabled:opacity-50 transition-colors"
          >
            {loading ? (
              <>
                <svg className="w-3.5 h-3.5 animate-spin" viewBox="0 0 24 24" fill="none">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                </svg>
                Analyzing...
              </>
            ) : result ? 'Re-analyze' : 'Detect Fillers'}
          </button>
        </div>
      </div>

      {error && (
        <div className="px-5 py-3 text-sm text-red-600 dark:text-red-400">{error}</div>
      )}

      {result && !isCollapsed && (
        <div className="px-5 py-4">
          {result.summary.total_fillers === 0 ? (
            <p className="text-sm text-gray-500 dark:text-gray-400 text-center py-2">No filler words detected</p>
          ) : (
            <>
              {/* Summary stats */}
              <div className="grid grid-cols-3 gap-3 mb-4">
                <div className="text-center p-2 rounded-lg bg-gray-50 dark:bg-gray-900">
                  <p className="text-lg font-bold text-gray-900 dark:text-gray-100">{result.summary.total_fillers}</p>
                  <p className="text-xs text-gray-500 dark:text-gray-400">Total fillers</p>
                </div>
                <div className="text-center p-2 rounded-lg bg-gray-50 dark:bg-gray-900">
                  <p className="text-lg font-bold text-gray-900 dark:text-gray-100">{result.summary.segments_with_fillers}</p>
                  <p className="text-xs text-gray-500 dark:text-gray-400">Segments</p>
                </div>
                <div className="text-center p-2 rounded-lg bg-gray-50 dark:bg-gray-900">
                  <p className="text-lg font-bold text-gray-900 dark:text-gray-100">
                    {result.summary.total_segments > 0
                      ? ((result.summary.segments_with_fillers / result.summary.total_segments) * 100).toFixed(0)
                      : 0}%
                  </p>
                  <p className="text-xs text-gray-500 dark:text-gray-400">Affected</p>
                </div>
              </div>

              {/* Top filler words */}
              <div className="mb-4">
                <h4 className="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider mb-2">Most common</h4>
                <div className="flex flex-wrap gap-1.5">
                  {Object.entries(result.summary.filler_counts).slice(0, 8).map(([word, count]) => (
                    <span key={word} className="inline-flex items-center gap-1 px-2 py-1 rounded-md text-xs bg-amber-50 text-amber-700 dark:bg-amber-900/20 dark:text-amber-300">
                      &ldquo;{word}&rdquo; <span className="font-medium">{count}</span>
                    </span>
                  ))}
                </div>
              </div>

              {/* Segments with fillers */}
              <div>
                <h4 className="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider mb-2">Segments</h4>
                <div className="space-y-1.5 max-h-48 overflow-y-auto">
                  {result.segments.map((seg) => (
                    <button
                      key={seg.segment_id}
                      onClick={() => onScrollToSegment?.(seg.segment_id)}
                      className="w-full text-left px-3 py-2 rounded-md text-xs hover:bg-gray-50 dark:hover:bg-gray-700/50 transition-colors group"
                    >
                      <span className="text-gray-500 dark:text-gray-400 mr-1">#{seg.segment_index + 1}</span>
                      <span className="text-gray-700 dark:text-gray-300 line-clamp-1">
                        {seg.text}
                      </span>
                      <span className="ml-1 text-amber-600 dark:text-amber-400 font-medium">
                        ({seg.fillers.length} filler{seg.fillers.length !== 1 ? 's' : ''})
                      </span>
                    </button>
                  ))}
                </div>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}

import { useProjectStore } from '@/stores/projectStore';
import { type ChatAttachment } from './AttachmentPicker';

interface ChatHeaderProps {
  attached: ChatAttachment[];
  onDetach: (id: string) => void;
  onClose: () => void;
  onClear?: () => void;
  onSave?: () => void;
  onViewHistory?: () => void;
  hasMessages?: boolean;
  generalMode?: boolean;
  onToggleGeneralMode?: () => void;
  webSearchEnabled?: boolean;
  onToggleWebSearch?: () => void;
}

export function ChatHeader({
  attached,
  onDetach,
  onClose,
  onClear,
  onSave,
  onViewHistory,
  hasMessages = false,
  generalMode = false,
  onToggleGeneralMode,
  webSearchEnabled = false,
  onToggleWebSearch,
}: ChatHeaderProps) {
  const { selectedProjects } = useProjectStore();

  return (
    <div className="border-b border-gray-200 dark:border-gray-700 px-4 py-3">
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <div className="w-8 h-8 rounded-full bg-blue-100 dark:bg-blue-900/30 flex items-center justify-center">
            <svg className="w-4 h-4 text-blue-600 dark:text-blue-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2" aria-hidden="true">
              <path strokeLinecap="round" strokeLinejoin="round" d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z" />
            </svg>
          </div>
          <div>
            <h2 className="font-semibold text-gray-900 dark:text-gray-100">Max</h2>
            <div className="flex items-center gap-1.5 text-xs text-gray-500">
              <span>Scoped to:</span>
              {selectedProjects.length > 0 ? (
                <span className="inline-flex items-center gap-1">
                  {selectedProjects[0].color && (
                    <span className="w-2 h-2 rounded-full" style={{ backgroundColor: selectedProjects[0].color }} />
                  )}
                  {selectedProjects[0].name}
                  {selectedProjects.length > 1 && (
                    <span className="text-gray-400"> +{selectedProjects.length - 1}</span>
                  )}
                </span>
              ) : (
                <span>All Projects</span>
              )}
            </div>
          </div>
        </div>
        <div className="flex items-center gap-1">
          {/* Web search toggle */}
          {onToggleWebSearch && (
            <button
              onClick={onToggleWebSearch}
              className={`min-w-touch min-h-touch flex items-center justify-center rounded transition-colors ${
                webSearchEnabled
                  ? 'text-green-600 dark:text-green-400 bg-green-50 dark:bg-green-900/30'
                  : 'text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-700'
              }`}
              aria-label={webSearchEnabled ? 'Disable web search' : 'Enable web search'}
              title={webSearchEnabled ? 'Web search on' : 'Web search off'}
            >
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2" aria-hidden="true">
                <path strokeLinecap="round" strokeLinejoin="round" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
              </svg>
            </button>
          )}
          {/* General mode toggle */}
          {onToggleGeneralMode && (
            <button
              onClick={onToggleGeneralMode}
              className={`min-w-touch min-h-touch flex items-center justify-center rounded transition-colors ${
                generalMode
                  ? 'text-blue-600 dark:text-blue-400 bg-blue-50 dark:bg-blue-900/30'
                  : 'text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-700'
              }`}
              aria-label={generalMode ? 'Switch to Verbatim Studio mode' : 'Switch to General mode'}
              title={generalMode ? 'General mode (click for Verbatim Studio mode)' : 'Verbatim Studio mode (click for General mode)'}
            >
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2" aria-hidden="true">
                <path strokeLinecap="round" strokeLinejoin="round" d="M3.055 11H5a2 2 0 012 2v1a2 2 0 002 2 2 2 0 012 2v2.945M8 3.935V5.5A2.5 2.5 0 0010.5 8h.5a2 2 0 012 2 2 2 0 104 0 2 2 0 012-2h1.064M15 20.488V18a2 2 0 012-2h3.064M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
            </button>
          )}
          {/* History button */}
          {onViewHistory && (
            <button
              onClick={onViewHistory}
              className="min-w-touch min-h-touch flex items-center justify-center rounded text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors"
              aria-label="View saved chats"
              title="Saved Chats"
            >
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2" aria-hidden="true">
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
            </button>
          )}
          {/* Save button */}
          {onSave && (
            <button
              onClick={onSave}
              disabled={!hasMessages}
              className="min-w-touch min-h-touch flex items-center justify-center rounded text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
              aria-label="Save conversation"
              title="Save"
            >
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2" aria-hidden="true">
                <path strokeLinecap="round" strokeLinejoin="round" d="M8 7H5a2 2 0 00-2 2v9a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-3m-1 4l-3 3m0 0l-3-3m3 3V4" />
              </svg>
            </button>
          )}
          {/* Clear button */}
          {onClear && (
            <button
              onClick={onClear}
              disabled={!hasMessages && attached.length === 0}
              className="min-w-touch min-h-touch flex items-center justify-center rounded text-gray-400 hover:text-red-500 dark:hover:text-red-400 hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
              aria-label="Clear conversation"
              title="Clear"
            >
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2" aria-hidden="true">
                <path strokeLinecap="round" strokeLinejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
              </svg>
            </button>
          )}
          {/* Close button */}
          <button
            onClick={onClose}
            className="min-w-touch min-h-touch flex items-center justify-center rounded text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors"
            aria-label="Close chat"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2" aria-hidden="true">
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>
      </div>
      {attached.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {attached.map((a) => (
            <span
              key={a.id}
              className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-blue-100 dark:bg-blue-900/30 text-blue-700 dark:text-blue-300"
            >
              {/* Type icon */}
              {a.type === 'transcript' && (
                <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M19 11a7 7 0 01-7 7m0 0a7 7 0 01-7-7m7 7v4m0 0H8m4 0h4m-4-8a3 3 0 01-3-3V5a3 3 0 116 0v6a3 3 0 01-3 3z" />
                </svg>
              )}
              {a.type === 'document' && (
                <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                </svg>
              )}
              {a.type === 'file' && (
                <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M15.172 7l-6.586 6.586a2 2 0 102.828 2.828l6.414-6.586a4 4 0 00-5.656-5.656l-6.415 6.585a6 6 0 108.486 8.486L20.5 13" />
                </svg>
              )}
              <span className="truncate max-w-[100px]">{a.title}</span>
              <button
                onClick={() => onDetach(a.id)}
                className="hover:text-blue-900 dark:hover:text-blue-100"
                aria-label={`Remove ${a.title}`}
              >
                <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2" aria-hidden="true">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

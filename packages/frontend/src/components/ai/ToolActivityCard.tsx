interface ToolActivity {
  name: string;
  args?: Record<string, unknown>;
  summary?: string;
  status: 'running' | 'complete';
}

const TOOL_ICONS: Record<string, string> = {
  web_search: 'M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z',
  project_search: 'M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z',
  global_search: 'M3.055 11H5a2 2 0 012 2v1a2 2 0 002 2 2 2 0 012 2v2.945M8 3.935V5.5A2.5 2.5 0 0010.5 8h.5a2 2 0 012 2 2 2 0 104 0 2 2 0 012-2h1.064M15 20.488V18a2 2 0 012-2h3.064',
  generate_document: 'M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z',
  export_transcript: 'M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z',
  summarize_transcript: 'M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2',
  get_context: 'M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10',
  highlight_segments: 'M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z',
  add_note: 'M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z',
};

const TOOL_LABELS: Record<string, string> = {
  web_search: 'Searching the web',
  project_search: 'Searching project',
  global_search: 'Searching all projects',
  generate_document: 'Creating document',
  export_transcript: 'Exporting transcript',
  summarize_transcript: 'Summarizing',
  quality_review: 'Reviewing quality',
  get_context: 'Reading content',
  app_help: 'Looking up help',
  highlight_segments: 'Highlighting',
  add_note: 'Adding note',
  create_project: 'Creating project',
  tag_recordings: 'Tagging',
  get_recording_info: 'Looking up info',
  system_status: 'Checking system',
};

const DEFAULT_ICON = 'M13 10V3L4 14h7v7l9-11h-7z';

export function ToolActivityCard({ activity }: { activity: ToolActivity }) {
  const icon = TOOL_ICONS[activity.name] || DEFAULT_ICON;
  const label = TOOL_LABELS[activity.name] || activity.name;
  const queryArg = activity.args?.query as string | undefined;

  return (
    <div className="flex items-center gap-2 rounded-lg border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-800 px-3 py-2 text-xs">
      <svg
        className={`w-3.5 h-3.5 flex-shrink-0 ${activity.status === 'running' ? 'text-blue-500 animate-pulse' : 'text-gray-400 dark:text-gray-500'}`}
        fill="none"
        viewBox="0 0 24 24"
        stroke="currentColor"
        strokeWidth="2"
        aria-hidden="true"
      >
        <path strokeLinecap="round" strokeLinejoin="round" d={icon} />
      </svg>
      <span className="text-gray-600 dark:text-gray-300">
        {activity.status === 'complete' && activity.summary
          ? activity.summary
          : `${label}${queryArg ? `: "${queryArg}"` : ''}...`}
      </span>
    </div>
  );
}

export type { ToolActivity };

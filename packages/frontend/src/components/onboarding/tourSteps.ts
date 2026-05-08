export interface TourStep {
  id: string;
  target: string; // data-tour attribute selector
  title: string;
  description: string;
  position: 'right' | 'left' | 'top' | 'bottom';
  navigateTo?: string; // optional navigation target (page or settings tab)
  externalUrl?: string; // optional URL the user can open from the step
  externalUrlLabel?: string; // CTA label for the external link
  category?: string; // grouping label shown above the title in the tooltip
}

// The tour is structured as a journey through Verbatim's full surface.
// Inch-wide-mile-deep: each step shows where a feature lives, names what
// it does in concrete terms, and trusts the user to dig in once they know
// the door exists.
//
// Sections:
//   1. Workspace foundation (project selector, dashboard, sidebar map)
//   2. Capture (recordings, live, documents)
//   3. Process inside content (inline notes, OCR, vocabulary extraction,
//      transcript editing, speaker labeling)
//   4. Make sense (Max, voice chat, chat history, org-wide search)
//   5. Manage state (files browser, trash, project isolation)
//   6. Customize (settings tabs — general / transcription / AI / system)
//   7. Extend (Chrome extension, quick assistant FAB)
export const TOUR_STEPS: TourStep[] = [
  // ── Section 1: Workspace foundation ─────────────────────────────────
  {
    id: 'project-selector',
    category: 'Workspace',
    target: '[data-tour="project-selector"]',
    title: 'Project scope, top-left',
    description: 'Everything in Verbatim — recordings, documents, transcripts, search, AI chat — can be scoped to a project. Pick one here and the whole app filters to it. Pick "All Projects" to see your entire workspace.',
    position: 'right',
  },
  {
    id: 'dashboard',
    category: 'Workspace',
    target: '[data-tour="dashboard"]',
    title: 'Dashboard',
    description: 'Your at-a-glance home: recent recordings, active projects, today\'s activity, system health, and storage usage. Quick links to everything else.',
    position: 'right',
    navigateTo: 'dashboard',
  },

  // ── Section 2: Capture ──────────────────────────────────────────────
  {
    id: 'projects',
    category: 'Capture',
    target: '[data-tour="projects"]',
    title: 'Projects = real folders',
    description: 'Each project is a real folder on your filesystem (or a cloud-storage folder if you opt in). Drop a file in via Finder or Explorer and Verbatim picks it up automatically. Permissions, sharing, backup — all the OS-level tools work as you\'d expect.',
    position: 'right',
    navigateTo: 'projects',
  },
  {
    id: 'recordings',
    category: 'Capture',
    target: '[data-tour="recordings"]',
    title: 'Recordings',
    description: 'Upload audio or video for AI transcription. Speaker diarization, multi-language detection, custom vocabulary correction, and quality review all run automatically. Bulk-upload a batch and walk away.',
    position: 'right',
    navigateTo: 'recordings',
  },
  {
    id: 'live',
    category: 'Capture',
    target: '[data-tour="live"]',
    title: 'Live transcription',
    description: 'Press record and watch words appear in real time as you speak. Capture meetings, interviews, lectures, or just dictate. Same vocabulary correction and speaker detection as uploaded recordings, no waiting at the end.',
    position: 'right',
    navigateTo: 'live',
  },
  {
    id: 'documents',
    category: 'Capture',
    target: '[data-tour="documents"]',
    title: 'Documents',
    description: 'PDFs, DOCX, PPTX, XLSX, scanned images, RTF — all become searchable. Local OCR + vision-language models read scanned pages, handwritten notes, and image text without ever leaving your machine.',
    position: 'right',
    navigateTo: 'documents',
  },

  // ── Section 3: Process inside content ───────────────────────────────
  {
    id: 'inline-notes',
    category: 'Inside your content',
    target: '[data-tour="documents"]',
    title: 'Inline note-taking',
    description: 'Open any document and select text to attach a note. Notes anchor to the exact page + selection so jumping back from your note returns you to the source. Everything you annotate becomes searchable alongside the document itself.',
    position: 'right',
  },
  {
    id: 'ocr-vlm',
    category: 'Inside your content',
    target: '[data-tour="documents"]',
    title: 'OCR + vision models',
    description: 'For scanned or handwritten content, click "Run OCR" on any document. Choose between fast traditional OCR or larger vision-language models for handwriting, complex layouts, and figure descriptions. All processed locally — your scans never leave your device.',
    position: 'right',
  },
  {
    id: 'vocab-extraction',
    category: 'Inside your content',
    target: '[data-tour="documents"]',
    title: 'Extract vocabulary from documents',
    description: 'Click "Extract vocabulary" on any document and Verbatim\'s local AI pulls out acronyms, proper nouns, and domain terms — then dedupes against the bundled 555K-term corpus before adding genuinely new ones to your vocabulary. Future transcripts pick up your team\'s jargon automatically.',
    position: 'right',
  },
  {
    id: 'transcript-editing',
    category: 'Inside your content',
    target: '[data-tour="recordings"]',
    title: 'Edit transcripts inline',
    description: 'Click any segment to edit. Speaker labels, word-level corrections, fillers (uh, um) toggleable, multi-language translation, and re-run vocabulary correction on demand. Edits are tracked so you can always see the original.',
    position: 'right',
  },

  // ── Section 4: Make sense of it ─────────────────────────────────────
  {
    id: 'chats',
    category: 'Make sense of it',
    target: '[data-tour="chats"]',
    title: 'Chat with Max',
    description: 'Max is your local AI assistant. Ask questions about your transcripts, documents, or project context — Max can read across everything in scope, summarize meetings, draft follow-ups, or pull quotes. Runs on your machine via the bundled Granite model; no API calls leaving your device.',
    position: 'right',
    navigateTo: 'chats',
  },
  {
    id: 'chat-history',
    category: 'Make sense of it',
    target: '[data-tour="chats"]',
    title: 'Chat history is persistent + searchable',
    description: 'Every conversation with Max is saved and reopenable. Pick up where you left off, share a thread (export to Markdown), or search across all your chats from the Search page. Project-scoped chats stay tied to their project.',
    position: 'right',
  },
  {
    id: 'voice-chat',
    category: 'Make sense of it',
    target: '[data-tour="assistant"]',
    title: 'Voice chat with Max',
    description: 'Click the assistant bubble (bottom-right) and switch to voice mode. Max speaks responses out loud using your selected TTS voice, including a custom Max voice clone. Useful when your hands are full or you\'re reviewing a transcript hands-free.',
    position: 'top',
  },
  {
    id: 'search',
    category: 'Make sense of it',
    target: '[data-tour="search"]',
    title: 'Org-wide search',
    description: 'Hybrid search (lexical + semantic) across every transcript, document, note, chat, and recording in your workspace. Filter by source type, project, date range. Type a phrase or ask in plain English — works either way.',
    position: 'right',
    navigateTo: 'search',
  },

  // ── Section 5: Manage state ─────────────────────────────────────────
  {
    id: 'browser',
    category: 'Manage your data',
    target: '[data-tour="browser"]',
    title: 'Files — direct filesystem access',
    description: 'Browse the underlying folder structure. Move files between projects, drag from Finder/Explorer, or open a file in its native app. The Files view is just a window into the project folders on disk.',
    position: 'right',
    navigateTo: 'browser',
  },
  {
    id: 'archive',
    category: 'Manage your data',
    target: '[data-tour="archive"]',
    title: 'Trash — soft-delete with undo',
    description: 'Deleted recordings, documents, projects, and transcripts go here for 30 days before permanent removal. Restore anything in one click. Auto-purge interval is configurable in Settings → General.',
    position: 'right',
    navigateTo: 'archive',
  },

  // ── Section 6: Customize ────────────────────────────────────────────
  {
    id: 'settings',
    category: 'Customize',
    target: '[data-tour="settings"]',
    title: 'Settings — four tabs, deep coverage',
    description: 'General, Transcription, AI, System. The defaults work out of the box — visit when you want to tune speed, accuracy, model choice, storage location, or keyboard shortcuts.',
    position: 'right',
    navigateTo: 'settings',
  },
  {
    id: 'settings-general',
    category: 'Customize',
    target: '[data-tour="settings-general"]',
    title: 'General',
    description: 'Theme (light/dark/system), timezone, default language, default playback speed, full keyboard-shortcut customization, trash auto-purge interval, app updates, and the About page.',
    position: 'bottom',
    navigateTo: 'settings#general',
  },
  {
    id: 'settings-transcription',
    category: 'Customize',
    target: '[data-tour="settings-transcription"]',
    title: 'Transcription',
    description: 'Pick your engine (mlx-whisper for Apple Silicon, faster-whisper elsewhere, or external like Deepgram), model size, GPU acceleration, speaker diarization (requires HuggingFace token), audio enhancement (noise reduction + volume normalization), custom vocabulary corpus + per-document term extraction, and post-transcription automation (auto-summarize, vocabulary auto-correction, AI cleanup, auto-learn from edits, auto-export).',
    position: 'bottom',
    navigateTo: 'settings#transcription',
  },
  {
    id: 'settings-ai',
    category: 'Customize',
    target: '[data-tour="settings-ai"]',
    title: 'AI',
    description: 'Download or activate local AI models — vision (OCR), language (Max chat + summarization), and text-to-speech. Optional GPU acceleration on Windows. Bring-your-own-key for OpenAI, Anthropic, Groq, Ollama, or LM Studio if you prefer remote models. Web-search provider configuration for live-data questions.',
    position: 'bottom',
    navigateTo: 'settings#ai',
  },
  {
    id: 'settings-system',
    category: 'Customize',
    target: '[data-tour="settings-system"]',
    title: 'System',
    description: 'Choose where your data lives (local, S3, Google Drive, Dropbox, OneDrive). Set up cloud storage credentials, configure backup + restore, view hardware info, GPU status (Windows), and Verbatim version + build details.',
    position: 'bottom',
    navigateTo: 'settings#system',
  },

  // ── Section 7: Extend ───────────────────────────────────────────────
  {
    id: 'assistant',
    category: 'Always within reach',
    target: '[data-tour="assistant"]',
    title: 'Assistant FAB — anywhere',
    description: 'The bubble in the bottom-right opens Max from any page without losing your current view. Quick questions, app help, draft assistance — without context-switching.',
    position: 'top',
  },
  {
    id: 'chrome-extension',
    category: 'Extend',
    target: '[data-tour="assistant"]',
    title: 'Verbatim Chrome extension',
    description: 'Capture audio, screen, or selections from any browser tab and send them straight to your local Verbatim app. Adds a popup, side panel, and right-click menu so you can transcribe a YouTube video, summarize a page, or save a quote without switching windows.',
    position: 'top',
    externalUrl: 'https://chromewebstore.google.com/search/verbatim%20studio',
    externalUrlLabel: 'Open Chrome Web Store',
  },
];

export const TOUR_STORAGE_KEYS = {
  completed: 'verbatim-tour-completed',
  skipped: 'verbatim-tour-skipped',
} as const;

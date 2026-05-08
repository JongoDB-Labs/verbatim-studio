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
  // When true, this step is only included in the tour if the sample
  // workspace was installed. Skipping the install hides drilldown
  // steps that wouldn't make sense without seed data.
  requiresDemo?: boolean;
  // Optional recommended-setting toggles surfaced inside the tooltip
  // as "try this" tips. Each tip has a label + an optional reason.
  recommendations?: Array<{ label: string; reason?: string }>;
}

// The tour is structured as a journey through Verbatim's full surface.
// Inch-wide-mile-deep: each step shows where a feature lives, names what
// it does in concrete terms, and trusts the user to dig in once they know
// the door exists. Steps with `requiresDemo: true` drill into specific
// seeded entities and only show when the sample workspace is installed.
//
// Sections:
//   1. Workspace foundation (project selector, dashboard)
//   2. Capture (recordings, live, documents)
//   3. Process inside content (inline notes, OCR, vocabulary extraction,
//      transcript editing, speaker labeling)
//   4. Make sense (Max, voice chat, chat history, org-wide search)
//   5. Manage state (files browser, trash, project isolation)
//   6. Customize (settings tabs — general / transcription / AI / system)
//      with concrete recommended-toggle tips
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
    description: 'At-a-glance home: recent recordings, active projects, today\'s activity, system health, storage usage. Quick links to everything else.',
    position: 'right',
    navigateTo: 'dashboard',
  },

  // ── Section 2: Capture ──────────────────────────────────────────────
  {
    id: 'projects',
    category: 'Capture',
    target: '[data-tour="projects"]',
    title: 'Projects = real folders',
    description: 'Each project is a real folder on your filesystem (or a cloud-storage folder if you opt in). Drop a file in via Finder or Explorer and Verbatim picks it up. Permissions, sharing, backup — all the OS-level tools work as you\'d expect.',
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
    id: 'recording-drilldown',
    category: 'Capture',
    target: '[data-tour="recordings"]',
    title: 'Open a transcribed recording',
    description: 'This is the Apollo 11 recording from your sample workspace — opened to its full transcript. Click any segment to edit, watch speaker labels stay aligned, and notice how the audio scrubs in sync with the words. Try the export, translate, and re-correct vocabulary buttons in the toolbar.',
    position: 'left',
    navigateTo: 'recording:apollo11',
    requiresDemo: true,
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
    id: 'inline-notes-drilldown',
    category: 'Inside your content',
    target: '[data-tour="documents"]',
    title: 'Inline notes — see them in action',
    description: 'This is the Q4 roadmap brief from your sample workspace. We pre-attached a note to the "MCTSSA → Mctissa" line. Open the Notes panel (top-right of the document viewer) to see it, then try selecting any other text and adding your own — notes anchor to the exact selection so jumping back returns you to the source.',
    position: 'left',
    navigateTo: 'document:roadmap-brief',
    requiresDemo: true,
  },
  {
    id: 'inline-notes',
    category: 'Inside your content',
    target: '[data-tour="documents"]',
    title: 'Inline note-taking',
    description: 'Open any document and select text to attach a note. Notes anchor to the exact page + selection so jumping back from your note returns you to the source. Everything you annotate becomes searchable alongside the document itself.',
    position: 'right',
  },
  {
    id: 'ocr-vlm-drilldown',
    category: 'Inside your content',
    target: '[data-tour="documents"]',
    title: 'OCR & vision models in action',
    description: 'This is the NIST Cybersecurity Framework PDF — already with extracted text. For scanned or handwritten content like the whiteboard photo in your workspace, click "Run OCR" in the document toolbar. Choose between fast traditional OCR or larger vision-language models for handwriting + complex layouts. All processed locally.',
    position: 'left',
    navigateTo: 'document:nist-overview',
    requiresDemo: true,
    recommendations: [
      {
        label: 'Try Run OCR on the whiteboard photo',
        reason: 'Whiteboard handwriting is exactly what the vision-language model is built for — see how it handles handwritten text.',
      },
    ],
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
    recommendations: [
      {
        label: 'Try Extract Vocabulary on the roadmap brief',
        reason: 'The roadmap brief mentions MCTSSA, MeSH, C4ISR — these are exactly the kind of acronyms vocabulary extraction is designed to capture.',
      },
    ],
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
    description: 'Every conversation with Max is saved and reopenable — see the two pre-saved chats in your sample workspace ("Roadmap brief — what are the action items?" and "What did P03 say about vocabulary issues?"). Pick up where you left off, share a thread (export to Markdown), or search across chats from the Search page.',
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
    recommendations: [
      {
        label: 'Try searching "MCTSSA" or "ATO"',
        reason: 'Both terms appear in your sample workspace — see how the same query surfaces hits across the recording transcript, the docx brief, and the chat history.',
      },
    ],
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
    description: 'Theme (light/dark/system), timezone, default language, default playback speed, full keyboard-shortcut customization, trash auto-purge interval, app updates, and the About page. The "Onboarding Tour" section here is also where you can retake the tour or remove the sample workspace later.',
    position: 'bottom',
    navigateTo: 'settings#general',
  },
  {
    id: 'settings-transcription',
    category: 'Customize',
    target: '[data-tour="settings-transcription"]',
    title: 'Transcription — accuracy controls',
    description: 'Engine (mlx-whisper / faster-whisper / external like Deepgram), model size, GPU acceleration, speaker diarization (requires HuggingFace token), audio enhancement, custom vocabulary corpus, post-transcription automation.',
    position: 'bottom',
    navigateTo: 'settings#transcription',
    recommendations: [
      {
        label: 'Toggle on "Vocabulary auto-correction"',
        reason: 'Phase 2 phonetic post-correction. Catches typos and near-spellings via Double Metaphone matching against your custom vocabulary. Adds milliseconds, runs offline.',
      },
      {
        label: 'Toggle on "AI vocabulary cleanup"',
        reason: 'Slower (~5–7 min per 30-min recording) but catches the long tail Phase 2 misses — acronyms whose spoken form diverges from the spelling, multi-word terms Whisper splits incorrectly. Best for high-stakes recordings where accuracy matters most.',
      },
      {
        label: 'Download the full semantic corpus',
        reason: 'Adds 555K-term semantic retrieval (medical, legal, military, tech, etc) for vocabulary correction. ~1.1 GB; can be removed anytime.',
      },
    ],
  },
  {
    id: 'settings-ai',
    category: 'Customize',
    target: '[data-tour="settings-ai"]',
    title: 'AI — local + remote models',
    description: 'Download or activate local AI models (vision/OCR, language for Max + summarization, text-to-speech). Optional GPU acceleration on Windows. Bring-your-own-key for OpenAI / Anthropic / Groq / Ollama / LM Studio. Web-search provider for live-data questions.',
    position: 'bottom',
    navigateTo: 'settings#ai',
    recommendations: [
      {
        label: 'Download Granite-Tiny',
        reason: 'Required for chat with Max, AI vocabulary cleanup, summarization, and document extraction. ~2 GB; runs locally on CPU or GPU.',
      },
      {
        label: 'Download Qwen-VL or similar vision model',
        reason: 'Enables OCR on scanned PDFs and handwritten content. The bundled vision model is best for layouts, handwriting, and figures.',
      },
      {
        label: 'Add a HuggingFace token in Transcription',
        reason: 'Required for speaker diarization. Free token from huggingface.co/settings/tokens — paste into Settings → Transcription → HF Token.',
      },
    ],
  },
  {
    id: 'settings-system',
    category: 'Customize',
    target: '[data-tour="settings-system"]',
    title: 'System — storage + hardware',
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

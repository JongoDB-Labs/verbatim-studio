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
  // Steps tagged requiresDemo only show when sample workspace installed
  requiresDemo?: boolean;
  // Inline cards rendered in the tooltip:
  recommendations?: Array<{ label: string; reason?: string }>;
  // Highlights important caveats — usually about model downloads
  // required for AI features besides transcription.
  caveats?: Array<{ label: string; detail?: string }>;
  // Trigger app actions when step activates (e.g. "open the notes
  // panel"). The OnboardingTour dispatches a window event the
  // relevant component listens for.
  triggerEvent?: { type: string; detail?: Record<string, unknown> };
}

// Sections:
//   1. Workspace foundation (project selector, dashboard)
//   2. Capture (recordings, live, documents)
//   3. Inside your content (transcript editing, inline notes,
//      OCR/vision, vocabulary extraction)
//   4. Make sense (Max, voice chat, chat history, search)
//   5. Manage your data (files, trash)
//   6. Customize (settings drilldowns into subsections)
//   7. Extend (Chrome extension, FAB)
//   8. Where to find this later (sample-data lifecycle)
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
    title: 'Dashboard — your home base',
    description: 'A live snapshot of your workspace: recently viewed recordings and documents, project activity, processing queue (what\'s transcribing right now), storage usage, and ML status (which models are downloaded + active). Use it to jump back into work-in-progress in one click.',
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
    caveats: [
      {
        label: 'Speaker diarization needs a HuggingFace token',
        detail: 'Free token from huggingface.co/settings/tokens — paste into Settings → Transcription → HF Token. Without it, transcripts work but won\'t separate speakers.',
      },
    ],
  },
  {
    id: 'recording-drilldown',
    category: 'Capture',
    target: '[data-tour="ai-analysis"]',
    title: 'Transcript page — rich editing surface',
    description: 'This is the Apollo 11 recording from your sample workspace, opened to its full transcript. Click any segment to edit it. Speaker labels are editable. Toolbar buttons let you export, translate, re-run vocabulary correction, and toggle filler words (uh, um). The AI Analysis panel below auto-summarizes longer recordings.',
    position: 'top',
    navigateTo: 'recording:apollo11',
    requiresDemo: true,
  },
  {
    id: 'speaker-panel',
    category: 'Capture',
    target: '[data-tour="speaker-panel"]',
    title: 'Speaker statistics + relabeling',
    description: 'When diarization is on, Verbatim splits the audio into speakers and gives you stats per speaker (talk time, segment count). Rename "Speaker 1" to a real name and it propagates everywhere — the transcript view, search results, exports.',
    position: 'top',
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
    description: 'PDFs, DOCX, PPTX, XLSX, scanned images, RTF — all become searchable. Extracted text is indexed for search; selections become anchors for inline notes; uploaded files trigger OCR or vocabulary extraction on demand.',
    position: 'right',
    navigateTo: 'documents',
  },

  // ── Section 3: Inside your content ──────────────────────────────────
  {
    id: 'inline-notes-drilldown',
    category: 'Inside your content',
    target: '[data-tour="notes-toggle"]',
    title: 'Inline notes — anchored to your selections',
    description: 'This is the Q4 roadmap brief from your sample workspace. We pre-attached a note to the phrase "industry jargon" — click the Notes button (highlighted) to open the side panel and see it. Notes anchor to the exact text selection; jumping back from your note returns you here.',
    position: 'left',
    navigateTo: 'document:roadmap-brief',
    requiresDemo: true,
    triggerEvent: { type: 'tour-open-notes-panel' },
  },
  {
    id: 'ocr-vlm-drilldown',
    category: 'Inside your content',
    target: '[data-tour="run-ocr"]',
    title: 'OCR + vision models — read handwriting and scans',
    description: 'This is a real handwritten-notes photograph from your sample workspace. Click "Run OCR" (highlighted) to extract the text. Verbatim runs OCR / vision-language models locally — your scans never leave the device.',
    position: 'bottom',
    navigateTo: 'document:handwritten-notes',
    requiresDemo: true,
    caveats: [
      {
        label: 'Requires a vision model download',
        detail: 'Settings → AI → Vision Models. The default ships disabled; download a vision model first (ranges from ~500 MB for fast OCR to ~4 GB for full vision-language models that handle handwriting + complex layouts).',
      },
    ],
  },
  {
    id: 'vocab-extract-drilldown',
    category: 'Inside your content',
    target: '[data-tour="extract-vocab"]',
    title: 'Extract vocabulary from documents',
    description: 'Click "Extract vocabulary" (highlighted). The local AI scans this document for acronyms, proper nouns, and domain-specific terms; dedupes against the bundled 555K-term corpus; adds genuinely new terms to your vocabulary so future transcriptions recognize them.',
    position: 'bottom',
    requiresDemo: true,
    caveats: [
      {
        label: 'Requires a language model download',
        detail: 'Settings → AI → Language Models. Granite-Tiny (~2 GB) is the default; needed for vocabulary extraction, AI summarization, and chat with Max.',
      },
    ],
  },

  // ── Section 4: Make sense of it ─────────────────────────────────────
  {
    id: 'chats',
    category: 'Make sense of it',
    target: '[data-tour="chats"]',
    title: 'Chat with Max',
    description: 'Max is your local AI assistant. Ask questions about your transcripts, documents, or project context — Max can read across everything in scope, summarize meetings, draft follow-ups, or pull quotes. Runs on your machine; no API calls leaving your device.',
    position: 'right',
    navigateTo: 'chats',
    caveats: [
      {
        label: 'Requires a language model download',
        detail: 'Settings → AI → Language Models. Without one, the chat bubble shows a tooltip pointing you there — no chat until a model is active.',
      },
    ],
  },
  {
    id: 'chat-history-drilldown',
    category: 'Make sense of it',
    target: '[data-tour="chat-history-list"]',
    title: 'Saved chats — every conversation is reopenable',
    description: 'Each chat with Max is saved automatically. The two threads in your sample workspace ("Roadmap brief — what are the action items?" and "What did P03 say about vocabulary issues?") show real conversational continuity. Click any chat to resume from where you left off; export to Markdown to share; chats stay tied to their project so context follows the conversation.',
    position: 'top',
    requiresDemo: true,
  },
  {
    id: 'voice-chat',
    category: 'Make sense of it',
    target: '[data-tour="assistant"]',
    title: 'Voice chat with Max',
    description: 'Click the assistant bubble (bottom-right) and switch to voice mode. Max speaks responses out loud using your selected TTS voice, including a custom Max voice clone. Useful when your hands are full or you\'re reviewing a transcript hands-free.',
    position: 'top',
    caveats: [
      {
        label: 'Requires a text-to-speech model download',
        detail: 'Settings → AI → Text-to-Speech Models. Without one, voice chat is text-only.',
      },
    ],
  },
  {
    id: 'search',
    category: 'Make sense of it',
    target: '[data-tour="search"]',
    title: 'Org-wide search',
    description: 'Hybrid search (lexical + semantic) across every transcript, document, note, chat, and recording in your workspace. Filter by source type, project, date range. Type a phrase or ask in plain English — both work.',
    position: 'right',
    navigateTo: 'search',
    recommendations: [
      {
        label: 'Try searching "vocabulary" or "ATO"',
        reason: 'Both terms appear in your sample workspace — see the same query surface hits across the recording transcript, the docx brief, and the chat history.',
      },
    ],
  },

  // ── Section 5: Manage your data ─────────────────────────────────────
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
    title: 'Settings — four tabs of configuration',
    description: 'General, Transcription, AI, System. Defaults work out of the box; visit when you want to tune accuracy, choose models, set storage location, or customize keyboard shortcuts.',
    position: 'right',
    navigateTo: 'settings',
  },
  {
    id: 'settings-vocab',
    category: 'Customize',
    target: '[data-tour="settings-vocab"]',
    title: 'Custom Vocabulary — keep transcripts accurate',
    description: 'Verbatim ships with a 555K-term bundled vocabulary. The "Semantic vocabulary corpus" download adds embeddings (~1.1 GB) for hybrid retrieval — the model can match terms semantically even without exact token overlap. Drag any document into the drop zone to extract its acronyms and add to your vocabulary in one step.',
    position: 'top',
    navigateTo: 'settings#transcription',
  },
  {
    id: 'settings-post-tx',
    category: 'Customize',
    target: '[data-tour="settings-post-tx"]',
    title: 'Post-transcription automation',
    description: 'Choose what runs automatically after each transcription completes. Auto-summarize, auto-vocabulary-correction, auto-AI-cleanup, auto-learn from manual edits, auto-export.',
    position: 'top',
    navigateTo: 'settings#transcription',
    recommendations: [
      {
        label: 'Toggle on Vocabulary auto-correction',
        reason: 'Fast (millisecond-scale) phonetic post-correction. Catches typos and near-spellings via Double Metaphone matching against your custom vocabulary. Low false-positive risk.',
      },
      {
        label: 'Toggle on AI vocabulary cleanup',
        reason: 'Slower (~5-7 min per 30-min recording) but catches the long tail Phase 2 misses — acronyms whose spoken form diverges from spelling, multi-word terms Whisper splits incorrectly. Best for high-stakes recordings.',
      },
      {
        label: 'Toggle on Auto-learn from corrections',
        reason: 'When you fix a transcript word manually, Verbatim auto-adds proper-noun corrections to your vocabulary. Future recordings pick them up — accuracy compounds over time.',
      },
    ],
  },
  {
    id: 'settings-ai-language',
    category: 'Customize',
    target: '[data-tour="settings-ai-language-models"]',
    title: 'AI: Language Models',
    description: 'The bundled language model is what powers chat with Max, document summarization, vocabulary extraction, and AI vocabulary cleanup. Granite-Tiny is the default — small (~2 GB), runs on CPU or GPU.',
    position: 'top',
    navigateTo: 'settings#ai',
    recommendations: [
      {
        label: 'Download Granite-Tiny if you haven\'t',
        reason: 'Required for any feature that says "Max" or "AI cleanup" or "extract" or "summarize". The chat bubble in the corner stays disabled until you download one.',
      },
    ],
  },
  {
    id: 'settings-ai-vision',
    category: 'Customize',
    target: '[data-tour="settings-ai-vision-models"]',
    title: 'AI: Vision Models (OCR)',
    description: 'For scanned PDFs, handwritten notes, photographed whiteboards. Two tiers: fast traditional OCR (~500 MB) for clean printed text, and vision-language models (~3-4 GB) for handwriting, complex layouts, and figure descriptions.',
    position: 'top',
    navigateTo: 'settings#ai',
    recommendations: [
      {
        label: 'Download a vision model for handwriting',
        reason: 'The handwritten notes in your sample workspace are exactly what the vision-language model is built for. Without it, "Run OCR" stays disabled.',
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
    title: 'Assistant FAB — anywhere, context-aware',
    description: 'The bubble in the bottom-right opens Max from any page without losing your current view. Max picks up the context of whatever you\'re looking at — open a transcript and ask "what was the action item?", open a document and ask "summarize this", open the search page and ask Max to refine your query. The conversation is grounded in what\'s on screen, so you don\'t have to paste or describe what you mean.',
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

  // ── Section 8: Sample-data lifecycle ────────────────────────────────
  {
    id: 'sample-data-lifecycle',
    category: 'Sample data',
    target: '[data-tour="onboarding-section"]',
    title: 'Where to find this later',
    description: 'Settings → General → Onboarding Tour is your home for everything tour-related: retake the tour anytime, install the sample workspace if you skipped it, or remove it cleanly. Removal deletes only the tour-tagged content; your real recordings, documents, and chats are untouched.',
    position: 'top',
    navigateTo: 'settings#general',
    requiresDemo: true,
  },
];

export const TOUR_STORAGE_KEYS = {
  completed: 'verbatim-tour-completed',
  skipped: 'verbatim-tour-skipped',
} as const;

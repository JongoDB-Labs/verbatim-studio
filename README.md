<h1 align="center">
  <img src="docs/screenshots/logo.png" alt="Verbatim Studio Logo" width="120"><br>
  Verbatim Studio
</h1>

<p align="center">
  <strong>Your data. Your device. Your rules.</strong>
</p>

<p align="center">
  <a href="#features">Features</a> •
  <a href="#installation">Installation</a> •
  <a href="#architecture">Architecture</a> •
  <a href="#roadmap">Roadmap</a> •
  <a href="#contributing">Contributing</a>
</p>

<p align="center">
  <img src="https://img.shields.io/github/v/tag/JongoDB/verbatim-studio?label=version&color=blue" alt="Version">
  <img src="https://img.shields.io/badge/license-AGPL--3.0-green.svg" alt="License">
  <img src="https://img.shields.io/badge/platform-macOS%20%7C%20Windows-lightgrey.svg" alt="Platform">
</p>

<p align="center">
  <img src="docs/screenshots/dashboard-dark.png" alt="Verbatim Studio Dashboard" width="100%">
</p>

---

## Why Verbatim Studio?

Organizations handling confidential information—law firms, medical practices, government agencies, research institutions—face a critical challenge: **cloud transcription services require sending sensitive data to third-party servers.**

Verbatim Studio eliminates this risk entirely. All transcription and AI processing happens locally on your machine. Your files never leave your control.

### Built for Compliance

- **HIPAA-ready** — Patient interviews and medical dictation stay on-premises
- **Legal privilege** — Attorney-client communications remain confidential
- **Government security** — Classified briefings never touch external networks
- **Research ethics** — IRB-protected interviews maintain participant privacy

### Built for Everyone

Verbatim Studio works just as well for everyday use:

- **Project managers** documenting meetings and standups
- **Content creators** transcribing interviews and podcasts
- **Students and academics** processing lectures and research
- **Anyone** who wants accurate transcription without privacy trade-offs or subscription fees

---

## System Requirements

### Hardware

|  | Minimum | Recommended |
|---|---|---|
| **RAM** | 8 GB | 16 GB+ |
| **Disk** | ~2 GB (base install) | ~8 GB (all AI models) |

### Platform Support

| Platform | Requirement | Notes |
|----------|-------------|-------|
| **macOS** | Apple Silicon (M1/M2/M3/M4) | Optimized for Metal / MLX — Intel Macs not supported |
| **Windows** | x86-64 with 8 GB+ RAM | NVIDIA GPU optional — enables CUDA-accelerated transcription |

**Minimum** covers transcription and basic editing. **Recommended** includes the full AI suite (Max assistant, semantic search, OCR).

### Memory Usage by Feature

Each AI feature loads its own model. You only pay for what you use — deactivate models in **Settings > AI** to reclaim memory.

| Feature | Memory | Loaded when... |
|---------|--------|----------------|
| App (idle) | ~300 MB | Always |
| Transcription (Whisper base) | +200-300 MB | Transcribing audio/video |
| Speaker ID (pyannote) | +1 GB | Diarization enabled |
| Semantic search (nomic-embed) | +600 MB | Search index active |
| Max assistant (Granite 4.0) | +5 GB | AI chat / summaries |
| Voice chat (Kokoro TTS) | +1 GB | Voice sessions active |
| OCR (Qwen2-VL 2B) | +5 GB | Processing images / scanned PDFs |

> **Tip:** On a 16 GB machine you can comfortably run transcription + diarization + search + Max + voice simultaneously. OCR is loaded on-demand and released when idle.

### Windows GPU / VRAM

Out of the box, only transcription uses your NVIDIA GPU (via CTranslate2). To accelerate **all** AI features on the GPU, open **Settings > AI** and click **Enable Full GPU Acceleration** (~2.8 GB download).

| Feature | VRAM | Notes |
|---------|------|-------|
| Transcription (Whisper base) | ~200 MB | GPU by default (CTranslate2) |
| Transcription (Whisper large-v3) | ~3 GB | GPU by default (CTranslate2) |
| Speaker ID (pyannote) | ~1 GB | Requires GPU acceleration pack |
| Semantic search (nomic-embed) | ~600 MB | Requires GPU acceleration pack |
| AI Assistant (Granite 8B) | ~5 GB | Requires GPU acceleration pack |
| OCR (Qwen2-VL 2B) | ~5 GB | Requires GPU acceleration pack |

> **Minimum VRAM:** 4 GB handles transcription + diarization + search. **Recommended:** 8 GB+ for the full AI suite including Granite and OCR.
>
> No NVIDIA GPU? Everything works on CPU — just slower for transcription.

---

## Features

### Transcription That Actually Works

- **OpenAI Whisper accuracy** — State-of-the-art speech recognition running entirely on your device
- **Multi-language support** — Transcribe in 12+ languages with automatic detection
- **Automatic speaker identification** — Know who said what without manual tagging
- **Live transcription** — Real-time speech-to-text from your microphone
- **Video support** — Drop in MP4, MOV, WebM, or MKV files and get transcripts automatically

<p align="center">
  <img src="docs/screenshots/transcript-editor.png" alt="Transcript Editor with Speaker Diarization and AI Analysis" width="100%">
</p>

### Live Transcription

Record directly from your microphone with real-time speech-to-text. Choose your language, enable speaker diarization, and watch the transcript appear as you speak. Save sessions as recordings for later editing and export.

<p align="center">
  <img src="docs/screenshots/live-transcription.png" alt="Live Transcription" width="100%">
</p>

### Max: Your AI Research Assistant

Max isn't just a chatbot—it's a research tool that actually understands your content:

- **Query across your entire library** — Ask questions that span multiple files and documents
- **Voice chat** — Talk to Max directly with full-duplex voice conversations (LiveKit + Kokoro TTS)
- **Persistent conversations** — Pick up where you left off with saved chat history
- **Document-aware** — Upload PDFs, images, and notes for Max to reference
- **Web search** — Pull in current information when you need it
- **OCR built-in** — Extract text from scanned documents and images automatically
- **Smart delegation** — Granite Tiny handles quick replies; main LLM tackles complex questions
- **Tool calling** — Max can search your library, export transcripts, and more

All powered by IBM Granite, running 100% locally. No API keys. No usage limits. No data leaving your machine.

<p align="center">
  <img src="docs/screenshots/ai-assistant.png" alt="Max AI Assistant" width="100%">
</p>

### Documents & OCR

Upload PDFs and images for automatic text extraction. The built-in OCR model (Qwen2-VL) reads printed text and handwriting alike — all processed locally on your machine.

<p align="center">
  <img src="docs/screenshots/document-ocr.png" alt="Document OCR - Handwriting Recognition" width="100%">
</p>

### Find Anything, Instantly

- **Semantic search** — Find content by meaning, not just exact keywords
- **Search everything** — Files, transcripts, documents, notes, and chat history in one place
- **Smart results** — See context snippets with keyword highlighting and semantic match indicators

<p align="center">
  <img src="docs/screenshots/search-semantic.png" alt="Semantic and Keyword Search" width="100%">
</p>

### Professional Editing Tools

- **Clickable timestamps** — Jump to any moment instantly
- **Highlights and bookmarks** — Mark important segments for quick reference
- **In-transcript search** — Find exactly what you're looking for with highlighted navigation
- **Keyboard-first workflow** — Control playback without leaving your keyboard
- **Inline annotations** — Add notes directly to your documents and transcripts
- **Entity extraction** — Automatically identify people, organizations, locations, and key terms

### Organize Your Way

- **Projects with custom metadata** — Templates for legal cases, medical records, interviews
- **Trash / recycle bin** — Soft-delete with 30-day auto-purge, bulk restore
- **Bulk operations** — Select multiple files and act on them at once
- **Flexible storage** — Keep files local, on network drives, or synced with Google Drive, OneDrive, and Dropbox
- **Full exports** — TXT, SRT, VTT, DOCX, PDF, JSON, or complete backup archives

### Local AI Models

All AI runs on your machine. Download and manage models directly from the settings page — no API keys, no cloud dependencies. Deactivate models when you don't need them to reclaim memory.

<p align="center">
  <img src="docs/screenshots/settings-ai.png" alt="AI Model Management" width="100%">
</p>

---

## Installation

### Desktop App

Download for your platform:

| Platform | Download | Notes |
|----------|----------|-------|
| **macOS (Apple Silicon)** | [Download .dmg](https://github.com/JongoDB/verbatim-studio-releases/releases/latest) | M1/M2/M3/M4 optimized |
| **Windows (x64)** | [Download .exe](https://github.com/JongoDB/verbatim-studio-releases/releases/latest) | NVIDIA GPU optional for faster transcription |

The app is self-contained—no Python, Node.js, or other dependencies required. Just download, install, and run.

### First Launch

On first launch, Verbatim Studio will guide you through downloading the AI models you need. Choose what fits your workflow—transcription only, or the full suite with Max and semantic search.

<details>
<summary><strong>macOS: "App is damaged" or "unidentified developer" warning</strong></summary>

The app is not yet code-signed. To open it:

1. **Right-click** (or Control-click) the app and select **Open**
2. Click **Open** in the dialog that appears

Or, if that doesn't work:

1. Open **System Settings** → **Privacy & Security**
2. Scroll down to find the blocked app message
3. Click **Open Anyway**

**Alternative: Remove quarantine via Terminal**

After downloading, run this command to remove the quarantine attribute:

```bash
xattr -c ~/Downloads/Verbatim.Studio-<version>-arm64.dmg
```

Replace `<version>` with the version number you downloaded (e.g., `0.26.21`).

</details>

<details>
<summary><strong>Development Setup (Build from Source)</strong></summary>

#### Prerequisites

- Python 3.12+
- Node.js 20+
- pnpm 9+
- ffmpeg 7+

#### Clone and Install

```bash
# Clone the repository
git clone https://github.com/JongoDB/verbatim-studio.git
cd verbatim-studio

# Install Node dependencies
pnpm install

# Set up Python environment
cd packages/backend
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cd ../..
```

#### Run Development Servers

```bash
# Run both frontend and backend
pnpm dev

# Or run separately
# Terminal 1 - Backend
cd packages/backend && source .venv/bin/activate
python -m uvicorn api.main:app --reload --port 52780

# Terminal 2 - Frontend
cd packages/frontend && pnpm dev
```

Open [http://localhost:5173](http://localhost:5173) in your browser.

</details>

---

## Architecture

<pre align="center">
┌─────────────────────────────────────────────────────────────┐
│                     <b>Frontend</b> (React)                        │
│   Dashboard • Recordings • Projects • Documents • Search    │
├─────────────────────────────────────────────────────────────┤
│                     <b>Backend</b> (FastAPI)                       │
│                                                             │
│    ┌─────────────┐  ┌─────────────┐  ┌─────────────┐       │
│    │  Database   │  │Transcription│  │     AI      │       │
│    │   Adapter   │  │   Engine    │  │   Service   │       │
│    └──────┬──────┘  └──────┬──────┘  └──────┬──────┘       │
│           │                │                │               │
│       SQLite          WhisperX         llama.cpp           │
│                      MLX Whisper        Granite            │
└─────────────────────────────────────────────────────────────┘
</pre>

### Tech Stack

| Layer | Technology |
|-------|------------|
| Frontend | React 18, TypeScript, Vite, Tailwind CSS |
| Backend | FastAPI, SQLAlchemy, Pydantic |
| Transcription | WhisperX, MLX Whisper, pyannote.audio |
| AI/LLM | llama-cpp-python, sentence-transformers |
| Voice | LiveKit, Kokoro TTS (ONNX / MLX), Silero VAD |
| OCR | Qwen2-VL (vision-language model) |
| Audio | WaveSurfer.js, ffmpeg |
| Storage | SQLite, Google Drive, OneDrive, Dropbox |

---

## Roadmap

### Current Release (v0.62.x)

**Core Platform**
- [x] Native macOS desktop app (Apple Silicon optimized)
- [x] Native Windows desktop app (NVIDIA CUDA optimized)
- [x] Cross-platform auto-updates with release notes
- [x] Local AI transcription with speaker identification
- [x] Live transcription from microphone
- [x] Video file support with automatic audio extraction

**AI Assistant (Max)**
- [x] Multi-document conversations with chat history
- [x] Full-duplex voice chat (LiveKit + Kokoro TTS)
- [x] Smart delegation — Granite Tiny for quick replies, main LLM for complex questions
- [x] Web search integration (Tavily, Brave)
- [x] Semantic search across all content
- [x] Tool calling (search, export, web lookup)
- [x] OCR for scanned documents and images
- [x] Streaming token output in text and voice modes
- [x] Conversation memory compression for long sessions

**Editing & Organization**
- [x] Clickable timestamps and playback keyboard shortcuts
- [x] Segment highlights and bookmarks
- [x] In-transcript search with navigation
- [x] Inline document annotations
- [x] Entity extraction (people, organizations, locations, key terms)
- [x] AI transcript quality review
- [x] Projects with custom metadata and filesystem folders
- [x] Trash / recycle bin with 30-day auto-purge
- [x] Bulk operations with multi-select

**Storage & Export**
- [x] Local, network, and cloud storage options
- [x] Google Drive, OneDrive, Dropbox integration
- [x] Export to TXT, SRT, VTT, DOCX, PDF, JSON
- [x] AI summary and speaker statistics in exports
- [x] Map-reduce summarization for long transcripts

### Planned

- [ ] Windows code signing (SmartScreen trust)
- [ ] External LLM connections (Ollama, OpenAI, self-hosted)
- [ ] Multi-user with role-based access control
- [ ] Meeting bots for Teams, Google Meet, and Zoom
- [ ] PostgreSQL database support
- [ ] Administration dashboard
- [ ] Mobile companion app

---

## Configuration

Most settings are available through the **Settings** page in the app.

### AI Models

On first use, Verbatim Studio downloads the AI models you select:

| Model | Size | Purpose |
|-------|------|---------|
| Whisper (base) | ~150 MB | Transcription (pre-bundled; configurable up to large-v3) |
| pyannote | ~200 MB | Speaker diarization |
| nomic-embed-text | ~550 MB | Semantic search (pre-bundled) |
| IBM Granite 4.0 | ~5 GB | AI assistant, summarization, and tool calling |
| Granite Tiny | ~350 MB | Fast voice chat responses |
| Kokoro 82M | ~350 MB | Text-to-speech for voice chat |
| Qwen2 VL | ~4.4 GB | Image OCR and document parsing |

Models are cached locally and only download once.

<details>
<summary><strong>Environment Variables (Developers)</strong></summary>

Create a `.env` file in `packages/backend/`:

```bash
# Core settings
VERBATIM_MODE=basic
VERBATIM_DATA_DIR=~/.verbatim-studio

# Transcription
VERBATIM_WHISPERX_MODEL=base
VERBATIM_WHISPERX_DEVICE=auto

# OAuth (optional - for cloud storage)
VERBATIM_GOOGLE_CLIENT_ID=your-client-id
VERBATIM_GOOGLE_CLIENT_SECRET=your-secret
```

</details>

---

## Contributing

Contributions are welcome. See the [Development Setup](#development-setup-build-from-source) section to get started.

### Building the Desktop App

```bash
# Build for your current platform
pnpm build:electron

# Build for specific platform
pnpm build:electron:mac
pnpm build:electron:win
pnpm build:electron:linux
```

### Running Tests

```bash
# Backend tests
cd packages/backend && pytest

# Frontend tests
cd packages/frontend && pnpm test

# Type checking
cd packages/frontend && pnpm typecheck
```

---

## License

AGPL-3.0 License. See [LICENSE](LICENSE) for details.

---

<p align="center">
  <strong>Verbatim Studio</strong> — Transcription you can trust.
</p>

<p align="center">
  <a href="https://github.com/JongoDB/verbatim-studio/issues">Report Issue</a> •
  <a href="https://github.com/JongoDB/verbatim-studio/discussions">Discussions</a>
</p>

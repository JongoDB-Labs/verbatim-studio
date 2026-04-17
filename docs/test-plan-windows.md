# Verbatim Studio — Windows Test Report

> **Version:** 0.62.1
> **Date:** 2026-04-16
> **Platform:** Windows x64 (DESKTOP-9SD92LH)
> **Tester:** Claude (automated via SSH)
> **Backend Python:** Bundled (Program Files path — AppLocker-safe)

---

## How to Read This Report

- Each test has a **status**: `PASS`, `FAIL`, `SKIP`, `BLOCKED`
- `SKIP` = not testable without hardware/data (e.g., GPU, microphone)
- `BLOCKED` = dependency prevents testing
- **Notes** contain error messages, response bodies, or observations
- Sections ordered by criticality: startup > core CRUD > AI > UI > edge cases

---

## 0. PLATFORM-SPECIFIC: Windows Environment

### 0.1 Application Control / Security
| # | Test | Status | Notes |
|---|------|--------|-------|
| W-0.1.1 | Backend starts with bundled Python (Program Files path) | | |
| W-0.1.2 | greenlet DLL loads successfully (no AppLocker block) | | |
| W-0.1.3 | CUDA DLLs load from bundled path | | |
| W-0.1.4 | FFmpeg loads from bundled path | | |
| W-0.1.5 | Backend starts with migrated Python (AppData path) | | Expected: may fail under AppLocker |

### 0.2 Windows-Specific Path Handling
| # | Test | Status | Notes |
|---|------|--------|-------|
| W-0.2.1 | Database path with spaces works (AppData\Roaming\@verbatim\electron) | | |
| W-0.2.2 | File upload with backslash paths | | |
| W-0.2.3 | UNC/network paths handled correctly | | |
| W-0.2.4 | Long path support (>260 chars) | | |

### 0.3 Process Management
| # | Test | Status | Notes |
|---|------|--------|-------|
| W-0.3.1 | taskkill terminates backend process tree | | |
| W-0.3.2 | Port 52780 released after backend stop | | |
| W-0.3.3 | No orphan Python processes after crash | | |

### 0.4 Windows App Integration
| # | Test | Status | Notes |
|---|------|--------|-------|
| W-0.4.1 | verbatim:// protocol handler registered | | |
| W-0.4.2 | Deep link opens correct page | | |
| W-0.4.3 | Single instance lock prevents duplicate windows | | |
| W-0.4.4 | Auto-updater checks for updates | | |
| W-0.4.5 | Windows installer (.exe) runs cleanly | | |
| W-0.4.6 | PowerShell update script execution | | |

---

## 1. HEALTH & STARTUP

### 1.1 Health Endpoints
| # | Test | Method | Path | Expected | Status | Notes |
|---|------|--------|------|----------|--------|-------|
| 1.1.1 | Basic health check | GET | /health | `{"status":"healthy"}` | | |
| 1.1.2 | Readiness check | GET | /health/ready | 200 with service statuses | | |
| 1.1.3 | API info | GET | /api/info | name, version, mode | | |
| 1.1.4 | System info | GET | /api/system/info | OS, platform, Python version | | |
| 1.1.5 | Hardware info | GET | /api/system/hardware | CPU, RAM, GPU | | |
| 1.1.6 | Memory usage | GET | /api/system/memory | Memory stats | | |
| 1.1.7 | GPU status | GET | /api/system/gpu-status | GPU availability | | |
| 1.1.8 | ML status | GET | /api/system/ml-status | ML library availability | | |
| 1.1.9 | Dependency check | GET | /api/system/dependency-check | All deps status | | |
| 1.1.10 | Dashboard stats | GET | /api/stats | Counts and aggregates | | |

---

## 2. PROJECTS (Workspace Organization)

### 2.1 CRUD
| # | Test | Method | Path | Expected | Status | Notes |
|---|------|--------|------|----------|--------|-------|
| 2.1.1 | Create project | POST | /api/projects | 201, returns project with ID | | |
| 2.1.2 | Create project with metadata | POST | /api/projects | Custom fields stored | | |
| 2.1.3 | Create project with icon/color | POST | /api/projects | Icon and color persisted | | |
| 2.1.4 | List projects | GET | /api/projects | Array of projects | | |
| 2.1.5 | Get single project | GET | /api/projects/{id} | Full project with metadata | | |
| 2.1.6 | Update project name | PATCH | /api/projects/{id} | Updated name returned | | |
| 2.1.7 | Update project description | PATCH | /api/projects/{id} | Updated description | | |
| 2.1.8 | Delete project (soft) | DELETE | /api/projects/{id} | 200, moved to trash | | |
| 2.1.9 | Permanent delete trashed project | DELETE | /api/projects/{id}/permanent | 200, fully removed | | |
| 2.1.10 | Get deleted project returns 404 | GET | /api/projects/{id} | 404 after permanent delete | | |

### 2.2 Archive & Trash
| # | Test | Method | Path | Expected | Status | Notes |
|---|------|--------|------|----------|--------|-------|
| 2.2.1 | Archive project | PATCH | /api/projects/{id}/archive | Moved to trash | | |
| 2.2.2 | Unarchive (restore) project | PATCH | /api/projects/{id}/unarchive | Restored from trash | | |
| 2.2.3 | Archived project excluded from list | GET | /api/projects?archived=false | Not in results | | |
| 2.2.4 | Archived project visible in trash | GET | /api/projects?archived=true | In results | | |

### 2.3 Project Recordings & Items
| # | Test | Method | Path | Expected | Status | Notes |
|---|------|--------|------|----------|--------|-------|
| 2.3.1 | List project recordings | GET | /api/projects/{id}/recordings | Array | | |
| 2.3.2 | Add recording to project | POST | /api/projects/{id}/recordings/{rid} | 200 | | |
| 2.3.3 | Remove recording from project | DELETE | /api/projects/{id}/recordings/{rid} | 200 | | |
| 2.3.4 | Get project sections/counts | GET | /api/projects/{id}/sections | Counts object | | |
| 2.3.5 | Bulk move items to project | POST | /api/projects/{id}/move-items | Items moved | | |

### 2.4 Active Project
| # | Test | Method | Path | Expected | Status | Notes |
|---|------|--------|------|----------|--------|-------|
| 2.4.1 | Set active project | PUT | /api/projects/active/current | 200 | | |
| 2.4.2 | Get active project | GET | /api/projects/active/current | Returns set project | | |

### 2.5 Filtering & Search
| # | Test | Method | Path | Expected | Status | Notes |
|---|------|--------|------|----------|--------|-------|
| 2.5.1 | Filter by search term | GET | /api/projects?search=test | Matching projects | | |
| 2.5.2 | Filter by project_type | GET | /api/projects?project_type=X | Filtered list | | |
| 2.5.3 | Filter by tag | GET | /api/projects?tag=Y | Tagged projects | | |

---

## 3. PROJECT TYPES (Templates)

| # | Test | Method | Path | Expected | Status | Notes |
|---|------|--------|------|----------|--------|-------|
| 3.1 | List project types | GET | /api/project-types | Array with system types | | |
| 3.2 | Get single project type | GET | /api/project-types/{id} | Type with schema | | |
| 3.3 | Create custom type | POST | /api/project-types | 201, new type | | |
| 3.4 | Update project type | PATCH | /api/project-types/{id} | Updated | | |
| 3.5 | Delete project type | DELETE | /api/project-types/{id} | 200 | | |

---

## 4. RECORDINGS

### 4.1 Upload & CRUD
| # | Test | Method | Path | Expected | Status | Notes |
|---|------|--------|------|----------|--------|-------|
| 4.1.1 | Upload MP3 | POST | /api/recordings/upload | 201, recording created | | |
| 4.1.2 | Upload WAV | POST | /api/recordings/upload | 201 | | |
| 4.1.3 | Upload FLAC | POST | /api/recordings/upload | 201 | | |
| 4.1.4 | Upload M4A | POST | /api/recordings/upload | 201 | | |
| 4.1.5 | Upload MP4 (video) | POST | /api/recordings/upload | 201, audio extracted | | |
| 4.1.6 | Upload WebM | POST | /api/recordings/upload | 201 | | |
| 4.1.7 | Upload invalid file (e.g. .exe) | POST | /api/recordings/upload | 400/422, rejected | | |
| 4.1.8 | List recordings | GET | /api/recordings | Array with metadata | | |
| 4.1.9 | Get single recording | GET | /api/recordings/{id} | Full metadata | | |
| 4.1.10 | Get recording properties | GET | /api/recordings/{id}/properties | Detailed props | | |
| 4.1.11 | Update recording title | PATCH | /api/recordings/{id} | Updated title | | |
| 4.1.12 | Update recording tags | PATCH | /api/recordings/{id} | Tags updated | | |
| 4.1.13 | List archived recordings | GET | /api/recordings/archived | Trashed items | | |

### 4.2 Bulk Operations
| # | Test | Method | Path | Expected | Status | Notes |
|---|------|--------|------|----------|--------|-------|
| 4.2.1 | Bulk delete recordings | POST | /api/recordings/bulk-delete | Multiple deleted | | |
| 4.2.2 | Bulk assign to project | POST | /api/recordings/bulk-assign | All assigned | | |

### 4.3 Recording Templates
| # | Test | Method | Path | Expected | Status | Notes |
|---|------|--------|------|----------|--------|-------|
| 4.3.1 | List recording templates | GET | /api/recording-templates | Array | | |
| 4.3.2 | Create custom template | POST | /api/recording-templates | 201 | | |
| 4.3.3 | Update template | PATCH | /api/recording-templates/{id} | Updated | | |
| 4.3.4 | Delete template | DELETE | /api/recording-templates/{id} | 200 | | |

### 4.4 Transcription Lifecycle
| # | Test | Method | Path | Expected | Status | Notes |
|---|------|--------|------|----------|--------|-------|
| 4.4.1 | Start transcription | POST | /api/recordings/{id}/transcribe | Job enqueued, job_id returned | | |
| 4.4.2 | Cancel in-progress transcription | POST | /api/recordings/{id}/cancel | Job cancelled, status = cancelled | | |
| 4.4.3 | Retry failed transcription | POST | /api/recordings/{id}/retry | New job enqueued | | |
| 4.4.4 | Download audio file | GET | /api/recordings/{id}/audio | Audio stream returned | | |
| 4.4.5 | Archive recording | PATCH | /api/recordings/{id}/archive | Moved to trash | | |
| 4.4.6 | Unarchive recording | PATCH | /api/recordings/{id}/unarchive | Restored from trash | | |

---

## 5. TRANSCRIPTS & SEGMENTS

### 5.1 Transcript Access
| # | Test | Method | Path | Expected | Status | Notes |
|---|------|--------|------|----------|--------|-------|
| 5.1.1 | Get transcript by ID | GET | /api/transcripts/{id} | Full transcript | | |
| 5.1.2 | Get transcript by recording ID | GET | /api/transcripts/by-recording/{id} | Linked transcript | | |
| 5.1.3 | Get segments (paginated) | GET | /api/transcripts/{id}/segments?skip=0&limit=50 | Paginated segments | | |
| 5.1.4 | Get segments page 2 | GET | /api/transcripts/{id}/segments?skip=50&limit=50 | Next page | | |

### 5.2 Segment Editing
| # | Test | Method | Path | Expected | Status | Notes |
|---|------|--------|------|----------|--------|-------|
| 5.2.1 | Edit segment text | PATCH | /api/transcripts/{tid}/segments/{sid} | Text updated, original preserved | | |
| 5.2.2 | Edit segment speaker | PATCH | /api/transcripts/{tid}/segments/{sid} | Speaker changed | | |
| 5.2.3 | Delete single segment | DELETE | /api/transcripts/{tid}/segments/{sid} | Segment removed | | |
| 5.2.4 | Bulk delete segments | POST | /api/transcripts/{tid}/segments/bulk-delete | Multiple removed | | |

### 5.3 Export
| # | Test | Method | Path | Expected | Status | Notes |
|---|------|--------|------|----------|--------|-------|
| 5.3.1 | Export as TXT | GET | /api/transcripts/{id}/export?format=txt | Plain text file | | |
| 5.3.2 | Export as SRT | GET | /api/transcripts/{id}/export?format=srt | SubRip subtitle file with timecodes | | |
| 5.3.3 | Export as VTT | GET | /api/transcripts/{id}/export?format=vtt | WebVTT file with cues | | |
| 5.3.4 | Export as DOCX | GET | /api/transcripts/{id}/export?format=docx | Word document | | |
| 5.3.5 | Export as PDF | GET | /api/transcripts/{id}/export?format=pdf | PDF document | | |

---

## 6. SPEAKERS

| # | Test | Method | Path | Expected | Status | Notes |
|---|------|--------|------|----------|--------|-------|
| 6.1 | List unique speakers | GET | /api/speakers/unique | All speakers | | |
| 6.2 | Get speakers by transcript | GET | /api/speakers/by-transcript/{id} | Speakers for transcript | | |
| 6.3 | Update speaker name | PATCH | /api/speakers/{id} | Name changed | | |
| 6.4 | Update speaker color | PATCH | /api/speakers/{id} | Color changed | | |
| 6.5 | Merge speakers | POST | /api/speakers/{id}/merge | Identities merged | | |
| 6.6 | Reassign segment speaker | POST | /api/speakers/reassign-segment | Segment reassigned | | |

---

## 7. TAGS

| # | Test | Method | Path | Expected | Status | Notes |
|---|------|--------|------|----------|--------|-------|
| 7.1 | List all tags | GET | /api/tags | Tags with counts | | |
| 7.2 | Create tag | POST | /api/tags | 201, new tag | | |
| 7.3 | Delete tag | DELETE | /api/tags/{id} | 200 | | |
| 7.4 | Assign tag to recording | POST | /api/tags/{tid}/recordings/{rid} | 200 | | |
| 7.5 | Remove tag from recording | DELETE | /api/tags/{tid}/recordings/{rid} | 200 | | |
| 7.6 | Get recordings with tag | GET | /api/tags/{tid}/recordings | Filtered list | | |
| 7.7 | Assign tag to document | POST | /api/tags/{tid}/documents/{did} | 200 | | |
| 7.8 | Remove tag from document | DELETE | /api/tags/{tid}/documents/{did} | 200 | | |

---

## 8. DOCUMENTS

### 8.1 Upload & CRUD
| # | Test | Method | Path | Expected | Status | Notes |
|---|------|--------|------|----------|--------|-------|
| 8.1.1 | Upload PDF | POST | /api/documents | 201, text extracted | | |
| 8.1.2 | Upload DOCX | POST | /api/documents | 201 | | |
| 8.1.3 | Upload XLSX | POST | /api/documents | 201 | | |
| 8.1.4 | Upload PPTX | POST | /api/documents | 201 | | |
| 8.1.5 | Upload PNG image | POST | /api/documents | 201 | | |
| 8.1.6 | Upload JPEG image | POST | /api/documents | 201 | | |
| 8.1.7 | List documents | GET | /api/documents | Paginated list | | |
| 8.1.8 | Get single document | GET | /api/documents/{id} | Full metadata | | |
| 8.1.9 | Update document title | PATCH | /api/documents/{id} | Updated | | |
| 8.1.10 | Delete document | DELETE | /api/documents/{id} | Moved to trash | | |
| 8.1.11 | List archived documents | GET | /api/documents/archived | Trashed docs | | |

### 8.2 Bulk Operations
| # | Test | Method | Path | Expected | Status | Notes |
|---|------|--------|------|----------|--------|-------|
| 8.2.1 | Bulk delete documents | POST | /api/documents/bulk-delete | Multiple deleted | | |
| 8.2.2 | Bulk assign to project | POST | /api/documents/bulk-assign | All assigned | | |

### 8.3 Document Processing
| # | Test | Method | Path | Expected | Status | Notes |
|---|------|--------|------|----------|--------|-------|
| 8.3.1 | Download document file | GET | /api/documents/{id}/file | File stream returned | | |
| 8.3.2 | Process document (extract text) | POST | /api/documents/{id}/process | Processing job started | | |
| 8.3.3 | Run OCR on document | POST | /api/documents/{id}/ocr | Text extracted via OCR model | | |
| 8.3.4 | Permanent delete document | DELETE | /api/documents/{id}/permanent | Fully removed from database | | |

---

## 9. NOTES

| # | Test | Method | Path | Expected | Status | Notes |
|---|------|--------|------|----------|--------|-------|
| 9.1 | Create note on recording | POST | /api/notes | 201 (body requires `anchor_type` + `anchor_data`) | | |
| 9.2 | Create note on document | POST | /api/notes | 201 (body requires `anchor_type` + `anchor_data`) | | |
| 9.3 | List notes | GET | /api/notes?recording_id={id} | Paginated list (requires recording_id or document_id) | | |
| 9.4 | Get single note | GET | /api/notes/{id} | Full note | | |
| 9.5 | Update note | PATCH | /api/notes/{id} | Content updated | | |
| 9.6 | Delete note | DELETE | /api/notes/{id} | 200 | | |

---

## 10. SEGMENT COMMENTS & HIGHLIGHTS

| # | Test | Method | Path | Expected | Status | Notes |
|---|------|--------|------|----------|--------|-------|
| 10.1 | Add comment to segment | POST | /api/segments/{sid}/comments | 201 (body: `{text: "..."}`) | | Field is `text` not `content` |
| 10.2 | Get segment comments | GET | /api/segments/{sid}/comments | Array | | |
| 10.3 | Update comment | PATCH | /api/comments/{id} | Updated | | |
| 10.4 | Delete comment | DELETE | /api/comments/{id} | 200 | | |
| 10.5 | Set segment highlight | PUT | /api/segments/{sid}/highlight | Color applied | | |
| 10.6 | Remove highlight | DELETE | /api/segments/{sid}/highlight | Removed | | |
| 10.7 | Bulk highlight | POST | /api/transcripts/{tid}/bulk-highlight | Multiple highlighted | | |

---

## 11. SEARCH

### 11.1 Full-Text Search
| # | Test | Method | Path | Expected | Status | Notes |
|---|------|--------|------|----------|--------|-------|
| 11.1.1 | Search segments | GET | /api/search/segments?q=test | Matching segments | | |
| 11.1.2 | Search documents | GET | /api/search/documents?q=test | Matching docs | | |
| 11.1.3 | Global search | GET | /api/search/global?q=test | Cross-type results | | |
| 11.1.4 | Empty query returns empty | GET | /api/search/segments?q= | Empty array or 422 | | |
| 11.1.5 | Case-insensitive search | GET | /api/search/segments?q=TEST | Same as lowercase | | |

### 11.2 Semantic Search
| # | Test | Method | Path | Expected | Status | Notes |
|---|------|--------|------|----------|--------|-------|
| 11.2.1 | Rebuild search index | POST | /api/search/rebuild-index | Job started | | |
| 11.2.2 | Semantic search query | GET | /api/search/segments?q=meaning&mode=semantic | Semantically relevant results | | |
| 11.2.3 | Hybrid search (semantic + keyword) | GET | /api/search/global?q=test&mode=hybrid | Combined ranked results | | |

### 11.3 Search History
| # | Test | Method | Path | Expected | Status | Notes |
|---|------|--------|------|----------|--------|-------|
| 11.3.1 | Get search history | GET | /api/search/history | Array of queries | | |
| 11.3.2 | Delete history entry | DELETE | /api/search/history/{id} | 200 | | |
| 11.3.3 | Clear all history | DELETE | /api/search/history | All cleared | | |

---

## 12. CONVERSATIONS (Chat)

| # | Test | Method | Path | Expected | Status | Notes |
|---|------|--------|------|----------|--------|-------|
| 12.1 | Create conversation | POST | /api/conversations | 201, new conversation | | |
| 12.2 | List conversations | GET | /api/conversations | Paginated list | | |
| 12.3 | Get conversation with messages | GET | /api/conversations/{id} | Full conversation | | |
| 12.4 | Update conversation title | PATCH | /api/conversations/{id} | Title updated | | |
| 12.5 | Add message | POST | /api/conversations/{id}/messages | Message added | | |
| 12.6 | Delete conversation | DELETE | /api/conversations/{id} | 200 | | |
| 12.7 | Filter by project | GET | /api/conversations?project_id=X | Filtered list | | |

---

## 13. AI SERVICES

### 13.1 Model Management
| # | Test | Method | Path | Expected | Status | Notes |
|---|------|--------|------|----------|--------|-------|
| 13.1.1 | List AI models | GET | /api/ai/models | Available models | | |
| 13.1.2 | Get AI status | GET | /api/ai/status | Status object | | |
| 13.1.3 | AI debug info | GET | /api/ai/debug | Debug config | | |
| 13.1.4 | Download AI model (streaming) | POST | /api/ai/models/{id}/download | SSE progress stream, model stored | | |
| 13.1.5 | Activate AI model | POST | /api/ai/models/{id}/activate | Model loaded into memory | | |
| 13.1.6 | Deactivate AI model | POST | /api/ai/models/{id}/deactivate | Model unloaded, memory freed | | |
| 13.1.7 | Delete AI model | DELETE | /api/ai/models/{id} | Model removed from disk | | |
| 13.1.8 | Install LLM dependencies | POST | /api/ai/install-deps | Dependencies installed | | |

### 13.2 Whisper Models
| # | Test | Method | Path | Expected | Status | Notes |
|---|------|--------|------|----------|--------|-------|
| 13.2.1 | List Whisper models | GET | /api/whisper/models | Model list with sizes | | |
| 13.2.2 | Download Whisper model (streaming) | POST | /api/whisper/models/{id}/download | SSE progress, model stored | | |
| 13.2.3 | Activate Whisper model | POST | /api/whisper/models/{id}/activate | Model loaded | | |
| 13.2.4 | Delete Whisper model | DELETE | /api/whisper/models/{id} | Model removed | | |

### 13.3 Diarization Models
| # | Test | Method | Path | Expected | Status | Notes |
|---|------|--------|------|----------|--------|-------|
| 13.3.1 | List diarization models | GET | /api/diarization/models | Model list | | |
| 13.3.2 | Download diarization model (streaming) | POST | /api/diarization/models/{id}/download | SSE progress, model stored | | |
| 13.3.3 | Delete diarization model | DELETE | /api/diarization/models/{id} | Model removed | | |

### 13.4 OCR
| # | Test | Method | Path | Expected | Status | Notes |
|---|------|--------|------|----------|--------|-------|
| 13.4.1 | List OCR models | GET | /api/ocr/models | Model list | | |
| 13.4.2 | Get OCR status | GET | /api/ocr/status | Status | | |
| 13.4.3 | Download OCR model | POST | /api/ocr/models/{id}/download | Model stored | | |
| 13.4.4 | Activate OCR model | POST | /api/ocr/models/{id}/activate | Model loaded | | |
| 13.4.5 | Deactivate OCR model | POST | /api/ocr/models/{id}/deactivate | Model unloaded | | |
| 13.4.6 | Install OCR dependencies | POST | /api/ocr/install-deps | Dependencies installed | | |

### 13.5 Chat & Inference (requires active LLM)
| # | Test | Method | Path | Expected | Status | Notes |
|---|------|--------|------|----------|--------|-------|
| 13.5.1 | AI chat (non-streaming) | POST | /api/ai/chat | Response text | | |
| 13.5.2 | AI chat (streaming) | POST | /api/ai/chat/stream | SSE stream | | |
| 13.5.3 | Multi-document chat (streaming) | POST | /api/ai/chat/multi | Context-aware response across docs | | |
| 13.5.4 | Summarize transcript | POST | /api/ai/transcripts/{id}/summarize | Summary JSON | | |
| 13.5.5 | Ask about transcript | POST | /api/ai/transcripts/{id}/ask | Answer | | |
| 13.5.6 | Analyze transcript | POST | /api/ai/transcripts/{id}/analyze | Sentiment, topics, actions | | |
| 13.5.7 | Extract entities | POST | /api/ai/extract-entities | Entities list | | |
| 13.5.8 | Entity extraction templates | GET | /api/ai/extraction-templates | Template list | | |
| 13.5.9 | AI chat with tool calling | POST | /api/ai/chat/stream | Tool invoked, result in response | | |
| 13.5.10 | AI chat with web search | POST | /api/ai/chat/stream | Web results cited in response | | |
| 13.5.11 | Serve generated document | GET | /api/ai/generated/{filename} | File returned | | |

### 13.6 Voice Agent
| # | Test | Method | Path | Expected | Status | Notes |
|---|------|--------|------|----------|--------|-------|
| 13.6.1 | Get voice status | GET | /api/voice/status | Status | | |
| 13.6.2 | List TTS models | GET | /api/voice/tts/models | Model list | | |
| 13.6.3 | Download TTS model | POST | /api/voice/tts/models/{id}/download | Model stored | | |
| 13.6.4 | Activate TTS model | POST | /api/voice/tts/models/{id}/activate | Model loaded | | |

### 13.7 Windows-Specific AI (CUDA/NVIDIA)
| # | Test | Method | Path | Expected | Status | Notes |
|---|------|--------|------|----------|--------|-------|
| W-13.7.1 | CUDA availability reported | GET | /api/system/gpu-status | CUDA backend detected | | |
| W-13.7.2 | NVIDIA GPU VRAM reported | GET | /api/system/hardware | VRAM size and usage | | |
| W-13.7.3 | GPU acceleration toggle | POST | /api/system/enable-gpu | GPU enabled for transcription | | |

---

## 14. JOBS (Async Queue)

| # | Test | Method | Path | Expected | Status | Notes |
|---|------|--------|------|----------|--------|-------|
| 14.1 | List jobs | GET | /api/jobs | Job array | | |
| 14.2 | Get job status | GET | /api/jobs/{id} | Status + progress | | |
| 14.3 | Cancel job | POST | /api/jobs/{id}/cancel | Job cancelled | | |
| 14.4 | Clear completed jobs | POST | /api/jobs/clear-completed | Cleared | | |

---

## 15. CONFIGURATION

### 15.1 Settings
| # | Test | Method | Path | Expected | Status | Notes |
|---|------|--------|------|----------|--------|-------|
| 15.1.1 | Get config status | GET | /api/config/status | Config state | | |
| 15.1.2 | Get AI config | GET | /api/config/ai | Model, context, temp | | |
| 15.1.3 | Update AI config | PUT | /api/config/ai | Updated | | |
| 15.1.4 | Get transcription config | GET | /api/config/transcription | Language, model | | |
| 15.1.5 | Update transcription config | PUT | /api/config/transcription | Updated | | |
| 15.1.6 | Get web search config | GET | /api/config/web-search | Enabled, provider | | |
| 15.1.7 | Update web search config | PUT | /api/config/web-search | Updated | | |
| 15.1.8 | Get trash config | GET | /api/config/trash | Retention settings | | |
| 15.1.9 | Update trash config | PUT | /api/config/trash | Updated | | |
| 15.1.10 | Empty trash | POST | /api/config/trash/empty | All purged | | |

### 15.2 OAuth
| # | Test | Method | Path | Expected | Status | Notes |
|---|------|--------|------|----------|--------|-------|
| 15.2.1 | List OAuth providers | GET | /api/oauth/providers | Provider list | | |
| 15.2.2 | Get OAuth credentials | GET | /api/config/oauth-credentials | Stored creds | | |
| 15.2.3 | Start OAuth flow | POST | /api/oauth/start | Redirect URL returned | | |
| 15.2.4 | Check OAuth status | GET | /api/oauth/status/{state} | Status (pending/complete) | | |
| 15.2.5 | Cancel OAuth flow | POST | /api/oauth/cancel/{state} | Flow cancelled | | |

### 15.3 Storage Locations
| # | Test | Method | Path | Expected | Status | Notes |
|---|------|--------|------|----------|--------|-------|
| 15.3.1 | List storage locations | GET | /api/storage-locations | Location array | | |
| 15.3.2 | Create storage location | POST | /api/storage-locations | 201 | | |
| 15.3.3 | Test storage connection | POST | /api/storage-locations/test | Connection result | | |
| 15.3.4 | Delete storage location | DELETE | /api/storage-locations/{id} | 200 | | |
| 15.3.5 | Start data transfer between locations | POST | /api/storage-locations/transfer | Transfer job started | | |
| 15.3.6 | Check transfer status | GET | /api/storage-locations/transfer/status | Progress reported | | |

---

## 16. ARCHIVE (Import/Export)

| # | Test | Method | Path | Expected | Status | Notes |
|---|------|--------|------|----------|--------|-------|
| 16.1 | Get archive info | GET | /api/archive/info | Size, counts | | |
| 16.2 | Export archive | POST | /api/archive/export | VERBATIM file | | |
| 16.3 | Import archive | POST | /api/archive/import | Data restored | | |

---

## 17. FILE BROWSER

| # | Test | Method | Path | Expected | Status | Notes |
|---|------|--------|------|----------|--------|-------|
| 17.1 | Browse directory | GET | /api/browse?path=/ | Files and folders | | |
| 17.2 | Get folder tree | GET | /api/browse/tree | Tree structure | | |
| 17.3 | Move item | POST | /api/browse/move | Item moved to target | | |
| 17.4 | Copy item | POST | /api/browse/copy | Item duplicated | | |
| 17.5 | Rename item | POST | /api/browse/rename | Item renamed | | |
| 17.6 | Delete item | DELETE | /api/browse/{type}/{id} | Item removed | | |

---

## 18. PROJECT ANALYTICS

| # | Test | Method | Path | Expected | Status | Notes |
|---|------|--------|------|----------|--------|-------|
| 18.1 | Get project analytics | GET | /api/projects/{id}/analytics | Word count, duration, speakers | | |

---

## 19. QUALITY REVIEW

| # | Test | Method | Path | Expected | Status | Notes |
|---|------|--------|------|----------|--------|-------|
| 19.1 | Start quality review | POST | /api/quality-review/{tid}/start | Job started | | |
| 19.2 | Get latest review results | GET | /api/quality-review/{tid}/latest | Corrections list | | |
| 19.3 | Get review by job ID | GET | /api/quality-review/{tid}/{job_id} | Specific review results | | |
| 19.4 | Apply selected corrections | POST | /api/quality-review/{tid}/{job_id}/apply | Selected segments corrected | | |
| 19.5 | Apply all corrections | POST | /api/quality-review/{tid}/{job_id}/apply-all | All suggestions applied | | |

---

## 20. FRONTEND (Browser-based UI Testing)

### 20.1 Page Loading
| # | Test | Path | Expected | Status | Notes |
|---|------|------|----------|--------|-------|
| 20.1.1 | Dashboard loads | / | Stats widgets render | | |
| 20.1.2 | Recordings page loads | /recordings | Recording list renders | | |
| 20.1.3 | Projects page loads | /projects | Project grid renders | | |
| 20.1.4 | Documents page loads | /documents | Document list renders | | |
| 20.1.5 | Search page loads | /search | Search box renders | | |
| 20.1.6 | Chats page loads | /chats | Conversation list renders | | |
| 20.1.7 | Settings page loads | /settings | Settings tabs render | | |
| 20.1.8 | Live transcription page loads | /live | Capture UI renders | | |
| 20.1.9 | Archive page loads | /archive | Trash list renders | | |
| 20.1.10 | File browser loads | /browser | Folder tree renders | | |

### 20.2 Navigation
| # | Test | Expected | Status | Notes |
|---|------|----------|--------|-------|
| 20.2.1 | Sidebar navigation works | All links clickable | | |
| 20.2.2 | Back/forward browser navigation | History works | | |
| 20.2.3 | Deep link to recording works | Direct URL loads | | |
| 20.2.4 | Deep link to project works | Direct URL loads | | |

### 20.3 Interactive Features
| # | Test | Expected | Status | Notes |
|---|------|----------|--------|-------|
| 20.3.1 | Create project from UI | Modal opens, project created | | |
| 20.3.2 | Upload recording from UI | File dialog, upload progress | | |
| 20.3.3 | Upload document from UI | File dialog, processing | | |
| 20.3.4 | Search from search box | Results appear | | |
| 20.3.5 | Open chat panel | Chat FAB opens panel | | |
| 20.3.6 | Send chat message | Response received | | |
| 20.3.7 | Transcript viewer renders | Segments with timestamps | | |
| 20.3.8 | Segment editing works | Click to edit, save | | |
| 20.3.9 | Audio player controls | Play, pause, seek | | |
| 20.3.10 | Settings save correctly | Changes persist on reload | | |
| 20.3.11 | Audio player speed control | Playback rate changes (0.5x–2x) | | |
| 20.3.12 | Audio player volume control | Volume adjusts | | |
| 20.3.13 | Waveform visualization renders | WaveSurfer waveform visible | | |
| 20.3.14 | Segment highlight color picker | Color applied to segment | | |
| 20.3.15 | Entity panel in transcript view | Extracted entities displayed | | |
| 20.3.16 | Multi-select recordings | Checkbox selection, bulk action bar appears | | |
| 20.3.17 | Drag-and-drop file upload | UploadDropzone accepts dropped files | | |
| 20.3.18 | Transcript in-page search | Matches highlighted, nav between results | | |
| 20.3.19 | Chat attachment picker | Documents attachable to conversation | | |
| 20.3.20 | Dashboard stats show correct data | Widget counts match /api/stats | | |
| 20.3.21 | What's New dialog on update | Version release notes displayed | | |
| 20.3.22 | Document viewer renders | PDF/image content displayed | | |
| 20.3.23 | Notes panel in recording view | Notes anchored to timestamps | | |

---

## 21. INTEGRATION TESTS (Cross-feature)

| # | Test | Expected | Status | Notes |
|---|------|----------|--------|-------|
| 21.1 | Upload recording + transcribe + search | Full pipeline works | | |
| 21.2 | Create project + add recording + get analytics | Analytics reflect data | | |
| 21.3 | Upload document + search content | Document searchable | | |
| 21.4 | Create conversation + send message + list | Conversation persisted | | |
| 21.5 | Tag recording + filter by tag | Tag filtering works | | |
| 21.6 | Delete recording + verify in trash | Trash workflow complete | | |
| 21.7 | Restore from trash + verify active | Restore works | | |
| 21.8 | Export transcript + verify file content | Export matches data | | |
| 21.9 | Bulk operations (select multiple, delete) | All items affected | | |
| 21.10 | WebSocket sync fires on CRUD | Real-time updates received | | |
| 21.11 | Upload document + OCR + search extracted text | OCR pipeline end-to-end | | |
| 21.12 | Download model + activate + transcribe | First-run model pipeline | | |
| 21.13 | Quality review + apply corrections + verify text | Correction workflow end-to-end | | |

---

## 22. ERROR HANDLING & EDGE CASES

| # | Test | Expected | Status | Notes |
|---|------|----------|--------|-------|
| 22.1 | GET nonexistent resource (404) | 404 with message | | |
| 22.2 | POST with missing required fields | 422 validation error | | |
| 22.3 | PATCH with invalid ID | 404 | | |
| 22.4 | Upload zero-byte file | Rejected gracefully | | |
| 22.5 | Upload very large file name (255+ chars) | Handled | | |
| 22.6 | Concurrent writes to same resource | No corruption | | |
| 22.7 | Double-delete same resource | Idempotent or 404 | | |
| 22.8 | Unicode in project name / search | Full Unicode support | | |
| 22.9 | Special chars in file paths | Handled correctly | | |
| 22.10 | API request with invalid JSON body | 422, not 500 | | |

---

## 23. LIVE TRANSCRIPTION (WebSocket)

| # | Test | Method | Path | Expected | Status | Notes |
|---|------|--------|------|----------|--------|-------|
| 23.1 | WebSocket connects | WS | /api/live/transcribe | Connection established | | |
| 23.2 | Send audio chunk, receive transcription | WS | /api/live/transcribe | Segment JSON returned | | |
| 23.3 | Pause and resume audio stream | WS | /api/live/transcribe | Audio paused, resumes cleanly | | |
| 23.4 | Language selection during live session | WS | /api/live/transcribe (lang param) | Correct language model used | | |
| 23.5 | Save live session to library | POST | /api/live/save | Recording + transcript created | | |
| 23.6 | Autosave live session | POST | /api/live/autosave | Session data persisted | | |
| 23.7 | Discard live session | DELETE | /api/live/session/{id} | Session removed | | |
| 23.8 | Disconnect cleanly | WS | Close frame | Resources freed, no orphans | | |

---

## 24. KEYBOARD SHORTCUTS

| # | Test | Key | Context | Expected | Status | Notes |
|---|------|-----|---------|----------|--------|-------|
| 24.1 | Space plays/pauses | Space | Transcript page | Audio toggles play/pause | | |
| 24.2 | J skips back 10s | J | Transcript page | Playback rewinds 10s | | |
| 24.3 | L skips forward 10s | L | Transcript page | Playback advances 10s | | |
| 24.4 | Arrow keys skip 5s | Left/Right | Transcript page | Playback skips 5s | | |
| 24.5 | Up/Down navigate segments | Up/Down | Transcript page | Active segment changes | | |
| 24.6 | R starts/stops live recording | R | Live page | Recording toggles | | |
| 24.7 | P pauses/resumes live | P | Live page | Recording pauses/resumes | | |
| 24.8 | Ctrl+S saves live session | Ctrl+S | Live page | Session saved | | |
| 24.9 | M mutes live mic | M | Live page | Microphone muted | | |
| 24.10 | Custom keybinding override | Settings | Keybinding editor | Override persists, new key works | | |

---

## 25. SYSTEM MANAGEMENT

| # | Test | Method | Path | Expected | Status | Notes |
|---|------|--------|------|----------|--------|-------|
| 25.1 | Get category counts | GET | /api/system/category-counts | Count per data category | | |
| 25.2 | Selective clear (specific category) | POST | /api/system/clear-selective | Category data deleted | | |
| 25.3 | Clear memory | POST | /api/system/clear-memory | Memory freed, usage drops | | |
| 25.4 | Enable GPU acceleration | POST | /api/system/enable-gpu | GPU enabled for processing | | |
| 25.5 | Reset database | POST | /api/system/reset-database | All data wiped, fresh state | | |

---

## 26. ONBOARDING & APPEARANCE

| # | Test | Expected | Status | Notes |
|---|------|----------|--------|-------|
| 26.1 | Welcome modal shows on first launch | Modal rendered with intro | | |
| 26.2 | Onboarding tour steps navigate | All tour steps reachable | | |
| 26.3 | Tour state persists (skip/complete) | Tour does not re-show after completion | | |
| 26.4 | Dark mode applies | Dark theme CSS active | | |
| 26.5 | Light mode applies | Light theme CSS active | | |
| 26.6 | System theme follows OS | Matches OS preference automatically | | |
| 26.7 | Theme persists on reload | Saved to localStorage | | |

---

## SUMMARY

| Category | Total Tests | Pass | Fail | Skip | Blocked |
|----------|-------------|------|------|------|---------|
| 0. Windows-Specific | 18 | | | | |
| 1. Health & Startup | 10 | | | | |
| 2. Projects | 24 | | | | |
| 3. Project Types | 5 | | | | |
| 4. Recordings | 25 | | | | |
| 5. Transcripts | 13 | | | | |
| 6. Speakers | 6 | | | | |
| 7. Tags | 8 | | | | |
| 8. Documents | 17 | | | | |
| 9. Notes | 6 | | | | |
| 10. Comments & Highlights | 7 | | | | |
| 11. Search | 11 | | | | |
| 12. Conversations | 7 | | | | |
| 13. AI Services | 38 | | | | |
| 14. Jobs | 4 | | | | |
| 15. Configuration | 21 | | | | |
| 16. Archive | 3 | | | | |
| 17. File Browser | 6 | | | | |
| 18. Analytics | 1 | | | | |
| 19. Quality Review | 5 | | | | |
| 20. Frontend UI | 37 | | | | |
| 21. Integration | 13 | | | | |
| 22. Error Handling | 10 | | | | |
| 23. Live Transcription | 8 | | | | |
| 24. Keyboard Shortcuts | 10 | | | | |
| 25. System Management | 5 | | | | |
| 26. Onboarding & Appearance | 7 | | | | |
| **TOTAL** | **330** | | | | |

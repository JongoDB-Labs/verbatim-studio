# Verbatim Studio V1 Readiness Document

**Created:** 2026-03-21
**Current Version:** v0.56.0
**Target:** V1.0 — Production-ready, app-store-distributable, near-perfect UX

---

## How to Use This Document

This document is the comprehensive audit and work plan for bringing Verbatim Studio from v0.56 to v1.0. It is designed to be loaded into Claude Code sessions as context for implementation work.

**For Claude Code:** Reference this file at `docs/V1_READINESS.md`. When starting a work session, tell Claude: "Read docs/V1_READINESS.md and work on the next unchecked item in [section]."

**Tracking:** Each item has a checkbox. Mark `[x]` when complete. Items are ordered by priority within each section.

---

## Table of Contents

1. [P0 — Ship Blockers](#p0--ship-blockers)
2. [P1 — Data Integrity & Safety](#p1--data-integrity--safety)
3. [P2 — Core UX Polish](#p2--core-ux-polish)
4. [P3 — AI Quality](#p3--ai-quality)
5. [P4 — Desktop Distribution](#p4--desktop-distribution)
6. [P5 — Performance](#p5--performance)
7. [P6 — Security Hardening](#p6--security-hardening)
8. [P7 — Accessibility Baseline](#p7--accessibility-baseline)
9. [P8 — Hide for V1](#p8--hide-for-v1)
10. [P9 — Nice-to-Have Polish](#p9--nice-to-have-polish)
11. [V1 Ship Checklist](#v1-ship-checklist)

---

## P0 — Ship Blockers

These prevent shipping. Fix first.

### Code Signing & Distribution

- [ ] **P0-01: macOS code signing disabled in CI**
  - File: `.github/workflows/build-electron.yml` lines 391, 411
  - Issue: `--config.mac.identity=null` disables signing. Gatekeeper blocks unsigned apps.
  - Fix: Add `CSC_LINK` and `CSC_KEY_PASSWORD` secrets. Remove `identity=null`. You need an Apple Developer account ($99/year) and a Developer ID Application certificate.

- [ ] **P0-02: macOS notarization not configured**
  - File: No `afterSign` hook exists in electron-builder config
  - Issue: macOS 10.15+ refuses to open un-notarized apps without manual Gatekeeper bypass.
  - Fix: Add `afterSign` notarization script using `@electron/notarize`. Requires Apple ID + app-specific password in CI secrets. The entitlements file at `apps/electron/entitlements.mac.plist` is already correct.

- [ ] **P0-03: Windows code signing needs verification**
  - File: `.github/workflows/build-electron.yml`
  - Issue: `WIN_CSC_LINK` env var is passed but no verification step. Unsigned Windows EXEs trigger SmartScreen warnings and may be blocked in enterprise environments.
  - Fix: Verify signing works, add a CI step to check signature. You need an EV code signing certificate (costs ~$200-400/year from providers like Sectigo, DigiCert).

- [ ] **P0-04: Update integrity — no checksum verification on downloaded updates**
  - File: `apps/electron/src/main/updater.ts`
  - Issue: Downloaded DMGs/EXEs are not verified against checksums before execution. CDN cache poisoning or MITM could deliver malicious binaries.
  - Fix: Generate SHA-256 checksums during CI build, publish alongside release assets. Verify before applying update.

- [ ] **P0-05: macOS update deletes app before verifying replacement**
  - File: `apps/electron/src/main/update-script.ts` line 90
  - Issue: `rm -rf /Applications/Verbatim Studio.app` runs before `cp -R` of the new version. If copy fails (disk full, permissions, DMG corruption), user has no installed app and no rollback.
  - Fix: Copy new app to temp location first, verify it, then swap atomically (rename old to `.bak`, rename new to final, delete `.bak`).

### Data Loss Prevention

- [ ] **P0-06: No database backup before migrations**
  - File: `packages/backend/persistence/database.py` lines 157-226
  - Issue: `init_db()` runs `_run_migrations()` on every startup directly against the live SQLite file. Failed migration = corrupted database with no recovery.
  - Fix: Copy `verbatim.db` to `verbatim.db.backup-{timestamp}` before running migrations. Keep last 3 backups. Add `PRAGMA integrity_check` after migration.

- [ ] **P0-07: Live transcription audio stored only in server memory**
  - File: `packages/backend/api/routes/live.py` line 293
  - Issue: `session.audio_chunks.append(audio_data)` accumulates all audio in RAM. Server crash = all audio lost. 1-hour session ≈ 200-400 MB RAM.
  - Fix: Stream audio chunks to a temp file on disk. Load from disk for final save. Add periodic auto-save to database (not just the "autosave" endpoint which only confirms session is alive).

- [ ] **P0-08: Bulk move items doesn't move physical files**
  - File: `packages/backend/api/routes/projects.py` lines 828-860
  - Issue: `bulk_move_items` updates `project_id` in DB but does NOT call `storage_service.move_to_project()`. If original project is deleted with `delete_files=True`, the files are destroyed even though the DB record moved.
  - Fix: Call `storage_service.move_to_project()` for each item, same as the single-item move endpoint.

- [ ] **P0-09: Stuck transcription jobs never recover**
  - File: `packages/backend/services/jobs.py`
  - Issue: If app crashes during transcription, recordings stay in `status="processing"` permanently. No startup recovery.
  - Fix: On startup, query for recordings with `status="processing"` and reset to `status="failed"` with message "Interrupted — please retry". Add a "Retry" button in the UI.

---

## P1 — Data Integrity & Safety

### Database

- [ ] **P1-01: `PRAGMA synchronous=NORMAL` risks data loss on power failure**
  - File: `packages/backend/persistence/database.py` line 28
  - Fix: Change to `synchronous=FULL`. Slightly slower writes but transactions survive OS crashes. For a desktop app handling irreplaceable transcripts, correctness > speed.

- [ ] **P1-02: No unique constraint on `(transcript_id, segment_index)`**
  - File: `packages/backend/persistence/models.py` line 230
  - Fix: Add `UniqueConstraint('transcript_id', 'segment_index')` to the Segment model. This prevents duplicate segment ordering that would corrupt exports.

- [ ] **P1-03: Project name has no uniqueness constraint**
  - File: `packages/backend/persistence/models.py` line 62
  - Issue: Two projects named "Client A" share the same filesystem folder, causing file collisions.
  - Fix: Add `unique=True` to `Project.name` column. Handle the migration for existing duplicates.

- [ ] **P1-04: Archive export omits most data types**
  - File: `packages/backend/api/routes/archive.py` lines 58-144
  - Issue: Only exports projects, recordings, transcripts, segments, speakers. Missing: tags, documents, notes, conversations, comments, highlights, embeddings, settings.
  - Fix: Expand `_export_data()` to include all data types. This is critical for data portability and backup.

- [ ] **P1-05: Archive import doesn't restore `is_archived` status**
  - File: `packages/backend/api/routes/archive.py` lines 361-374
  - Fix: Read `is_archived` from imported data and set it on the recording model.

- [ ] **P1-06: Conversation deletion on project delete is SET NULL, not documented**
  - File: `packages/backend/persistence/models.py` line 448
  - Issue: Deleting a project orphans conversations (sets `project_id = NULL`). User may not realize conversations still exist but lost their project association.
  - Fix: Document this behavior in the project delete confirmation dialog. Or offer a choice: "Also delete conversations in this project?"

- [ ] **P1-07: Recording delete doesn't check for running transcription jobs**
  - File: `packages/backend/api/routes/recordings.py` lines 1132-1174
  - Fix: Check `JobQueue` for active jobs on this recording before allowing delete. If running, return 409 Conflict with message.

### File Integrity

- [ ] **P1-08: No integrity check for missing audio/document files**
  - Issue: If audio files are externally deleted, the app shows recordings but playback silently fails.
  - Fix: Add a `file_exists` field to the Recording/Document API response. Frontend should show a "File missing" warning badge. Add a Settings > Maintenance > "Check file integrity" button.

- [ ] **P1-09: Bulk delete silently swallows file deletion failures**
  - File: `packages/backend/api/routes/recordings.py` lines 848-867
  - Issue: If `storage_service.delete_file()` fails, the DB record is still deleted, orphaning the file.
  - Fix: Either make deletion transactional (rollback DB if file delete fails) or report partial failures in the response.

### Exports

- [ ] **P1-10: SRT export uses non-standard `<v>` tags for speaker labels**
  - File: `packages/backend/services/export.py` line 238
  - Issue: SRT format doesn't support `<v Name>` tags. Many players show the raw markup as text.
  - Fix: Use `Name: text` prefix format for SRT speaker labels.

- [ ] **P1-11: PDF export crashes on special characters (`<`, `>`, `&`)**
  - File: `packages/backend/services/export.py` lines 665-678
  - Issue: Segment text is embedded directly into reportlab Paragraph XML without escaping.
  - Fix: XML-escape segment text before passing to reportlab (`html.escape()` or `xml.sax.saxutils.escape()`).

- [ ] **P1-12: Filename sanitization produces empty/uninformative names for non-ASCII**
  - File: `packages/backend/api/routes/transcripts.py` line 596
  - Issue: Non-English titles (Chinese, Arabic) become strings of underscores like `______.txt`.
  - Fix: Use `unicodedata.normalize('NFKD')` + keep Unicode letters, or use the recording ID as fallback.

---

## P2 — Core UX Polish

### Error States (Missing or Misleading)

- [ ] **P2-01: Replace all native `alert()`/`confirm()` with in-app components**
  - Files: 15 instances across 9 files (see details below)
  - `ChatPanel.tsx` lines 137, 147, 173, 176
  - `ChatsPage.tsx` lines 32, 37
  - `AttachmentPicker.tsx` lines 74, 92
  - `ExportButton.tsx` line 58
  - `TranscriptPicker.tsx` line 50
  - `FileBrowserPage.tsx` lines 404, 613
  - `SettingsPage.tsx` line 1236
  - `SpeakerPanel.tsx` line 135
  - `OAuthCredentialsConfig.tsx` line 151
  - Fix: Create a shared `useConfirmDialog()` hook and `Toast` component. Use styled modals for destructive confirmations, toasts for success/error feedback.

- [ ] **P2-02: DocumentsPage has no error state**
  - File: `packages/frontend/src/pages/documents/DocumentsPage.tsx`
  - Issue: API failure shows "No documents yet" instead of an error. User thinks they have no documents.
  - Fix: Destructure `error` from `useDocuments()` hook. Add error banner like RecordingsPage does.

- [ ] **P2-03: SearchPage silently swallows API errors**
  - File: `packages/frontend/src/pages/search/SearchPage.tsx`
  - Issue: Search failure shows "No results found" with no error indication.
  - Fix: Add error state with retry button. Distinguish "no results" from "search failed."

- [ ] **P2-04: ProjectHomePage returns `null` during loading (blank flash)**
  - File: `packages/frontend/src/pages/projects/ProjectHomePage.tsx` line 41
  - Fix: Add a loading spinner consistent with other pages (use `text-primary` spinner pattern).

- [ ] **P2-05: WaveformPlayer has no error state for corrupt/unreachable audio**
  - File: `packages/frontend/src/components/audio/WaveformPlayer.tsx` lines 93-98
  - Issue: Load error produces a blank div with no message.
  - Fix: Show "Audio file could not be loaded" error banner in the waveform area.

- [ ] **P2-06: ProjectDetailPage shows "not found" on network errors**
  - File: `packages/frontend/src/pages/projects/ProjectDetailPage.tsx` line 59
  - Fix: Distinguish between 404 (not found) and other errors (show error message + retry).

- [ ] **P2-07: Dashboard stats error not displayed**
  - File: `packages/frontend/src/components/dashboard/Dashboard.tsx` line 100
  - Issue: Stats API failure shows "0" values, not an error.
  - Fix: Show error indicator on stat cards or a banner.

### Chat/AI UX

- [ ] **P2-08: Chat messages rendered as plain text, not markdown**
  - File: `packages/frontend/src/components/ai/ChatMessages.tsx` line 57
  - Issue: `<p className="text-sm whitespace-pre-wrap">{msg.content}</p>` shows raw `#`, `**`, `` ``` `` characters.
  - Fix: Use `react-markdown` (already a dependency) to render assistant messages. Keep user messages as plain text.

- [ ] **P2-09: No loading indicator when LLM model loads (~10-30 seconds)**
  - File: `packages/backend/adapters/ai/llama_cpp.py` lines 145-183
  - Issue: First chat message after launch produces no tokens while model loads. User sees spinning state with no explanation.
  - Fix: Send an SSE event like `{"type": "status", "message": "Loading AI model..."}` during `_ensure_loaded()`. Frontend should show a status indicator.

- [ ] **P2-10: Generic chat error messages**
  - File: `packages/frontend/src/components/ai/ChatPanel.tsx` lines 111-118
  - Issue: All errors show "Sorry, I encountered an error. Please try again." regardless of cause.
  - Fix: Parse the error response from the SSE stream. Show specific messages: "AI model not downloaded — go to Settings", "Out of memory — try a smaller model", etc.

- [ ] **P2-11: `window.location.href = '/settings'` bypasses SPA navigation**
  - File: `packages/frontend/src/components/ai/AIAnalysisPanel.tsx` line 147
  - Issue: Full page reload destroys all in-memory state.
  - Fix: Use the app's navigation mechanism (`setNavigation()` callback or pass a `navigate` prop).

### Consistency

- [ ] **P2-12: Standardize loading spinners**
  - Issue: 4+ different spinner styles across pages (some `text-primary`, some `text-blue-600`, some border-based).
  - Fix: Create a shared `<LoadingSpinner />` component. Replace all instances.

- [ ] **P2-13: Centralize language lists**
  - Files: `SettingsPage.tsx` (12 langs), `TranscribeDialog.tsx` (31 langs), `LiveTranscriptionPage.tsx` (separate list)
  - Fix: Create `lib/constants.ts` with a single `SUPPORTED_LANGUAGES` array. Import everywhere.

- [ ] **P2-14: Centralize `formatDuration` utility**
  - Issue: 6+ independent implementations with different output formats.
  - Fix: Use the existing `lib/utils.ts` `formatDuration`. Replace all custom implementations.

- [ ] **P2-15: Remove/gate console.log statements**
  - Files: `api.ts` (9 logs), `useDataSync.tsx` (7 logs), `main.tsx` (1 log)
  - Fix: Gate behind `import.meta.env.DEV` or remove.

### Destructive Actions

- [ ] **P2-16: No confirmation for bulk segment delete**
  - File: `packages/frontend/src/pages/transcript/TranscriptPage.tsx` lines 287-303
  - Fix: Add confirmation dialog before `bulkDeleteSegments()`. Show count of segments being deleted.

- [ ] **P2-17: Verify recording delete has confirmation**
  - File: `packages/frontend/src/pages/recordings/RecordingsPage.tsx` lines 269-274
  - Issue: `handleDelete` calls `deleteRecording.mutate()` directly. Confirmation may or may not be in child component.
  - Fix: Verify the full flow. If no confirmation exists, add one.

- [ ] **P2-18: Archived project buttons in sidebar are non-functional**
  - File: `packages/frontend/src/components/layout/ProjectSelector.tsx` lines 200-208
  - Issue: Rendered as clickable `<button>` with hover styles but no `onClick`.
  - Fix: Either remove from dropdown, render as non-interactive with "(Archived)" label and reduced opacity, or add a useful action.

---

## P3 — AI Quality

- [ ] **P3-01: Semantic search loads ALL embeddings into memory**
  - File: `packages/backend/api/routes/search.py` lines 298-373
  - Issue: Every query loads every embedding and computes O(n) cosine similarity in Python.
  - Fix: Use `sqlite-vec` virtual table extension for vector similarity search (already suggested in code comments). Or implement chunked processing with early termination.

- [ ] **P3-02: Project auto-context loads unbounded data**
  - File: `packages/backend/api/routes/ai.py` lines 830-881
  - Issue: Loads ALL recordings and documents from active projects with no cap. A project with 50 hour-long recordings generates millions of characters.
  - Fix: Limit auto-context to top-N most recent recordings. Show user what's included. Add truncation before loading, not after.

- [ ] **P3-03: Conversation memory compression doubles latency**
  - File: `packages/backend/api/routes/ai.py` lines 1066-1096
  - Issue: Full LLM call for compression before every user response after 8 messages.
  - Fix: Run compression asynchronously after response is sent (using a background task). Or increase threshold to 16 messages. Or compress only when context window is actually near capacity.

- [ ] **P3-04: Cosine similarity threshold of 0.3 is too low**
  - File: `packages/backend/api/routes/search.py` lines 356, 449
  - Fix: Raise to 0.5 for nomic-embed. Test with real user queries.

- [ ] **P3-05: Web search triggers on transcript analysis questions**
  - File: `packages/backend/services/web_search.py` lines 73-112
  - Issue: "What is the speaker's main argument?" triggers web search.
  - Fix: Check if user has attachments before triggering web search. Add more `_NON_SEARCH_PATTERNS`.

- [ ] **P3-06: Embedding model downloads silently with `trust_remote_code=True`**
  - File: `packages/backend/services/embedding.py`
  - Issue: First semantic search silently downloads the model. `trust_remote_code=True` is a security risk.
  - Fix: Add embedding model to the managed download flow in Settings > AI Models. Set `trust_remote_code=False` (or verify it's safe for nomic-embed specifically).

- [ ] **P3-07: Hallucination detection is English-only static string matching**
  - File: `packages/backend/services/quality_review.py`
  - Fix: For V1, this is acceptable but add a disclaimer that quality review is English-only. Post-V1: use confidence scores + repetition detection.

- [ ] **P3-08: No disk space check before model downloads**
  - File: `packages/backend/api/routes/ai.py` line 405
  - Fix: Check available disk space before starting download. Show warning if insufficient.

- [ ] **P3-09: RAM estimates not shown in download UI**
  - File: `packages/backend/core/model_catalog.py`
  - Issue: Catalog has `ram_estimates` per model but they're not exposed in the API or UI.
  - Fix: Include `ram_estimates` in the `AIModelInfo` response. Show in download dialog: "This model requires ~6 GB RAM. Your system has 16 GB."

---

## P4 — Desktop Distribution

- [ ] **P4-01: Port conflict causes hard startup failure**
  - File: `apps/electron/src/main/utils.ts`
  - Issue: Port 52780 in use = app won't start. No fallback.
  - Fix: Try 3-5 consecutive ports (52780, 52781, 52782...) before failing. Or use port 0 (OS-assigned) and pass to frontend via IPC.

- [ ] **P4-02: 180-second health check timeout with no user feedback**
  - File: `apps/electron/src/main/backend.ts` line 278
  - Fix: Add a "This is taking longer than expected..." message after 30 seconds. Add a "Cancel and retry" button after 60 seconds.

- [ ] **P4-03: No database backup before auto-updates**
  - Issue: Updates can change schema. No backup = no recovery.
  - Fix: Copy database before update begins. Store in a versioned backup location.

- [ ] **P4-04: Windows update doesn't verify download integrity**
  - File: `apps/electron/src/main/updater.ts` lines 460-461
  - Fix: Compare downloaded file size against `asset.size` from API. Verify SHA-256 checksum.

- [ ] **P4-05: No window state persistence**
  - File: `apps/electron/src/main/windows.ts` line 9
  - Fix: Use `electron-window-state` package or custom localStorage persistence for window bounds.

- [ ] **P4-06: No application menu**
  - Issue: No custom menu items for common actions, keyboard shortcuts, or "Check for Updates."
  - Fix: Create a menu template with File (New Recording, Import, Export), Edit, View, Window, Help (Check for Updates, About). Register keyboard shortcuts.

- [ ] **P4-07: No Content Security Policy**
  - Fix: Set CSP via `session.defaultSession.webRequest.onHeadersReceived`. Restrict to `self` for scripts, styles. Allow `data:` for images. Block external connections except for update checks and cloud OAuth.

- [ ] **P4-08: Cleanup `/tmp/verbatim-update` on startup**
  - File: `apps/electron/src/main/update-script.ts`
  - Fix: Add a cleanup step at app startup to remove stale update artifacts.

- [ ] **P4-09: No crash reporter**
  - Fix: Add `crashReporter.start()` with a local dump path. For V1, collect dumps locally. Post-V1: consider Sentry.

- [ ] **P4-10: Host downloads on own domain instead of GitHub releases**
  - Issue: GitHub releases page signals "side project" to non-developer audiences (researchers, journalists, podcasters). Download links shared on Twitter/Reddit/HN should point to a branded domain.
  - Infrastructure: MinIO on local Proxmox host, exposed via Cloudflare Tunnels. S3-compatible API so CI tooling (aws cli, mc) works out of the box. GitLab CE on the same host for future CI migration.
  - Flow: GitHub Actions builds → uploads to GitHub releases (auto-updater compat) + MinIO via `mc cp` or `aws s3 cp` → Cloudflare Tunnel exposes MinIO bucket → website `/download` page links to `downloads.verbatimstudio.app/latest/macos` etc.
  - Cloudflare benefit: Tunnel handles TLS + caching at the edge, so MinIO doesn't need to handle burst traffic from a viral HN/Reddit post.
  - Auto-updater: Continues using GitHub releases API unchanged. Download page is user-facing only.

---

## P5 — Performance

- [ ] **P5-01: Archive export loads entire database into memory**
  - File: `packages/backend/api/routes/archive.py` lines 58-144
  - Fix: Stream export using chunked queries. Generate ZIP on-the-fly with streaming response.

- [ ] **P5-02: Cloud file streaming reads entire file into memory**
  - File: `packages/backend/api/routes/recordings.py` lines 1088-1105
  - Fix: Use streaming response with chunked reads from cloud adapter.

- [ ] **P5-03: Upload reads entire file into memory before validation**
  - Files: `recordings.py` line 555, `documents.py` line 157
  - Fix: Stream to temp file using chunked reads. Validate file size from headers before reading body.

- [ ] **P5-04: N+1 query problems in project and conversation listings**
  - Files: `projects.py` lines 184-218, `conversations.py` lines 112-139, `browse.py` lines 158-182
  - Fix: Use subqueries or joined loads for counts. Add pagination to conversations list.

- [ ] **P5-05: Search history doesn't filter archived items**
  - File: `packages/backend/api/routes/search.py` lines 538-604
  - Fix: Add `where(Recording.is_archived == False)` to search queries.

- [ ] **P5-06: Label overflow in AI chat context after 26 items**
  - File: `packages/backend/api/routes/ai.py` lines 774-877
  - Issue: `chr(65 + label_index)` overflows after Z.
  - Fix: Use `AA`, `AB`, etc. after Z. Or use numeric labels: `[1]`, `[2]`, etc.

---

## P6 — Security Hardening

- [ ] **P6-01: Archive import vulnerable to zip path traversal**
  - File: `packages/backend/api/routes/archive.py` lines 311-319
  - Fix: Sanitize each zip member name. Reject entries with `..` in path. Or use `extractall()` with `filter='data'` (Python 3.12+).

- [ ] **P6-02: `pip install` endpoint is unauthenticated**
  - File: `packages/backend/api/routes/ai.py` lines 525-551
  - Issue: Any local process can trigger package installation.
  - Fix: Add a confirmation token or only allow when triggered from the Electron main process via IPC.

- [ ] **P6-03: File path from database used without validation in FileResponse**
  - File: `packages/backend/api/routes/recordings.py` lines 1114-1129
  - Fix: Validate that `file_path` is within the expected media directory before serving.

- [ ] **P6-04: OAuth tokens stored with weak encryption fallback**
  - File: `packages/backend/services/encryption.py`
  - Issue: In Electron mode, keychain is skipped. Key stored as plaintext file alongside database.
  - Fix: Use Electron's `safeStorage` API to encrypt the key. Or use the OS keychain properly via Electron's main process.

- [ ] **P6-05: LIKE queries allow wildcard injection**
  - Files: search.py, recordings.py, documents.py, projects.py, browse.py
  - Fix: Escape `%` and `_` in user search input before passing to `ilike()`.

- [ ] **P6-06: No rate limiting on expensive operations**
  - Files: AI chat, model download, OCR, search rebuild, archive export/import
  - Fix: Add simple per-endpoint rate limiting using `slowapi` or a custom middleware. Even basic limits (e.g., 1 concurrent model download, 5 chat messages/minute) prevent resource exhaustion.

---

## P7 — Accessibility Baseline

These aren't comprehensive a11y compliance but are the minimum for a professional product.

- [ ] **P7-01: Add `role="dialog"` and `aria-modal="true"` to all modal dialogs**
  - Reference implementation: `TranscribeDialog.tsx` lines 93-95
  - Missing in: ChatPanel save dialog, project dialogs, FileBrowserPage dialogs, live transcription save dialog, SettingsPage dialogs
  - Fix: Audit every overlay/modal. Add role, aria-modal, aria-labelledby.

- [ ] **P7-02: Add focus traps to all modal dialogs**
  - Fix: Create a shared `useFocusTrap()` hook or use a library like `focus-trap-react`. Apply to every modal.

- [ ] **P7-03: Add `aria-current="page"` to active sidebar navigation**
  - File: `packages/frontend/src/components/layout/Sidebar.tsx`
  - Fix: `aria-current={isActive ? 'page' : undefined}` on each nav button.

- [ ] **P7-04: Add Escape key handler to all modals**
  - Reference: `TranscribeDialog.tsx` has this. Most other modals don't.
  - Fix: Include in the shared focus trap/dialog component.

---

## P8 — Hide for V1

These features are incomplete and should be hidden or removed to avoid confusing users.

- [ ] **P8-01: Hide tool calling system**
  - Files: `packages/backend/services/tool_registry.py`, `tool_executor.py`
  - Issue: Full architecture scaffolded (registry, parser, executor, multi-turn loop) but NOT wired into the chat endpoint. No tools are registered. Dead code.
  - Fix: Remove the tool prompt injection from the system prompt. Don't load tool_registry at startup. Keep the code for post-V1 but don't activate it.

- [ ] **P8-02: Hide plugin system UI**
  - File: `packages/frontend/src/app/App.tsx` lines 742-763
  - Issue: Plugin route infrastructure exists but no plugins ship. Navigating to a plugin route shows a broken state.
  - Fix: Add "No plugins installed" fallback. Or remove plugin routes entirely from V1 build. Keep backend plugin infrastructure dormant.

- [ ] **P8-03: Hide SearXNG search option**
  - File: `packages/backend/services/web_search.py` line 70
  - Issue: Config exposes `searxng_url` but no provider is implemented.
  - Fix: Remove from settings UI. Keep in config for future use.

- [ ] **P8-04: Remove dead ProjectDetailPage**
  - File: `packages/frontend/src/pages/projects/ProjectDetailPage.tsx`
  - Issue: Entire page is unreachable — `App.tsx` routes to `ProjectHomePage` instead.
  - Fix: Delete the file. If features from it are needed, integrate into ProjectHomePage.

- [ ] **P8-05: Evaluate File Browser for V1**
  - File: `packages/frontend/src/pages/browser/FileBrowserPage.tsx`
  - Issue: Powerful but exposes raw filesystem concepts (MIME types, file paths, folder IDs). May confuse non-technical users.
  - Decision: Either keep and add an "Advanced" label/tooltip, or move behind a Settings toggle. Not necessarily hidden, but consider the target audience.

- [ ] **P8-06: Verify onboarding tour targets still exist**
  - Files: `packages/frontend/src/components/onboarding/`
  - Issue: Sidebar layout and pages have changed since tour was built. Tour steps may point to missing elements.
  - Fix: Manually walk through the tour. Fix any broken step targets.

---

## P9 — Nice-to-Have Polish

These improve the product but are not blocking V1.

- [ ] **P9-01: Add retry button for failed transcription jobs**
  - Issue: No `retry_job()` method. Users must re-upload to retry.
  - Fix: Add retry endpoint. Show "Retry" button on failed recordings.

- [ ] **P9-02: Show confidence indicators on transcript segments**
  - Issue: Whisper confidence scores are stored but never surfaced.
  - Fix: Show color-coded confidence on segments (green/yellow/red). Flag low-confidence segments.

- [ ] **P9-03: Live transcription reconnection after network interruption**
  - File: `packages/backend/api/routes/live.py` lines 463-480
  - Fix: Add a "reconnect" WebSocket message type that resumes an existing session.

- [ ] **P9-04: System tray integration**
  - Fix: Add tray icon with "Show/Hide", transcription progress, and "Quit" options.

- [ ] **P9-05: Desktop notifications for completed transcriptions**
  - Fix: Use Electron `Notification` API when transcription jobs finish.

- [ ] **P9-06: File associations for audio/video files**
  - Fix: Add `fileAssociations` to electron-builder config. Handle `open-file` event.

- [ ] **P9-07: Whisper model loading progress reporting**
  - File: `packages/backend/adapters/transcription/mlx_whisper.py`
  - Issue: Progress jumps from 10% to 60% with no updates during model load.
  - Fix: Add intermediate progress events during model loading phase.

- [ ] **P9-08: ErrorBoundary dark mode support**
  - File: `packages/frontend/src/components/shared/ErrorBoundary.tsx`
  - Issue: Hardcoded white background. Dark mode users see a blinding white crash page.
  - Fix: Detect theme via `prefers-color-scheme` media query in inline styles.

- [ ] **P9-09: Add JSON export format**
  - Issue: No lossless export format. JSON would be the most interoperable.
  - Fix: Add JSON option to export endpoint with full segment data.

- [ ] **P9-10: Add bulk export (zip of multiple recordings)**
  - Fix: Accept array of recording IDs, export each, bundle into zip.

- [ ] **P9-11: Settings page refactor — extract sub-components**
  - File: `packages/frontend/src/pages/settings/SettingsPage.tsx`
  - Issue: 30+ useState calls, 10,000+ tokens. Unmaintainable.
  - Fix: Extract AI Models, OCR Models, Whisper Models, Storage each into separate components.

- [ ] **P9-12: Deprecation fix — `datetime.utcnow()` to `datetime.now(UTC)`**
  - Files: `live.py` lines 101, 216, 480; `archive.py` lines 63, 238
  - Fix: Replace with `datetime.now(timezone.utc)`.

- [ ] **P9-13: macOS Intel builds**
  - Issue: Only arm64 builds exist. Pre-M1 Mac users (2020 and earlier) cannot use the app.
  - Decision: Evaluate whether Intel user base justifies the CI cost. If yes, add x64 to build matrix.

---

## V1 Ship Checklist

Final verification before tagging v1.0.

### Clean Install Test
- [ ] Fresh macOS (Apple Silicon) — download DMG, install, open without Gatekeeper warning
- [ ] Fresh macOS (Intel, if supported) — same
- [ ] Fresh Windows 10/11 — download EXE, install without SmartScreen block
- [ ] First launch — splash screen shows progress, backend starts within 60 seconds
- [ ] Model download — user prompted to download AI model, progress visible, completes successfully
- [ ] Disk space insufficient — clear warning before download begins

### First 30-Minute Session
- [ ] Record audio via microphone — live transcription works, audio saved on disconnect
- [ ] Upload audio file — appears in recordings list, shows processing state
- [ ] Transcription completes — segments appear with timestamps, speakers identified
- [ ] Play audio — waveform renders, clicking timestamps seeks correctly
- [ ] Edit transcript text — changes save, undo works
- [ ] Search — keyword and semantic results appear correctly
- [ ] Ask Max a question about the transcript — relevant, grounded response
- [ ] Export to TXT, SRT, VTT — files are correct, timestamps accurate, speakers labeled
- [ ] Create project — assign recordings, filter by project

### Data Safety
- [ ] Kill app mid-transcription — recording is intact, can retry
- [ ] Kill app mid-live-transcription — audio chunks are recoverable
- [ ] Upgrade from v0.56 to v1.0 — all data preserved, no migration failures
- [ ] Database backup exists after upgrade
- [ ] `PRAGMA integrity_check` passes after upgrade
- [ ] Export and reimport — all data types round-trip correctly

### Edge Cases
- [ ] Open 100+ recordings library — app loads without hanging
- [ ] Very long transcript (2+ hours) — editor and export work
- [ ] Non-English audio — transcription and export handle Unicode correctly
- [ ] Corrupt audio file — clear error message, app doesn't crash
- [ ] No internet — app works fully offline (except cloud sync and web search)
- [ ] Multiple windows — app handles gracefully (prevents or supports)

### Distribution
- [ ] macOS — signed, notarized, no Gatekeeper warnings
- [ ] Windows — signed, no SmartScreen warnings
- [ ] Auto-update — v1.0.0 → v1.0.1 patch update succeeds with integrity verification
- [ ] Auto-update failure — old app is still functional (rollback works)
- [ ] Download page — `verbatimstudio.app/download` serves binaries from own domain (S3+CloudFront)
- [ ] Download links work in social sharing — Twitter/Reddit/HN posts link to branded domain, not GitHub

---

## Appendix: Backend Quirks to Be Aware Of

These are not bugs but important architectural details to know when working on V1 fixes:

1. **Migrations are PRAGMA-check based** (not Alembic). Each migration checks if a column exists before adding it. They run on every startup. No version tracking. See `database.py` lines 174-226.

2. **GPU lock is process-wide** (`threading.Lock()`). Only one GPU operation at a time. See `jobs.py` line 25.

3. **Job queue max_workers=2**. Only 2 concurrent background jobs (transcription, embedding, etc.). See `jobs.py` lines 98-113.

4. **`active_sessions` dict for live transcription is in-process memory only**. Not persisted. See `live.py` line 96.

5. **Mamba-2/Granite-4.0 requires state reset before every LLM call** to prevent `llama_decode returned -1`. See `llama_cpp.py` lines 133-143.

6. **Silent date filter failures** — invalid dates in query params are silently ignored. See `recordings.py` lines 330-343.

7. **`set_active_project` accepts arbitrary dict** instead of Pydantic model. See `projects.py` lines 244-268.

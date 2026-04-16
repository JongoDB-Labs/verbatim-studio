# Windows Compatibility Fixes — v0.49→v0.60 Audit Remediation

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix all Windows-breaking issues discovered in the v0.49→v0.60 audit so the next release builds, installs, and runs correctly on Windows 10/11 with NVIDIA CUDA.

**Architecture:** Phase 1 fixes ship-blocking bugs (hardcoded paths, file locks, missing deps, security). Phase 2 makes voice chat platform-aware so the UI doesn't show broken features on Windows. No new Windows TTS adapter in this plan — that's a separate feature plan.

**Tech Stack:** Python 3.12, FastAPI, Electron 34, TypeScript, Node.js, SQLite

---

## Phase 1: Ship-Blocking Fixes

### Task 1: Fix UPDATE_DIR Hardcoded Unix Path

**Files:**
- Modify: `apps/electron/src/main/update-script.ts:1-4`

**Step 1: Fix the import and constant**

Replace the hardcoded `/tmp/verbatim-update` with `os.tmpdir()`:

```typescript
import { promises as fs } from 'fs';
import os from 'os';
import path from 'path';

export const UPDATE_DIR = path.join(os.tmpdir(), 'verbatim-update');
```

The rest of the file (the macOS shell script generator) already uses `UPDATE_DIR` by reference, so this single change fixes both the macOS and Windows paths.

**Step 2: Verify TypeScript compiles**

Run: `cd apps/electron && npx tsc --noEmit`

**Step 3: Commit**

```
fix: use os.tmpdir() for UPDATE_DIR instead of hardcoded /tmp
```

---

### Task 2: Fix Temp WAV Double-Open in Voice STT (Windows File Lock)

**Files:**
- Modify: `packages/backend/services/voice_agent.py:130-146`
- Modify: `packages/backend/api/routes/voice.py:625-632`

**Step 1: Fix WhisperSTTAdapter.recognize()**

The `NamedTemporaryFile` handle must be closed before `wave.open()` re-opens the same path. Change:

```python
    async def recognize(self, audio_data: bytes, *, sample_rate: int = 16000) -> str:
        import wave

        # Write audio to temp file — close handle first for Windows compatibility
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tmp.close()
        try:
            with wave.open(tmp.name, "wb") as wf:
```

**Step 2: Fix STT warmup in voice.py create_voice_session()**

Same pattern at line 625-626. Change:

```python
    try:
        import tempfile
        import wave as _wave
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tmp.close()
        with _wave.open(tmp.name, "wb") as wf:
```

**Step 3: Commit**

```
fix: close temp file handle before wave.open() for Windows compatibility
```

---

### Task 3: Make mlx_audio Imports Lazy in qwen3_tts.py

**Files:**
- Modify: `packages/backend/adapters/ai/qwen3_tts.py`

**Step 1: The module currently has no top-level mlx imports (they're in `_ensure_loaded` and `_cleanup_model`), which is correct. But the `_cleanup_model` function at line 93 does `import mlx.core as mx` — this is already in a try/except so it's safe.**

**However, the voice.py status endpoint at line 133 does:**
```python
from adapters.ai.qwen3_tts import _PRESET_VOICES
```

**This import will succeed on Windows since `_PRESET_VOICES` is a module-level list and `qwen3_tts.py` has no top-level mlx imports. Verify this is actually safe by checking there are no top-level imports of mlx in the module.**

If there ARE any top-level `mlx` imports, move them inside the functions that use them.

**Step 2: Commit (if changes needed)**

```
fix: ensure qwen3_tts.py has no top-level mlx imports for Windows compat
```

---

### Task 4: Add LiveKit Packages to Windows Requirements

**Files:**
- Modify: `scripts/requirements-ml-windows.txt`

**Step 1: Add LiveKit packages after the "Other ML dependencies" section:**

```
# =============================================================================
# Voice assistant (LiveKit)
# =============================================================================
livekit-api>=1.1.0
livekit-agents>=1.5.0
livekit-plugins-silero>=1.5.0
```

These packages have Windows wheels on PyPI and are required for the voice session endpoint to not crash on import.

**Step 2: Commit**

```
fix: add LiveKit packages to Windows ML requirements
```

---

### Task 5: Add Platform Guards on Voice/TTS Endpoints

**Files:**
- Modify: `packages/backend/api/routes/voice.py`

**Step 1: Make the voice status endpoint platform-aware**

Replace the `mlx_audio` import check (line 147-150) with a platform-aware check:

```python
    # Check TTS availability (platform-specific)
    import sys
    if sys.platform == "darwin":
        try:
            import mlx_audio  # noqa: F401
        except ImportError:
            missing_deps.append("mlx-audio[tts]")
    else:
        # Windows/Linux: TTS not yet available (future: kokoro-onnx)
        missing_deps.append("tts-engine (not yet available on this platform)")
```

**Step 2: Add platform field to TTSModelInfo and filter catalog by platform**

In `list_tts_models()`, filter by current platform:

```python
@router.get("/tts/models")
async def list_tts_models() -> list[TTSModelInfo]:
    import sys
    active_id = _get_active_tts_model()
    items: list[TTSModelInfo] = []

    for model_id, entry in TTS_CATALOG.items():
        # Only show models compatible with current platform
        if entry.get("platform") and entry["platform"] != sys.platform:
            continue
        downloaded = _is_tts_model_downloaded(model_id)
        items.append(TTSModelInfo(
            id=model_id,
            label=entry["label"],
            description=entry["description"],
            size_bytes=entry["size_bytes"],
            ram_gb=entry["ram_gb"],
            downloaded=downloaded,
            active=(model_id == active_id),
        ))

    return items
```

**Step 3: Add platform check to download endpoint**

At the top of `download_tts_model()`:

```python
    import sys
    entry = TTS_CATALOG.get(model_id)
    if not entry:
        raise HTTPException(status_code=404, detail="TTS model not in catalog")

    if entry.get("platform") and entry["platform"] != sys.platform:
        raise HTTPException(
            status_code=400,
            detail=f"This TTS model requires {entry['platform']}. Current platform: {sys.platform}",
        )
```

**Step 4: Commit**

```
fix: add platform guards to voice/TTS endpoints for Windows
```

---

### Task 6: Fix Path Traversal Check in Local Storage Adapter

**Files:**
- Modify: `packages/backend/storage/adapters/local.py:31`

**Step 1: Replace string startswith with is_relative_to()**

Change line 31 from:
```python
        if not str(resolved).startswith(str(self.base_path.resolve())):
```
To:
```python
        if not resolved.is_relative_to(self.base_path.resolve()):
```

`is_relative_to()` is available in Python 3.9+ and handles Windows drive letter casing and separator normalization correctly.

**Step 2: Commit**

```
fix: use Path.is_relative_to() for traversal check (Windows drive letter safety)
```

---

### Task 7: Fix Migration Script Forward-Slash Path Checks

**Files:**
- Modify: `packages/backend/scripts/migrate_filesystem_ui.py:78,161`

**Step 1: Replace string startswith with Path.parts check**

Line 78 — change:
```python
                if not str(relative).startswith("recordings/"):
```
To:
```python
                if relative.parts[0] != "recordings":
```

Line 161 — change:
```python
                if not str(relative).startswith("documents/"):
```
To:
```python
                if relative.parts[0] != "documents":
```

`Path.parts` is platform-independent — on Windows it uses backslashes internally but `.parts` splits correctly regardless.

**Step 2: Commit**

```
fix: use Path.parts for cross-platform path prefix checks in migration
```

---

## Phase 2: Correctness & Robustness Fixes

### Task 8: Fix asyncio.get_event_loop() Deprecation

**Files:**
- Modify: `packages/backend/services/path_manager.py:155,184,213`

**Step 1: Replace all three occurrences**

Change each `asyncio.get_event_loop()` to `asyncio.get_running_loop()`:

```python
            loop = asyncio.get_running_loop()
```

This is the correct call inside a coroutine (Python 3.10+).

**Step 2: Commit**

```
fix: replace deprecated get_event_loop() with get_running_loop()
```

---

### Task 9: Add Linux Data Directory Path

**Files:**
- Modify: `packages/backend/core/config.py:11-16`

**Step 1: Add Linux XDG branch**

Replace `_default_data_dir()`:

```python
def _default_data_dir() -> Path:
    """Return the platform-specific default data directory."""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming"))
        return Path(base) / "Verbatim Studio"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Verbatim Studio"
    # Linux / other: follow XDG Base Directory spec
    xdg = os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))
    return Path(xdg) / "verbatim-studio"
```

**Step 2: Commit**

```
fix: add Linux XDG data directory path in config.py
```

---

### Task 10: Fix RAM Detection Windows Fallback

**Files:**
- Modify: `packages/backend/api/routes/system.py:~276-293`

**Step 1: Add Windows fallback using ctypes**

After the existing `elif plat == "linux":` block, add a Windows branch:

```python
            elif plat == "win32":
                import ctypes
                kernel32 = ctypes.windll.kernel32
                c_ulonglong = ctypes.c_ulonglong

                class MEMORYSTATUSEX(ctypes.Structure):
                    _fields_ = [
                        ("dwLength", ctypes.c_ulong),
                        ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", c_ulonglong),
                        ("ullAvailPhys", c_ulonglong),
                        ("ullTotalPageFile", c_ulonglong),
                        ("ullAvailPageFile", c_ulonglong),
                        ("ullTotalVirtual", c_ulonglong),
                        ("ullAvailVirtual", c_ulonglong),
                        ("ullAvailExtendedVirtual", c_ulonglong),
                    ]

                stat = MEMORYSTATUSEX()
                stat.dwLength = ctypes.sizeof(stat)
                kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
                total_ram_gb = round(stat.ullTotalPhys / (1024 ** 3), 1)
```

**Step 2: Commit**

```
fix: add Windows RAM detection fallback via ctypes
```

---

### Task 11: Fix STT Warmup Double-Open in create_voice_session

This was partially addressed in Task 2 but the pattern at voice.py:625-632 needs the same fix. Verify the Task 2 change covers this.

---

### Task 12: Fix electron-builder-update.yml Publish Repo

**Files:**
- Modify: `apps/electron/electron-builder-update.yml:~82`

**Step 1: Change publish.repo to the public releases repo**

```yaml
publish:
  provider: github
  owner: JongoDB
  repo: verbatim-studio-releases
```

**Step 2: Commit**

```
fix: point electron-builder-update.yml publish to releases repo
```

---

### Task 13: Verify TypeScript Compilation

**Step 1: Run TypeScript check across the Electron package**

Run: `cd apps/electron && npx tsc --noEmit`

Ensure no type errors from any of the changes.

---

## Summary

| Task | Severity | Est. | Description |
|------|----------|------|-------------|
| 1 | Critical | 5m | Fix UPDATE_DIR hardcoded /tmp path |
| 2 | Critical | 5m | Fix temp WAV double-open (Windows file lock) |
| 3 | Critical | 5m | Verify mlx imports are lazy in qwen3_tts.py |
| 4 | Critical | 5m | Add LiveKit packages to Windows requirements |
| 5 | Critical | 15m | Platform guards on voice/TTS endpoints |
| 6 | High | 5m | Fix path traversal check (is_relative_to) |
| 7 | High | 5m | Fix migration script forward-slash paths |
| 8 | Medium | 5m | Fix deprecated asyncio.get_event_loop() |
| 9 | Medium | 5m | Add Linux XDG data directory |
| 10 | Medium | 10m | Add Windows RAM detection fallback |
| 12 | Medium | 5m | Fix electron-builder publish repo |
| 13 | — | 2m | Verify TypeScript compilation |

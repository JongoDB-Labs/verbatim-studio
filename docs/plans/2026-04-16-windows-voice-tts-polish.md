# Windows Voice TTS & Platform Polish — v0.62.0

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add cross-platform TTS via Kokoro ONNX so voice chat works on Windows, plus remaining P2/P3 polish items from the Windows audit.

**Architecture:** New `KokoroOnnxTTSService` adapter implementing `ITTSService`, dispatched via platform check in `_get_tts_service()`. The voice agent pipeline stays unchanged — only the factory and catalog need updates.

**Tech Stack:** kokoro-onnx 0.5.0, ONNX Runtime (CPU/CUDA), Python 3.12

---

## Task 1: Create Kokoro ONNX TTS Adapter

**Files:**
- Create: `packages/backend/adapters/ai/kokoro_onnx_tts.py`

Implements `ITTSService` using `kokoro-onnx`. Same singleton + lazy-load pattern as `qwen3_tts.py`.

**Key API:**
- `Kokoro(model_path, voices_path)` — constructor
- `kokoro.create(text, voice, speed, lang)` → `(np.ndarray, 24000)`
- `kokoro.get_voices()` → `list[str]`
- `kokoro.create_stream(text, voice, speed, lang)` → async iterator of `(samples, sample_rate)`
- Output: 24000 Hz float32 numpy array (same sample rate as Qwen3)

---

## Task 2: Add Windows TTS Models to Catalog

**Files:**
- Modify: `packages/backend/core/tts_catalog.py`

Add Kokoro ONNX entry with `"platform": "win32"`. Model files live on HuggingFace at `onnx-community/Kokoro-82M-v1.0-ONNX`.

---

## Task 3: Platform Dispatch in Voice Agent

**Files:**
- Modify: `packages/backend/services/voice_agent.py:761-790`

Update `_get_tts_service()` to import the correct adapter based on `sys.platform`.

---

## Task 4: Add kokoro-onnx to Windows Requirements

**Files:**
- Modify: `scripts/requirements-ml-windows.txt`

Add `kokoro-onnx>=0.4.0` to the voice section.

---

## Task 5: Fix navigator.platform → electronAPI.platform

**Files:**
- Modify: `packages/frontend/src/app/App.tsx:34`
- Modify: `packages/frontend/src/components/layout/Sidebar.tsx:11`
- Modify: `packages/frontend/src/components/layout/TitleBar.tsx:4`

Replace `navigator.platform.toLowerCase().includes('mac')` with `window.electronAPI?.platform === 'darwin'`.

---

## Task 6: Add `protocols` Key for Deep Links in Electron Builder

**Files:**
- Modify: `apps/electron/package.json`

Add `protocols` entry so `verbatim://` is registered by the NSIS installer on Windows.

---

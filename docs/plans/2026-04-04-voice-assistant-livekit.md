# Full-Duplex Voice Assistant (LiveKit Agents + Qwen3-TTS) — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a full-duplex voice chat assistant to Verbatim Studio that can converse naturally while querying transcripts, documents, and recordings via the existing tool system.

**Architecture:** LiveKit Agents SDK orchestrates a modular STT→LLM→TTS pipeline. LiveKit Server handles WebRTC transport (full-duplex audio, echo cancellation, interruptions). The voice agent reuses existing Whisper STT and Granite LLM services, adds Qwen3-TTS for speech output, and exposes existing tools (search, summarize, analyze) to the LLM during voice conversations. Electron embeds LiveKit Server as a sidecar process alongside the Python backend.

**Tech Stack:** Python (LiveKit Agents SDK, Qwen3-TTS MLX/PyTorch), TypeScript (livekit-client), Go (LiveKit Server binary), Electron (sidecar process management), FastAPI (voice session endpoints)

**GitHub Issue:** #122

---

## Architecture Overview

```
┌─ Electron ──────────────────────────────────────────────────────┐
│  BackendManager (existing)          LiveKitManager (new)        │
│    └─ uvicorn :52780                  └─ livekit-server :7880   │
└─────────────────────────────────────────────────────────────────┘
         ↕ HTTP/SSE                          ↕ WebRTC
┌─ Python Backend ────────────────────────────────────────────────┐
│  api/routes/voice.py                                            │
│    └─ POST /voice/sessions → LiveKit room token                 │
│                                                                 │
│  services/voice_agent.py (LiveKit AgentSession)                 │
│    ├─ STT: Whisper (reuse existing transcription engine)        │
│    ├─ LLM: Granite (reuse existing LlamaCppAIService)           │
│    ├─ TTS: Qwen3-TTS (new adapter: adapters/ai/qwen3_tts.py)   │
│    └─ Tools: Existing ToolRegistry (search, summarize, etc.)    │
└─────────────────────────────────────────────────────────────────┘
         ↕ WebRTC
┌─ Frontend ──────────────────────────────────────────────────────┐
│  components/ai/VoiceChatPanel.tsx                                │
│    ├─ livekit-client SDK (audio pub/sub)                        │
│    ├─ Mode toggle on ChatPanel (text ↔ voice)                   │
│    ├─ Audio level meters (reuse AudioLevelMeter)                │
│    └─ Tool call display + live transcript                       │
└─────────────────────────────────────────────────────────────────┘
```

---

## Prerequisites & Dependencies

**Python packages (new):**
- `livekit-agents` — Agent framework
- `livekit-plugins-silero` — VAD (voice activity detection)
- `livekit` — Server SDK (room token generation)
- `mlx-lm` — Already present if MLX Whisper is installed (macOS)
- Qwen3-TTS MLX weights — Downloaded via model management

**npm packages (new):**
- `livekit-client` — WebRTC client SDK

**Binary (new):**
- `livekit-server` — Go binary, bundled in Electron resources

**Existing services reused (no changes needed):**
- `adapters/transcription/` — Whisper STT (MLX or CTranslate2)
- `adapters/ai/llama_cpp.py` — Granite LLM
- `services/tool_registry.py` + `services/tool_executor.py` — All 11 existing tools
- `services/context_manager.py` — Token budget allocation

---

## Phase 1: TTS Model Service

Add Qwen3-TTS as a new model type alongside the existing LLM model system.

### Task 1: TTS Service Interface

**Files:**
- Create: `packages/backend/core/interfaces/tts.py`
- Modify: `packages/backend/core/interfaces/__init__.py`

**Step 1: Define the ITTSService interface**

```python
# packages/backend/core/interfaces/tts.py
from abc import ABC, abstractmethod
from typing import AsyncIterator


class ITTSService(ABC):
    """Interface for text-to-speech services."""

    @abstractmethod
    async def synthesize(self, text: str, voice: str | None = None) -> bytes:
        """Synthesize text to audio bytes (WAV/PCM)."""
        ...

    @abstractmethod
    async def synthesize_stream(self, text: str, voice: str | None = None) -> AsyncIterator[bytes]:
        """Stream audio chunks as they're generated."""
        ...

    @abstractmethod
    async def list_voices(self) -> list[dict]:
        """Return available voices with metadata."""
        ...

    @abstractmethod
    async def is_available(self) -> bool:
        """Check if TTS model is loaded and ready."""
        ...

    @abstractmethod
    async def load(self, model_path: str) -> None:
        """Load a TTS model from disk."""
        ...

    @abstractmethod
    async def unload(self) -> None:
        """Unload TTS model and free memory."""
        ...
```

**Step 2: Export from interfaces package**

Add `from core.interfaces.tts import ITTSService` to `__init__.py`.

**Step 3: Commit**

```bash
git add packages/backend/core/interfaces/tts.py packages/backend/core/interfaces/__init__.py
git commit -m "feat: add ITTSService interface for text-to-speech adapters"
```

---

### Task 2: TTS Model Catalog

**Files:**
- Create: `packages/backend/core/tts_catalog.py`

**Step 1: Define the TTS model catalog**

```python
# packages/backend/core/tts_catalog.py
"""TTS model catalog — Qwen3-TTS variants."""

TTS_CATALOG: dict[str, dict] = {
    "qwen3-tts-0.6b": {
        "repo": "Qwen/Qwen3-TTS-0.6B-MLX-8bit",
        "label": "Qwen3-TTS 0.6B (Lite)",
        "description": "Fast, lightweight TTS. ~2-3 GB RAM. Good for most use cases.",
        "size_bytes": 700_000_000,
        "ram_gb": 3,
        "tier": "basic",
        "platform": "darwin",  # MLX = macOS only
    },
    "qwen3-tts-1.7b": {
        "repo": "Qwen/Qwen3-TTS-1.7B-MLX-8bit",
        "label": "Qwen3-TTS 1.7B (Pro)",
        "description": "Higher quality, more natural speech. ~6 GB RAM.",
        "size_bytes": 2_000_000_000,
        "ram_gb": 6,
        "tier": "basic",
        "platform": "darwin",
    },
}
```

**Step 2: Commit**

```bash
git add packages/backend/core/tts_catalog.py
git commit -m "feat: add TTS model catalog with Qwen3-TTS variants"
```

---

### Task 3: Qwen3-TTS Adapter

**Files:**
- Create: `packages/backend/adapters/ai/qwen3_tts.py`

**Step 1: Implement the adapter**

This wraps the Qwen3-TTS MLX inference. The model uses a codec-based architecture that generates audio tokens, then decodes them to waveform.

```python
# packages/backend/adapters/ai/qwen3_tts.py
"""Qwen3-TTS adapter using MLX for Apple Silicon."""

import logging
from typing import AsyncIterator

from core.interfaces.tts import ITTSService

logger = logging.getLogger(__name__)

# Module-level singleton (same pattern as llama_cpp.py)
_tts_service: "Qwen3TTSService | None" = None
_tts_model_path: str | None = None


def get_tts_service(model_path: str) -> "Qwen3TTSService":
    """Get or create cached TTS service instance."""
    global _tts_service, _tts_model_path
    if _tts_service and _tts_model_path == model_path:
        return _tts_service
    if _tts_service:
        cleanup_tts_service()
    _tts_service = Qwen3TTSService(model_path)
    _tts_model_path = model_path
    return _tts_service


def cleanup_tts_service() -> None:
    """Unload TTS model and free memory."""
    global _tts_service, _tts_model_path
    if _tts_service:
        import gc
        _tts_service._model = None
        _tts_service._processor = None
        _tts_service = None
        _tts_model_path = None
        gc.collect()
        try:
            import mlx.core as mx
            mx.metal.clear_cache()
        except Exception:
            pass
        logger.info("TTS model unloaded")


class Qwen3TTSService(ITTSService):
    """Qwen3-TTS via MLX on Apple Silicon."""

    def __init__(self, model_path: str):
        self._model_path = model_path
        self._model = None
        self._processor = None
        self._sample_rate = 24000

    def _ensure_loaded(self):
        if self._model is not None:
            return
        logger.info("Loading Qwen3-TTS from %s", self._model_path)
        # Lazy import — MLX may not be installed on all platforms
        from transformers import AutoProcessor
        import mlx.core as mx
        from mlx_lm import load

        self._model, _ = load(self._model_path)
        self._processor = AutoProcessor.from_pretrained(
            self._model_path, trust_remote_code=True
        )
        logger.info("Qwen3-TTS loaded successfully")

    async def synthesize(self, text: str, voice: str | None = None) -> bytes:
        """Generate complete audio for text."""
        import asyncio
        return await asyncio.to_thread(self._synthesize_sync, text, voice)

    def _synthesize_sync(self, text: str, voice: str | None = None) -> bytes:
        """Synchronous synthesis — runs in thread pool."""
        self._ensure_loaded()
        import numpy as np
        import struct

        # Build prompt with voice description or default
        voice_desc = voice or "A calm, clear English-speaking narrator"
        prompt = f"<|voice_preset|>{voice_desc}<|text|>{text}"

        inputs = self._processor(prompt, return_tensors="np")
        # Generate audio tokens → decode to waveform
        # (Exact API depends on the Qwen3-TTS MLX implementation)
        audio = self._model.generate_audio(inputs, max_new_tokens=4096)

        # Convert float32 audio to 16-bit PCM WAV
        audio_np = np.array(audio, dtype=np.float32)
        audio_int16 = (audio_np * 32767).astype(np.int16)
        return self._to_wav(audio_int16)

    def _to_wav(self, audio_int16) -> bytes:
        """Convert int16 PCM array to WAV bytes."""
        import struct
        import io

        num_samples = len(audio_int16)
        data_size = num_samples * 2  # 16-bit = 2 bytes per sample
        buf = io.BytesIO()
        # WAV header
        buf.write(b'RIFF')
        buf.write(struct.pack('<I', 36 + data_size))
        buf.write(b'WAVE')
        buf.write(b'fmt ')
        buf.write(struct.pack('<I', 16))  # chunk size
        buf.write(struct.pack('<H', 1))   # PCM
        buf.write(struct.pack('<H', 1))   # mono
        buf.write(struct.pack('<I', self._sample_rate))
        buf.write(struct.pack('<I', self._sample_rate * 2))  # byte rate
        buf.write(struct.pack('<H', 2))   # block align
        buf.write(struct.pack('<H', 16))  # bits per sample
        buf.write(b'data')
        buf.write(struct.pack('<I', data_size))
        buf.write(audio_int16.tobytes())
        return buf.getvalue()

    async def synthesize_stream(self, text: str, voice: str | None = None) -> AsyncIterator[bytes]:
        """Stream audio chunks. Initial implementation yields full audio as single chunk."""
        audio = await self.synthesize(text, voice)
        yield audio

    async def list_voices(self) -> list[dict]:
        return [
            {"id": "default", "name": "Default Narrator", "description": "A calm, clear English-speaking narrator"},
            {"id": "warm-female", "name": "Warm Female", "description": "A warm, friendly female voice"},
            {"id": "professional-male", "name": "Professional Male", "description": "A professional male broadcaster voice"},
        ]

    async def is_available(self) -> bool:
        return self._model is not None

    async def load(self, model_path: str) -> None:
        self._model_path = model_path
        self._model = None  # Force reload
        self._ensure_loaded()

    async def unload(self) -> None:
        cleanup_tts_service()
```

**Note:** The exact Qwen3-TTS MLX inference API may differ from what's shown above. The adapter must be validated against the actual `Qwen3-TTS-MLX-WebUI-Enhanced` repo's API during implementation. The key contract is: text in → PCM audio bytes out.

**Step 2: Commit**

```bash
git add packages/backend/adapters/ai/qwen3_tts.py
git commit -m "feat: add Qwen3-TTS MLX adapter with singleton lifecycle"
```

---

### Task 4: TTS Download & Activation Endpoints

**Files:**
- Create: `packages/backend/api/routes/voice.py`
- Modify: `packages/backend/api/main.py` (register router)

**Step 1: Create voice routes with TTS model management**

```python
# packages/backend/api/routes/voice.py
"""Voice assistant endpoints — TTS model management and session control."""

import json
import logging
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.tts_catalog import TTS_CATALOG
from persistence.database import get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/voice", tags=["voice"])

ACTIVE_TTS_FILE = "active_tts_model.json"


class TTSModelInfo(BaseModel):
    id: str
    label: str
    description: str
    size_bytes: int
    ram_gb: int
    downloaded: bool
    active: bool


class VoiceStatusResponse(BaseModel):
    tts_available: bool
    tts_model: str | None
    voices: list[dict]


@router.get("/status")
async def get_voice_status() -> VoiceStatusResponse:
    """Check voice assistant readiness."""
    active_model = _get_active_tts_model()
    tts_available = False
    voices = []

    if active_model:
        try:
            from adapters.ai.qwen3_tts import get_tts_service
            model_path = settings.MODELS_DIR / "tts" / active_model
            svc = get_tts_service(str(model_path))
            tts_available = await svc.is_available()
            if tts_available:
                voices = await svc.list_voices()
        except Exception:
            pass

    return VoiceStatusResponse(
        tts_available=tts_available,
        tts_model=active_model,
        voices=voices,
    )


@router.get("/tts/models")
async def list_tts_models() -> list[TTSModelInfo]:
    """List available TTS models with download status."""
    active = _get_active_tts_model()
    result = []
    for model_id, info in TTS_CATALOG.items():
        model_dir = settings.MODELS_DIR / "tts" / model_id
        result.append(TTSModelInfo(
            id=model_id,
            label=info["label"],
            description=info["description"],
            size_bytes=info["size_bytes"],
            ram_gb=info["ram_gb"],
            downloaded=model_dir.exists(),
            active=(model_id == active),
        ))
    return result


@router.post("/tts/models/{model_id}/download")
async def download_tts_model(model_id: str) -> StreamingResponse:
    """Download a TTS model from HuggingFace. Streams progress via SSE."""
    if model_id not in TTS_CATALOG:
        raise HTTPException(status_code=404, detail=f"Unknown TTS model: {model_id}")

    info = TTS_CATALOG[model_id]
    model_dir = settings.MODELS_DIR / "tts" / model_id

    async def _stream():
        try:
            yield f"data: {json.dumps({'status': 'downloading', 'progress': 0})}\n\n"

            from huggingface_hub import snapshot_download
            snapshot_download(
                repo_id=info["repo"],
                local_dir=str(model_dir),
                local_dir_use_symlinks=False,
            )

            # Auto-activate if no TTS model is active
            if not _get_active_tts_model():
                _set_active_tts_model(model_id)

            yield f"data: {json.dumps({'status': 'complete', 'model_id': model_id})}\n\n"
        except Exception as e:
            logger.exception("TTS model download failed")
            yield f"data: {json.dumps({'status': 'error', 'error': str(e)})}\n\n"

    return StreamingResponse(_stream(), media_type="text/event-stream")


@router.post("/tts/models/{model_id}/activate")
async def activate_tts_model(model_id: str):
    """Set the active TTS model."""
    if model_id not in TTS_CATALOG:
        raise HTTPException(status_code=404, detail=f"Unknown TTS model: {model_id}")
    model_dir = settings.MODELS_DIR / "tts" / model_id
    if not model_dir.exists():
        raise HTTPException(status_code=400, detail="Model not downloaded")
    _set_active_tts_model(model_id)
    return {"message": f"TTS model {model_id} activated"}


def _get_active_tts_model() -> str | None:
    path = settings.MODELS_DIR / ACTIVE_TTS_FILE
    if path.exists():
        data = json.loads(path.read_text())
        return data.get("model_id")
    return None


def _set_active_tts_model(model_id: str):
    path = settings.MODELS_DIR / ACTIVE_TTS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"model_id": model_id}))
```

**Step 2: Register router in main.py**

In `packages/backend/api/main.py`, add:
```python
from api.routes.voice import router as voice_router
app.include_router(voice_router, prefix="/api")
```

**Step 3: Test endpoints manually**

```bash
# List TTS models
curl http://localhost:8001/api/voice/tts/models | python -m json.tool

# Check status
curl http://localhost:8001/api/voice/status | python -m json.tool
```

**Step 4: Commit**

```bash
git add packages/backend/api/routes/voice.py packages/backend/api/main.py
git commit -m "feat: add TTS model management endpoints (download, activate, status)"
```

---

## Phase 2: LiveKit Server Integration

### Task 5: LiveKit Server Binary Management

**Files:**
- Create: `apps/electron/src/main/livekit.ts`
- Modify: `apps/electron/src/main/index.ts` (or `main.ts`)

**Step 1: Create LiveKitManager**

Follow the exact pattern of `BackendManager` in `apps/electron/src/main/backend.ts`:

```typescript
// apps/electron/src/main/livekit.ts
import { ChildProcess, spawn } from 'child_process';
import { EventEmitter } from 'events';
import path from 'path';
import fs from 'fs';
import net from 'net';

const LIVEKIT_PORT = 7880;
const LIVEKIT_RTC_PORT_MIN = 7882;
const LIVEKIT_RTC_PORT_MAX = 7892;
const HEALTH_CHECK_INTERVAL = 15_000;
const MAX_HEALTH_WAIT = 30_000;

export class LiveKitManager extends EventEmitter {
  private process: ChildProcess | null = null;
  private port: number = LIVEKIT_PORT;
  private healthInterval: ReturnType<typeof setInterval> | null = null;
  private apiKey = 'verbatim';          // Local-only, not a secret
  private apiSecret = 'verbatim-local-dev-secret-key-min-32-chars!!';

  async start(): Promise<void> {
    this.port = await this.findAvailablePort(LIVEKIT_PORT);

    const binaryPath = this.getLiveKitBinaryPath();
    if (!fs.existsSync(binaryPath)) {
      console.log('[LiveKit] Binary not found, voice assistant unavailable');
      return;
    }

    const configPath = this.writeConfig();

    this.process = spawn(binaryPath, ['--config', configPath, '--bind', '127.0.0.1'], {
      stdio: ['pipe', 'pipe', 'pipe'],
      env: { ...process.env },
    });

    this.process.stdout?.on('data', (data) => {
      console.log(`[LiveKit] ${data.toString().trim()}`);
    });

    this.process.stderr?.on('data', (data) => {
      console.error(`[LiveKit] ${data.toString().trim()}`);
    });

    this.process.on('exit', (code) => {
      console.log(`[LiveKit] Process exited with code ${code}`);
      this.emit('exit', code);
    });

    await this.waitForHealth();
    this.startHealthCheck();
    this.emit('ready', this.getUrl());
  }

  getUrl(): string {
    return `ws://127.0.0.1:${this.port}`;
  }

  getApiKey(): string { return this.apiKey; }
  getApiSecret(): string { return this.apiSecret; }

  async stop(): Promise<void> {
    if (this.healthInterval) clearInterval(this.healthInterval);
    if (this.process) {
      this.process.kill('SIGTERM');
      this.process = null;
    }
  }

  private getLiveKitBinaryPath(): string {
    const isDev = !process.resourcesPath || process.env.NODE_ENV === 'development';
    const binaryName = process.platform === 'win32' ? 'livekit-server.exe' : 'livekit-server';

    if (isDev) {
      // Dev: expect binary in project root or PATH
      return binaryName;
    }
    return path.join(process.resourcesPath, 'bin', binaryName);
  }

  private writeConfig(): string {
    const configDir = path.join(require('electron').app.getPath('userData'), 'livekit');
    fs.mkdirSync(configDir, { recursive: true });
    const configPath = path.join(configDir, 'config.yaml');

    const config = `
port: ${this.port}
bind_addresses:
  - 127.0.0.1
rtc:
  port_range_start: ${LIVEKIT_RTC_PORT_MIN}
  port_range_end: ${LIVEKIT_RTC_PORT_MAX}
  use_external_ip: false
keys:
  ${this.apiKey}: ${this.apiSecret}
logging:
  level: warn
`;
    fs.writeFileSync(configPath, config.trim());
    return configPath;
  }

  private async waitForHealth(): Promise<void> {
    const start = Date.now();
    while (Date.now() - start < MAX_HEALTH_WAIT) {
      try {
        const res = await fetch(`http://127.0.0.1:${this.port}`);
        if (res.ok) return;
      } catch {}
      await new Promise(r => setTimeout(r, 500));
    }
    throw new Error('LiveKit server failed to start within timeout');
  }

  private startHealthCheck(): void {
    this.healthInterval = setInterval(async () => {
      try {
        await fetch(`http://127.0.0.1:${this.port}`);
      } catch {
        console.warn('[LiveKit] Health check failed');
        this.emit('unhealthy');
      }
    }, HEALTH_CHECK_INTERVAL);
  }

  private findAvailablePort(start: number): Promise<number> {
    return new Promise((resolve, reject) => {
      const server = net.createServer();
      server.listen(start, '127.0.0.1', () => {
        server.close(() => resolve(start));
      });
      server.on('error', () => {
        if (start < start + 10) resolve(this.findAvailablePort(start + 1));
        else reject(new Error('No available ports'));
      });
    });
  }
}
```

**Step 2: Integrate into Electron main process**

In the main entry, add alongside `BackendManager`:

```typescript
import { LiveKitManager } from './livekit';

const livekitManager = new LiveKitManager();

// After backend is ready:
livekitManager.start().catch(err => {
  console.warn('[LiveKit] Voice assistant unavailable:', err.message);
});

// Expose to renderer via IPC
ipcMain.handle('get-livekit-url', () => livekitManager.getUrl());

// On app quit:
app.on('before-quit', async () => {
  await livekitManager.stop();
});
```

**Step 3: Commit**

```bash
git add apps/electron/src/main/livekit.ts apps/electron/src/main/index.ts
git commit -m "feat: add LiveKit server sidecar manager for Electron"
```

**Note:** For dev mode, install LiveKit Server locally: `brew install livekit` (macOS) or download from https://github.com/livekit/livekit/releases. The binary will be bundled into Electron resources during the build step (Phase 6).

---

## Phase 3: Voice Agent Service

### Task 6: LiveKit Agent Worker

**Files:**
- Create: `packages/backend/services/voice_agent.py`

This is the core voice agent that connects to LiveKit as a worker, receives audio from users, processes through STT → LLM → TTS, and sends audio responses back.

**Step 1: Implement the agent worker**

```python
# packages/backend/services/voice_agent.py
"""Voice agent — bridges LiveKit Agents SDK with Verbatim services."""

import asyncio
import logging
from typing import Any

from livekit.agents import (
    Agent,
    AgentSession,
    AutoSubscribe,
    WorkerOptions,
    cli,
    llm as lk_llm,
    stt as lk_stt,
    tts as lk_tts,
)
from livekit.plugins import silero as silero_vad

logger = logging.getLogger(__name__)


class VerbatimSTTAdapter(lk_stt.STT):
    """Adapts existing Whisper transcription engine to LiveKit STT interface."""

    def __init__(self):
        super().__init__()
        from core.factory import get_factory
        self._factory = get_factory()

    async def recognize(self, *, buffer: lk_stt.SpeechBuffer) -> lk_stt.SpeechEvent:
        """Transcribe an audio buffer using existing Whisper engine."""
        import tempfile
        import os

        engine = self._factory.create_transcription_engine()

        # Write audio to temp file (Whisper expects file input)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(buffer.to_wav())
            tmp_path = f.name

        try:
            result = engine.transcribe(tmp_path, {"language": "en"})
            text = " ".join(seg.text for seg in result.segments)
            return lk_stt.SpeechEvent(
                type=lk_stt.SpeechEventType.FINAL_TRANSCRIPT,
                alternatives=[lk_stt.SpeechData(text=text, language="en")],
            )
        finally:
            os.unlink(tmp_path)


class VerbatimLLMAdapter(lk_llm.LLM):
    """Adapts existing LlamaCppAIService to LiveKit LLM interface."""

    def __init__(self):
        super().__init__()

    async def chat(self, *, chat_ctx: lk_llm.ChatContext) -> lk_llm.ChatChunk:
        """Route chat through existing Granite LLM service."""
        from core.factory import get_factory
        from core.interfaces import ChatMessage, ChatOptions

        factory = get_factory()
        ai_service = factory.create_ai_service()

        messages = [
            ChatMessage(
                role=msg.role,
                content=msg.content or "",
            )
            for msg in chat_ctx.messages
        ]

        response = await ai_service.chat(messages, ChatOptions(temperature=0.7, max_tokens=512))
        return lk_llm.ChatChunk(
            choices=[lk_llm.Choice(
                delta=lk_llm.ChoiceDelta(content=response.content, role="assistant"),
                index=0,
            )]
        )


class VerbatimTTSAdapter(lk_tts.TTS):
    """Adapts Qwen3-TTS to LiveKit TTS interface."""

    def __init__(self):
        super().__init__()
        self._service = None

    def _ensure_service(self):
        if self._service:
            return
        from adapters.ai.qwen3_tts import get_tts_service
        from api.routes.voice import _get_active_tts_model
        from core.config import settings

        model_id = _get_active_tts_model()
        if not model_id:
            raise RuntimeError("No TTS model activated")
        model_path = str(settings.MODELS_DIR / "tts" / model_id)
        self._service = get_tts_service(model_path)

    async def synthesize(self, text: str) -> lk_tts.SynthesizedAudio:
        self._ensure_service()
        audio_bytes = await self._service.synthesize(text)
        return lk_tts.SynthesizedAudio(
            data=audio_bytes,
            sample_rate=24000,
            num_channels=1,
        )


class VerbatimVoiceAgent(Agent):
    """The Verbatim voice assistant agent."""

    def __init__(self):
        super().__init__(
            instructions=(
                "You are Max, the Verbatim Studio AI assistant. "
                "You help users with their transcripts, recordings, and documents. "
                "Be concise in voice responses — aim for 1-3 sentences. "
                "If the user asks about their data, use the available tools to search and retrieve it."
            ),
        )


def create_agent_session() -> AgentSession:
    """Create a configured voice agent session."""
    return AgentSession(
        stt=VerbatimSTTAdapter(),
        llm=VerbatimLLMAdapter(),
        tts=VerbatimTTSAdapter(),
        vad=silero_vad.VAD.load(),
        agent=VerbatimVoiceAgent(),
    )
```

**Note:** The exact LiveKit Agents SDK API may differ from what's shown. The adapters need to be validated against the current `livekit-agents` package version. The key pattern is: wrap existing services behind LiveKit's plugin interfaces.

**Step 2: Commit**

```bash
git add packages/backend/services/voice_agent.py
git commit -m "feat: add voice agent with STT/LLM/TTS adapters for LiveKit"
```

---

### Task 7: Voice Session Endpoints

**Files:**
- Modify: `packages/backend/api/routes/voice.py`

**Step 1: Add session creation endpoint**

Add to the existing `voice.py`:

```python
from livekit.api import AccessToken, VideoGrants

LIVEKIT_URL = "ws://127.0.0.1:7880"
LIVEKIT_API_KEY = "verbatim"
LIVEKIT_API_SECRET = "verbatim-local-dev-secret-key-min-32-chars!!"


class VoiceSessionResponse(BaseModel):
    token: str
    url: str
    room_name: str


@router.post("/sessions", response_model=VoiceSessionResponse)
async def create_voice_session():
    """Create a voice chat session. Returns a LiveKit room token."""
    import uuid

    room_name = f"voice-{uuid.uuid4().hex[:8]}"
    participant_name = "user"

    # Generate access token for the user
    token = (
        AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET)
        .with_identity(participant_name)
        .with_name("User")
        .with_grants(VideoGrants(
            room_join=True,
            room=room_name,
            can_publish=True,
            can_subscribe=True,
        ))
        .to_jwt()
    )

    # Start the agent worker for this room
    from services.voice_agent import create_agent_session
    session = create_agent_session()
    # Agent connects to the room asynchronously
    asyncio.create_task(_start_agent_in_room(session, room_name))

    return VoiceSessionResponse(
        token=token,
        url=LIVEKIT_URL,
        room_name=room_name,
    )


async def _start_agent_in_room(session, room_name: str):
    """Connect the voice agent to a LiveKit room."""
    try:
        from livekit import rtc
        room = rtc.Room()
        await room.connect(LIVEKIT_URL, _generate_agent_token(room_name))
        await session.start(room=room)
        logger.info("Voice agent started in room %s", room_name)
    except Exception:
        logger.exception("Failed to start voice agent in room %s", room_name)


def _generate_agent_token(room_name: str) -> str:
    """Generate a token for the agent participant."""
    return (
        AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET)
        .with_identity("max-agent")
        .with_name("Max")
        .with_grants(VideoGrants(
            room_join=True,
            room=room_name,
            can_publish=True,
            can_subscribe=True,
            agent=True,
        ))
        .to_jwt()
    )
```

**Step 2: Test**

```bash
curl -X POST http://localhost:8001/api/voice/sessions | python -m json.tool
```

**Step 3: Commit**

```bash
git add packages/backend/api/routes/voice.py
git commit -m "feat: add voice session creation with LiveKit room tokens"
```

---

## Phase 4: Voice Tool Integration

### Task 8: Expose Existing Tools to Voice Agent

**Files:**
- Modify: `packages/backend/services/voice_agent.py`

The existing 11 tools in `ToolRegistry` can be bridged to the voice agent. The most useful subset for voice:

| Tool | Voice Use Case |
|------|----------------|
| `project_search` | "What did Mark say about the budget?" |
| `global_search` | "Find all mentions of quarterly review" |
| `summarize_transcript` | "Summarize my last meeting" |
| `get_recording_info` | "How long was today's recording?" |
| `context_tool` | "Read me the first part of the transcript" |

**Step 1: Add tool definitions to VerbatimVoiceAgent**

Update the agent to include tool schemas that the LLM can invoke during voice conversations. The tools route through the existing `ToolExecutor`:

```python
# In voice_agent.py, add to VerbatimVoiceAgent.__init__:

from services.tool_registry import tool_registry

# Convert ToolDefs to LiveKit function tools
self._verbatim_tools = []
voice_tool_names = ["project_search", "global_search", "summarize_transcript",
                     "get_recording_info", "context_tool"]
for tool_def in tool_registry.list_tools():
    if tool_def.name in voice_tool_names:
        self._verbatim_tools.append(tool_def)
```

**Step 2: Implement tool execution callback**

```python
async def on_tool_call(self, tool_name: str, args: dict) -> str:
    """Execute a Verbatim tool and return the result as text."""
    from services.tool_registry import tool_registry
    tool = tool_registry.get_tool(tool_name)
    if not tool:
        return f"Tool '{tool_name}' not found"
    try:
        result = await tool.handler(**args)
        # Truncate for voice — long results should be summarized
        text = str(result)
        if len(text) > 500:
            text = text[:500] + "... (truncated for voice response)"
        return text
    except Exception as e:
        return f"Tool error: {e}"
```

**Step 3: Commit**

```bash
git add packages/backend/services/voice_agent.py
git commit -m "feat: bridge existing tools to voice agent for data-grounded responses"
```

---

## Phase 5: Frontend Voice UI

### Task 9: Install LiveKit Client SDK

**Step 1: Add dependency**

```bash
cd packages/frontend
pnpm add livekit-client @livekit/components-react
```

**Step 2: Commit**

```bash
git add packages/frontend/package.json packages/frontend/pnpm-lock.yaml
git commit -m "chore: add livekit-client and components-react dependencies"
```

---

### Task 10: Voice Chat Panel Component

**Files:**
- Create: `packages/frontend/src/components/ai/VoiceChatPanel.tsx`

**Step 1: Implement the voice chat UI**

```tsx
// packages/frontend/src/components/ai/VoiceChatPanel.tsx
import { useState, useCallback, useRef, useEffect } from 'react';
import {
  Room,
  RoomEvent,
  Track,
  RemoteTrack,
  RemoteTrackPublication,
  RemoteParticipant,
  LocalParticipant,
} from 'livekit-client';
import { api } from '@/lib/api';

type VoiceState = 'idle' | 'connecting' | 'listening' | 'thinking' | 'speaking';

interface VoiceChatPanelProps {
  onClose: () => void;
}

export function VoiceChatPanel({ onClose }: VoiceChatPanelProps) {
  const [state, setState] = useState<VoiceState>('idle');
  const [transcript, setTranscript] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const roomRef = useRef<Room | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);

  const connect = useCallback(async () => {
    setState('connecting');
    setError(null);

    try {
      // Create voice session via backend
      const session = await api.request<{
        token: string;
        url: string;
        room_name: string;
      }>('/api/voice/sessions', { method: 'POST' });

      const room = new Room();
      roomRef.current = room;

      // Handle agent audio tracks
      room.on(RoomEvent.TrackSubscribed, (
        track: RemoteTrack,
        publication: RemoteTrackPublication,
        participant: RemoteParticipant,
      ) => {
        if (track.kind === Track.Kind.Audio) {
          const element = track.attach();
          document.body.appendChild(element);
          audioRef.current = element as HTMLAudioElement;
          setState('speaking');
        }
      });

      room.on(RoomEvent.TrackUnsubscribed, () => {
        setState('listening');
        if (audioRef.current) {
          audioRef.current.remove();
          audioRef.current = null;
        }
      });

      // Connect and publish microphone
      await room.connect(session.url, session.token);
      await room.localParticipant.setMicrophoneEnabled(true);

      setState('listening');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to connect');
      setState('idle');
    }
  }, []);

  const disconnect = useCallback(async () => {
    if (roomRef.current) {
      await roomRef.current.disconnect();
      roomRef.current = null;
    }
    if (audioRef.current) {
      audioRef.current.remove();
      audioRef.current = null;
    }
    setState('idle');
  }, []);

  useEffect(() => {
    return () => {
      disconnect();
    };
  }, [disconnect]);

  const stateLabel: Record<VoiceState, string> = {
    idle: 'Start Voice Chat',
    connecting: 'Connecting...',
    listening: 'Listening...',
    thinking: 'Thinking...',
    speaking: 'Max is speaking...',
  };

  const stateColor: Record<VoiceState, string> = {
    idle: 'bg-gray-100 dark:bg-gray-800',
    connecting: 'bg-yellow-50 dark:bg-yellow-900/20',
    listening: 'bg-green-50 dark:bg-green-900/20',
    thinking: 'bg-blue-50 dark:bg-blue-900/20',
    speaking: 'bg-purple-50 dark:bg-purple-900/20',
  };

  return (
    <div className="flex flex-col items-center gap-4 p-6">
      {/* State indicator */}
      <div className={`rounded-full w-24 h-24 flex items-center justify-center ${stateColor[state]} transition-colors`}>
        {state === 'listening' && (
          <div className="w-4 h-4 rounded-full bg-green-500 animate-pulse" />
        )}
        {state === 'speaking' && (
          <div className="flex gap-1 items-end h-8">
            {[0, 1, 2, 3, 4].map(i => (
              <div
                key={i}
                className="w-1 bg-purple-500 rounded-full animate-bounce"
                style={{ animationDelay: `${i * 0.1}s`, height: `${12 + Math.random() * 20}px` }}
              />
            ))}
          </div>
        )}
        {state === 'thinking' && (
          <svg className="w-8 h-8 text-blue-500 animate-spin" fill="none" viewBox="0 0 24 24">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
          </svg>
        )}
        {state === 'idle' && (
          <svg className="w-8 h-8 text-gray-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
            <path strokeLinecap="round" strokeLinejoin="round" d="M19 11a7 7 0 01-7 7m0 0a7 7 0 01-7-7m7 7v4m0 0H8m4 0h4m-4-8a3 3 0 01-3-3V5a3 3 0 116 0v6a3 3 0 01-3 3z" />
          </svg>
        )}
      </div>

      <p className="text-sm text-gray-600 dark:text-gray-400">{stateLabel[state]}</p>

      {error && (
        <p className="text-sm text-red-500">{error}</p>
      )}

      {/* Controls */}
      <div className="flex gap-2">
        {state === 'idle' ? (
          <button
            onClick={connect}
            className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 text-sm"
          >
            Start Voice Chat
          </button>
        ) : (
          <button
            onClick={disconnect}
            className="px-4 py-2 bg-red-600 text-white rounded-lg hover:bg-red-700 text-sm"
          >
            End
          </button>
        )}
        <button
          onClick={onClose}
          className="px-4 py-2 bg-gray-200 dark:bg-gray-700 rounded-lg hover:bg-gray-300 dark:hover:bg-gray-600 text-sm"
        >
          Close
        </button>
      </div>

      {/* Live transcript */}
      {transcript.length > 0 && (
        <div className="w-full mt-4 space-y-1 text-sm text-gray-600 dark:text-gray-400 max-h-40 overflow-y-auto">
          {transcript.map((line, i) => (
            <p key={i}>{line}</p>
          ))}
        </div>
      )}
    </div>
  );
}
```

**Step 2: Commit**

```bash
git add packages/frontend/src/components/ai/VoiceChatPanel.tsx
git commit -m "feat: add VoiceChatPanel with LiveKit connection and state management"
```

---

### Task 11: Integrate Voice Toggle into ChatPanel

**Files:**
- Modify: `packages/frontend/src/components/ai/ChatHeader.tsx`
- Modify: `packages/frontend/src/components/ai/ChatPanel.tsx`

**Step 1: Add voice toggle button to ChatHeader**

Add a microphone button alongside the existing action buttons:

```tsx
{/* Voice chat toggle */}
<button
  onClick={onToggleVoice}
  className={`p-1.5 rounded-md transition-colors ${
    voiceActive
      ? 'bg-purple-100 text-purple-600 dark:bg-purple-900/30 dark:text-purple-400'
      : 'text-gray-400 hover:text-gray-600 dark:hover:text-gray-300'
  }`}
  title={voiceActive ? 'Switch to text' : 'Switch to voice'}
>
  <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
    <path strokeLinecap="round" strokeLinejoin="round" d="M19 11a7 7 0 01-7 7m0 0a7 7 0 01-7-7m7 7v4m0 0H8m4 0h4m-4-8a3 3 0 01-3-3V5a3 3 0 116 0v6a3 3 0 01-3 3z" />
  </svg>
</button>
```

**Step 2: Add voice mode state to ChatPanel**

In `ChatPanel.tsx`, add state and conditional rendering:

```tsx
const [voiceMode, setVoiceMode] = useState(false);

// In render:
{voiceMode ? (
  <VoiceChatPanel onClose={() => setVoiceMode(false)} />
) : (
  // ... existing text chat UI
)}
```

**Step 3: Test via `pnpm dev`**

Start frontend and backend dev servers. Verify the voice toggle appears in the chat header and clicking it shows the VoiceChatPanel.

**Step 4: Commit**

```bash
git add packages/frontend/src/components/ai/ChatHeader.tsx packages/frontend/src/components/ai/ChatPanel.tsx
git commit -m "feat: add voice/text mode toggle to Max chat panel"
```

---

## Phase 6: Packaging & Deployment

### Task 12: Bundle LiveKit Server Binary

**Files:**
- Modify: `.github/workflows/build-electron.yml`
- Modify: `apps/electron/electron-builder.yml` (or equivalent config)

**Step 1: Add LiveKit download step to CI**

Add a build step that downloads the appropriate LiveKit Server binary for each platform:

```yaml
- name: Download LiveKit Server
  run: |
    LIVEKIT_VERSION="1.7.2"  # Pin to tested version
    if [ "${{ matrix.os }}" = "macos-14" ]; then
      curl -L "https://github.com/livekit/livekit/releases/download/v${LIVEKIT_VERSION}/livekit_${LIVEKIT_VERSION}_darwin_arm64.tar.gz" | tar xz
      mkdir -p build/resources/bin
      mv livekit-server build/resources/bin/
    elif [ "${{ matrix.os }}" = "windows-latest" ]; then
      curl -L "https://github.com/livekit/livekit/releases/download/v${LIVEKIT_VERSION}/livekit_${LIVEKIT_VERSION}_windows_amd64.zip" -o livekit.zip
      unzip livekit.zip
      mkdir -p build/resources/bin
      mv livekit-server.exe build/resources/bin/
    fi
```

**Step 2: Include in electron-builder extraResources**

```yaml
extraResources:
  - from: "../../build/resources/bin"
    to: "bin"
    filter: ["livekit-server*"]
```

**Step 3: Add Python voice dependencies to requirements**

Add to `scripts/requirements-core.txt`:

```
livekit-agents>=0.12.0
livekit-plugins-silero>=0.7.0
livekit>=0.18.0
```

**Step 4: Commit**

```bash
git add .github/workflows/build-electron.yml apps/electron/electron-builder.yml scripts/requirements-core.txt
git commit -m "feat: bundle LiveKit server and voice agent dependencies"
```

---

### Task 13: Docker Compose for Enterprise

**Files:**
- Modify: `docker-compose.yml` (in enterprise repo)

**Step 1: Add LiveKit Server service**

```yaml
livekit:
  image: livekit/livekit-server:v1.7
  ports:
    - "7880:7880"
    - "7882-7892:7882-7892/udp"
  volumes:
    - ./livekit-config.yaml:/etc/livekit.yaml
  command: ["--config", "/etc/livekit.yaml"]
  restart: unless-stopped

redis:
  image: redis:7-alpine
  restart: unless-stopped
```

**Step 2: Commit** (in enterprise repo)

---

## Testing Checklist

### Phase 1 (TTS)
- [ ] `GET /api/voice/tts/models` returns catalog entries
- [ ] `POST /api/voice/tts/models/qwen3-tts-0.6b/download` downloads model
- [ ] `GET /api/voice/status` shows TTS availability after download
- [ ] TTS synthesis produces valid WAV audio

### Phase 2 (LiveKit)
- [ ] LiveKit Server starts on port 7880 in dev mode
- [ ] Health check endpoint responds
- [ ] LiveKit Manager handles port conflicts gracefully

### Phase 3 (Voice Agent)
- [ ] Voice session creation returns valid LiveKit token
- [ ] Agent connects to room and receives audio
- [ ] STT adapter transcribes user speech
- [ ] LLM generates contextual response
- [ ] TTS produces spoken response
- [ ] Full-duplex: user can speak while agent is responding

### Phase 4 (Tools)
- [ ] "What did Mark say?" → agent uses `project_search` → answers from transcript
- [ ] "Summarize my last meeting" → agent uses `summarize_transcript`
- [ ] Tool results are spoken naturally (not raw JSON)

### Phase 5 (Frontend)
- [ ] Voice toggle appears in ChatHeader
- [ ] Clicking toggle shows VoiceChatPanel
- [ ] Start Voice Chat → mic permission → connection → listening state
- [ ] Agent responses play through speakers
- [ ] End button disconnects cleanly
- [ ] State indicators (listening/thinking/speaking) update correctly

### Phase 6 (Packaging)
- [ ] Electron build includes LiveKit binary
- [ ] App starts with both backend and LiveKit running
- [ ] Docker Compose starts LiveKit + Redis alongside backend

---

## Open Questions (Resolve During Implementation)

1. **Memory pressure:** Whisper + Granite + Qwen3-TTS simultaneously on 16GB M1 — test and measure. May need model unloading strategy (unload TTS when not in voice mode).

2. **Qwen3-TTS MLX API:** The adapter in Task 3 is based on the expected API. Validate against the actual `Qwen3-TTS-MLX-WebUI-Enhanced` repo during implementation.

3. **LiveKit Agents SDK version:** The agent code in Task 6 targets `livekit-agents>=0.12.0`. Pin to a specific tested version.

4. **Echo cancellation:** LiveKit's WebRTC handles AEC at the browser level. Verify this works in Electron's Chromium (it should, but test with actual hardware).

5. **Voice cloning:** Deferred to post-MVP. The ITTSService interface supports a `voice` parameter, so the adapter is ready when custom voices are added.

6. **Conversation persistence:** Voice conversations should be saved to the existing conversation system. Add this after the core pipeline works — capture the STT output as conversation messages.

7. **LiveKit Server embedding:** For v1 desktop, bundling the Go binary is simplest. Evaluate if a Go → C library approach is worth the complexity reduction vs. process management.

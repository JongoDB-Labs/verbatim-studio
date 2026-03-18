"""AI analysis endpoints."""

import asyncio
import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.factory import get_factory
from core.interfaces import ChatMessage, ChatOptions
from core.model_catalog import MODEL_CATALOG
from persistence.database import get_db
from persistence.models import Document, Recording, Transcript, Segment

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/ai", tags=["ai"])

# ── LLM Dependencies ──────────────────────────────────────────────────

LLM_PYTHON_DEPS = [
    "llama-cpp-python>=0.2.0",
]


def _check_llm_deps_installed(force_refresh: bool = False) -> bool:
    """Check if llama-cpp-python is installed.

    Args:
        force_refresh: If True, invalidate import caches first (useful after pip install)
    """
    import importlib
    import sys

    if force_refresh:
        # Clear llama_cpp from sys.modules if it exists (so it can be reimported fresh)
        for mod_name in list(sys.modules.keys()):
            if mod_name.startswith('llama_cpp'):
                del sys.modules[mod_name]
        # Invalidate import caches to detect newly installed packages
        importlib.invalidate_caches()

    try:
        from llama_cpp import Llama
        return True
    except (ImportError, ModuleNotFoundError):
        return False


def _install_llm_deps_sync() -> tuple[bool, str]:
    """Install LLM Python dependencies synchronously."""
    logger.info("Installing LLM Python dependencies: %s", LLM_PYTHON_DEPS)

    try:
        python_exe = sys.executable
        pip_cmd = [python_exe, "-m", "pip", "install", "--upgrade"] + LLM_PYTHON_DEPS

        result = subprocess.run(
            pip_cmd,
            capture_output=True,
            text=True,
            timeout=600,  # 10 minute timeout
        )

        if result.returncode == 0:
            logger.info("LLM Python dependencies installed successfully")
            return True, "Dependencies installed"
        else:
            logger.error("pip install failed: %s", result.stderr)
            return False, f"pip install failed: {result.stderr[:500]}"

    except subprocess.TimeoutExpired:
        logger.error("LLM dependency installation timed out")
        return False, "Installation timed out"
    except Exception as e:
        logger.exception("Failed to install LLM dependencies")
        return False, str(e)

# ── Active model tracking ──────────────────────────────────────────────

def _active_model_path() -> Path:
    """Path to the JSON file tracking which model is active."""
    return settings.MODELS_DIR / "active_model.json"


def _read_active_model() -> str | None:
    """Read the currently active model ID from disk."""
    p = _active_model_path()
    if p.exists():
        try:
            data = json.loads(p.read_text())
            return data.get("model_id")
        except (json.JSONDecodeError, OSError):
            pass
    return None


def _write_active_model(model_id: str) -> None:
    """Persist the active model ID."""
    settings.ensure_directories()
    _active_model_path().write_text(json.dumps({"model_id": model_id}))


def _model_file_path(model_id: str) -> Path | None:
    """Return the local file path for a catalog model, or None if not in catalog."""
    entry = MODEL_CATALOG.get(model_id)
    if not entry:
        return None
    return settings.MODELS_DIR / entry["filename"]


def _ensure_active_model_loaded() -> None:
    """Ensure settings.AI_MODEL_PATH is set from the active model if available.

    This bridges the gap between the persisted active_model.json and the
    runtime settings used by the AI service factory.
    """
    if settings.AI_MODEL_PATH:
        logger.debug("AI model path already set: %s", settings.AI_MODEL_PATH)
        return  # Already configured (e.g., via env var)

    active_id = _read_active_model()
    if not active_id:
        logger.debug("No active model ID found in active_model.json")
        return

    file_path = _model_file_path(active_id)
    if not file_path:
        logger.warning("Model ID '%s' not found in MODEL_CATALOG", active_id)
        return

    if not file_path.exists():
        logger.warning("Model file does not exist: %s", file_path)
        return

    settings.AI_MODEL_PATH = str(file_path)
    logger.info("Loaded active AI model from disk: %s", file_path.name)


class ChatRequest(BaseModel):
    """Request model for chat."""

    message: str
    context: str | None = None  # Optional transcript context
    temperature: float = 0.7
    max_tokens: int | None = None


class ChatResponse(BaseModel):
    """Response model for chat."""

    content: str
    model: str


class HistoryMessage(BaseModel):
    """A message in chat history."""
    role: str
    content: str


class MultiChatRequest(BaseModel):
    """Request model for multi-transcript chat."""
    message: str
    recording_ids: list[str] = []  # Recording IDs (frontend sends these, we look up transcripts)
    document_ids: list[str] = []  # Document IDs for context
    file_context: str | None = None  # Text content from uploaded files (temporary)
    history: list[HistoryMessage] = []
    compressed_memory: str | None = None  # Compressed summary of older conversation turns
    web_search_enabled: bool = False
    conversation_id: str | None = None  # For web cache lookups
    temperature: float = Field(default=0.7, ge=0, le=2)
    general_mode: bool = False  # When True, Max answers any question


class StreamToken(BaseModel):
    """A single token in a streaming response.

    This model documents the SSE response format for /chat/multi.
    The endpoint yields JSON dicts matching this schema.
    """
    token: str | None = None
    done: bool = False
    model: str | None = None
    error: str | None = None


MAX_SYSTEM_PROMPT_GENERAL = """You are Max, a helpful AI assistant. You are knowledgeable about Verbatim Studio, \
a privacy-first local transcription application, and can help users with it. \
However, you can also assist with any topic the user asks about.

Guidelines:
- Be concise and factual
- For Verbatim Studio questions: reference specific UI locations
- For transcript analysis: quote specific passages when relevant
- For general questions: provide helpful, accurate answers
"""

MAX_SYSTEM_PROMPT = """You are Max, the Verbatim Studio assistant. You have two roles:
1. Help users navigate and use Verbatim Studio (the app)
2. Analyze transcripts and documents when attached

Verbatim Studio is a privacy-first local transcription application. All processing happens on the user's device.

When in doubt about what the user is asking (e.g., "how do I export?" or "where are my files?"), assume they're asking about Verbatim Studio, not general knowledge.

Guidelines:
- Be concise and factual
- For app help: reference specific UI locations (e.g., "Navigate to Settings > Transcription")
- For transcript analysis: quote specific passages when relevant
- When comparing transcripts, label which one (e.g., "In Transcript A...")
- If asked about something not in attached content, say so
"""

# Help context injected when help-related keywords are detected
MAX_HELP_CONTEXT = """
Verbatim Studio Navigation:
- Dashboard: Overview stats, recent items, quick actions, onboarding tour
- Recordings: Upload audio/video, apply templates, transcribe, bulk operations
- Projects: Organize recordings with custom project types and metadata
- Documents: Upload PDFs/images, OCR text extraction, page-anchored notes
- Chats: View and resume saved AI conversations
- Live: Real-time microphone transcription (BETA)
- Search: Keyword or semantic (AI-powered) search across all content
- Files: Browse folder structure, move files between storage locations
- Settings: Transcription, AI models, storage locations, backup/restore

Core Features:
- Recording Templates: Custom metadata fields (text, date, number, dropdown) for recordings
- Project Types: Custom metadata schemas for projects
- Tags: Color-coded labels for filtering recordings
- Speakers: Auto-detected (diarization), can rename, merge, assign colors
- Highlights: Color-code segments (yellow, green, blue, red, purple, orange)
- Comments: Add notes to transcript segments
- Notes: Anchor to timestamps (recordings) or pages (documents)
- Semantic Search: AI-powered meaning-based search using embeddings
- AI Analysis: Summarization, sentiment, entity extraction, action items

Storage Options:
- Local: File system storage
- Network: SMB (Windows shares), NFS
- Cloud (OAuth): Google Drive, OneDrive, Dropbox

Common Tasks:
- Transcribe: Recordings > Upload > (optional) Select template > Transcribe
- Edit transcript: Click segment text to edit, speaker label to reassign
- Highlight: Click highlight icon on segment, choose color
- Merge speakers: In speakers panel, merge duplicates
- Export: Transcript view > Export > TXT/SRT/VTT/DOCX/PDF
- Semantic search: Search page > Enter query > Select "Semantic" match type
- Cloud storage: Settings > Storage > Add > Select provider > Authenticate
- Backup: Settings > Backup/Archive > Export (creates .vbz file)

Keyboard Shortcuts (transcript view):
- Space/K: Play/Pause
- J/L: Skip back/forward 10s
- Arrow keys: Skip 5s or jump segments
- Shift+,/.: Skip 1s

Troubleshooting:
- Model not loading: Settings > AI/LLM > Download a model
- Transcription failed: Try smaller model (tiny/base), check file format, switch to CPU
- No speakers: Enable diarization in Settings > Transcription (needs HuggingFace token)
- Cloud auth expired: Settings > Storage > Re-authenticate
- Semantic search empty: Embeddings generate automatically, may take time
"""


class SummarizationResponse(BaseModel):
    """Response model for transcript summarization."""

    summary: str
    key_points: list[str] | None
    action_items: list[str] | None
    topics: list[str] | None
    named_entities: list[str] | None


class AnalysisResponse(BaseModel):
    """Response model for transcript analysis."""

    analysis_type: str
    content: dict


class AIStatusResponse(BaseModel):
    """Response model for AI service status."""

    available: bool
    provider: str
    model_loaded: bool
    model_path: str | None
    models: list[dict]
    deps_installed: bool = True  # Whether llama-cpp-python is installed


class AIModelInfo(BaseModel):
    """Info about a catalog model."""

    id: str
    label: str
    description: str
    repo: str
    filename: str
    size_bytes: int
    is_default: bool
    downloaded: bool
    active: bool
    download_path: str | None
    tier: str | None = None
    ram_gb: int | None = None
    legacy_note: str | None = None


class AIModelListResponse(BaseModel):
    """Response listing all catalog models."""

    models: list[AIModelInfo]


# ── Model management endpoints ─────────────────────────────────────────

@router.get("/models", response_model=AIModelListResponse)
async def list_models() -> AIModelListResponse:
    """Return the model catalog merged with download/active status."""
    active_id = _read_active_model()
    items: list[AIModelInfo] = []

    for model_id, entry in MODEL_CATALOG.items():
        file_path = _model_file_path(model_id)
        downloaded = file_path is not None and file_path.exists()
        items.append(AIModelInfo(
            id=model_id,
            label=entry["label"],
            description=entry["description"],
            repo=entry["repo"],
            filename=entry["filename"],
            size_bytes=entry["size_bytes"],
            is_default=entry.get("default", False),
            downloaded=downloaded,
            active=(model_id == active_id),
            download_path=str(file_path) if downloaded else None,
            tier=entry.get("tier"),
            ram_gb=entry.get("ram_gb"),
            legacy_note=entry.get("legacy_note"),
        ))

    return AIModelListResponse(models=items)


# Track in-progress downloads so we don't start duplicates
_download_tasks: dict[str, asyncio.Task] = {}


@router.post("/models/{model_id}/download")
async def download_model(model_id: str) -> StreamingResponse:
    """Download a model from HuggingFace, streaming byte-level progress via SSE."""
    entry = MODEL_CATALOG.get(model_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Model not in catalog")

    file_path = _model_file_path(model_id)
    if file_path and file_path.exists():
        raise HTTPException(status_code=409, detail="Model already downloaded")

    settings.ensure_directories()

    async def _stream_progress():
        import httpx

        # First, ensure LLM dependencies are installed
        if not _check_llm_deps_installed():
            yield f"data: {json.dumps({'status': 'progress', 'phase': 'deps_install', 'message': 'Installing llama-cpp-python (this may take a few minutes)...'})}\n\n"
            loop = asyncio.get_event_loop()
            success, msg = await loop.run_in_executor(None, _install_llm_deps_sync)
            if not success:
                yield f"data: {json.dumps({'status': 'error', 'error': f'Failed to install LLM dependencies: {msg}'})}\n\n"
                return
            # Force refresh the import cache to detect newly installed package
            if not _check_llm_deps_installed(force_refresh=True):
                yield f"data: {json.dumps({'status': 'error', 'error': 'LLM dependencies installed but not importable. Please restart the app.'})}\n\n"
                return
            yield f"data: {json.dumps({'status': 'progress', 'phase': 'deps_install', 'message': 'LLM dependencies installed successfully'})}\n\n"

        try:
            from huggingface_hub import hf_hub_url

            url = hf_hub_url(repo_id=entry["repo"], filename=entry["filename"])
        except ImportError:
            yield f"data: {json.dumps({'status': 'error', 'error': 'huggingface-hub is not installed. Install with: pip install huggingface-hub'})}\n\n"
            return

        yield f"data: {json.dumps({'status': 'starting', 'model_id': model_id})}\n\n"

        total_bytes = entry["size_bytes"]
        dest = settings.MODELS_DIR / entry["filename"]
        tmp_dest = dest.with_suffix(".part")

        try:
            downloaded = 0
            last_pct = -1
            async with httpx.AsyncClient(follow_redirects=True, timeout=None) as client:
                async with client.stream("GET", url) as resp:
                    resp.raise_for_status()
                    content_length = resp.headers.get("content-length")
                    if content_length:
                        total_bytes = int(content_length)

                    with open(tmp_dest, "wb") as f:
                        async for chunk in resp.aiter_bytes(chunk_size=256 * 1024):
                            f.write(chunk)
                            downloaded += len(chunk)
                            pct = int(downloaded * 100 / total_bytes) if total_bytes else 0
                            # Emit progress every 1%
                            if pct != last_pct:
                                last_pct = pct
                                yield f"data: {json.dumps({'status': 'progress', 'model_id': model_id, 'downloaded_bytes': downloaded, 'total_bytes': total_bytes})}\n\n"

            # Verify download completed and rename .part → final filename
            if not tmp_dest.exists():
                yield f"data: {json.dumps({'status': 'error', 'error': 'Download incomplete - file not found'})}\n\n"
                return

            # Verify file size (allow 1% tolerance for headers/metadata)
            actual_size = tmp_dest.stat().st_size
            if total_bytes > 0 and actual_size < total_bytes * 0.99:
                tmp_dest.unlink()
                yield f"data: {json.dumps({'status': 'error', 'error': f'Download incomplete - got {actual_size} bytes, expected {total_bytes}'})}\n\n"
                return

            tmp_dest.rename(dest)

            yield f"data: {json.dumps({'status': 'complete', 'model_id': model_id, 'path': str(dest)})}\n\n"

            # Auto-activate if no model is currently active
            if _read_active_model() is None:
                _write_active_model(model_id)
                settings.AI_MODEL_PATH = str(dest)
                yield f"data: {json.dumps({'status': 'activated', 'model_id': model_id})}\n\n"

        except Exception as exc:
            logger.exception("Model download failed")
            # Clean up partial file
            if tmp_dest.exists():
                tmp_dest.unlink()
            yield f"data: {json.dumps({'status': 'error', 'error': str(exc)})}\n\n"

    return StreamingResponse(_stream_progress(), media_type="text/event-stream")


@router.post("/models/{model_id}/activate")
async def activate_model(model_id: str):
    """Set a downloaded model as the active model."""
    entry = MODEL_CATALOG.get(model_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Model not in catalog")

    file_path = _model_file_path(model_id)
    if file_path is None or not file_path.exists():
        raise HTTPException(status_code=400, detail="Model is not downloaded")

    _write_active_model(model_id)
    settings.AI_MODEL_PATH = str(file_path)

    return {"status": "activated", "model_id": model_id, "path": str(file_path)}


@router.post("/models/{model_id}/deactivate")
async def deactivate_model(model_id: str):
    """Deactivate the active model and unload it from memory.

    This frees the ~5 GB of RAM used by the LLM without deleting the model file.
    The model can be re-activated later via the activate endpoint.
    """
    entry = MODEL_CATALOG.get(model_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Model not in catalog")

    active_id = _read_active_model()
    if model_id != active_id:
        raise HTTPException(status_code=400, detail="Model is not currently active")

    # Clear active model state
    _active_model_path().unlink(missing_ok=True)
    settings.AI_MODEL_PATH = None

    # Unload from memory
    from adapters.ai.llama_cpp import cleanup_llama_service
    cleanup_llama_service()

    logger.info("Deactivated and unloaded model %s", model_id)
    return {"status": "deactivated", "model_id": model_id}


@router.delete("/models/{model_id}")
async def delete_model(model_id: str):
    """Delete a downloaded model file. If it's the active model, deactivate first."""
    entry = MODEL_CATALOG.get(model_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Model not in catalog")

    file_path = _model_file_path(model_id)
    if file_path is None or not file_path.exists():
        raise HTTPException(status_code=404, detail="Model file not found")

    # If this is the active model, clear the active state
    active_id = _read_active_model()
    if model_id == active_id:
        _active_model_path().unlink(missing_ok=True)
        settings.AI_MODEL_PATH = None

    file_path.unlink()
    return {"status": "deleted", "model_id": model_id}


@router.post("/install-deps")
async def install_llm_dependencies():
    """Install LLM Python dependencies (llama-cpp-python).

    This is automatically called during model download, but can also be
    called manually if dependencies were uninstalled.
    """
    if _check_llm_deps_installed(force_refresh=True):
        return {"status": "already_installed", "message": "LLM dependencies are already installed"}

    async def _stream_install():
        yield f"data: {json.dumps({'status': 'starting', 'message': 'Installing llama-cpp-python...'})}\n\n"

        # Run installation in thread pool to not block
        loop = asyncio.get_event_loop()
        success, msg = await loop.run_in_executor(None, _install_llm_deps_sync)

        if success:
            # Force refresh to make the module importable
            if _check_llm_deps_installed(force_refresh=True):
                yield f"data: {json.dumps({'status': 'complete', 'message': 'LLM dependencies installed and ready.'})}\n\n"
            else:
                yield f"data: {json.dumps({'status': 'complete', 'message': 'LLM dependencies installed. Please restart the app if AI features are not available.'})}\n\n"
        else:
            yield f"data: {json.dumps({'status': 'error', 'error': msg})}\n\n"

    return StreamingResponse(_stream_install(), media_type="text/event-stream")


async def get_transcript_text(db: AsyncSession, transcript_id: str) -> str:
    """Get full transcript text from segments."""
    result = await db.execute(
        select(Segment)
        .where(Segment.transcript_id == transcript_id)
        .order_by(Segment.segment_index)
    )
    segments = result.scalars().all()

    if not segments:
        raise HTTPException(status_code=404, detail="Transcript not found or empty")

    # Format with speaker labels if available
    lines = []
    for seg in segments:
        if seg.speaker:
            lines.append(f"[{seg.speaker}]: {seg.text}")
        else:
            lines.append(seg.text)

    return "\n".join(lines)


@router.get("/status", response_model=AIStatusResponse)
async def get_ai_status() -> AIStatusResponse:
    """Get AI service status and available models."""
    _ensure_active_model_loaded()
    factory = get_factory()
    ai_service = factory.create_ai_service()

    available = await ai_service.is_available()
    info = await ai_service.get_service_info()
    models = await ai_service.get_available_models() if available else []

    return AIStatusResponse(
        available=available,
        provider=str(info.get("name", "unknown")),
        model_loaded=bool(info.get("model_loaded", False)),
        model_path=str(info.get("model_path")) if info.get("model_path") else None,
        models=models,
        deps_installed=_check_llm_deps_installed(),
    )


@router.get("/debug")
async def get_ai_debug_info():
    """Get detailed debug information about AI service state.

    This endpoint helps diagnose why AI features might be unavailable.
    """
    # Get persisted state
    active_model_file = _active_model_path()
    active_model_exists = active_model_file.exists()
    active_model_id = _read_active_model()

    # Get expected file path for active model
    expected_file_path = None
    expected_file_exists = False
    if active_model_id:
        expected_file_path = _model_file_path(active_model_id)
        if expected_file_path:
            expected_file_exists = expected_file_path.exists()

    # Get runtime settings BEFORE loading
    settings_before = settings.AI_MODEL_PATH

    # Load active model
    _ensure_active_model_loaded()

    # Get runtime settings AFTER loading
    settings_after = settings.AI_MODEL_PATH

    # Create service and check availability
    factory = get_factory()
    ai_service = factory.create_ai_service()
    available = await ai_service.is_available()
    service_info = await ai_service.get_service_info()

    # Check llama_cpp installation
    llama_cpp_installed = False
    try:
        from llama_cpp import Llama
        llama_cpp_installed = True
    except ImportError:
        pass

    return {
        "persisted_state": {
            "active_model_file": str(active_model_file),
            "active_model_file_exists": active_model_exists,
            "active_model_id": active_model_id,
            "expected_model_path": str(expected_file_path) if expected_file_path else None,
            "expected_model_exists": expected_file_exists,
        },
        "runtime_state": {
            "settings_ai_model_path_before_load": settings_before,
            "settings_ai_model_path_after_load": settings_after,
        },
        "service_state": {
            "llama_cpp_installed": llama_cpp_installed,
            "available": available,
            "service_info": service_info,
        },
        "model_catalog": list(MODEL_CATALOG.keys()),
    }


@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    """Send a chat message to the AI."""
    _ensure_active_model_loaded()
    factory = get_factory()
    ai_service = factory.create_ai_service()

    if not await ai_service.is_available():
        raise HTTPException(
            status_code=503,
            detail="AI service not available. Please configure a model path.",
        )

    messages = []
    max_tokens = request.max_tokens or 512

    # Add context if provided, with truncation
    if request.context:
        context = request.context
        original_len = len(context)
        if hasattr(ai_service, '_truncate_to_fit'):
            prefix = "You are a helpful assistant. Here is some context:\n\n"
            overhead = ai_service._count_tokens(prefix + request.message) + 30
            available = ai_service._n_ctx - overhead - max_tokens
            if available < 100:
                available = 100
            context = ai_service._truncate_to_fit(context, available)
        truncation_note = ""
        if len(context) < original_len:
            truncation_note = (
                "\n\nNote: The provided context was too large to include in full. "
                "Some content has been omitted."
            )
        messages.append(ChatMessage(
            role="system",
            content=f"You are a helpful assistant. Here is some context:\n\n{context}{truncation_note}",
        ))

    messages.append(ChatMessage(role="user", content=request.message))

    options = ChatOptions(
        temperature=request.temperature,
        max_tokens=max_tokens,
    )

    try:
        response = await ai_service.chat(messages, options)
        return ChatResponse(content=response.content, model=response.model)
    except Exception as e:
        logger.exception("Chat request failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/chat/stream")
async def chat_stream(request: ChatRequest) -> StreamingResponse:
    """Send a streaming chat message to the AI."""
    _ensure_active_model_loaded()
    factory = get_factory()
    ai_service = factory.create_ai_service()

    if not await ai_service.is_available():
        raise HTTPException(
            status_code=503,
            detail="AI service not available. Please configure a model path.",
        )

    messages = []
    if request.context:
        messages.append(ChatMessage(
            role="system",
            content=f"You are a helpful assistant. Here is some context:\n\n{request.context}",
        ))
    messages.append(ChatMessage(role="user", content=request.message))

    options = ChatOptions(
        temperature=request.temperature,
        max_tokens=request.max_tokens,
    )

    async def generate():
        try:
            async for chunk in ai_service.chat_stream(messages, options):
                yield f"data: {chunk.content}\n\n"
                if chunk.finish_reason:
                    yield f"data: [DONE]\n\n"
        except Exception as e:
            logger.exception("Stream chat failed")
            yield f"data: [ERROR] {str(e)}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


@router.post("/chat/multi")
async def chat_multi_stream(
    request: MultiChatRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> StreamingResponse:
    """Stream a chat response with multi-transcript context."""
    _ensure_active_model_loaded()
    factory = get_factory()
    ai_service = factory.create_ai_service()

    if not await ai_service.is_available():
        raise HTTPException(
            status_code=503,
            detail="AI service not available. Please configure a model path.",
        )

    # Build context from recordings (look up transcripts by recording_id)
    context_parts = []
    label_index = 0

    if request.recording_ids:
        for recording_id in request.recording_ids:
            label = chr(65 + label_index)  # A, B, C, ...
            try:
                # Get recording for title
                recording_result = await db.execute(
                    select(Recording).where(Recording.id == recording_id)
                )
                recording = recording_result.scalar_one_or_none()
                if not recording:
                    logger.warning("Recording not found: %s", recording_id)
                    continue

                # Get transcript via recording_id
                transcript_result = await db.execute(
                    select(Transcript).where(Transcript.recording_id == recording_id)
                )
                transcript = transcript_result.scalar_one_or_none()
                if not transcript:
                    logger.warning("No transcript for recording: %s", recording_id)
                    continue

                text = await get_transcript_text(db, transcript.id)
                title = recording.title
                context_parts.append(f"=== Transcript {label}: {title} ===\n{text}\n")
                label_index += 1
            except Exception as e:
                logger.warning("Could not load recording %s: %s", recording_id, e)
                continue

    # Add documents to context
    if request.document_ids:
        for doc_id in request.document_ids:
            label = chr(65 + label_index)  # Continue labeling from transcripts
            try:
                doc = await db.get(Document, doc_id)
                if doc and doc.extracted_text:
                    context_parts.append(f"=== Document {label}: {doc.title} ===\n{doc.extracted_text}\n")
                    label_index += 1
                elif doc and doc.extracted_markdown:
                    context_parts.append(f"=== Document {label}: {doc.title} ===\n{doc.extracted_markdown}\n")
                    label_index += 1
                else:
                    logger.warning("Document %s has no extracted text", doc_id)
            except Exception as e:
                logger.warning("Could not load document %s: %s", doc_id, e)
                continue

    # Add temporary file content if provided
    if request.file_context:
        label = chr(65 + label_index)
        context_parts.append(f"=== Uploaded File {label} ===\n{request.file_context}\n")
        label_index += 1

    # --- Web search (if enabled and query detected) ---
    web_results_text = None
    web_sources = []
    if request.web_search_enabled:
        from services.web_search import (
            extract_search_query,
            create_search_provider,
            format_results_for_context,
            WebSearchConfig,
            WebSearchResult,
        )
        search_query = extract_search_query(request.message)
        if search_query:
            try:
                results = None

                # Check cache first (if conversation is saved)
                if request.conversation_id:
                    from persistence.models import ChatWebCache
                    cached = await db.execute(
                        select(ChatWebCache).where(
                            ChatWebCache.conversation_id == request.conversation_id,
                            ChatWebCache.query == search_query.text,
                        )
                    )
                    cached_result = cached.scalar_one_or_none()
                    if cached_result:
                        cached_data = json.loads(cached_result.results)
                        results = [
                            WebSearchResult(
                                title=r["title"],
                                url=r["url"],
                                content=r["content"],
                                relevance_score=r.get("relevance_score", 0.0),
                            )
                            for r in cached_data
                        ]

                # If not cached, query the search provider
                if results is None:
                    from core.config import settings as app_settings
                    config = WebSearchConfig(
                        provider=app_settings.WEB_SEARCH_PROVIDER or "tavily",
                        api_key=app_settings.WEB_SEARCH_API_KEY or "",
                    )
                    provider = create_search_provider(config)
                    results = await provider.search(search_query.text)

                    # Save to cache for this conversation
                    if request.conversation_id and results:
                        from persistence.models import ChatWebCache
                        cache_entry = ChatWebCache(
                            conversation_id=request.conversation_id,
                            query=search_query.text,
                            results=json.dumps([{
                                "title": r.title, "url": r.url,
                                "content": r.content, "relevance_score": r.relevance_score,
                            } for r in results]),
                        )
                        db.add(cache_entry)
                        await db.commit()

                if results:
                    web_results_text = format_results_for_context(results, max_tokens=1000)
                    web_sources = [{"title": r.title, "url": r.url} for r in results]
            except Exception as e:
                logger.warning("Web search failed: %s", e)

    # Detect if user is asking for help with the app
    def is_help_intent(message: str) -> bool:
        """Detect if the user message is asking for help with Verbatim Studio."""
        msg_lower = message.lower()

        # Help-related phrases
        help_phrases = [
            "how do i", "how can i", "how to", "where is", "where do i", "where can i",
            "what is", "what does", "what are", "can i", "can you help",
            "help me", "help with", "show me", "tell me how", "guide", "tutorial",
            "i can't find", "i don't know how", "having trouble", "not working",
        ]

        # App-specific terms (strong signal)
        app_terms = [
            # Navigation
            "sidebar", "dashboard", "settings", "recordings", "projects", "documents",
            "chats", "files", "browser", "navigation", "menu",
            # Core features
            "transcribe", "transcript", "transcription", "export", "import",
            "upload", "download", "model", "whisper", "diarization",
            "speaker", "segment", "highlight", "comment", "note",
            # Organization
            "project", "tag", "template", "recording template", "project type",
            "metadata", "custom field",
            # Search
            "search", "semantic", "keyword", "embedding", "find",
            # AI
            "max", "chat", "assistant", "summarize", "analyze", "sentiment",
            # Storage
            "storage", "cloud", "google drive", "onedrive", "dropbox", "oauth",
            "backup", "restore", "archive",
            # Settings
            "shortcut", "keyboard", "theme", "language", "huggingface",
            # Live
            "live", "microphone", "real-time", "realtime",
            # Documents
            "ocr", "pdf", "document",
        ]

        # Check for help phrases
        has_help_phrase = any(phrase in msg_lower for phrase in help_phrases)

        # Check for app terms
        has_app_term = any(term in msg_lower for term in app_terms)

        # If no content is attached and user asks a question, likely asking about app
        no_attachments = not context_parts
        is_question = "?" in message or has_help_phrase

        return has_help_phrase or (has_app_term and is_question) or (no_attachments and is_question)

    # Build system message
    system_content = MAX_SYSTEM_PROMPT_GENERAL if request.general_mode else MAX_SYSTEM_PROMPT

    # Inject help context if help intent detected
    if is_help_intent(request.message):
        system_content += MAX_HELP_CONTEXT

    max_response_tokens = 1024

    # --- Memory compression ---
    from services.conversation_memory import ConversationMemoryService
    from services.context_manager import ContextManager

    memory_service = ConversationMemoryService()
    n_ctx = ai_service._n_ctx if hasattr(ai_service, '_n_ctx') else 8192
    ctx_mgr = ContextManager(n_ctx=n_ctx, max_response_tokens=max_response_tokens)

    compressed_memory = request.compressed_memory
    history_dicts = [{"role": m.role, "content": m.content} for m in request.history]

    # Check if history needs compression
    if memory_service.should_compress(history_dicts):
        old_msgs, recent_msgs = memory_service.split_history(history_dicts)
        if old_msgs:
            # Compress old messages via the LLM
            compress_prompt = memory_service.build_compression_prompt(old_msgs, compressed_memory)
            compress_system = memory_service.get_system_prompt(compressed_memory)
            try:
                compress_response = await ai_service.chat(
                    [
                        ChatMessage(role="system", content=compress_system),
                        ChatMessage(role="user", content=compress_prompt),
                    ],
                    ChatOptions(temperature=0.3, max_tokens=300),
                )
                compressed_memory = compress_response.content
            except Exception:
                logger.warning("Memory compression failed, keeping full history")
                recent_msgs = history_dicts
    else:
        recent_msgs = history_dicts

    # Build context string from attached items
    context_str = None
    if context_parts:
        context_header = f"You have access to {len(context_parts)} attached item(s) (transcripts, documents, or files):\n\n"
        full_context = "\n".join(context_parts)
        original_context_len = len(full_context)

        # Use ContextManager for budget allocation
        count = ai_service._count_tokens if hasattr(ai_service, '_count_tokens') else lambda t: len(t) // 3
        budget = ctx_mgr.allocate_budget(
            system_tokens=count(system_content),
            user_message_tokens=count(request.message),
            memory_tokens=count(compressed_memory) if compressed_memory else 0,
            web_result_tokens=count(web_results_text) if web_results_text else 0,
            history_tokens=count(" ".join(m["content"] for m in recent_msgs)) if recent_msgs else 0,
            context_tokens=count(context_header + full_context),
        )

        # Truncate context to fit budget
        if hasattr(ai_service, '_truncate_to_fit') and budget.context < count(context_header + full_context):
            full_context = ai_service._truncate_to_fit(full_context, budget.context) if budget.context > 0 else ""

        context_str = context_header + full_context
        if len(full_context) < original_context_len:
            context_str += (
                "\n\nNote: The attached content was too large to include in full. "
                "Some content has been omitted. For complete analysis, use the Summarize or Analyze features."
            )

        # Trim history to fit budget
        while recent_msgs and budget.history < count(" ".join(m["content"] for m in recent_msgs)):
            recent_msgs.pop(0)
    else:
        context_str = "No transcripts or documents are currently attached. Help with general questions about Verbatim Studio."
        # Still need to trim history even without context
        count = ai_service._count_tokens if hasattr(ai_service, '_count_tokens') else lambda t: len(t) // 3
        budget = ctx_mgr.allocate_budget(
            system_tokens=count(system_content),
            user_message_tokens=count(request.message),
            memory_tokens=count(compressed_memory) if compressed_memory else 0,
            web_result_tokens=count(web_results_text) if web_results_text else 0,
            history_tokens=count(" ".join(m["content"] for m in recent_msgs)) if recent_msgs else 0,
            context_tokens=count(context_str),
        )
        while recent_msgs and budget.history < count(" ".join(m["content"] for m in recent_msgs)):
            recent_msgs.pop(0)

    # Build final messages using ContextManager
    messages = ctx_mgr.build_messages(
        system_content=system_content,
        compressed_memory=compressed_memory,
        web_results=web_results_text,
        attached_context=context_str,
        recent_history=recent_msgs,
        user_message=request.message,
    )

    options = ChatOptions(temperature=request.temperature, max_tokens=max_response_tokens)

    async def generate():
        try:
            async for chunk in ai_service.chat_stream(messages, options):
                if chunk.content:
                    yield f"data: {json.dumps({'token': chunk.content})}\n\n"
                if chunk.finish_reason:
                    yield f"data: {json.dumps({'done': True, 'compressed_memory': compressed_memory, 'web_sources': web_sources})}\n\n"
        except Exception as e:
            logger.exception("Multi-chat stream failed")
            yield f"data: {json.dumps({'error': str(e)})}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


@router.post("/transcripts/{transcript_id}/summarize", response_model=SummarizationResponse)
async def summarize_transcript(
    transcript_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    temperature: Annotated[float, Query(ge=0, le=2)] = 0.3,
) -> SummarizationResponse:
    """Generate a summary of a transcript."""
    _ensure_active_model_loaded()
    factory = get_factory()
    ai_service = factory.create_ai_service()

    if not await ai_service.is_available():
        raise HTTPException(
            status_code=503,
            detail="AI service not available. Please configure a model path.",
        )

    # Verify transcript exists
    result = await db.execute(select(Transcript).where(Transcript.id == transcript_id))
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Transcript not found")

    transcript_text = await get_transcript_text(db, transcript_id)

    try:
        options = ChatOptions(temperature=temperature, max_tokens=2048)
        result = await ai_service.summarize_transcript(transcript_text, options)

        # Persist summary to transcript record
        await db.execute(
            update(Transcript)
            .where(Transcript.id == transcript_id)
            .values(ai_summary={
                "summary": result.summary,
                "key_points": result.key_points,
                "action_items": result.action_items,
                "topics": result.topics,
                "named_entities": result.named_entities,
            })
        )
        await db.commit()

        return SummarizationResponse(
            summary=result.summary,
            key_points=result.key_points,
            action_items=result.action_items,
            topics=result.topics,
            named_entities=result.named_entities,
        )
    except Exception as e:
        logger.exception("Summarization failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/transcripts/{transcript_id}/analyze", response_model=AnalysisResponse)
async def analyze_transcript(
    transcript_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    analysis_type: Annotated[
        str,
        Query(description="Type of analysis: sentiment, topics, entities, questions, action_items"),
    ] = "topics",
    temperature: Annotated[float, Query(ge=0, le=2)] = 0.3,
) -> AnalysisResponse:
    """Perform analysis on a transcript."""
    _ensure_active_model_loaded()
    factory = get_factory()
    ai_service = factory.create_ai_service()

    if not await ai_service.is_available():
        raise HTTPException(
            status_code=503,
            detail="AI service not available. Please configure a model path.",
        )

    # Verify transcript exists
    result = await db.execute(select(Transcript).where(Transcript.id == transcript_id))
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Transcript not found")

    transcript_text = await get_transcript_text(db, transcript_id)

    valid_types = ["sentiment", "topics", "entities", "questions", "action_items"]
    if analysis_type not in valid_types:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid analysis type. Valid types: {', '.join(valid_types)}",
        )

    try:
        options = ChatOptions(temperature=temperature, max_tokens=1024)
        result = await ai_service.analyze_transcript(transcript_text, analysis_type, options)

        return AnalysisResponse(
            analysis_type=result.analysis_type,
            content=result.content,
        )
    except Exception as e:
        logger.exception("Analysis failed")
        raise HTTPException(status_code=500, detail=str(e))


class ExtractTextResponse(BaseModel):
    """Response model for text extraction."""
    text: str
    format: str
    page_count: int | None = None


@router.post("/extract-text", response_model=ExtractTextResponse)
async def extract_text_from_file(
    file: UploadFile = File(..., description="File to extract text from"),
) -> ExtractTextResponse:
    """Extract text from an uploaded file without saving it permanently.

    Supports: PDF, DOCX, XLSX, PPTX, images (with OCR if available), and text files.
    """
    from tempfile import NamedTemporaryFile
    import mimetypes
    from services.document_processor import document_processor

    # Determine MIME type
    mime_type = file.content_type
    if not mime_type or mime_type == "application/octet-stream":
        # Try to guess from filename
        guessed, _ = mimetypes.guess_type(file.filename or "")
        if guessed:
            mime_type = guessed

    if not mime_type:
        raise HTTPException(status_code=400, detail="Could not determine file type")

    # Check if supported
    supported_mimes = {
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "text/plain",
        "text/markdown",
        "image/png",
        "image/jpeg",
        "image/gif",
        "image/webp",
        "image/tiff",
    }

    if mime_type not in supported_mimes:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type: {mime_type}. Supported: PDF, Word, Excel, PowerPoint, images, text files."
        )

    # Save to temp file
    suffix = Path(file.filename).suffix if file.filename else ""
    try:
        with NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            content = await file.read()
            tmp.write(content)
            tmp_path = Path(tmp.name)

        # Extract text (enable OCR for images)
        enable_ocr = mime_type.startswith("image/")
        result = document_processor.process(tmp_path, mime_type, enable_ocr=enable_ocr)

        # Clean up temp file
        tmp_path.unlink(missing_ok=True)

        extracted_text = result.get("text") or result.get("markdown") or ""

        if not extracted_text.strip():
            raise HTTPException(
                status_code=422,
                detail="Could not extract text from file. It may be a scanned document requiring OCR."
            )

        return ExtractTextResponse(
            text=extracted_text,
            format=result.get("metadata", {}).get("format", mime_type),
            page_count=result.get("page_count"),
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Text extraction failed")
        # Clean up on error
        if 'tmp_path' in locals():
            tmp_path.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"Text extraction failed: {str(e)}")


@router.post("/transcripts/{transcript_id}/ask", response_model=ChatResponse)
async def ask_about_transcript(
    transcript_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    question: Annotated[str, Query(description="Question about the transcript")],
    temperature: Annotated[float, Query(ge=0, le=2)] = 0.5,
) -> ChatResponse:
    """Ask a question about a specific transcript."""
    _ensure_active_model_loaded()
    factory = get_factory()
    ai_service = factory.create_ai_service()

    if not await ai_service.is_available():
        raise HTTPException(
            status_code=503,
            detail="AI service not available. Please configure a model path.",
        )

    # Verify transcript exists
    result = await db.execute(select(Transcript).where(Transcript.id == transcript_id))
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Transcript not found")

    transcript_text = await get_transcript_text(db, transcript_id)

    # Truncate transcript to fit the model's context window
    system_prefix = """You are a helpful assistant that answers questions about transcripts.
Here is the transcript to reference:

"""
    system_suffix = """

Answer questions based on the content of this transcript. If the answer cannot be found in the transcript, say so."""

    max_response_tokens = 512
    # Use the service's truncation if available, otherwise estimate
    if hasattr(ai_service, '_truncate_to_fit'):
        overhead_tokens = ai_service._count_tokens(system_prefix + system_suffix + question) + 40
        available = ai_service._n_ctx - overhead_tokens - max_response_tokens
        if available < 100:
            available = 100
        transcript_text = ai_service._truncate_to_fit(transcript_text, available)

    messages = [
        ChatMessage(
            role="system",
            content=f"{system_prefix}{transcript_text}{system_suffix}",
        ),
        ChatMessage(role="user", content=question),
    ]

    try:
        options = ChatOptions(temperature=temperature, max_tokens=max_response_tokens)
        response = await ai_service.chat(messages, options)
        return ChatResponse(content=response.content, model=response.model)
    except Exception as e:
        logger.exception("Ask about transcript failed")
        raise HTTPException(status_code=500, detail=str(e))

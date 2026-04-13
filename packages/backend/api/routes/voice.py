"""Voice assistant endpoints — TTS model management, status, and sessions."""

import asyncio
import json
import logging
import os
import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_active_project_ids
from core.config import settings
from core.tts_catalog import TTS_CATALOG
from persistence.database import get_db
from persistence.models import Document, Recording, Segment, Transcript

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/voice", tags=["voice"])

# ── LiveKit constants ─────────────────────────────────────────────────

LIVEKIT_URL = os.environ.get("LIVEKIT_URL", "ws://127.0.0.1:7880")
LIVEKIT_API_KEY = "verbatim"
LIVEKIT_API_SECRET = "verbatim-local-dev-secret-key-min-32-chars!!"

# ── Pydantic models ───────────────────────────────────────────────────


class TTSModelInfo(BaseModel):
    """Information about a TTS model from the catalog."""

    id: str
    label: str
    description: str
    size_bytes: int
    ram_gb: int
    downloaded: bool
    active: bool


class VoiceStatusResponse(BaseModel):
    """Current voice/TTS readiness status."""

    tts_available: bool
    tts_model: str | None
    voices: list[dict]
    livekit_available: bool = False
    missing_deps: list[str] = []


class CreateSessionRequest(BaseModel):
    """Request body for creating a voice chat session."""

    voice: str | None = None
    recording_ids: list[str] = []
    document_ids: list[str] = []
    project_ids: list[str] = []


class VoiceSessionResponse(BaseModel):
    """Response from creating a voice chat session."""

    token: str
    url: str
    room_name: str


# ── Active TTS model tracking ────────────────────────────────────────


def _active_tts_model_path() -> Path:
    """Path to the JSON file tracking which TTS model is active."""
    return settings.MODELS_DIR / "active_tts_model.json"


def _get_active_tts_model() -> str | None:
    """Read the currently active TTS model ID from disk."""
    p = _active_tts_model_path()
    if p.exists():
        try:
            data = json.loads(p.read_text())
            return data.get("model_id")
        except (json.JSONDecodeError, OSError):
            pass
    return None


def _set_active_tts_model(model_id: str) -> None:
    """Persist the active TTS model ID."""
    settings.ensure_directories()
    _active_tts_model_path().write_text(json.dumps({"model_id": model_id}))


def _tts_model_dir(model_id: str) -> Path:
    """Return the local directory path for a TTS model."""
    return settings.MODELS_DIR / "tts" / model_id


def _is_tts_model_downloaded(model_id: str) -> bool:
    """Check if a TTS model has been downloaded."""
    model_dir = _tts_model_dir(model_id)
    # A model is considered downloaded if its directory exists and is non-empty
    return model_dir.exists() and any(model_dir.iterdir())


# ── Endpoints ─────────────────────────────────────────────────────────


@router.get("/status")
async def voice_status() -> VoiceStatusResponse:
    """Check if TTS is loaded and ready.

    Lightweight check — does NOT load the model, just checks if one
    is active and its files are present.
    """
    active_id = _get_active_tts_model()
    tts_available = False
    voices: list[dict] = []

    if active_id and active_id in TTS_CATALOG:
        tts_available = _is_tts_model_downloaded(active_id)

        if tts_available:
            # Return preset voices from the adapter without loading the model
            from adapters.ai.qwen3_tts import _PRESET_VOICES

            voices = list(_PRESET_VOICES)

    # Check for required dependencies
    missing_deps: list[str] = []
    livekit_available = False

    try:
        from livekit.api import AccessToken  # noqa: F401
        livekit_available = True
    except ImportError:
        missing_deps.append("livekit-api")

    try:
        import mlx_audio  # noqa: F401
    except ImportError:
        missing_deps.append("mlx-audio[tts]")

    return VoiceStatusResponse(
        tts_available=tts_available,
        tts_model=active_id if tts_available else None,
        voices=voices,
        livekit_available=livekit_available,
        missing_deps=missing_deps,
    )


@router.get("/tts/models")
async def list_tts_models() -> list[TTSModelInfo]:
    """List all TTS models from the catalog with download/active status."""
    active_id = _get_active_tts_model()
    items: list[TTSModelInfo] = []

    for model_id, entry in TTS_CATALOG.items():
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


@router.post("/tts/models/{model_id}/download")
async def download_tts_model(model_id: str) -> StreamingResponse:
    """Download a TTS model from HuggingFace, streaming progress via SSE.

    Uses ``huggingface_hub.snapshot_download`` to fetch the full model
    repository.  Auto-activates the model if it is the first one downloaded.
    """
    entry = TTS_CATALOG.get(model_id)
    if not entry:
        raise HTTPException(status_code=404, detail="TTS model not in catalog")

    if _is_tts_model_downloaded(model_id):
        raise HTTPException(status_code=409, detail="TTS model already downloaded")

    settings.ensure_directories()

    async def _stream_progress():
        import asyncio
        import threading

        dest_dir = _tts_model_dir(model_id)
        repo_id = entry["repo"]
        total_bytes = entry["size_bytes"]

        yield f"data: {json.dumps({'status': 'starting', 'model_id': model_id})}\n\n"

        try:
            try:
                from huggingface_hub import snapshot_download
            except ImportError:
                yield f"data: {json.dumps({'status': 'error', 'error': 'huggingface-hub is not installed'})}\n\n"
                return

            # Track download progress via a shared state dict
            progress_state = {"downloaded": 0, "total": total_bytes, "done": False, "error": None, "path": None}
            lock = threading.Lock()

            def _do_download():
                try:
                    result = snapshot_download(
                        repo_id=repo_id,
                        local_dir=str(dest_dir),
                        local_dir_use_symlinks=False,
                    )
                    with lock:
                        progress_state["path"] = result
                        progress_state["done"] = True
                except Exception as exc:
                    with lock:
                        progress_state["error"] = str(exc)
                        progress_state["done"] = True

            loop = asyncio.get_running_loop()
            loop.run_in_executor(None, _do_download)

            # Poll progress by checking directory size
            last_pct = -1
            while True:
                await asyncio.sleep(1)

                with lock:
                    done = progress_state["done"]
                    error = progress_state["error"]
                    result_path = progress_state["path"]

                # Estimate progress from disk usage
                downloaded = 0
                if dest_dir.exists():
                    try:
                        downloaded = sum(f.stat().st_size for f in dest_dir.rglob("*") if f.is_file())
                    except OSError:
                        pass

                pct = int(downloaded * 100 / total_bytes) if total_bytes else 0
                pct = min(pct, 99 if not done else 100)

                if pct != last_pct:
                    last_pct = pct
                    yield f"data: {json.dumps({'status': 'progress', 'model_id': model_id, 'downloaded_bytes': downloaded, 'total_bytes': total_bytes})}\n\n"

                if done:
                    break

            if error:
                raise Exception(error)

            if not dest_dir.exists() or not any(dest_dir.iterdir()):
                yield f"data: {json.dumps({'status': 'error', 'error': 'Download completed but model directory is empty'})}\n\n"
                return

            yield f"data: {json.dumps({'status': 'complete', 'model_id': model_id, 'path': str(result_path)})}\n\n"

            if _get_active_tts_model() is None:
                _set_active_tts_model(model_id)
                yield f"data: {json.dumps({'status': 'activated', 'model_id': model_id})}\n\n"

        except Exception as exc:
            logger.exception("TTS model download failed")
            # Clean up partial download
            import shutil

            if dest_dir.exists():
                shutil.rmtree(dest_dir, ignore_errors=True)
            yield f"data: {json.dumps({'status': 'error', 'error': str(exc)})}\n\n"

    return StreamingResponse(_stream_progress(), media_type="text/event-stream")


@router.post("/tts/models/{model_id}/activate")
async def activate_tts_model(model_id: str) -> dict:
    """Set a downloaded TTS model as the active model."""
    entry = TTS_CATALOG.get(model_id)
    if not entry:
        raise HTTPException(status_code=404, detail="TTS model not in catalog")

    if not _is_tts_model_downloaded(model_id):
        raise HTTPException(status_code=400, detail="TTS model is not downloaded")

    _set_active_tts_model(model_id)

    return {"status": "activated", "model_id": model_id, "path": str(_tts_model_dir(model_id))}


# ── Voice session management ──────────────────────────────────────────

# Track active agent tasks so they can be cleaned up if needed
_active_sessions: dict[str, asyncio.Task] = {}


def _generate_user_token(room_name: str, identity: str = "user") -> str:
    """Generate a LiveKit access token for the user participant.

    Args:
        room_name: The room to grant access to.
        identity: Participant identity (default "user").

    Returns:
        JWT access token string.

    Raises:
        HTTPException: If the livekit-api package is not installed.
    """
    try:
        from livekit.api import AccessToken, VideoGrants
    except ImportError:
        raise HTTPException(
            status_code=503,
            detail=(
                "livekit-api is not installed. "
                "Install with: pip install livekit-api"
            ),
        )

    token = (
        AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET)
        .with_identity(identity)
        .with_name(identity.title())
        .with_grants(VideoGrants(
            room_join=True,
            room=room_name,
            can_publish=True,
            can_subscribe=True,
        ))
    )
    return token.to_jwt()


def _generate_agent_token(room_name: str) -> str:
    """Generate a LiveKit access token for the agent participant.

    The agent gets the same room access as the user but with a distinct
    identity so LiveKit can differentiate the two participants.

    Args:
        room_name: The room to grant access to.

    Returns:
        JWT access token string.
    """
    try:
        from livekit.api import AccessToken, VideoGrants
    except ImportError:
        raise HTTPException(
            status_code=503,
            detail=(
                "livekit-api is not installed. "
                "Install with: pip install livekit-api"
            ),
        )

    token = (
        AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET)
        .with_identity("max-agent")
        .with_name("Max")
        .with_grants(VideoGrants(
            room_join=True,
            room=room_name,
            can_publish=True,
            can_subscribe=True,
        ))
    )
    return token.to_jwt()


async def _start_agent_in_room(
    agent,
    room_name: str,
) -> None:
    """Connect the voice agent to a LiveKit room.

    Runs until the session ends or an error occurs.

    Args:
        agent: A VerbatimVoiceAgent instance.
        room_name: The LiveKit room to join.
    """
    from services.voice_agent import connect_agent_to_room

    agent_token = _generate_agent_token(room_name)

    try:
        await connect_agent_to_room(
            agent=agent,
            room_name=room_name,
            token=agent_token,
            url=LIVEKIT_URL,
        )
    except Exception:
        logger.exception("Agent session failed for room %s", room_name)
    finally:
        # Clean up session tracking
        _active_sessions.pop(room_name, None)
        logger.info("Agent session ended for room %s", room_name)


async def _get_transcript_text(db: AsyncSession, transcript_id: str) -> str:
    """Get full transcript text from segments (mirrors ai.py helper)."""
    result = await db.execute(
        select(Segment)
        .where(Segment.transcript_id == transcript_id)
        .order_by(Segment.segment_index)
    )
    segments = result.scalars().all()

    if not segments:
        return ""

    lines = []
    for seg in segments:
        if seg.speaker:
            lines.append(f"[{seg.speaker}]: {seg.text}")
        else:
            lines.append(seg.text)

    return "\n".join(lines)


@router.post("/sessions", response_model=VoiceSessionResponse)
async def create_voice_session(
    body: CreateSessionRequest | None = None,
    db: AsyncSession = Depends(get_db),
    active_project_ids: Annotated[list[str], Depends(get_active_project_ids)] = [],
) -> VoiceSessionResponse:
    """Create a new voice chat session.

    Generates a unique LiveKit room, creates access tokens for both the
    user and the agent, and starts the agent in the room as a background
    task. Loads attached transcripts, documents, and project context into
    the agent's system prompt.

    Args:
        body: Optional request body with voice selection and attachment IDs.
        db: Database session for loading context.
        active_project_ids: Project IDs from X-Active-Project header.

    Returns:
        VoiceSessionResponse with the user's token, LiveKit URL, and room name.
    """
    # Check that TTS is available before starting a session
    active_model = _get_active_tts_model()
    if not active_model or not _is_tts_model_downloaded(active_model):
        raise HTTPException(
            status_code=503,
            detail=(
                "No TTS model is active. Download and activate a TTS model "
                "before starting a voice session."
            ),
        )

    selected_voice = body.voice if body else None

    # Generate unique room name
    room_name = f"voice-{uuid.uuid4().hex[:8]}"

    # Generate user token
    user_token = _generate_user_token(room_name)

    # Create the voice agent
    try:
        from services.voice_agent import create_agent_session

        agent = create_agent_session(voice=selected_voice)
    except Exception as e:
        logger.exception("Failed to create voice agent session")
        raise HTTPException(
            status_code=503,
            detail=f"Failed to create voice agent: {e}",
        )

    # ── Load attached context ─────────────────────────────────────────
    context_parts: list[str] = []
    label_index = 0

    # Load explicitly-attached transcripts
    for recording_id in (body.recording_ids if body else []):
        label = chr(65 + label_index)  # A, B, C, ...
        try:
            recording_result = await db.execute(
                select(Recording).where(Recording.id == recording_id)
            )
            recording = recording_result.scalar_one_or_none()
            if not recording:
                logger.warning("Voice context: recording not found: %s", recording_id)
                continue

            transcript_result = await db.execute(
                select(Transcript).where(Transcript.recording_id == recording_id)
            )
            transcript = transcript_result.scalar_one_or_none()
            if not transcript:
                logger.warning("Voice context: no transcript for recording: %s", recording_id)
                continue

            text = await _get_transcript_text(db, transcript.id)
            if text:
                title = recording.title
                context_parts.append(f"=== Transcript {label}: {title} ===\n{text}\n")
                label_index += 1
        except Exception as e:
            logger.warning("Voice context: could not load recording %s: %s", recording_id, e)
            continue

    # Load explicitly-attached documents
    for doc_id in (body.document_ids if body else []):
        label = chr(65 + label_index)
        try:
            doc = await db.get(Document, doc_id)
            if doc and doc.extracted_text:
                context_parts.append(f"=== Document {label}: {doc.title} ===\n{doc.extracted_text}\n")
                label_index += 1
            elif doc and doc.extracted_markdown:
                context_parts.append(f"=== Document {label}: {doc.title} ===\n{doc.extracted_markdown}\n")
                label_index += 1
            else:
                logger.warning("Voice context: document %s has no extracted text", doc_id)
        except Exception as e:
            logger.warning("Voice context: could not load document %s: %s", doc_id, e)
            continue

    # Auto-inject project context when no manual attachments are provided
    has_manual_attachments = (body and (body.recording_ids or body.document_ids))
    if active_project_ids and not has_manual_attachments:
        try:
            # Load all recordings with transcripts from scoped projects
            rec_result = await db.execute(
                select(Recording).where(
                    Recording.project_id.in_(active_project_ids),
                    Recording.is_archived == False,  # noqa: E712
                )
            )
            project_recordings = rec_result.scalars().all()

            for recording in project_recordings:
                label = chr(65 + label_index)
                try:
                    transcript_result = await db.execute(
                        select(Transcript).where(Transcript.recording_id == recording.id)
                    )
                    transcript = transcript_result.scalar_one_or_none()
                    if not transcript:
                        continue
                    text = await _get_transcript_text(db, transcript.id)
                    if text:
                        context_parts.append(f"=== Transcript {label}: {recording.title} ===\n{text}\n")
                        label_index += 1
                except Exception as e:
                    logger.warning("Voice auto-context: could not load transcript for recording %s: %s", recording.id, e)

            # Load all documents from scoped projects
            doc_result = await db.execute(
                select(Document).where(
                    Document.project_id.in_(active_project_ids),
                    Document.is_archived == False,  # noqa: E712
                )
            )
            project_documents = doc_result.scalars().all()

            for doc in project_documents:
                label = chr(65 + label_index)
                if doc.extracted_text:
                    context_parts.append(f"=== Document {label}: {doc.title} ===\n{doc.extracted_text}\n")
                    label_index += 1
                elif doc.extracted_markdown:
                    context_parts.append(f"=== Document {label}: {doc.title} ===\n{doc.extracted_markdown}\n")
                    label_index += 1

            if label_index > 0:
                logger.info("Voice project auto-context: loaded %d item(s) from %d project(s)", label_index, len(active_project_ids))
        except Exception as e:
            logger.warning("Voice project auto-context failed: %s", e)

    # Inject loaded context into the agent's system prompt
    agent.set_context(context_parts)

    if context_parts:
        logger.info("Voice session context: %d item(s) attached", len(context_parts))

    # Start the agent in the room as a background task
    task = asyncio.create_task(
        _start_agent_in_room(agent, room_name),
        name=f"voice-agent-{room_name}",
    )
    _active_sessions[room_name] = task

    logger.info("Voice session created: room=%s", room_name)

    return VoiceSessionResponse(
        token=user_token,
        url=LIVEKIT_URL,
        room_name=room_name,
    )

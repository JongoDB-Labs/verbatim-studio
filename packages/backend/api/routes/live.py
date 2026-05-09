"""Live transcription WebSocket endpoint.

Thin shell around `RollingTranscriber` (services/live_transcription_service)
which holds all state and runs the rolling-buffer pipeline.
"""

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import aiofiles
from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.routes.sync import broadcast
from core.config import settings
from core.factory import get_factory
from persistence.database import get_db
from persistence.models import (
    Recording,
    RecordingTag,
    Segment,
    Speaker,
    Tag,
    Transcript,
)
from services.live_transcription_service import RollingTranscriber

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/live", tags=["live"])

# Sessions are kept in memory for this long after disconnect to allow saving
SESSION_TTL_SECONDS = 600  # 10 minutes


@dataclass
class LiveSession:
    """Wraps a RollingTranscriber with session-level metadata.

    The transcriber owns audio + segment state; this dataclass just
    tracks the session id, when it disconnected, and editing-friendly
    metadata used by the save endpoint.
    """

    session_id: str
    started_at: datetime
    transcriber: RollingTranscriber
    disconnected_at: datetime | None = None
    edit_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class SaveSessionRequest(BaseModel):
    session_id: str
    title: str
    save_audio: bool = True
    project_id: str | None = None
    tags: list[str] = []
    description: str | None = None


class SaveSessionResponse(BaseModel):
    recording_id: str
    transcript_id: str
    message: str


class AutosaveRequest(BaseModel):
    session_id: str


class AutosaveResponse(BaseModel):
    saved_segments: int
    total_duration: float


class EditSegmentRequest(BaseModel):
    text: str


# Active sessions storage
active_sessions: dict[str, LiveSession] = {}


def _cleanup_expired_sessions() -> int:
    """Remove sessions that have been disconnected longer than SESSION_TTL_SECONDS."""
    now = datetime.utcnow()
    expired = [
        sid
        for sid, s in active_sessions.items()
        if s.disconnected_at
        and (now - s.disconnected_at).total_seconds() > SESSION_TTL_SECONDS
    ]
    for sid in expired:
        del active_sessions[sid]
    if expired:
        logger.info("Cleaned up %d expired session(s)", len(expired))
    return len(expired)


async def _get_diarization_service() -> tuple[object | None, str | None]:
    """Lazy import to avoid loading pyannote unless needed.

    Returns (service, reason) — reason is None on success, otherwise
    a short key: "no_token", "models_missing", "import_error".
    """
    try:
        from services.diarization import DiarizationService
        from core.transcription_settings import get_transcription_settings

        ts = await get_transcription_settings()
        hf_token = ts.get("hf_token")
        if not hf_token:
            logger.warning("No HuggingFace token configured for diarization")
            return None, "no_token"

        from core.pyannote_catalog import are_all_models_downloaded
        if not are_all_models_downloaded():
            logger.warning("Required diarization models not downloaded")
            return None, "models_missing"

        from core.transcription_settings import detect_diarization_device
        device = detect_diarization_device()
        return DiarizationService(device=device, hf_token=hf_token), None
    except ImportError:
        return None, "import_error"


_DIA_HINTS = {
    "no_token": (
        "No HuggingFace token configured. Add your token in"
        " Settings → Transcription to enable speaker identification."
    ),
    "models_missing": (
        "Diarization models not downloaded. Download them in"
        " Settings → AI to enable speaker identification."
    ),
    "import_error": (
        "Diarization dependencies not available. Speaker identification"
        " is disabled."
    ),
}


@router.websocket("/transcribe")
async def live_transcribe(websocket: WebSocket):
    """WebSocket endpoint for live transcription.

    Protocol:
        client → server:
            {"type": "start", "language": "en", "high_detail_mode": false}
            <binary audio chunk>
            {"type": "stop"}
            {"type": "ping"}
            {"type": "disconnect"}

        server → client:
            {"type": "ready"}
            {"type": "session_start", "session_id": ...}
            {"type": "segments_replace",
             "confirmed": [...], "tentative": [...], "duration": ...}
            {"type": "speaker_update", "updates": [{"id", "speaker"}, ...]}
            {"type": "session_end", "session_id": ...}
            {"type": "warning"|"error", ...}
    """
    await websocket.accept()
    _cleanup_expired_sessions()

    session: LiveSession | None = None
    diarization_service = None

    factory = get_factory()
    engine = factory.create_transcription_engine()
    if not await engine.is_available():
        await websocket.send_json({
            "type": "error",
            "error_type": "engine_unavailable",
            "message": (
                "Transcription engine not available."
                " Check Settings to configure your engine."
            ),
            "retryable": False,
        })
        await websocket.close()
        return

    await websocket.send_json({
        "type": "ready",
        "message": "Connected to live transcription service",
    })

    async def emit(event: dict) -> None:
        try:
            await websocket.send_json(event)
        except Exception as exc:
            logger.debug("emit failed: %s", exc)

    try:
        while True:
            try:
                message = await websocket.receive()
            except RuntimeError as exc:
                # Starlette raises RuntimeError when the peer has already
                # closed the WebSocket and we try to receive again. Treat
                # as a normal disconnect rather than an error.
                if "disconnect" in str(exc).lower():
                    logger.debug("WebSocket peer disconnected: %s", exc)
                    break
                raise

            # Handle text messages (JSON commands)
            if "text" in message:
                try:
                    data = json.loads(message["text"])
                except json.JSONDecodeError:
                    await emit({
                        "type": "error",
                        "error_type": "protocol",
                        "message": "Invalid JSON message",
                        "retryable": False,
                    })
                    continue

                msg_type = data.get("type")

                if msg_type == "start":
                    session_id = str(uuid.uuid4())
                    language = data.get("language", "en")
                    high_detail = data.get("high_detail_mode", False)

                    if high_detail and diarization_service is None:
                        diarization_service, reason = await _get_diarization_service()
                        if reason:
                            await emit({
                                "type": "warning",
                                "message": _DIA_HINTS.get(
                                    reason, f"Diarization unavailable: {reason}"
                                ),
                            })

                    transcriber = RollingTranscriber(
                        engine=engine,
                        diarization_service=diarization_service,
                        language=language,
                        high_detail=high_detail,
                        data_dir=settings.DATA_DIR,
                        emit=emit,
                    )
                    session = LiveSession(
                        session_id=session_id,
                        started_at=datetime.utcnow(),
                        transcriber=transcriber,
                    )
                    active_sessions[session_id] = session

                    await emit({
                        "type": "session_start",
                        "session_id": session_id,
                        "message": "Recording started",
                    })
                    logger.info(
                        "Live session started: %s (high_detail=%s)",
                        session_id, high_detail,
                    )

                elif msg_type == "stop":
                    if session:
                        # Final flush: promote tentative tail to confirmed.
                        try:
                            await session.transcriber.finalize()
                        except Exception as exc:
                            logger.warning("finalize failed: %s", exc)

                        await emit({
                            "type": "session_end",
                            "session_id": session.session_id,
                            "total_segments": len(session.transcriber.all_segments),
                            "total_duration": session.transcriber.total_duration,
                        })
                        logger.info(
                            "Live session ended: %s (%d segments, %.1fs)",
                            session.session_id,
                            len(session.transcriber.all_segments),
                            session.transcriber.total_duration,
                        )
                        session = None

                elif msg_type == "disconnect":
                    break

                elif msg_type == "ping":
                    await emit({"type": "pong"})

            # Handle binary messages (audio chunks)
            elif "bytes" in message:
                if not session:
                    await emit({
                        "type": "error",
                        "error_type": "no_session",
                        "message": "No active session. Click Start Recording first.",
                        "retryable": False,
                    })
                    continue

                audio_data = message["bytes"]
                try:
                    async with session.edit_lock:
                        await session.transcriber.process_chunk(audio_data)
                except Exception as exc:
                    logger.exception("Chunk processing error")
                    err_msg = str(exc)
                    if "out of memory" in err_msg.lower() or "cuda" in err_msg.lower():
                        error_type = "resource"
                        user_msg = "Transcription engine ran out of memory. Try a smaller model."
                        retryable = False
                    else:
                        error_type = "transcription"
                        user_msg = "Failed to process audio chunk. Recording continues."
                        retryable = True
                    await emit({
                        "type": "error",
                        "error_type": error_type,
                        "message": user_msg,
                        "retryable": retryable,
                    })

    except WebSocketDisconnect:
        logger.info("WebSocket disconnected")
    except Exception:
        logger.exception("WebSocket error")
        try:
            await emit({
                "type": "error",
                "error_type": "connection",
                "message": "Connection error. Please reconnect.",
                "retryable": True,
            })
        except Exception:
            pass
    finally:
        if session and session.session_id in active_sessions:
            session.disconnected_at = datetime.utcnow()


@router.post("/save", response_model=SaveSessionResponse)
async def save_live_session(
    request: SaveSessionRequest,
    db: AsyncSession = Depends(get_db),
):
    """Save a live transcription session as a Recording + Transcript."""
    session = active_sessions.get(request.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found or expired")

    transcriber = session.transcriber

    # Force-finalize so any unflushed tentative tail becomes confirmed.
    # The user may have hit Save immediately after Stop, before the
    # final segments_replace landed.
    async with session.edit_lock:
        try:
            await transcriber.finalize()
        except Exception as exc:
            logger.warning("finalize-on-save failed: %s", exc)

    recording_id = str(uuid.uuid4())
    file_path = None
    file_size = None

    if request.save_audio and transcriber.encoded_chunks:
        audio_filename = f"live-{recording_id}.webm"
        audio_path = settings.MEDIA_DIR / audio_filename

        async with aiofiles.open(audio_path, "wb") as f:
            for chunk in transcriber.encoded_chunks:
                await f.write(chunk)

        file_path = str(audio_path)
        file_size = audio_path.stat().st_size

    metadata = {
        "source": "live",
        "session_id": request.session_id,
    }
    if request.description:
        metadata["description"] = request.description

    audio_filename = f"live-{recording_id}.webm"
    recording = Recording(
        id=recording_id,
        title=request.title,
        project_id=request.project_id,
        file_path=file_path or f"live://{recording_id}",
        file_name=audio_filename if file_path else f"live-{recording_id}.txt",
        file_size=file_size or 0,
        duration_seconds=transcriber.total_duration,
        mime_type="audio/webm" if file_path else "text/plain",
        metadata_=metadata,
        status="completed",
    )
    db.add(recording)

    transcript_id = str(uuid.uuid4())
    transcript = Transcript(
        id=transcript_id,
        recording_id=recording_id,
        language=transcriber.state.language,
        model_used="live-transcription",
    )
    db.add(transcript)

    for i, seg in enumerate(transcriber.all_segments):
        segment = Segment(
            id=str(uuid.uuid4()),
            transcript_id=transcript_id,
            segment_index=i,
            speaker=seg.speaker,
            start_time=seg.start,
            end_time=seg.end,
            text=seg.text,
            confidence=seg.confidence,
            edited_by=seg.edited_by,
        )
        db.add(segment)

    for speaker_label in sorted(transcriber.speakers_found):
        speaker = Speaker(
            id=str(uuid.uuid4()),
            transcript_id=transcript_id,
            speaker_label=speaker_label,
        )
        db.add(speaker)

    if request.tags:
        for tag_name in request.tags:
            tag_name = tag_name.strip()
            if not tag_name:
                continue
            result = await db.execute(select(Tag).where(Tag.name == tag_name))
            tag = result.scalar_one_or_none()
            if not tag:
                tag = Tag(id=str(uuid.uuid4()), name=tag_name)
                db.add(tag)
                await db.flush()
            db.add(RecordingTag(recording_id=recording_id, tag_id=tag.id))

    await db.commit()

    await broadcast("recordings", "created", recording_id)

    del active_sessions[request.session_id]

    logger.info(
        "Saved live session %s as recording %s with %d segments",
        request.session_id,
        recording_id,
        len(transcriber.all_segments),
    )

    return SaveSessionResponse(
        recording_id=recording_id,
        transcript_id=transcript_id,
        message=f"Saved {len(transcriber.all_segments)} segments",
    )


@router.post("/autosave", response_model=AutosaveResponse)
async def autosave_session(request: AutosaveRequest):
    """Autosave endpoint - confirms session state is preserved."""
    session = active_sessions.get(request.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found or expired")

    transcriber = session.transcriber
    return AutosaveResponse(
        saved_segments=len(transcriber.all_segments),
        total_duration=transcriber.total_duration,
    )


@router.delete("/session/{session_id}")
async def discard_session(session_id: str):
    """Discard a live session without saving."""
    if session_id in active_sessions:
        del active_sessions[session_id]
        return {"message": "Session discarded"}
    raise HTTPException(status_code=404, detail="Session not found")


@router.patch("/session/{session_id}/segment/{segment_id}")
async def edit_live_segment(
    session_id: str,
    segment_id: str,
    request: EditSegmentRequest,
):
    """Edit text of a segment in an active live session."""
    session = active_sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found or expired")

    async with session.edit_lock:
        if not session.transcriber.update_segment_text(segment_id, request.text):
            raise HTTPException(status_code=404, detail="Segment not found")
    return {"message": "Segment updated"}


@router.delete("/session/{session_id}/segment/{segment_id}")
async def delete_live_segment(session_id: str, segment_id: str):
    """Delete a segment from an active live session."""
    session = active_sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found or expired")

    async with session.edit_lock:
        if not session.transcriber.delete_segment(segment_id):
            raise HTTPException(status_code=404, detail="Segment not found")
    return {"message": "Segment deleted"}

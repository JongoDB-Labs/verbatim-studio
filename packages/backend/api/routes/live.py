"""Live transcription WebSocket endpoint."""

import json
import logging
import tempfile
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
from core.interfaces import TranscriptionOptions
from persistence.database import get_db
from persistence.models import (
    Recording,
    RecordingTag,
    Segment,
    Speaker,
    Tag,
    Transcript,
)
from services.transcript_filter import is_hallucination

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/live", tags=["live"])


# Chunk interval must match frontend (1.5 seconds for lower latency)
CHUNK_INTERVAL_SECONDS = 1.5

# Auto-save interval in seconds
AUTOSAVE_INTERVAL_SECONDS = 30.0

# Sessions are kept in memory for this long after disconnect to allow saving
SESSION_TTL_SECONDS = 600  # 10 minutes


@dataclass
class LiveSession:
    """State for a live transcription session."""

    session_id: str
    started_at: datetime
    segments: list[dict] = field(default_factory=list)
    audio_chunks: list[bytes] = field(default_factory=list)
    total_duration: float = 0.0
    language: str = "en"
    chunk_count: int = 0  # Track chunk index for time offset
    high_detail_mode: bool = False
    speakers_found: set = field(default_factory=set)
    diarization_warned: bool = False
    disconnected_at: datetime | None = None
    # Rolling tail of recent transcript text used as Whisper initial_prompt
    # so word boundaries at chunk seams resolve correctly. Capped at
    # ~200 chars (Whisper conditioning is most useful for the recent past).
    prompt_context: str = ""
    # Cross-chunk speaker stitching: maps the per-chunk pyannote label
    # ("SPEAKER_00", "SPEAKER_01", ...) to a stable session label. Without
    # this, the same person's label flips between chunks because pyannote
    # clusters each 1.5s window independently.
    speaker_label_map: dict[str, str] = field(default_factory=dict)
    # Greedy session-label allocator
    next_speaker_id: int = 0


# Maximum characters to feed back into Whisper as initial_prompt. Whisper
# conditioning is "soft" — too much context biases the model heavily, too
# little gives no benefit. ~200 chars (~40 words) hits the sweet spot.
PROMPT_CONTEXT_CHARS = 200


class SaveSessionRequest(BaseModel):
    """Request to save a live session."""

    session_id: str
    title: str
    save_audio: bool = True
    project_id: str | None = None
    tags: list[str] = []
    description: str | None = None


class SaveSessionResponse(BaseModel):
    """Response after saving a live session."""

    recording_id: str
    transcript_id: str
    message: str


class AutosaveRequest(BaseModel):
    """Request to autosave live session segments."""

    session_id: str


class AutosaveResponse(BaseModel):
    """Response after autosaving."""

    saved_segments: int
    total_duration: float


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

    Reads HF token from DB settings (user configures via Settings UI)
    rather than relying on environment variables alone.

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


@router.websocket("/transcribe")
async def live_transcribe(websocket: WebSocket):
    """WebSocket endpoint for live transcription.

    Protocol:
    1. Client connects
    2. Client sends JSON: {"type": "start", "language": "en", "high_detail_mode": false}
    3. Client sends binary audio chunks (WebM/Opus or WAV)
    4. Server responds with JSON transcript messages
    5. Client sends JSON: {"type": "stop"} to end session
    6. Server responds with JSON: {"type": "session_end", "session_id": "..."}
    """
    await websocket.accept()

    # Lazily clean up sessions that were abandoned after disconnect
    _cleanup_expired_sessions()

    session: LiveSession | None = None
    chunk_index = 0
    dia_service = None

    try:
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
            "message": "Connected to live transcription service"
        })

        while True:
            message = await websocket.receive()

            # Handle text messages (JSON commands)
            if "text" in message:
                try:
                    data = json.loads(message["text"])
                except json.JSONDecodeError:
                    await websocket.send_json({
                        "type": "error",
                        "error_type": "protocol",
                        "message": "Invalid JSON message",
                        "retryable": False,
                    })
                    continue

                msg_type = data.get("type")

                if msg_type == "start":
                    # Start new session
                    session_id = str(uuid.uuid4())
                    language = data.get("language", "en")
                    high_detail = data.get("high_detail_mode", False)
                    session = LiveSession(
                        session_id=session_id,
                        started_at=datetime.utcnow(),
                        language=language,
                        high_detail_mode=high_detail,
                    )
                    active_sessions[session_id] = session
                    chunk_index = 0

                    # Pre-load diarization service if needed
                    if high_detail and dia_service is None:
                        dia_service, dia_reason = await _get_diarization_service()
                        if dia_reason:
                            _DIA_HINTS = {
                                "no_token": (
                                    "No HuggingFace token configured."
                                    " Add your token in Settings \u2192 Transcription"
                                    " to enable speaker identification."
                                ),
                                "models_missing": (
                                    "Diarization models not downloaded."
                                    " Download them in Settings \u2192 AI"
                                    " to enable speaker identification."
                                ),
                                "import_error": (
                                    "Diarization dependencies not available."
                                    " Speaker identification is disabled."
                                ),
                            }
                            await websocket.send_json({
                                "type": "warning",
                                "message": _DIA_HINTS.get(dia_reason, f"Diarization unavailable: {dia_reason}"),
                            })

                    await websocket.send_json({
                        "type": "session_start",
                        "session_id": session_id,
                        "message": "Recording started"
                    })
                    logger.info(
                        "Live session started: %s (high_detail=%s)",
                        session_id, high_detail,
                    )

                elif msg_type == "stop":
                    if session:
                        await websocket.send_json({
                            "type": "session_end",
                            "session_id": session.session_id,
                            "total_segments": len(session.segments),
                            "total_duration": session.total_duration,
                        })
                        logger.info(
                            "Live session ended: %s (%d segments, %.1fs)",
                            session.session_id,
                            len(session.segments),
                            session.total_duration,
                        )
                        # Keep session in memory for saving, but allow new session
                        session = None
                        chunk_index = 0
                    # Don't break - keep connection open for new sessions

                elif msg_type == "disconnect":
                    # Explicit disconnect request
                    break

                elif msg_type == "ping":
                    await websocket.send_json({"type": "pong"})

            # Handle binary messages (audio chunks)
            elif "bytes" in message:
                if not session:
                    await websocket.send_json({
                        "type": "error",
                        "error_type": "no_session",
                        "message": "No active session. Click Start Recording first.",
                        "retryable": False,
                    })
                    continue

                audio_data = message["bytes"]
                session.audio_chunks.append(audio_data)

                # Calculate time offset for this chunk
                time_offset = session.chunk_count * CHUNK_INTERVAL_SECONDS

                # Each chunk is now a complete, standalone WebM file
                # Write just this chunk to temp file for transcription
                with tempfile.NamedTemporaryFile(
                    suffix=".webm",
                    delete=False,
                    dir=settings.DATA_DIR,
                ) as tmp:
                    tmp.write(audio_data)
                    tmp_path = tmp.name

                try:
                    # Transcribe just this chunk. Pass the rolling tail of
                    # recent transcript text as initial_prompt so Whisper
                    # has continuity context across chunk seams — this is
                    # the single biggest accuracy improvement for live
                    # streaming because chunks otherwise start cold.
                    options = TranscriptionOptions(
                        language=session.language,
                        word_timestamps=session.high_detail_mode,
                        initial_prompt=session.prompt_context or None,
                    )

                    result = await engine.transcribe(tmp_path, options)

                    # Run diarization if high detail mode
                    diarized_segments = None
                    if session.high_detail_mode and dia_service and result.segments:
                        try:
                            segments_data = []
                            for seg in result.segments:
                                seg_dict = {
                                    "start": seg.start,
                                    "end": seg.end,
                                    "text": seg.text,
                                }
                                if seg.words:
                                    seg_dict["words"] = [
                                        {
                                            "word": w.word,
                                            "start": w.start,
                                            "end": w.end,
                                            "score": w.confidence or 0.0,
                                        }
                                        for w in seg.words
                                    ]
                                segments_data.append(seg_dict)

                            dia_result = await dia_service.diarize(
                                audio_path=tmp_path,
                                segments=segments_data,
                            )
                            diarized_segments = dia_result.get("segments", [])
                            # Don't add raw pyannote labels here — the
                            # per-chunk labels aren't session-stable.
                            # speakers_found is populated below using the
                            # stitched session-level labels instead.
                        except Exception as dia_err:
                            logger.warning(
                                "Diarization failed for chunk %d: %s",
                                chunk_index, dia_err,
                            )
                            # Notify user once that diarization degraded
                            if not session.diarization_warned:
                                session.diarization_warned = True
                                err_str = str(dia_err)
                                if "401" in err_str or "token" in err_str.lower():
                                    hint = "Your HuggingFace token may be invalid or missing access to pyannote models. Accept the license at huggingface.co/pyannote/speaker-diarization-3.1"
                                elif "import" in err_str.lower() or "module" in err_str.lower():
                                    hint = "The diarization model is not installed. You can install it in Settings → AI."
                                else:
                                    hint = f"Diarization error: {err_str}"
                                await websocket.send_json({
                                    "type": "warning",
                                    "message": (
                                        f"Speaker identification failed — {hint}."
                                        " Transcription will continue without"
                                        " speaker labels."
                                    ),
                                })

                    # Track previous accepted text for stuck-loop detection.
                    # Whisper sometimes emits the same phrase twice when the
                    # second chunk has no real speech — drop the duplicate.
                    prev_accepted_text: str | None = (
                        session.segments[-1]["text"] if session.segments else None
                    )

                    # Process segments with time offset applied
                    for i, seg in enumerate(result.segments):
                        # Drop Whisper hallucinations (silence/music/short
                        # noise produces "Thanks for watching!", repeated
                        # words, etc.). Centralised in transcript_filter
                        # so live, voice chat, and post-hoc transcription
                        # share the same logic.
                        seg_duration = (seg.end - seg.start) if seg.end and seg.start else None
                        if is_hallucination(
                            seg.text,
                            confidence=seg.confidence,
                            duration=seg_duration,
                            prev_text=prev_accepted_text,
                        ):
                            logger.debug(
                                "Filtered hallucinated segment: %r (conf=%s, dur=%s)",
                                seg.text[:60], seg.confidence, seg_duration,
                            )
                            continue

                        # Resolve speaker label, stitching across chunks so
                        # SPEAKER_00 in this chunk maps to the same session
                        # label that the same voice received in earlier
                        # chunks. Pyannote clusters each chunk independently,
                        # so without this the labels are unstable.
                        speaker = None
                        chunk_speaker = None
                        if diarized_segments and i < len(diarized_segments):
                            chunk_speaker = diarized_segments[i].get("speaker")
                        chunk_speaker = chunk_speaker or seg.speaker

                        if chunk_speaker:
                            mapped = session.speaker_label_map.get(chunk_speaker)
                            if mapped is None:
                                mapped = f"Speaker {session.next_speaker_id + 1}"
                                session.speaker_label_map[chunk_speaker] = mapped
                                session.next_speaker_id += 1
                            speaker = mapped
                            session.speakers_found.add(mapped)

                        # Build word data for high detail mode
                        words_data = None
                        if session.high_detail_mode and seg.words:
                            words_data = [
                                {
                                    "word": w.word,
                                    "start": time_offset + w.start,
                                    "end": time_offset + w.end,
                                    "confidence": w.confidence,
                                }
                                for w in seg.words
                            ]

                        segment_data = {
                            "text": seg.text,
                            "start": time_offset + seg.start,
                            "end": time_offset + seg.end,
                            "speaker": speaker,
                            "confidence": seg.confidence,
                            "words": words_data,
                            "edited_by": None,
                        }
                        session.segments.append(segment_data)
                        prev_accepted_text = seg.text

                        # Send segment to client
                        msg = {
                            "type": "transcript",
                            "text": seg.text,
                            "start": segment_data["start"],
                            "end": segment_data["end"],
                            "chunk_index": chunk_index,
                            "speaker": segment_data["speaker"],
                            "confidence": segment_data["confidence"],
                        }
                        if words_data:
                            msg["words"] = words_data
                        await websocket.send_json(msg)

                    # Refresh the rolling prompt_context with the latest
                    # accepted text. Capped to PROMPT_CONTEXT_CHARS so the
                    # initial_prompt never grows unbounded.
                    if session.segments:
                        tail = " ".join(
                            s["text"] for s in session.segments[-6:]
                        ).strip()
                        session.prompt_context = tail[-PROMPT_CONTEXT_CHARS:]

                    # Update tracking
                    session.chunk_count += 1
                    if result.segments:
                        session.total_duration = time_offset + result.segments[-1].end
                    else:
                        session.total_duration = time_offset + CHUNK_INTERVAL_SECONDS
                    chunk_index += 1

                    logger.debug(
                        "Processed chunk %d: %d segments, total duration %.1fs",
                        chunk_index,
                        len(result.segments),
                        session.total_duration,
                    )

                except Exception as e:
                    logger.error("Transcription error: %s", e)
                    error_msg = str(e)
                    # Categorize the error for the frontend
                    if "out of memory" in error_msg.lower() or "cuda" in error_msg.lower():
                        error_type = "resource"
                        user_msg = "Transcription engine ran out of memory. Try a smaller model."
                        retryable = False
                    elif "no such file" in error_msg.lower() or "temp" in error_msg.lower():
                        error_type = "temporary"
                        user_msg = (
                            "Temporary processing error."
                            " The next chunk will retry automatically."
                        )
                        retryable = True
                    else:
                        error_type = "transcription"
                        user_msg = "Failed to process audio chunk. Recording continues."
                        retryable = True
                    await websocket.send_json({
                        "type": "error",
                        "error_type": error_type,
                        "message": user_msg,
                        "retryable": retryable,
                    })

                finally:
                    # Clean up temp file
                    try:
                        Path(tmp_path).unlink()
                    except OSError:
                        pass

    except WebSocketDisconnect:
        logger.info("WebSocket disconnected")
    except Exception as e:
        logger.error("WebSocket error: %s", e)
        try:
            await websocket.send_json({
                "type": "error",
                "error_type": "connection",
                "message": "Connection error. Please reconnect.",
                "retryable": True,
            })
        except Exception:
            pass
    finally:
        # Mark session as disconnected so TTL cleanup can reclaim it later.
        # Session stays in memory long enough for the user to click "Save".
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

    # Create recording
    recording_id = str(uuid.uuid4())
    file_path = None
    file_size = None

    # Optionally save audio
    if request.save_audio and session.audio_chunks:
        # Combine all audio chunks into one file
        audio_filename = f"live-{recording_id}.webm"
        audio_path = settings.MEDIA_DIR / audio_filename

        async with aiofiles.open(audio_path, "wb") as f:
            for chunk in session.audio_chunks:
                await f.write(chunk)

        file_path = str(audio_path)
        file_size = audio_path.stat().st_size

    metadata = {
        "source": "live",
        "session_id": request.session_id,
    }
    if request.description:
        metadata["description"] = request.description

    # file_path and file_name are NOT NULL in the schema; use a sentinel
    # for transcript-only live recordings where audio was not saved.
    audio_filename = f"live-{recording_id}.webm"
    recording = Recording(
        id=recording_id,
        title=request.title,
        project_id=request.project_id,
        file_path=file_path or f"live://{recording_id}",
        file_name=audio_filename if file_path else f"live-{recording_id}.txt",
        file_size=file_size or 0,
        duration_seconds=session.total_duration,
        mime_type="audio/webm" if file_path else "text/plain",
        metadata_=metadata,
        status="completed",
    )
    db.add(recording)

    # Create transcript
    transcript_id = str(uuid.uuid4())
    transcript = Transcript(
        id=transcript_id,
        recording_id=recording_id,
        language=session.language,
        model_used="live-transcription",
    )
    db.add(transcript)

    # Create segments
    for i, seg_data in enumerate(session.segments):
        segment = Segment(
            id=str(uuid.uuid4()),
            transcript_id=transcript_id,
            segment_index=i,
            speaker=seg_data.get("speaker"),
            start_time=seg_data["start"],
            end_time=seg_data["end"],
            text=seg_data["text"],
            confidence=seg_data.get("confidence"),
            edited_by=seg_data.get("edited_by"),
        )
        db.add(segment)

    # Create speaker records if diarization was used
    for speaker_label in sorted(session.speakers_found):
        speaker = Speaker(
            id=str(uuid.uuid4()),
            transcript_id=transcript_id,
            speaker_label=speaker_label,
        )
        db.add(speaker)

    # Handle tags — find or create tags by name, then associate
    if request.tags:
        for tag_name in request.tags:
            tag_name = tag_name.strip()
            if not tag_name:
                continue
            # Find existing tag or create new one
            result = await db.execute(select(Tag).where(Tag.name == tag_name))
            tag = result.scalar_one_or_none()
            if not tag:
                tag = Tag(id=str(uuid.uuid4()), name=tag_name)
                db.add(tag)
                await db.flush()
            db.add(RecordingTag(recording_id=recording_id, tag_id=tag.id))

    await db.commit()

    # Broadcast to data sync clients so UI updates immediately
    await broadcast("recordings", "created", recording_id)

    # Remove session from memory
    del active_sessions[request.session_id]

    logger.info(
        "Saved live session %s as recording %s with %d segments",
        request.session_id,
        recording_id,
        len(session.segments),
    )

    return SaveSessionResponse(
        recording_id=recording_id,
        transcript_id=transcript_id,
        message=f"Saved {len(session.segments)} segments",
    )


@router.post("/autosave", response_model=AutosaveResponse)
async def autosave_session(request: AutosaveRequest):
    """Autosave endpoint - confirms session state is preserved.

    The session data lives in-memory on the server. This endpoint lets the
    frontend periodically confirm the session is still alive and get current stats.
    """
    session = active_sessions.get(request.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found or expired")

    logger.debug(
        "Autosave check for session %s: %d segments, %.1fs",
        request.session_id,
        len(session.segments),
        session.total_duration,
    )

    return AutosaveResponse(
        saved_segments=len(session.segments),
        total_duration=session.total_duration,
    )


@router.delete("/session/{session_id}")
async def discard_session(session_id: str):
    """Discard a live session without saving."""
    if session_id in active_sessions:
        del active_sessions[session_id]
        return {"message": "Session discarded"}
    raise HTTPException(status_code=404, detail="Session not found")

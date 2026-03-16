"""Job queue service for async task processing."""

import asyncio
import logging
import threading
from collections.abc import Awaitable, Callable
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from persistence.database import get_session_factory
from persistence.models import Job, Recording, Segment, SegmentEmbedding, Speaker, Transcript
from api.routes.sync import broadcast
from core.events import emit as emit_event

logger = logging.getLogger(__name__)

# Serializes GPU-heavy operations (transcription + diarization) so concurrent
# uploads don't exhaust GPU memory and crash the app.
_gpu_lock = threading.Lock()


class JobCancelled(Exception):
    """Raised when a job is cancelled via cooperative cancellation."""


# Type alias for job handlers
# Handler receives (payload, progress_callback) and returns result dict
ProgressCallback = Callable[[float], Awaitable[None]]
JobHandler = Callable[[dict[str, Any], ProgressCallback], Awaitable[dict[str, Any]]]


class JobQueue:
    """Job queue using ThreadPoolExecutor for async task processing."""

    def __init__(self, max_workers: int = 2):
        """Initialize the job queue.

        Args:
            max_workers: Maximum number of concurrent workers.
        """
        self._executor = ThreadPoolExecutor(max_workers=max_workers)
        self._handlers: dict[str, JobHandler] = {}
        self._futures: dict[str, Future] = {}
        self._cancelled_jobs: set[str] = set()
        logger.info("JobQueue initialized with max_workers=%d", max_workers)

    def register_handler(self, job_type: str, handler: JobHandler) -> None:
        """Register a handler for a job type.

        Args:
            job_type: The type of job this handler processes.
            handler: Async function that processes the job.
        """
        self._handlers[job_type] = handler
        logger.info("Registered handler for job type: %s", job_type)

    async def enqueue(self, job_type: str, payload: dict[str, Any]) -> str:
        """Create a job and submit it to the executor.

        Args:
            job_type: The type of job to create.
            payload: The job payload data.

        Returns:
            The job ID.

        Raises:
            ValueError: If no handler is registered for the job type.
        """
        if job_type not in self._handlers:
            raise ValueError(f"No handler registered for job type: {job_type}")

        # Create job record in database
        async with get_session_factory()() as session:
            job = Job(
                job_type=job_type,
                status="queued",
                payload=payload,
                progress=0,
            )
            session.add(job)
            await session.commit()
            job_id = job.id
            logger.info("Created job %s of type %s", job_id, job_type)

        # Submit to executor
        future = self._executor.submit(self._run_job_sync, job_id)
        self._futures[job_id] = future

        return job_id

    def _run_job_sync(self, job_id: str) -> None:
        """Synchronous wrapper to run job in thread pool.

        This runs in the executor thread and creates a new event loop
        for async operations.

        Args:
            job_id: The job ID to process.
        """
        # Create a new event loop for this thread
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._run_job(job_id))
        finally:
            loop.close()

    async def _run_job(self, job_id: str) -> None:
        """Execute a job and update its status.

        Args:
            job_id: The job ID to process.
        """
        logger.info("Starting job %s", job_id)

        # Mark job as running
        async with get_session_factory()() as session:
            await session.execute(
                update(Job)
                .where(Job.id == job_id)
                .values(status="running", started_at=datetime.now(UTC))
            )
            await session.commit()

        try:
            # Get job details
            async with get_session_factory()() as session:
                result = await session.execute(select(Job).where(Job.id == job_id))
                job = result.scalar_one_or_none()

                if job is None:
                    logger.error("Job %s not found", job_id)
                    return

                if job.status == "cancelled":
                    logger.info("Job %s was cancelled, skipping", job_id)
                    return

                job_type = job.job_type
                payload = job.payload or {}
                # Add job_id to payload for cancellation checking
                payload["job_id"] = job_id

            # Get handler
            handler = self._handlers.get(job_type)
            if handler is None:
                raise ValueError(f"No handler registered for job type: {job_type}")

            # Create progress callback with cancellation check
            async def update_progress(progress: float) -> None:
                """Update job progress in database. Raises JobCancelled if requested."""
                if job_id in self._cancelled_jobs:
                    raise JobCancelled(f"Job {job_id} was cancelled")
                async with get_session_factory()() as session:
                    await session.execute(
                        update(Job).where(Job.id == job_id).values(progress=progress)
                    )
                    await session.commit()

            # Execute handler
            result = await handler(payload, update_progress)

            # Mark job as completed
            async with get_session_factory()() as session:
                await session.execute(
                    update(Job)
                    .where(Job.id == job_id)
                    .values(
                        status="completed",
                        result=result,
                        progress=100,
                        completed_at=datetime.now(UTC),
                    )
                )
                await session.commit()
            logger.info("Job %s completed successfully", job_id)

        except JobCancelled:
            logger.info("Job %s cancelled by user", job_id)
            async with get_session_factory()() as session:
                await session.execute(
                    update(Job)
                    .where(Job.id == job_id)
                    .values(
                        status="cancelled",
                        error="Cancelled by user",
                        completed_at=datetime.now(UTC),
                    )
                )
                await session.commit()

        except Exception as e:
            logger.exception("Job %s failed with error", job_id)
            # Mark job as failed
            async with get_session_factory()() as session:
                await session.execute(
                    update(Job)
                    .where(Job.id == job_id)
                    .values(
                        status="failed",
                        error=str(e),
                        completed_at=datetime.now(UTC),
                    )
                )
                await session.commit()

        finally:
            # Clean up future reference and cancellation tracking
            self._futures.pop(job_id, None)
            self._cancelled_jobs.discard(job_id)

    async def get_job(self, job_id: str) -> Job | None:
        """Get a job by ID.

        Args:
            job_id: The job ID to retrieve.

        Returns:
            The job record or None if not found.
        """
        async with get_session_factory()() as session:
            result = await session.execute(select(Job).where(Job.id == job_id))
            return result.scalar_one_or_none()

    async def cancel_job(self, job_id: str) -> bool:
        """Cancel a queued or running job.

        Queued jobs are cancelled immediately. Running jobs are cancelled
        cooperatively — the next progress callback will raise JobCancelled.

        Args:
            job_id: The job ID to cancel.

        Returns:
            True if job was cancelled (or cancellation requested), False if not found or not cancellable.
        """
        async with get_session_factory()() as session:
            result = await session.execute(select(Job).where(Job.id == job_id))
            job = result.scalar_one_or_none()

            if job is None:
                return False

            if job.status == "queued":
                # Immediate cancellation for queued jobs
                await session.execute(
                    update(Job)
                    .where(Job.id == job_id)
                    .values(status="cancelled", completed_at=datetime.now(UTC))
                )
                await session.commit()

                future = self._futures.pop(job_id, None)
                if future is not None:
                    future.cancel()

                logger.info("Job %s cancelled (was queued)", job_id)
                return True

            if job.status == "running":
                # Cooperative cancellation for running jobs
                self._cancelled_jobs.add(job_id)
                logger.info("Job %s cancellation requested (running)", job_id)
                return True

            return False

    async def list_jobs(self, status: str | None = None, limit: int = 100) -> list[Job]:
        """List jobs with optional status filter.

        Args:
            status: Optional status filter.
            limit: Maximum number of jobs to return.

        Returns:
            List of jobs.
        """
        async with get_session_factory()() as session:
            query = select(Job).order_by(Job.created_at.desc()).limit(limit)
            if status is not None:
                query = query.where(Job.status == status)
            result = await session.execute(query)
            return list(result.scalars().all())

    async def clear_completed_jobs(self) -> int:
        """Delete all completed, failed, and cancelled jobs from the database.

        Returns:
            Number of jobs deleted.
        """
        from sqlalchemy import delete

        async with get_session_factory()() as session:
            result = await session.execute(
                delete(Job).where(Job.status.in_(["completed", "failed", "cancelled"]))
            )
            await session.commit()
            count = result.rowcount
            logger.info("Cleared %d completed/failed/cancelled jobs", count)
            return count

    def shutdown(self, wait: bool = True) -> None:
        """Shutdown the executor gracefully.

        Args:
            wait: Whether to wait for pending jobs to complete.
        """
        logger.info("Shutting down JobQueue (wait=%s)", wait)
        self._executor.shutdown(wait=wait)
        self._futures.clear()


# Default job queue instance
job_queue = JobQueue()


async def handle_transcription(
    payload: dict[str, Any], progress_callback: ProgressCallback
) -> dict[str, Any]:
    """Handle transcription job with optional diarization.

    Args:
        payload: Job payload with:
            - recording_id: Required recording ID
            - language: Optional language code
            - diarize: Optional bool to enable diarization (default True)
        progress_callback: Callback to report progress.

    Returns:
        Result dictionary with transcript_id, segment count, speaker count.

    Raises:
        ValueError: If recording not found or invalid.
    """
    from core.factory import create_transcription_engine_from_settings
    from core.interfaces import TranscriptionOptions, TranscriptionProgress, TranscriptionResult
    from core.transcription_settings import detect_diarization_device, get_transcription_settings
    from services.diarization import DiarizationService

    recording_id = payload.get("recording_id")
    language = payload.get("language")
    diarize_requested = payload.get("diarize", True)  # Default to enabled

    # Read effective settings fresh for this job
    effective = await get_transcription_settings()
    dia_device = detect_diarization_device()
    logger.info(
        "Effective transcription settings: engine=%s, model=%s, device=%s, compute_type=%s, batch_size=%s, diarize=%s, dia_device=%s",
        effective.get("engine", "auto"),
        effective["model"],
        effective["device"],
        effective["compute_type"],
        effective["batch_size"],
        effective["diarize"],
        dia_device,
    )

    # Create transcription engine based on settings
    tx_engine = create_transcription_engine_from_settings(effective)

    # Check if engine supports diarization
    engine_info = await tx_engine.get_engine_info()
    supports_diarization = engine_info.get("supports_diarization", True)
    if not supports_diarization:
        logger.info(
            "Skipping diarization - not supported by %s",
            engine_info.get("name", "unknown engine"),
        )

    # Create diarization service
    dia_service = DiarizationService(
        device=dia_device,
        hf_token=effective.get("hf_token"),
    )

    # Determine if we should run diarization
    # Models must be pre-downloaded via Settings — no auto-downloads
    from core.pyannote_catalog import are_all_models_downloaded
    diarize = diarize_requested and effective["diarize"] and supports_diarization
    if diarize and not are_all_models_downloaded():
        logger.warning("Diarization requested but models not downloaded — skipping")
        diarize = False

    if not recording_id:
        raise ValueError("Missing recording_id in payload")

    # Track temp file for cleanup
    import tempfile
    from storage.factory import get_adapter
    from persistence.models import StorageLocation as StorageLoc

    temp_audio_file = None

    # Get recording and update status
    async with get_session_factory()() as session:
        result = await session.execute(select(Recording).where(Recording.id == recording_id))
        recording = result.scalar_one_or_none()

        if recording is None:
            raise ValueError(f"Recording not found: {recording_id}")

        # Update recording status to processing
        await session.execute(
            update(Recording).where(Recording.id == recording_id).values(status="processing")
        )
        await session.commit()

        # Broadcast status change so frontend updates immediately
        await broadcast("recordings", "status_changed", recording_id)

        # Resolve audio path - check for cloud storage
        audio_path = None

        if recording.storage_location_id:
            loc_result = await session.execute(
                select(StorageLoc).where(StorageLoc.id == recording.storage_location_id)
            )
            storage_loc = loc_result.scalar_one_or_none()

            if storage_loc and storage_loc.type == "cloud":
                # Download from cloud storage to temp file
                adapter = get_adapter(storage_loc)
                file_data = await adapter.read_file(recording.file_path)

                suffix = Path(recording.file_path).suffix
                temp_audio_file = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
                temp_audio_file.write(file_data)
                temp_audio_file.close()
                audio_path = temp_audio_file.name
            elif storage_loc and storage_loc.type == "local":
                base_path = storage_loc.config.get("path", "")
                audio_path = str(Path(base_path) / recording.file_path)

        # Fallback for recordings without storage_location_id
        if audio_path is None:
            audio_path = recording.file_path

    try:
        # Run transcription with streaming progress
        options = TranscriptionOptions(
            language=language,
            model_size=effective["model"],
            compute_type=effective["compute_type"],
            batch_size=effective["batch_size"],
        )

        # Serialize GPU-heavy operations so concurrent uploads don't OOM-crash.
        # Each thread has its own event loop, so the lock safely blocks the
        # second job's thread until the first finishes GPU work.
        logger.info("Waiting for GPU lock for recording %s", recording_id)
        with _gpu_lock:
            logger.info("Acquired GPU lock for recording %s", recording_id)

            transcription_result: TranscriptionResult | None = None
            async for item in tx_engine.transcribe_stream(audio_path, options):
                if isinstance(item, TranscriptionProgress):
                    # Map engine progress (0-1) to job progress (0-60%)
                    await progress_callback(item.progress * 60)
                elif isinstance(item, TranscriptionResult):
                    transcription_result = item

            if transcription_result is None:
                raise ValueError("Transcription did not produce a result")

            detected_language = transcription_result.language
            # Convert segments to dict format for diarization service
            segments_data = [
                {
                    "start": seg.start,
                    "end": seg.end,
                    "text": seg.text,
                    "confidence": seg.confidence,
                    "words": [
                        {"word": w.word, "start": w.start, "end": w.end, "score": w.confidence}
                        for w in seg.words
                    ] if seg.words else [],
                }
                for seg in transcription_result.segments
            ]
            speakers_found: list[str] = []

            # Run diarization if enabled
            if diarize and segments_data:
                # Wrap progress for diarization phase (60-95%)
                async def diarization_progress(p: float) -> None:
                    await progress_callback(60 + p * 0.35)

                try:
                    logger.info("Starting diarization for %s with %d segments", audio_path, len(segments_data))
                    diarization_result = await dia_service.diarize(
                        audio_path=audio_path,
                        segments=segments_data,
                        progress_callback=diarization_progress,
                    )
                    segments_data = diarization_result["segments"]
                    speakers_found = diarization_result["speakers"]
                    logger.info("Diarization found %d speakers", len(speakers_found))
                except Exception as e:
                    # Log but don't fail - diarization is optional
                    logger.warning("Diarization failed, continuing without speakers: %s", e, exc_info=True)

            logger.info("Released GPU lock for recording %s", recording_id)

        await progress_callback(95)

        # Calculate word count
        word_count = sum(len(seg.get("text", "").split()) for seg in segments_data)

        # Create transcript, segments, and speakers in database
        async with get_session_factory()() as session:
            # Delete existing transcript if re-transcribing (CASCADE deletes segments, speakers, etc.)
            existing = await session.execute(
                select(Transcript).where(Transcript.recording_id == recording_id)
            )
            existing_transcript = existing.scalar_one_or_none()
            if existing_transcript:
                await session.delete(existing_transcript)
                await session.flush()

            # Create transcript
            transcript = Transcript(
                recording_id=recording_id,
                language=detected_language,
                model_used=transcription_result.model_used or f"{engine_info.get('name', 'unknown')}-{effective['model']}",
                word_count=word_count,
            )
            session.add(transcript)
            await session.flush()

            # Create segments with speaker labels
            for idx, seg in enumerate(segments_data):
                segment = Segment(
                    transcript_id=transcript.id,
                    segment_index=idx,
                    speaker=seg.get("speaker"),  # Now includes speaker from diarization
                    start_time=seg.get("start", 0.0),
                    end_time=seg.get("end", 0.0),
                    text=seg.get("text", ""),
                    confidence=seg.get("confidence") or seg.get("score"),
                )
                session.add(segment)

            # Create speaker entries for renaming
            for speaker_label in speakers_found:
                speaker = Speaker(
                    transcript_id=transcript.id,
                    speaker_label=speaker_label,
                )
                session.add(speaker)

            # Update recording status to completed (only if still processing — cancel may have set it to cancelled)
            await session.execute(
                update(Recording)
                .where(Recording.id == recording_id, Recording.status == "processing")
                .values(status="completed")
            )
            await session.commit()

            # Check if the recording was cancelled while we were working
            rec_result = await session.execute(select(Recording.status).where(Recording.id == recording_id))
            rec_status = rec_result.scalar_one_or_none()

            transcript_id = transcript.id

        if rec_status == "cancelled":
            logger.info("Recording %s was cancelled during transcription, discarding result", recording_id)
            return {"transcript_id": transcript_id, "cancelled": True}

        # Broadcast status change so frontend updates immediately
        await broadcast("recordings", "status_changed", recording_id)
        await emit_event("transcription.complete", recording_id=recording_id, transcript_id=transcript_id)

        # Auto-queue embedding job if service is available
        from services.embedding import embedding_service
        if embedding_service.is_available():
            try:
                await job_queue.enqueue("embed", {"transcript_id": transcript_id})
                logger.info("Queued embedding job for transcript %s", transcript_id)
            except Exception as e:
                logger.warning("Failed to queue embedding job: %s", e)

        # Auto-queue summarization job if requested
        auto_summary = payload.get("auto_summary", False)
        logger.info("auto_summary flag in transcription job: %s", auto_summary)
        if auto_summary:
            try:
                summary_job_id = await job_queue.enqueue("summarize", {"transcript_id": transcript_id})
                logger.info("Queued summarization job %s for transcript %s", summary_job_id, transcript_id)
            except Exception as e:
                logger.warning("Failed to queue summarization job: %s", e)

        await progress_callback(100)

        logger.info(
            "Transcription complete for recording %s: %d segments, %d words, %d speakers",
            recording_id,
            len(segments_data),
            word_count,
            len(speakers_found),
        )

        return {
            "transcript_id": transcript_id,
            "segment_count": len(segments_data),
            "word_count": word_count,
            "language": detected_language,
            "speaker_count": len(speakers_found),
        }

    except JobCancelled:
        # Update recording status to cancelled
        async with get_session_factory()() as session:
            await session.execute(
                update(Recording).where(Recording.id == recording_id).values(status="cancelled")
            )
            await session.commit()
        # Broadcast status change so frontend updates immediately
        await broadcast("recordings", "status_changed", recording_id)
        logger.info("Transcription cancelled for recording %s", recording_id)
        raise

    except Exception as e:
        # Update recording status to failed (only if still processing — cancel may have set it to cancelled)
        async with get_session_factory()() as session:
            await session.execute(
                update(Recording)
                .where(Recording.id == recording_id, Recording.status == "processing")
                .values(status="failed")
            )
            await session.commit()
        # Broadcast status change so frontend updates immediately
        await broadcast("recordings", "status_changed", recording_id)
        await emit_event("transcription.failed", recording_id=recording_id, error=str(e))
        logger.exception("Transcription failed for recording %s", recording_id)
        raise

    finally:
        # Clean up temp audio file if used
        if temp_audio_file:
            Path(temp_audio_file.name).unlink(missing_ok=True)

        # Clean up ML models to free memory
        # This is important for releasing GPU memory between jobs
        import gc

        try:
            if hasattr(tx_engine, 'cleanup'):
                tx_engine.cleanup()
                logger.debug("Cleaned up transcription engine")
        except Exception as e:
            logger.debug("Error cleaning up transcription engine: %s", e)

        try:
            if hasattr(dia_service, 'cleanup'):
                dia_service.cleanup()
                logger.debug("Cleaned up diarization service")
        except Exception as e:
            logger.debug("Error cleaning up diarization service: %s", e)

        # Force garbage collection
        gc.collect()
        logger.debug("Forced garbage collection after transcription job")


# Register the transcription handler
job_queue.register_handler("transcribe", handle_transcription)


async def handle_embedding(
    payload: dict[str, Any], progress_callback: ProgressCallback
) -> dict[str, Any]:
    """Generate embeddings for all segments in a transcript.

    Args:
        payload: Job payload with:
            - transcript_id: Required transcript ID
        progress_callback: Callback to report progress.

    Returns:
        Result dictionary with segment_count.

    Raises:
        ValueError: If transcript not found.
    """
    import asyncio
    from sqlalchemy.exc import OperationalError
    from services.embedding import embedding_service, embedding_to_bytes

    transcript_id = payload.get("transcript_id")
    if not transcript_id:
        raise ValueError("Missing transcript_id in payload")

    # Check if embeddings are available
    if not embedding_service.is_available():
        logger.warning("Embedding service not available, skipping embed job")
        return {"segment_count": 0, "skipped": True}

    # Small delay to avoid SQLite lock contention with transcription commit
    await asyncio.sleep(2)

    # Load segments for this transcript
    async with get_session_factory()() as session:
        result = await session.execute(
            select(Segment)
            .where(Segment.transcript_id == transcript_id)
            .order_by(Segment.segment_index)
        )
        segments = result.scalars().all()

        if not segments:
            logger.warning("No segments found for transcript %s", transcript_id)
            return {"segment_count": 0}

        logger.info("Generating embeddings for %d segments", len(segments))

        # Extract texts
        texts = [seg.text for seg in segments]
        segment_ids = [seg.id for seg in segments]

    # Generate embeddings in batch
    await progress_callback(10)
    embeddings = await embedding_service.embed_texts(texts)
    await progress_callback(80)

    # Store embeddings with retry for database lock
    # NOTE: Don't update progress inside the session to avoid concurrent DB writes
    max_retries = 3
    for attempt in range(max_retries):
        try:
            async with get_session_factory()() as session:
                for seg_id, emb in zip(segment_ids, embeddings):
                    existing = await session.get(SegmentEmbedding, seg_id)
                    if existing:
                        existing.embedding = embedding_to_bytes(emb)
                        existing.model_used = embedding_service._model_name
                    else:
                        segment_embedding = SegmentEmbedding(
                            segment_id=seg_id,
                            embedding=embedding_to_bytes(emb),
                            model_used=embedding_service._model_name,
                        )
                        session.add(segment_embedding)
                await session.commit()
                break  # Success
        except OperationalError as e:
            if "database is locked" in str(e) and attempt < max_retries - 1:
                logger.warning("Database locked, retrying in %d seconds (attempt %d/%d)",
                             2 ** attempt, attempt + 1, max_retries)
                await asyncio.sleep(2 ** attempt)
            else:
                raise

    await progress_callback(100)

    # Clean up embedding model to free memory
    try:
        embedding_service.cleanup()
        logger.debug("Cleaned up embedding model")
    except Exception as e:
        logger.debug("Error cleaning up embedding model: %s", e)

    logger.info(
        "Embeddings complete for transcript %s: %d segments",
        transcript_id,
        len(segments),
    )

    return {"segment_count": len(segments), "transcript_id": transcript_id}


# Register the embedding handler
job_queue.register_handler("embed", handle_embedding)


async def handle_summarization(
    payload: dict[str, Any], progress_callback: ProgressCallback
) -> dict[str, Any]:
    """Generate AI summary for a transcript.

    Args:
        payload: Job payload with:
            - transcript_id: Required transcript ID
            - job_id: Job ID for progress broadcasting
        progress_callback: Callback to report progress.

    Returns:
        Result dictionary with success status.

    Raises:
        ValueError: If transcript not found or AI not available.
    """
    from api.routes.ai import _ensure_active_model_loaded
    from api.routes.sync import broadcast_job_progress
    from core.factory import get_factory
    from core.interfaces import ChatOptions

    transcript_id = payload.get("transcript_id")
    job_id = payload.get("job_id")
    recording_id = None

    if not transcript_id:
        raise ValueError("Missing transcript_id in payload")

    # Helper to broadcast progress to frontend
    async def broadcast_progress(progress: float, status: str = "running"):
        if job_id:
            logger.info(
                "Broadcasting summarization progress: job=%s status=%s progress=%d recording=%s",
                job_id, status, progress, recording_id
            )
            try:
                await broadcast_job_progress(
                    job_id=job_id,
                    job_type="summarize",
                    status=status,
                    progress=progress,
                    task_name="AI Summary",
                    recording_id=recording_id,
                    transcript_id=transcript_id,
                )
            except Exception as e:
                logger.warning("Failed to broadcast progress: %s", e)

    logger.info("Starting summarization job %s for transcript %s", job_id, transcript_id)

    await progress_callback(10)
    await broadcast_progress(10)

    # Ensure AI model path is loaded from active_model.json
    # This is needed because job handlers run in separate threads
    # and may not have settings.AI_MODEL_PATH set
    _ensure_active_model_loaded()

    from core.config import settings
    logger.info("AI model path after ensure_active_model_loaded: %s", settings.AI_MODEL_PATH)

    # Check if AI service is available
    factory = get_factory()
    ai_service = factory.create_ai_service()

    service_info = await ai_service.get_service_info()
    logger.info("AI service info: %s", service_info)

    if not await ai_service.is_available():
        service_info = await ai_service.get_service_info()
        logger.warning(
            "AI service not available, skipping summarization. Service info: %s",
            service_info,
        )
        await broadcast_progress(0, "failed")
        return {"success": False, "skipped": True, "reason": "AI service not available"}

    await progress_callback(20)
    await broadcast_progress(20)

    # Get transcript text
    async with get_session_factory()() as session:
        result = await session.execute(
            select(Transcript).where(Transcript.id == transcript_id)
        )
        transcript = result.scalar_one_or_none()

        if not transcript:
            raise ValueError(f"Transcript not found: {transcript_id}")

        recording_id = transcript.recording_id

        # Get segments
        seg_result = await session.execute(
            select(Segment)
            .where(Segment.transcript_id == transcript_id)
            .order_by(Segment.segment_index)
        )
        segments = seg_result.scalars().all()

        if not segments:
            logger.warning("No segments found for transcript %s", transcript_id)
            return {"success": False, "reason": "No segments found"}

        # Build transcript text
        transcript_text = "\n".join([
            f"[{seg.speaker or 'Speaker'}]: {seg.text}"
            for seg in segments
        ])

    await progress_callback(30)
    await broadcast_progress(30)

    # Generate summary
    try:
        logger.info("Generating summary for transcript %s (text length: %d chars)", transcript_id, len(transcript_text))
        options = ChatOptions(temperature=0.3, max_tokens=2048)
        result = await ai_service.summarize_transcript(transcript_text, options)
        logger.info("Summary generated successfully: summary=%d chars, key_points=%s, topics=%s",
            len(result.summary) if result.summary else 0,
            len(result.key_points) if result.key_points else 0,
            len(result.topics) if result.topics else 0
        )
        await progress_callback(80)
        await broadcast_progress(80)

        # Store summary in transcript
        async with get_session_factory()() as session:
            await session.execute(
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
            await session.commit()

        # Broadcast update so frontend refreshes
        await broadcast("recordings", "summary_generated", recording_id)

        await progress_callback(100)
        await broadcast_progress(100, "completed")

        logger.info("Summarization complete for transcript %s", transcript_id)
        return {"success": True, "transcript_id": transcript_id}

    except Exception as e:
        logger.exception("Summarization failed for transcript %s", transcript_id)
        await broadcast_progress(0, "failed")
        return {"success": False, "error": str(e)}


# Register the summarization handler
job_queue.register_handler("summarize", handle_summarization)


# Document processing handler
async def handle_document_processing(
    payload: dict[str, Any], update_progress: ProgressCallback
) -> dict[str, Any]:
    """Process a document: extract text and generate embeddings."""
    document_id = payload["document_id"]

    # Get job_id from payload for cancellation checking
    job_id = payload.get("job_id")

    async with get_session_factory()() as session:
        from persistence.models import Document, DocumentEmbedding
        from services.document_processor import document_processor, ProcessingCancelledError, cleanup_ocr_model
        from services.storage import storage_service

        # Create cancellation check function
        def check_cancelled() -> bool:
            """Check if this job has been cancelled."""
            if job_id and job_id in job_queue._cancelled_jobs:
                return True
            return False

        # Get document
        result = await session.execute(
            select(Document).where(Document.id == document_id)
        )
        doc = result.scalar_one_or_none()
        if not doc:
            raise ValueError(f"Document {document_id} not found")

        # Update status
        doc.status = "processing"
        await session.commit()
        await broadcast("documents", "status_changed", document_id)

        await update_progress(10)

        try:
            import tempfile
            from storage.factory import get_adapter
            from persistence.models import StorageLocation

            temp_file = None
            file_path = None
            enable_ocr = False  # Initialize early for exception handler

            # Check if stored in cloud storage
            if doc.storage_location_id:
                loc_result = await session.execute(
                    select(StorageLocation).where(StorageLocation.id == doc.storage_location_id)
                )
                storage_loc = loc_result.scalar_one_or_none()

                if storage_loc and storage_loc.type == "cloud":
                    # Download from cloud storage to temp file
                    adapter = get_adapter(storage_loc)
                    file_data = await adapter.read_file(doc.file_path)

                    # Create temp file with original extension
                    suffix = Path(doc.file_path).suffix
                    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
                    temp_file.write(file_data)
                    temp_file.close()
                    file_path = Path(temp_file.name)
                elif storage_loc and storage_loc.type == "local":
                    # Local storage - doc.file_path already contains full relative path
                    # (e.g., "verbatim/Police Report.PDF" when storage path is "verbatim")
                    file_path = Path(doc.file_path)

            # Fallback: check if already absolute or use default storage
            if file_path is None:
                file_path = Path(doc.file_path)
                if not file_path.is_absolute():
                    file_path = storage_service.get_full_path(doc.file_path)

            if not file_path.exists():
                raise FileNotFoundError(f"File not found: {file_path}")

            # Re-read metadata to pick up any concurrent changes
            # (e.g. /ocr route may have set enable_ocr=True after upload enqueued this job)
            await session.refresh(doc, ["metadata_"])
            enable_ocr = doc.metadata_.get("enable_ocr", False)

            # Extract text (pass cancellation check for OCR operations)
            extraction_result = document_processor.process(
                file_path,
                doc.mime_type,
                enable_ocr=enable_ocr,
                check_cancelled=check_cancelled if job_id else None,
            )

            # Clean up temp file if used
            if temp_file:
                Path(temp_file.name).unlink(missing_ok=True)

            await update_progress(50)

            # Refresh metadata from DB before merging, so concurrent changes
            # (e.g. /ocr route setting enable_ocr=True) aren't overwritten
            await session.refresh(doc, ["metadata_"])

            # Update document
            doc.extracted_text = extraction_result.get("text")
            doc.extracted_markdown = extraction_result.get("markdown")
            doc.page_count = extraction_result.get("page_count")
            if extraction_result.get("metadata"):
                doc.metadata_.update(extraction_result.get("metadata", {}))
                flag_modified(doc, "metadata_")

            await update_progress(70)

            # Generate embeddings if text was extracted
            if doc.extracted_text and len(doc.extracted_text.strip()) > 0:
                await _generate_document_embeddings(session, doc, update_progress)

            doc.status = "completed"
            doc.error_message = None
            await session.commit()
            await broadcast("documents", "status_changed", document_id)
            await emit_event("document.processed", document_id=document_id)

            await update_progress(100)

            # Clean up OCR model if it was used (free memory)
            if enable_ocr:
                cleanup_ocr_model()

            return {"document_id": document_id, "status": "completed"}

        except ProcessingCancelledError as e:
            # Clean up temp file
            if temp_file:
                Path(temp_file.name).unlink(missing_ok=True)
            # Clean up OCR model
            cleanup_ocr_model()
            # Set status to cancelled
            doc.status = "cancelled"
            doc.error_message = "Processing was cancelled"
            await session.commit()
            await broadcast("documents", "status_changed", document_id)
            logger.info(f"Document {document_id} processing cancelled")
            return {"document_id": document_id, "status": "cancelled"}

        except Exception as e:
            # Clean up temp file on error
            if temp_file:
                Path(temp_file.name).unlink(missing_ok=True)
            # Clean up OCR model on error
            if enable_ocr:
                cleanup_ocr_model()
            doc.status = "failed"
            doc.error_message = str(e)
            await session.commit()
            await broadcast("documents", "status_changed", document_id)
            raise


async def _generate_document_embeddings(
    session: AsyncSession, doc: "Document", update_progress: ProgressCallback
) -> None:
    """Generate embeddings for document chunks."""
    from persistence.models import DocumentEmbedding
    from services.embedding import embedding_service, embedding_to_bytes

    if not embedding_service.is_available():
        logger.warning("Embedding service not available, skipping embeddings")
        return

    # Delete existing embeddings
    existing = (await session.execute(
        select(DocumentEmbedding).where(DocumentEmbedding.document_id == doc.id)
    )).scalars().all()
    for emb in existing:
        await session.delete(emb)

    # Chunk text
    chunks = _chunk_document_text(doc.extracted_text, max_tokens=500)

    # Generate embeddings in batch for efficiency
    chunk_texts = [chunk["text"] for chunk in chunks]
    if not chunk_texts:
        return

    embeddings = await embedding_service.embed_texts(chunk_texts)

    # Store embeddings
    for i, (chunk, embedding_vector) in enumerate(zip(chunks, embeddings)):
        try:
            doc_embedding = DocumentEmbedding(
                document_id=doc.id,
                chunk_index=i,
                chunk_text=chunk["text"],
                chunk_metadata=chunk.get("metadata", {}),
                embedding=embedding_to_bytes(embedding_vector),
                model_used=embedding_service._model_name,
            )
            session.add(doc_embedding)
        except Exception as e:
            logger.warning(f"Failed to embed chunk {i}: {e}")

    await session.commit()
    logger.info(f"Generated {len(chunks)} embeddings for document {doc.id}")


def _chunk_document_text(text: str, max_tokens: int = 500) -> list[dict]:
    """Split text into chunks."""
    import re

    sentences = re.split(r'(?<=[.!?])\s+', text)
    chunks = []
    current_chunk = []
    current_length = 0

    for sentence in sentences:
        sentence_tokens = len(sentence) // 4

        if current_length + sentence_tokens > max_tokens and current_chunk:
            chunks.append({
                "text": " ".join(current_chunk),
                "metadata": {"chunk_index": len(chunks)},
            })
            current_chunk = []
            current_length = 0

        current_chunk.append(sentence)
        current_length += sentence_tokens

    if current_chunk:
        chunks.append({
            "text": " ".join(current_chunk),
            "metadata": {"chunk_index": len(chunks)},
        })

    return chunks


# Register document processing handler
job_queue.register_handler("process_document", handle_document_processing)


async def handle_quality_review(
    payload: dict[str, Any], progress_callback: ProgressCallback
) -> dict[str, Any]:
    """Run AI quality review on a transcript.

    Args:
        payload: Job payload with:
            - transcript_id: Required transcript ID
            - job_id: Job ID for progress broadcasting
            - context_hint: Optional context about the recording
            - aggressiveness: Review level (conservative/moderate/aggressive)
        progress_callback: Callback to report progress.

    Returns:
        Result dictionary with review stats.
    """
    from api.routes.ai import _ensure_active_model_loaded
    from api.routes.sync import broadcast_job_progress
    from core.factory import get_factory
    from services.quality_review import run_quality_review

    transcript_id = payload.get("transcript_id")
    job_id = payload.get("job_id")
    context_hint = payload.get("context_hint")
    aggressiveness = payload.get("aggressiveness", "moderate")

    if not transcript_id:
        raise ValueError("Missing transcript_id in payload")

    # Resolve recording_id for broadcast
    recording_id = None
    async with get_session_factory()() as session:
        from persistence.models import Transcript
        result = await session.execute(
            select(Transcript.recording_id).where(Transcript.id == transcript_id)
        )
        row = result.first()
        if row:
            recording_id = row[0]

    async def broadcast_progress(progress: float, status: str = "running"):
        if job_id:
            try:
                await broadcast_job_progress(
                    job_id=job_id,
                    job_type="quality_review",
                    status=status,
                    progress=progress,
                    task_name="AI Quality Review",
                    recording_id=recording_id,
                    transcript_id=transcript_id,
                )
            except Exception as e:
                logger.warning("Failed to broadcast quality review progress: %s", e)

    logger.info("Starting quality review job %s for transcript %s", job_id, transcript_id)

    await progress_callback(5)
    await broadcast_progress(5)

    # Ensure AI model is loaded
    _ensure_active_model_loaded()

    factory = get_factory()
    ai_service = factory.create_ai_service()

    if not await ai_service.is_available():
        await broadcast_progress(0, "failed")
        return {"success": False, "skipped": True, "reason": "AI service not available"}

    await progress_callback(10)
    await broadcast_progress(10)

    try:
        async def combined_progress(progress: float):
            await progress_callback(progress)
            await broadcast_progress(progress)

        review_result = await run_quality_review(
            transcript_id=transcript_id,
            job_id=job_id,
            context_hint=context_hint,
            aggressiveness=aggressiveness,
            ai_service=ai_service,
            progress_callback=combined_progress,
        )

        await progress_callback(100)
        await broadcast_progress(100, "completed")

        # Broadcast completion so frontend refreshes
        await broadcast("transcripts", "quality_review_complete", transcript_id)

        logger.info("Quality review complete for transcript %s", transcript_id)
        return {
            "success": True,
            "transcript_id": transcript_id,
            "stats": {
                "corrections": review_result.stats.corrections,
                "removals": review_result.stats.removals,
                "merges": review_result.stats.merges,
                "blank_removals": review_result.stats.blank_removals,
            },
        }

    except Exception as e:
        logger.exception("Quality review failed for transcript %s", transcript_id)
        await broadcast_progress(0, "failed")
        return {"success": False, "error": str(e)}


# Register the quality review handler
job_queue.register_handler("quality_review", handle_quality_review)

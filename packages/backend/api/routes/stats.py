"""Statistics endpoints for dashboard."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_active_project_ids
from core.config import settings
from persistence.database import get_db
from persistence.models import Recording, Transcript, Segment, Project, Job, Document
from services.storage import get_active_storage_location

router = APIRouter(prefix="/stats", tags=["stats"])


class RecordingStats(BaseModel):
    """Statistics about recordings."""

    total_recordings: int
    total_duration_seconds: float
    by_status: dict[str, int]
    avg_duration_seconds: float | None


class TranscriptionStats(BaseModel):
    """Statistics about transcriptions."""

    total_transcripts: int
    total_segments: int
    total_words: int
    languages: dict[str, int]


class ProjectStats(BaseModel):
    """Statistics about projects."""

    total_projects: int
    last_updated: str | None  # ISO timestamp of most recently updated project


class ProcessingStats(BaseModel):
    """Statistics about processing jobs."""

    active_count: int  # queued + running jobs
    queued_count: int
    running_count: int


class DocumentStats(BaseModel):
    """Statistics about documents."""

    total_documents: int


class DashboardStats(BaseModel):
    """Combined dashboard statistics."""

    recordings: RecordingStats
    transcriptions: TranscriptionStats
    projects: ProjectStats
    processing: ProcessingStats
    documents: DocumentStats


@router.get("", response_model=DashboardStats)
async def get_stats(
    db: Annotated[AsyncSession, Depends(get_db)],
    active_project_ids: Annotated[list[str], Depends(get_active_project_ids)] = [],
    project_ids: Annotated[str | None, Query(description="Comma-separated project IDs for scoped stats")] = None,
) -> DashboardStats:
    """Get dashboard statistics.

    Optionally scoped to specific projects via the X-Active-Project header
    or the project_ids query parameter. The explicit project_ids param
    takes precedence over the header.
    """

    # Determine which project IDs to scope stats to
    scope_project_ids: list[str] = []
    if project_ids and project_ids.strip():
        scope_project_ids = [pid.strip() for pid in project_ids.split(",") if pid.strip()]
    elif active_project_ids:
        scope_project_ids = active_project_ids

    # Build storage location filter (reused across queries)
    active_location = await get_active_storage_location()
    storage_filter = None
    if active_location:
        if active_location.type == "cloud":
            storage_filter = or_(
                Recording.storage_location_id == active_location.id,
                Recording.storage_location_id.is_(None),
            )
        elif active_location.config.get("path"):
            active_path = active_location.config.get("path")
            media_path = str(settings.MEDIA_DIR)
            storage_filter = or_(
                Recording.storage_location_id == active_location.id,
                Recording.storage_location_id.is_(None),
                Recording.file_path.startswith(active_path),
                Recording.file_path.startswith(media_path),
            )

    # Recording stats
    recording_query = select(
        func.count(Recording.id).label("total"),
        func.sum(Recording.duration_seconds).label("total_duration"),
        func.avg(Recording.duration_seconds).label("avg_duration"),
    )
    if storage_filter is not None:
        recording_query = recording_query.where(storage_filter)
    if scope_project_ids:
        recording_query = recording_query.where(Recording.project_id.in_(scope_project_ids))
    recording_result = await db.execute(recording_query)
    recording_row = recording_result.one()

    # Recordings by status
    status_query = select(
        Recording.status,
        func.count(Recording.id).label("count"),
    ).group_by(Recording.status)
    if storage_filter is not None:
        status_query = status_query.where(storage_filter)
    if scope_project_ids:
        status_query = status_query.where(Recording.project_id.in_(scope_project_ids))
    status_result = await db.execute(status_query)
    status_counts = {row.status: row.count for row in status_result.all()}

    # Get recording IDs for this storage location (and project scope) to filter transcripts/segments
    rec_id_query = select(Recording.id)
    has_rec_filter = False
    if storage_filter is not None:
        rec_id_query = rec_id_query.where(storage_filter)
        has_rec_filter = True
    if scope_project_ids:
        rec_id_query = rec_id_query.where(Recording.project_id.in_(scope_project_ids))
        has_rec_filter = True

    if has_rec_filter:
        rec_ids_result = await db.execute(rec_id_query)
        recording_ids = [row[0] for row in rec_ids_result.all()]
    else:
        recording_ids = None

    # Transcript stats (filtered by recordings in this storage location)
    transcript_query = select(
        func.count(Transcript.id).label("total"),
        func.sum(Transcript.word_count).label("total_words"),
    )
    if recording_ids is not None:
        if recording_ids:
            transcript_query = transcript_query.where(Transcript.recording_id.in_(recording_ids))
        else:
            # No recordings means no transcripts
            transcript_query = transcript_query.where(False)
    transcript_result = await db.execute(transcript_query)
    transcript_row = transcript_result.one()

    # Segment count (filtered by recordings in this storage location)
    segment_query = select(func.count(Segment.id)).select_from(Segment).join(Transcript)
    if recording_ids is not None:
        if recording_ids:
            segment_query = segment_query.where(Transcript.recording_id.in_(recording_ids))
        else:
            segment_query = segment_query.where(False)
    segment_count_result = await db.execute(segment_query)
    segment_count = segment_count_result.scalar() or 0

    # Languages breakdown (filtered by recordings in this storage location)
    language_query = select(
        Transcript.language,
        func.count(Transcript.id).label("count"),
    ).where(Transcript.language.is_not(None)).group_by(Transcript.language)
    if recording_ids is not None:
        if recording_ids:
            language_query = language_query.where(Transcript.recording_id.in_(recording_ids))
        else:
            language_query = language_query.where(False)
    language_result = await db.execute(language_query)
    language_counts = {row.language: row.count for row in language_result.all()}

    # Project stats
    project_result = await db.execute(
        select(
            func.count(Project.id).label("total"),
            func.max(Project.updated_at).label("last_updated"),
        )
    )
    project_row = project_result.one()
    last_updated_str = (
        project_row.last_updated.isoformat() if project_row.last_updated else None
    )

    # Processing stats (queued + running jobs)
    job_status_result = await db.execute(
        select(
            Job.status,
            func.count(Job.id).label("count"),
        )
        .where(Job.status.in_(["queued", "running"]))
        .group_by(Job.status)
    )
    job_counts = {row.status: row.count for row in job_status_result.all()}
    queued_count = job_counts.get("queued", 0)
    running_count = job_counts.get("running", 0)

    # Document stats
    document_count_query = select(func.count(Document.id))
    if scope_project_ids:
        document_count_query = document_count_query.where(Document.project_id.in_(scope_project_ids))
    document_count_result = await db.execute(document_count_query)
    document_count = document_count_result.scalar() or 0

    return DashboardStats(
        recordings=RecordingStats(
            total_recordings=recording_row.total or 0,
            total_duration_seconds=recording_row.total_duration or 0.0,
            by_status=status_counts,
            avg_duration_seconds=recording_row.avg_duration,
        ),
        transcriptions=TranscriptionStats(
            total_transcripts=transcript_row.total or 0,
            total_segments=segment_count,
            total_words=transcript_row.total_words or 0,
            languages=language_counts,
        ),
        projects=ProjectStats(
            total_projects=project_row.total or 0,
            last_updated=last_updated_str,
        ),
        processing=ProcessingStats(
            active_count=queued_count + running_count,
            queued_count=queued_count,
            running_count=running_count,
        ),
        documents=DocumentStats(
            total_documents=document_count,
        ),
    )

"""Recording file management endpoints."""

import asyncio
import io
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from core.config import settings
from persistence import get_db
from persistence.models import Job, Project, Recording, RecordingTag, RecordingTemplate, Speaker, StorageLocation, Tag, Transcript
from services.jobs import job_queue
from services.storage import storage_service, get_storage_adapter, get_active_storage_location
from storage.factory import get_adapter
from api.dependencies import get_active_project_id
from api.routes.sync import broadcast
from core.events import emit as emit_event

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/recordings", tags=["recordings"])


VIDEO_MIME_PREFIXES = ("video/",)


def _get_ffmpeg_path() -> str:
    """Get the path to ffmpeg binary.

    Checks for bundled ffmpeg in Electron app resources first,
    then falls back to system PATH.
    """
    import os
    import shutil

    # Check for bundled ffmpeg in Electron resources
    # When running in Electron, VERBATIM_ELECTRON=1 is set
    if os.environ.get("VERBATIM_ELECTRON") == "1":
        # Try to find the resources path from the Python path
        #   macOS:   resources/python/bin/python3  (3 levels up)
        #   Windows: resources/python/python.exe   (2 levels up)
        import sys
        python_path = Path(sys.executable)
        if sys.platform == "win32":
            resources_path = python_path.parent.parent          # resources/python/python.exe
        else:
            resources_path = python_path.parent.parent.parent   # resources/python/bin/python3

        if sys.platform == "win32":
            bundled_ffmpeg = resources_path / "ffmpeg" / "ffmpeg.exe"
        else:
            bundled_ffmpeg = resources_path / "ffmpeg" / "ffmpeg"

        if bundled_ffmpeg.exists():
            logger.debug("Using bundled ffmpeg: %s", bundled_ffmpeg)
            return str(bundled_ffmpeg)

    # Fall back to system ffmpeg
    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        logger.debug("Using system ffmpeg: %s", system_ffmpeg)
        return system_ffmpeg

    # Return "ffmpeg" and let it fail with FileNotFoundError
    return "ffmpeg"


def _is_video(mime_type: str | None) -> bool:
    """Check if a MIME type is a video format."""
    return mime_type is not None and mime_type.startswith(VIDEO_MIME_PREFIXES)


def _extract_duration(content: bytes, filename: str) -> float | None:
    """Extract audio/video duration in seconds using mutagen."""
    try:
        from mutagen import File as MutagenFile

        audio = MutagenFile(io.BytesIO(content), filename=filename)
        if audio is not None and audio.info is not None:
            return round(audio.info.length, 2)
    except Exception:
        logger.debug("Could not extract duration from %s", filename)
    return None



# Allowed MIME types for audio/video uploads
ALLOWED_MIME_TYPES = {
    # Audio formats
    "audio/mpeg",
    "audio/mp3",
    "audio/wav",
    "audio/wave",
    "audio/x-wav",
    "audio/ogg",
    "audio/flac",
    "audio/x-flac",
    "audio/aac",
    "audio/m4a",
    "audio/x-m4a",
    "audio/mp4",  # Browser recording fallback
    "audio/webm",
    "audio/webm;codecs=opus",  # Browser recording format
    # Video formats
    "video/mp4",
    "video/webm",
    "video/ogg",
    "video/quicktime",
    "video/x-msvideo",
    "video/x-matroska",
}

# Maximum file size (10 GB)
MAX_FILE_SIZE = 10 * 1024 * 1024 * 1024


# Pydantic models for responses
class RecordingTemplateInfo(BaseModel):
    """Embedded recording template info in response."""

    id: str
    name: str
    description: str | None
    metadata_schema: list[dict]
    is_system: bool


class RecordingResponse(BaseModel):
    """Response model for a recording."""

    id: str
    project_ids: list[str] = Field(default_factory=list)
    template_id: str | None
    template: RecordingTemplateInfo | None = None
    title: str
    file_path: str
    file_name: str
    file_size: int | None
    duration_seconds: float | None
    mime_type: str | None
    metadata: dict = Field(default_factory=dict)
    status: str
    tag_ids: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class RecordingListResponse(BaseModel):
    """Response model for paginated list of recordings."""

    items: list[RecordingResponse]
    total: int
    page: int
    page_size: int
    total_pages: int


class RecordingCreateResponse(BaseModel):
    """Response model for recording creation."""

    id: str
    title: str
    file_name: str
    file_size: int
    mime_type: str
    status: str
    created_at: datetime


class MessageResponse(BaseModel):
    """Response model for simple messages."""

    message: str
    id: str | None = None


class TranscribeResponse(BaseModel):
    """Response model for transcribe request."""

    job_id: str
    status: str


def _template_to_info(t: RecordingTemplate | None) -> RecordingTemplateInfo | None:
    """Convert RecordingTemplate to RecordingTemplateInfo."""
    if not t:
        return None
    return RecordingTemplateInfo(
        id=t.id,
        name=t.name,
        description=t.description,
        metadata_schema=t.metadata_schema,
        is_system=t.is_system,
    )


def _recording_to_response(
    recording: Recording,
    tag_ids: list[str] | None = None,
    project_ids: list[str] | None = None,
    template: RecordingTemplate | None = None,
) -> RecordingResponse:
    """Convert a Recording model to a response model."""
    return RecordingResponse(
        id=recording.id,
        project_ids=project_ids or [],
        template_id=recording.template_id,
        template=_template_to_info(template),
        title=recording.title,
        file_path=recording.file_path,
        file_name=recording.file_name,
        file_size=recording.file_size,
        duration_seconds=recording.duration_seconds,
        mime_type=recording.mime_type,
        metadata=recording.metadata_ or {},
        status=recording.status,
        tag_ids=tag_ids if tag_ids is not None else [],
        created_at=recording.created_at,
        updated_at=recording.updated_at,
    )


@router.get("", response_model=RecordingListResponse)
async def list_recordings(
    db: Annotated[AsyncSession, Depends(get_db)],
    page: Annotated[int, Query(ge=1, description="Page number")] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, description="Items per page")] = 20,
    project_id: Annotated[str | None, Query(description="Filter by project ID")] = None,
    status: Annotated[str | None, Query(description="Filter by status (pending, processing, completed, failed)")] = None,
    search: Annotated[str | None, Query(description="Search by title or filename")] = None,
    sort_by: Annotated[str, Query(description="Sort field (created_at, title, duration)")] = "created_at",
    sort_order: Annotated[str, Query(description="Sort order (asc, desc)")] = "desc",
    date_from: Annotated[str | None, Query(description="Filter from date (ISO 8601, e.g. 2024-01-01)")] = None,
    date_to: Annotated[str | None, Query(description="Filter to date (ISO 8601, e.g. 2024-12-31)")] = None,
    tag_ids: Annotated[str | None, Query(description="Comma-separated tag IDs to filter by")] = None,
    speaker: Annotated[str | None, Query(description="Filter by speaker name")] = None,
    template_id: Annotated[str | None, Query(description="Filter by template ID")] = None,
    active_project_id: Annotated[str | None, Depends(get_active_project_id)] = None,
) -> RecordingListResponse:
    """List all recordings with pagination and filtering.

    Args:
        db: Database session.
        page: Page number (1-indexed).
        page_size: Number of items per page.
        project_id: Optional project ID filter.
        status: Optional status filter (pending, processing, completed, failed).
        search: Optional search string for title or filename.
        sort_by: Field to sort by (created_at, title, duration).
        sort_order: Sort direction (asc, desc).
        date_from: Optional start date filter (inclusive).
        date_to: Optional end date filter (inclusive).
        tag_ids: Optional comma-separated tag IDs to filter by.
        speaker: Optional speaker name to filter by.
        template_id: Optional template ID to filter by.

    Returns:
        Paginated list of recordings.
    """
    from sqlalchemy import or_

    # Build base query with template eager load
    query = select(Recording).options(selectinload(Recording.template))

    # Filter by active storage location (also include live recordings
    # and recordings with no storage location assigned for backward compat)
    active_location = await get_active_storage_location()
    if active_location:
        if active_location.type == "cloud":
            query = query.where(
                or_(
                    Recording.storage_location_id == active_location.id,
                    Recording.storage_location_id.is_(None),
                    Recording.file_path.startswith("live://"),
                )
            )
        elif active_location.config.get("path"):
            active_path = active_location.config.get("path")
            media_path = str(settings.MEDIA_DIR)
            query = query.where(
                or_(
                    Recording.storage_location_id == active_location.id,
                    Recording.storage_location_id.is_(None),
                    Recording.file_path.startswith(active_path),
                    Recording.file_path.startswith("live://"),
                    Recording.file_path.startswith(media_path),
                )
            )

    # Apply filters
    if project_id is not None:
        # Filter by project using FK
        query = query.where(Recording.project_id == project_id)

    if status is not None:
        query = query.where(Recording.status == status)

    if search is not None and search.strip():
        search_term = f"%{search.strip()}%"
        query = query.where(
            or_(
                Recording.title.ilike(search_term),
                Recording.file_name.ilike(search_term),
            )
        )

    # Date range filters
    if date_from is not None:
        try:
            from_date = datetime.fromisoformat(date_from)
            query = query.where(Recording.created_at >= from_date)
        except ValueError:
            pass  # Ignore invalid date format

    if date_to is not None:
        try:
            # Set to end of day for inclusive range
            to_date = datetime.fromisoformat(date_to).replace(hour=23, minute=59, second=59)
            query = query.where(Recording.created_at <= to_date)
        except ValueError:
            pass  # Ignore invalid date format

    # Tag filter — recordings must have ALL specified tags
    if tag_ids is not None and tag_ids.strip():
        tag_id_list = [t.strip() for t in tag_ids.split(",") if t.strip()]
        if tag_id_list:
            query = query.where(
                Recording.id.in_(
                    select(RecordingTag.recording_id)
                    .where(RecordingTag.tag_id.in_(tag_id_list))
                    .group_by(RecordingTag.recording_id)
                    .having(func.count(distinct(RecordingTag.tag_id)) == len(tag_id_list))
                )
            )

    # Speaker filter — recordings whose transcript contains this speaker
    if speaker is not None and speaker.strip():
        speaker_term = f"%{speaker.strip()}%"
        query = query.where(
            Recording.id.in_(
                select(Transcript.recording_id)
                .join(Speaker, Speaker.transcript_id == Transcript.id)
                .where(
                    or_(
                        Speaker.speaker_name.ilike(speaker_term),
                        Speaker.speaker_label.ilike(speaker_term),
                    )
                )
            )
        )

    # Template filter
    if template_id is not None:
        query = query.where(Recording.template_id == template_id)

    # Project scoping (from X-Active-Project header)
    if active_project_id:
        query = query.where(Recording.project_id == active_project_id)

    # Get total count
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # Calculate pagination
    total_pages = (total + page_size - 1) // page_size if total > 0 else 1
    offset = (page - 1) * page_size

    # Apply sorting
    sort_column = {
        "created_at": Recording.created_at,
        "title": Recording.title,
        "duration": Recording.duration_seconds,
    }.get(sort_by, Recording.created_at)

    if sort_order == "asc":
        query = query.order_by(sort_column.asc())
    else:
        query = query.order_by(sort_column.desc())

    # Apply pagination
    query = query.offset(offset).limit(page_size)
    result = await db.execute(query)
    recordings = result.scalars().all()

    # Batch-load tag IDs for all recordings
    recording_ids = [r.id for r in recordings]
    tag_map: dict[str, list[str]] = {rid: [] for rid in recording_ids}
    if recording_ids:
        tag_result = await db.execute(
            select(RecordingTag.recording_id, RecordingTag.tag_id).where(
                RecordingTag.recording_id.in_(recording_ids)
            )
        )
        for row in tag_result:
            tag_map[row.recording_id].append(row.tag_id)

    return RecordingListResponse(
        items=[
            _recording_to_response(
                r,
                tag_ids=tag_map.get(r.id, []),
                project_ids=[r.project_id] if r.project_id else [],
                template=r.template,
            )
            for r in recordings
        ],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@router.post("/upload", response_model=RecordingCreateResponse, status_code=status.HTTP_201_CREATED)
async def upload_recording(
    db: Annotated[AsyncSession, Depends(get_db)],
    file: Annotated[UploadFile, File(description="Audio or video file to upload")],
    title: Annotated[str | None, Form(description="Recording title")] = None,
    project_id: Annotated[str | None, Form(description="Project ID to associate with")] = None,
    template_id: Annotated[str | None, Form(description="Recording template ID")] = None,
    description: Annotated[str | None, Form(description="Recording description")] = None,
    tags: Annotated[str | None, Form(description="Comma-separated tag names")] = None,
    participants: Annotated[str | None, Form(description="Comma-separated participant names")] = None,
    location: Annotated[str | None, Form(description="Recording location")] = None,
    recorded_date: Annotated[str | None, Form(description="Actual recording date (ISO 8601)")] = None,
    quality: Annotated[str | None, Form(description="Recording quality preset used")] = None,
    extra_metadata: Annotated[str | None, Form(description="Additional metadata as JSON")] = None,
) -> RecordingCreateResponse:
    """Upload an audio or video file with optional metadata.

    Args:
        db: Database session.
        file: The uploaded file.
        title: Optional title for the recording.
        project_id: Optional project ID.
        description: Optional description.
        tags: Optional comma-separated tag names (created if new).
        participants: Optional comma-separated participant names.
        location: Optional recording location.
        recorded_date: Optional recording date (ISO 8601).
        quality: Optional quality preset used (low/medium/high/lossless).

    Returns:
        Created recording details.

    Raises:
        HTTPException: If file type is invalid or file is too large.
    """
    # Validate content type
    content_type = file.content_type or ""
    if content_type not in ALLOWED_MIME_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Unsupported media type: {content_type}. Allowed types: audio/*, video/*",
        )

    # Read file content
    content = await file.read()
    file_size = len(content)

    # Validate file size
    if file_size > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File too large. Maximum size is {MAX_FILE_SIZE // (1024 * 1024)} MB",
        )

    if file_size == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is empty",
        )

    # Sanitize filename to prevent path traversal attacks
    # First normalize backslashes to forward slashes (handles Windows-style paths on any OS)
    # Then use Path().name to get only the basename, stripping any directory components
    raw_filename = file.filename or "unknown"
    normalized_filename = raw_filename.replace("\\", "/")
    safe_filename = Path(normalized_filename).name
    # Additional safety: if filename is empty after sanitization, use a default
    if not safe_filename or safe_filename in (".", ".."):
        safe_filename = "unknown"

    # Extract audio duration
    duration = _extract_duration(content, safe_filename)

    # Build metadata dict from form fields
    metadata: dict = {}
    if description:
        metadata["description"] = description
    if participants:
        metadata["participants"] = [p.strip() for p in participants.split(",") if p.strip()]
    if location:
        metadata["location"] = location
    if recorded_date:
        metadata["recorded_date"] = recorded_date
    if quality:
        metadata["quality"] = quality

    # Merge extra_metadata JSON if provided (for template fields)
    if extra_metadata:
        try:
            extra = json.loads(extra_metadata)
            if isinstance(extra, dict):
                metadata.update(extra)
        except json.JSONDecodeError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid JSON in extra_metadata field",
            )

    # Validate template_id if provided
    if template_id:
        template_result = await db.execute(
            select(RecordingTemplate).where(RecordingTemplate.id == template_id)
        )
        if not template_result.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid recording template ID",
            )

    # Determine title - strip extension if title ends with file extension
    file_extension = Path(safe_filename).suffix.lower() if safe_filename else ""
    if title:
        # If user provided title ends with file extension, strip it
        if file_extension and title.lower().endswith(file_extension):
            recording_title = title[:-len(file_extension)]
        else:
            recording_title = title
    else:
        # Default to filename without extension
        recording_title = safe_filename.rsplit(".", 1)[0] if safe_filename else "Untitled Recording"

    # Create recording record first to get ID
    recording = Recording(
        title=recording_title,
        file_path="",  # Will be updated after saving
        file_name=safe_filename,
        file_size=file_size,
        duration_seconds=duration,
        mime_type=content_type,
        metadata_=metadata,
        template_id=template_id,
        project_id=project_id,  # Set project_id directly on recording
        status="pending",
    )
    db.add(recording)
    await db.flush()  # Get the ID without committing

    # Create or find tags and assign to recording
    if tags:
        tag_names = [t.strip() for t in tags.split(",") if t.strip()]
        seen: set[str] = set()
        for tag_name in tag_names:
            lower_name = tag_name.lower()
            if lower_name in seen:
                continue
            seen.add(lower_name)
            result = await db.execute(
                select(Tag).where(func.lower(Tag.name) == lower_name)
            )
            tag = result.scalar_one_or_none()
            if tag is None:
                tag = Tag(name=tag_name)
                db.add(tag)
                await db.flush()
            db.add(RecordingTag(recording_id=recording.id, tag_id=tag.id))

    # Get project name for human-readable file path
    project_name = None
    if project_id:
        project_result = await db.execute(select(Project).where(Project.id == project_id))
        project = project_result.scalar_one_or_none()
        project_name = project.name if project else None

    # Save file to storage with human-readable path
    try:
        file_path = await storage_service.save_upload(
            content=content,
            title=recording.title,
            filename=safe_filename,
            project_name=project_name,
        )
        recording.file_path = str(file_path)

        # Handle both Path (local) and string (cloud) returns
        if isinstance(file_path, Path):
            recording.file_name = file_path.name  # Update to actual filename (may have collision suffix)
            recording.title = file_path.stem  # Update title to match actual filename (handles collision suffix)
        else:
            # Cloud storage returns relative path string
            filename = file_path.split("/")[-1]
            recording.file_name = filename
            if "." in filename:
                recording.title = filename.rsplit(".", 1)[0]
            else:
                recording.title = filename

        # Set storage_location_id AFTER saving, based on actual storage used.
        # save_upload returns Path for local, str for cloud. If cloud storage
        # failed and silently fell back to local, we must not point at cloud.
        storage_location = await get_active_storage_location()
        if storage_location:
            if storage_location.type == "cloud" and isinstance(file_path, Path):
                # Cloud was intended but file was saved locally (fallback) —
                # don't assign cloud location ID to a locally-stored file
                pass
            else:
                recording.storage_location_id = storage_location.id

        await db.commit()
        await broadcast("recordings", "created", str(recording.id))
        # Emit event for plugin hooks (webhooks, audit, etc.)
        await emit_event("recording.created", recording_id=recording.id, project_id=project_id)
    except Exception as e:
        await db.rollback()
        # Log the actual error for debugging, but return generic message to client
        logger.exception("Failed to save uploaded file for recording %s", recording.id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to save uploaded file. Please try again.",
        )

    return RecordingCreateResponse(
        id=recording.id,
        title=recording.title,
        file_name=recording.file_name,
        file_size=recording.file_size,
        mime_type=recording.mime_type,
        status=recording.status,
        created_at=recording.created_at,
    )


class RecordingUpdateRequest(BaseModel):
    """Request model for updating a recording."""

    title: str | None = None
    template_id: str | None = Field(default=None, description="Set to a template ID, or empty string to unassign")
    metadata: dict | None = Field(default=None, description="Recording metadata (merged with existing)")


@router.patch("/{recording_id}", response_model=RecordingResponse)
async def update_recording(
    recording_id: str,
    body: RecordingUpdateRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> RecordingResponse:
    """Update a recording's title, project assignment, template, or metadata.

    Args:
        recording_id: The recording's unique ID.
        body: Fields to update.
        db: Database session.

    Returns:
        Updated recording details.

    Raises:
        HTTPException: If recording not found or title is empty.
    """
    result = await db.execute(select(Recording).where(Recording.id == recording_id))
    recording = result.scalar_one_or_none()

    if recording is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Recording not found: {recording_id}",
        )

    if body.title is not None:
        stripped = body.title.strip()
        if not stripped:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Title cannot be empty",
            )
        # Rename file in storage if title changed
        if stripped != recording.title and recording.file_path:
            try:
                new_path = await storage_service.rename_item(
                    recording.file_path, stripped, recording.storage_location_id
                )
                if str(new_path) != recording.file_path:
                    recording.file_path = str(new_path)
                    if isinstance(new_path, Path):
                        recording.file_name = new_path.name
                    else:
                        recording.file_name = str(new_path).split("/")[-1]
            except Exception as e:
                logger.warning("Could not rename file for recording %s: %s", recording_id, e)
        recording.title = stripped

    if body.template_id is not None:
        # Validate template_id if not empty
        if body.template_id:
            template_result = await db.execute(
                select(RecordingTemplate).where(RecordingTemplate.id == body.template_id)
            )
            if not template_result.scalar_one_or_none():
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid recording template ID",
                )
        recording.template_id = body.template_id if body.template_id != "" else None

    if body.metadata is not None:
        # Merge with existing metadata (create new dict to trigger SQLAlchemy change detection)
        current = recording.metadata_ or {}
        recording.metadata_ = {**current, **body.metadata}

    await db.commit()
    await broadcast("recordings", "updated", recording_id)

    # Refresh with template relationship
    result = await db.execute(
        select(Recording)
        .options(selectinload(Recording.template))
        .where(Recording.id == recording_id)
    )
    recording = result.scalar_one()

    # Load tag IDs
    tag_result = await db.execute(
        select(RecordingTag.tag_id).where(RecordingTag.recording_id == recording_id)
    )
    tag_ids = [row[0] for row in tag_result]

    # Get project_id from recording directly
    project_ids = [recording.project_id] if recording.project_id else []

    return _recording_to_response(
        recording, tag_ids=tag_ids, project_ids=project_ids, template=recording.template
    )


class BulkIdsRequest(BaseModel):
    """Request model for bulk operations."""

    ids: list[str] = Field(..., min_length=1)


class BulkAssignRequest(BaseModel):
    """Request model for bulk project assignment."""

    ids: list[str] = Field(..., min_length=1)
    project_id: str | None = Field(default=None, description="Project ID or null to unassign")


@router.post("/bulk-delete", response_model=MessageResponse)
async def bulk_delete_recordings(
    body: BulkIdsRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> MessageResponse:
    """Delete multiple recordings and their files."""
    result = await db.execute(select(Recording).where(Recording.id.in_(body.ids)))
    recordings = result.scalars().all()

    for recording in recordings:
        if recording.file_path:
            try:
                await storage_service.delete_file(recording.file_path, recording.storage_location_id)
            except Exception as e:
                logger.warning(f"Failed to delete file {recording.file_path}: {e}")
        await db.delete(recording)

    await db.commit()
    await broadcast("recordings", "deleted")
    return MessageResponse(message=f"Deleted {len(recordings)} recording(s)")


@router.post("/bulk-assign", response_model=MessageResponse)
async def bulk_assign_recordings(
    body: BulkAssignRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> MessageResponse:
    """Assign multiple recordings to a project (or remove from project)."""
    # Get recordings that exist
    result = await db.execute(select(Recording).where(Recording.id.in_(body.ids)))
    recordings = result.scalars().all()

    if not recordings:
        return MessageResponse(message="No recordings found")

    # Get new project name (if assigning to a project)
    new_project_name = None
    if body.project_id:
        project_result = await db.execute(select(Project).where(Project.id == body.project_id))
        project = project_result.scalar_one_or_none()
        if project:
            new_project_name = project.name

    # Update project_id and move files for all recordings
    for recording in recordings:
        # Move file if project changed
        if recording.project_id != body.project_id and recording.file_path:
            try:
                new_path = await storage_service.move_to_project(
                    recording.file_path, new_project_name, recording.storage_location_id
                )
                recording.file_path = str(new_path)
            except Exception as e:
                logger.warning("Could not move file for recording %s: %s", recording.id, e)
        recording.project_id = body.project_id

    await db.commit()
    await broadcast("recordings", "updated")
    return MessageResponse(message=f"Updated {len(recordings)} recording(s)")


@router.get("/{recording_id}", response_model=RecordingResponse)
async def get_recording(
    recording_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> RecordingResponse:
    """Get a recording by ID.

    Args:
        recording_id: The recording's unique ID.
        db: Database session.

    Returns:
        Recording details.

    Raises:
        HTTPException: If recording not found.
    """
    result = await db.execute(
        select(Recording)
        .options(selectinload(Recording.template))
        .where(Recording.id == recording_id)
    )
    recording = result.scalar_one_or_none()

    if recording is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Recording not found: {recording_id}",
        )

    # Load tag IDs
    tag_result = await db.execute(
        select(RecordingTag.tag_id).where(RecordingTag.recording_id == recording_id)
    )
    tag_ids = [row[0] for row in tag_result]

    # Get project_id from recording directly
    project_ids = [recording.project_id] if recording.project_id else []

    return _recording_to_response(
        recording, tag_ids=tag_ids, project_ids=project_ids, template=recording.template
    )


class RecordingPropertiesResponse(BaseModel):
    """Response model for recording file properties."""

    id: str
    title: str
    file_path: str
    file_name: str
    file_size: int | None
    file_size_formatted: str
    file_exists: bool
    mime_type: str | None
    duration_seconds: float | None
    duration_formatted: str | None
    status: str
    created_at: datetime
    updated_at: datetime
    storage_location: str | None = None


def _format_file_size(size: int | None) -> str:
    """Format file size in human-readable format."""
    if size is None:
        return "Unknown"
    for unit in ["B", "KB", "MB", "GB"]:
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def _format_duration(seconds: float | None) -> str | None:
    """Format duration in human-readable format."""
    if seconds is None:
        return None
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


@router.get("/{recording_id}/properties", response_model=RecordingPropertiesResponse)
async def get_recording_properties(
    recording_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> RecordingPropertiesResponse:
    """Get detailed file properties for a recording.

    Returns information useful for a Properties dialog including
    file path, size, whether file exists, etc.
    """
    from persistence.models import StorageLocation

    result = await db.execute(select(Recording).where(Recording.id == recording_id))
    recording = result.scalar_one_or_none()

    if recording is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Recording not found: {recording_id}",
        )

    file_path = Path(recording.file_path) if recording.file_path else None
    file_exists = file_path.exists() if file_path else False

    # Get actual file size from disk if possible
    actual_size = recording.file_size
    if file_exists and file_path:
        try:
            actual_size = file_path.stat().st_size
        except OSError:
            pass

    # Try to get storage location name
    storage_location_name = None
    loc_result = await db.execute(
        select(StorageLocation).where(StorageLocation.is_active == True)
    )
    storage_loc = loc_result.scalar_one_or_none()
    if storage_loc:
        storage_location_name = storage_loc.name

    return RecordingPropertiesResponse(
        id=recording.id,
        title=recording.title,
        file_path=recording.file_path or "",
        file_name=recording.file_name or "",
        file_size=actual_size,
        file_size_formatted=_format_file_size(actual_size),
        file_exists=file_exists,
        mime_type=recording.mime_type,
        duration_seconds=recording.duration_seconds,
        duration_formatted=_format_duration(recording.duration_seconds),
        status=recording.status,
        created_at=recording.created_at,
        updated_at=recording.updated_at,
        storage_location=storage_location_name,
    )


@router.get("/{recording_id}/audio", response_model=None)
async def stream_audio(
    recording_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Stream the audio file for a recording.

    Args:
        recording_id: The recording's unique ID.
        db: Database session.

    Returns:
        Audio file stream.

    Raises:
        HTTPException: If recording or file not found.
    """
    result = await db.execute(select(Recording).where(Recording.id == recording_id))
    recording = result.scalar_one_or_none()

    if recording is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Recording not found: {recording_id}",
        )

    # Check if recording is in cloud storage
    if recording.storage_location_id:
        location_result = await db.execute(
            select(StorageLocation).where(StorageLocation.id == recording.storage_location_id)
        )
        location = location_result.scalar_one_or_none()

        if location and location.type == "cloud":
            # Use storage adapter to read from cloud
            try:
                adapter = get_adapter(location)
                content = await adapter.read_file(recording.file_path)
                media_type = recording.mime_type or "audio/mpeg"
                return Response(
                    content=content,
                    media_type=media_type,
                    headers={
                        "Content-Disposition": f'inline; filename="{recording.file_name}"',
                    },
                )
            except Exception as e:
                logger.error(f"Failed to read cloud file: {e}")
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Audio file not found in cloud storage",
                )

    # Live recordings without saved audio use a sentinel URI
    if recording.file_path.startswith("live://"):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="no_audio",
        )

    # Local storage - check file exists
    file_path = Path(recording.file_path)
    if not file_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Audio file not found on disk",
        )

    # Use stored mime_type or default to audio/mpeg
    media_type = recording.mime_type or "audio/mpeg"

    return FileResponse(
        path=file_path,
        media_type=media_type,
        filename=recording.file_name,
    )


@router.delete("/{recording_id}", response_model=MessageResponse)
async def delete_recording(
    recording_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> MessageResponse:
    """Delete a recording and its associated file.

    Args:
        recording_id: The recording's unique ID.
        db: Database session.

    Returns:
        Confirmation message.

    Raises:
        HTTPException: If recording not found.
    """
    result = await db.execute(select(Recording).where(Recording.id == recording_id))
    recording = result.scalar_one_or_none()

    if recording is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Recording not found: {recording_id}",
        )

    # Delete the file from storage
    if recording.file_path:
        try:
            await storage_service.delete_file(recording.file_path, recording.storage_location_id)
        except Exception as e:
            logger.warning(f"Failed to delete file {recording.file_path}: {e}")

    # Delete the database record
    await db.delete(recording)
    await db.commit()
    await broadcast("recordings", "deleted", recording_id)
    await emit_event("recording.deleted", recording_id=recording_id)

    return MessageResponse(
        message="Recording deleted successfully",
        id=recording_id,
    )


@router.post("/{recording_id}/transcribe", response_model=TranscribeResponse)
async def transcribe_recording(
    recording_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    language: Annotated[str | None, Query(description="Language code (e.g., 'en', 'es')")] = None,
    auto_summary: Annotated[bool, Query(description="Auto-generate AI summary after transcription")] = False,
) -> TranscribeResponse:
    """Start transcription for a recording.

    Enqueues a transcription job for the specified recording.

    Args:
        recording_id: The recording's unique ID.
        db: Database session.
        language: Optional language code for transcription.
        auto_summary: Whether to auto-generate AI summary after transcription.

    Returns:
        Job ID and status.

    Raises:
        HTTPException: If recording not found or already processing.
    """
    result = await db.execute(select(Recording).where(Recording.id == recording_id))
    recording = result.scalar_one_or_none()

    if recording is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Recording not found: {recording_id}",
        )

    if recording.status == "processing":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Recording is already being processed",
        )

    # Enqueue transcription job
    payload = {"recording_id": recording_id}
    if language:
        payload["language"] = language
    if auto_summary:
        payload["auto_summary"] = True

    job_id = await job_queue.enqueue("transcribe", payload)
    await broadcast("recordings", "status_changed", recording_id)

    logger.info("Enqueued transcription job %s for recording %s", job_id, recording_id)

    return TranscribeResponse(
        job_id=job_id,
        status="queued",
    )


@router.post("/{recording_id}/cancel", response_model=MessageResponse)
async def cancel_recording(
    recording_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> MessageResponse:
    """Cancel an in-progress transcription for a recording.

    Args:
        recording_id: The recording's unique ID.
        db: Database session.

    Returns:
        Confirmation message.

    Raises:
        HTTPException: If recording not found or not processing.
    """
    result = await db.execute(select(Recording).where(Recording.id == recording_id))
    recording = result.scalar_one_or_none()

    if recording is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Recording not found: {recording_id}",
        )

    if recording.status != "processing":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Recording is not processing (status: {recording.status})",
        )

    # Find the active job for this recording
    job_result = await db.execute(
        select(Job)
        .where(
            Job.payload["recording_id"].as_string() == recording_id,
            Job.status.in_(["queued", "running"]),
        )
        .order_by(Job.created_at.desc())
        .limit(1)
    )
    job = job_result.scalar_one_or_none()

    if job is None:
        # No active job found — set recording to cancelled directly
        recording.status = "cancelled"
        await db.commit()
        await broadcast("recordings", "status_changed", recording_id)
        return MessageResponse(message="Recording cancelled (no active job found)", id=recording_id)

    was_queued = job.status == "queued"
    cancelled = await job_queue.cancel_job(job.id)

    if not cancelled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Could not cancel the job",
        )

    # Update recording status immediately so the UI responds right away
    recording.status = "cancelled"
    await db.commit()
    await broadcast("recordings", "status_changed", recording_id)

    logger.info("Cancellation requested for recording %s (job %s)", recording_id, job.id)
    return MessageResponse(message="Cancellation requested", id=recording_id)


@router.post("/{recording_id}/retry", response_model=TranscribeResponse)
async def retry_recording(
    recording_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TranscribeResponse:
    """Retry a failed or cancelled transcription.

    Creates a new transcription job with the same payload as the last attempt.

    Args:
        recording_id: The recording's unique ID.
        db: Database session.

    Returns:
        New job ID and status.

    Raises:
        HTTPException: If recording not found or not in a retryable state.
    """
    result = await db.execute(select(Recording).where(Recording.id == recording_id))
    recording = result.scalar_one_or_none()

    if recording is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Recording not found: {recording_id}",
        )

    if recording.status not in ("failed", "cancelled"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Recording is not in a retryable state (status: {recording.status})",
        )

    # Find the last job for this recording to reuse its payload
    last_job_result = await db.execute(
        select(Job)
        .where(Job.payload["recording_id"].as_string() == recording_id)
        .order_by(Job.created_at.desc())
        .limit(1)
    )
    last_job = last_job_result.scalar_one_or_none()

    # Build payload — reuse previous settings or fall back to defaults
    payload: dict = {"recording_id": recording_id}
    if last_job and last_job.payload:
        if "language" in last_job.payload:
            payload["language"] = last_job.payload["language"]
        if "diarize" in last_job.payload:
            payload["diarize"] = last_job.payload["diarize"]

    # Reset recording status
    recording.status = "pending"
    await db.commit()

    # Enqueue new job
    job_id = await job_queue.enqueue("transcribe", payload)
    await broadcast("recordings", "status_changed", recording_id)

    logger.info("Retry enqueued job %s for recording %s", job_id, recording_id)

    return TranscribeResponse(
        job_id=job_id,
        status="queued",
    )

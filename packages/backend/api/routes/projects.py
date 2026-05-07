"""Project management endpoints."""

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from api.routes.sync import broadcast
from core.events import emit as emit_event
from persistence.database import get_db
from persistence.models import Document, Project, ProjectType, Recording, RecordingTag, Setting, Tag
from services.storage import storage_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/projects", tags=["projects"])


class ProjectCreate(BaseModel):
    """Request model for creating a project."""

    name: str
    description: str | None = None
    project_type_id: str | None = None
    metadata: dict | None = None


class ProjectUpdate(BaseModel):
    """Request model for updating a project."""

    name: str | None = None
    description: str | None = None
    project_type_id: str | None = None
    metadata: dict | None = None
    is_archived: bool | None = None
    sort_order: int | None = None
    icon: str | None = None
    color: str | None = None


class InheritedTag(BaseModel):
    """A tag inherited from recordings in the project."""

    id: str
    name: str
    color: str | None
    recording_count: int


class ProjectTypeInfo(BaseModel):
    """Embedded project type info in response."""

    id: str
    name: str
    description: str | None
    metadata_schema: list[dict]
    is_system: bool


class ProjectResponse(BaseModel):
    """Response model for a project."""

    id: str
    name: str
    description: str | None
    project_type: ProjectTypeInfo | None
    metadata: dict
    recording_count: int
    is_archived: bool
    deleted_at: str | None = None
    sort_order: int
    icon: str | None
    color: str | None
    document_count: int
    inherited_tags: list[InheritedTag]
    created_at: str
    updated_at: str


class ProjectListResponse(BaseModel):
    """Response model for listing projects."""

    items: list[ProjectResponse]
    total: int


class MessageResponse(BaseModel):
    """Generic message response."""

    message: str
    id: str | None = None


def _project_type_to_info(pt: ProjectType | None) -> ProjectTypeInfo | None:
    """Convert ProjectType to ProjectTypeInfo."""
    if not pt:
        return None
    return ProjectTypeInfo(
        id=pt.id,
        name=pt.name,
        description=pt.description,
        metadata_schema=pt.metadata_schema,
        is_system=pt.is_system,
    )


async def _compute_inherited_tags(
    db: AsyncSession, project_id: str
) -> list[InheritedTag]:
    """Compute tags inherited from recordings in a project."""
    # Get all recordings in this project
    result = await db.execute(
        select(Recording.id).where(Recording.project_id == project_id)
    )
    recording_ids = [r for r in result.scalars().all()]

    if not recording_ids:
        return []

    # Get all tags associated with these recordings
    result = await db.execute(
        select(Tag, RecordingTag.recording_id)
        .join(RecordingTag, RecordingTag.tag_id == Tag.id)
        .where(RecordingTag.recording_id.in_(recording_ids))
    )
    tag_recordings = result.all()

    # Count unique recordings per tag (use set to avoid duplicates)
    tag_recordings_map: dict[str, tuple[Tag, set[str]]] = {}
    for tag, recording_id in tag_recordings:
        if tag.id not in tag_recordings_map:
            tag_recordings_map[tag.id] = (tag, set())
        tag_recordings_map[tag.id][1].add(recording_id)

    # Convert to InheritedTag list, sorted by recording count descending
    return sorted(
        [
            InheritedTag(
                id=tag.id,
                name=tag.name,
                color=tag.color,
                recording_count=len(recording_ids),
            )
            for tag, recording_ids in tag_recordings_map.values()
        ],
        key=lambda t: (-t.recording_count, t.name),
    )


@router.get("", response_model=ProjectListResponse)
async def list_projects(
    db: Annotated[AsyncSession, Depends(get_db)],
    search: Annotated[str | None, Query(description="Search by name")] = None,
    project_type_id: Annotated[str | None, Query(description="Filter by project type")] = None,
    tag: Annotated[str | None, Query(description="Filter by tag in metadata.tags")] = None,
    include_archived: Annotated[bool, Query(description="Include archived projects")] = False,
) -> ProjectListResponse:
    """List all projects with recording counts."""
    # Base query with project type eager load
    from sqlalchemy.orm import selectinload

    query = (
        select(Project)
        .options(selectinload(Project.project_type))
        .order_by(Project.updated_at.desc())
    )

    if not include_archived:
        query = query.where(Project.is_archived == False)

    if search:
        query = query.where(Project.name.ilike(f"%{search}%"))

    if project_type_id:
        query = query.where(Project.project_type_id == project_type_id)

    result = await db.execute(query)
    projects = list(result.scalars().all())

    # Build response items with recording counts and inherited tags
    items = []
    for project in projects:
        count_result = await db.execute(
            select(func.count(Recording.id)).where(
                Recording.project_id == project.id
            )
        )
        recording_count = count_result.scalar() or 0

        doc_count_result = await db.execute(
            select(func.count(Document.id)).where(Document.project_id == project.id)
        )
        document_count = doc_count_result.scalar() or 0

        # Compute inherited tags from recordings
        inherited_tags = await _compute_inherited_tags(db, project.id)

        items.append(
            ProjectResponse(
                id=project.id,
                name=project.name,
                description=project.description,
                project_type=_project_type_to_info(project.project_type),
                metadata=project.metadata_,
                recording_count=recording_count,
                is_archived=project.is_archived,
                deleted_at=project.deleted_at.isoformat() if project.deleted_at else None,
                sort_order=project.sort_order,
                icon=project.icon,
                color=project.color,
                document_count=document_count,
                inherited_tags=inherited_tags,
                created_at=project.created_at.isoformat(),
                updated_at=project.updated_at.isoformat(),
            )
        )

    # Filter by tag if specified (check both project tags and inherited recording tags)
    if tag:
        items = [
            item for item in items
            if tag in (item.metadata.get("tags") or [])
            or any(t.name == tag for t in item.inherited_tags)
        ]

    return ProjectListResponse(items=items, total=len(items))


@router.get("/active/current")
async def get_active_project(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Get the currently active project ID."""
    result = await db.execute(
        select(Setting).where(Setting.key == "active_project_id")
    )
    setting = result.scalar_one_or_none()
    project_id = setting.value.get("id") if setting else None
    return {"active_project_id": project_id}


@router.put("/active/current")
async def set_active_project(
    db: Annotated[AsyncSession, Depends(get_db)],
    data: dict,
) -> dict:
    """Set the active project. Pass {"project_id": null} to clear."""
    project_id = data.get("project_id")

    if project_id:
        result = await db.execute(select(Project).where(Project.id == project_id))
        if not result.scalar_one_or_none():
            raise HTTPException(status_code=404, detail="Project not found")

    result = await db.execute(
        select(Setting).where(Setting.key == "active_project_id")
    )
    setting = result.scalar_one_or_none()
    if setting:
        setting.value = {"id": project_id}
    else:
        db.add(Setting(key="active_project_id", value={"id": project_id}))

    await db.commit()
    await broadcast("settings", "updated", "active_project_id")
    return {"active_project_id": project_id}


@router.post("", response_model=ProjectResponse, status_code=201)
async def create_project(
    db: Annotated[AsyncSession, Depends(get_db)],
    data: ProjectCreate,
) -> ProjectResponse:
    """Create a new project."""
    from sqlalchemy.orm import selectinload

    # Validate project_type_id if provided
    project_type = None
    if data.project_type_id:
        result = await db.execute(
            select(ProjectType).where(ProjectType.id == data.project_type_id)
        )
        project_type = result.scalar_one_or_none()
        if not project_type:
            raise HTTPException(status_code=400, detail="Invalid project type ID")

    project = Project(
        name=data.name,
        description=data.description,
        project_type_id=data.project_type_id,
        metadata_=data.metadata or {},
    )
    db.add(project)
    await db.commit()
    await broadcast("projects", "created", str(project.id))
    await emit_event("project.created", project_id=project.id, name=data.name)

    # Create project folder on disk
    try:
        await storage_service.ensure_project_folder(data.name)
    except Exception as e:
        logger.warning(f"Could not create folder for project {project.id}: {e}")

    # Refresh with relationships
    result = await db.execute(
        select(Project)
        .options(selectinload(Project.project_type))
        .where(Project.id == project.id)
    )
    project = result.scalar_one()

    return ProjectResponse(
        id=project.id,
        name=project.name,
        description=project.description,
        project_type=_project_type_to_info(project.project_type),
        metadata=project.metadata_,
        recording_count=0,
        is_archived=False,
        sort_order=0,
        icon=None,
        color=None,
        document_count=0,
        inherited_tags=[],  # New project has no recordings yet
        created_at=project.created_at.isoformat(),
        updated_at=project.updated_at.isoformat(),
    )


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(
    db: Annotated[AsyncSession, Depends(get_db)],
    project_id: str,
) -> ProjectResponse:
    """Get a project by ID."""
    from sqlalchemy.orm import selectinload

    result = await db.execute(
        select(Project)
        .options(selectinload(Project.project_type))
        .where(Project.id == project_id)
    )
    project = result.scalar_one_or_none()

    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    count_result = await db.execute(
        select(func.count(Recording.id)).where(
            Recording.project_id == project.id
        )
    )
    recording_count = count_result.scalar() or 0

    doc_count_result = await db.execute(
        select(func.count(Document.id)).where(Document.project_id == project.id)
    )
    document_count = doc_count_result.scalar() or 0

    # Compute inherited tags from recordings
    inherited_tags = await _compute_inherited_tags(db, project.id)

    return ProjectResponse(
        id=project.id,
        name=project.name,
        description=project.description,
        project_type=_project_type_to_info(project.project_type),
        metadata=project.metadata_,
        recording_count=recording_count,
        is_archived=project.is_archived,
        sort_order=project.sort_order,
        icon=project.icon,
        color=project.color,
        document_count=document_count,
        inherited_tags=inherited_tags,
        created_at=project.created_at.isoformat(),
        updated_at=project.updated_at.isoformat(),
    )


@router.patch("/{project_id}", response_model=ProjectResponse)
async def update_project(
    db: Annotated[AsyncSession, Depends(get_db)],
    project_id: str,
    data: ProjectUpdate,
) -> ProjectResponse:
    """Update a project."""
    from sqlalchemy.orm import selectinload

    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()

    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # Handle name change - rename folder and update all file paths
    if data.name is not None and data.name != project.name:
        old_name = project.name
        new_name = data.name

        try:
            # Rename the project folder
            new_folder = await storage_service.rename_project_folder(old_name, new_name)

            # Update file paths for non-trashed recordings in this project
            rec_result = await db.execute(
                select(Recording).where(
                    Recording.project_id == project_id,
                    Recording.is_archived == False,
                )
            )
            for rec in rec_result.scalars():
                if rec.file_path:
                    if rec.storage_location_id and isinstance(new_folder, str):
                        # Cloud: replace folder prefix in relative path
                        parts = rec.file_path.split("/")
                        if len(parts) >= 2:
                            parts[0] = new_folder
                            rec.file_path = "/".join(parts)
                    else:
                        old_path = Path(rec.file_path)
                        new_path = Path(new_folder) / old_path.name
                        rec.file_path = str(new_path)

            # Update file paths for non-trashed documents in this project
            doc_result = await db.execute(
                select(Document).where(
                    Document.project_id == project_id,
                    Document.is_archived == False,
                )
            )
            for doc in doc_result.scalars():
                if doc.file_path:
                    if doc.storage_location_id and isinstance(new_folder, str):
                        # Cloud: replace folder prefix in relative path
                        parts = doc.file_path.split("/")
                        if len(parts) >= 2:
                            parts[0] = new_folder
                            doc.file_path = "/".join(parts)
                    else:
                        old_path = Path(doc.file_path)
                        new_path = Path(new_folder) / old_path.name
                        doc.file_path = str(new_path)

        except Exception as e:
            logger.warning(f"Could not rename folder for project {project_id}: {e}")

        project.name = new_name

    if data.description is not None:
        project.description = data.description
    if data.project_type_id is not None:
        # Validate project_type_id
        if data.project_type_id:  # Not empty string
            pt_result = await db.execute(
                select(ProjectType).where(ProjectType.id == data.project_type_id)
            )
            if not pt_result.scalar_one_or_none():
                raise HTTPException(status_code=400, detail="Invalid project type ID")
        project.project_type_id = data.project_type_id if data.project_type_id else None
    if data.metadata is not None:
        project.metadata_ = data.metadata
    if data.is_archived is not None:
        project.is_archived = data.is_archived
    if data.sort_order is not None:
        project.sort_order = data.sort_order
    if data.icon is not None:
        project.icon = data.icon
    if data.color is not None:
        project.color = data.color

    await db.commit()
    await broadcast("projects", "updated", project_id)

    # Refresh with relationships
    result = await db.execute(
        select(Project)
        .options(selectinload(Project.project_type))
        .where(Project.id == project_id)
    )
    project = result.scalar_one()

    count_result = await db.execute(
        select(func.count(Recording.id)).where(
            Recording.project_id == project.id
        )
    )
    recording_count = count_result.scalar() or 0

    doc_count_result = await db.execute(
        select(func.count(Document.id)).where(Document.project_id == project.id)
    )
    document_count = doc_count_result.scalar() or 0

    # Compute inherited tags from recordings
    inherited_tags = await _compute_inherited_tags(db, project.id)

    return ProjectResponse(
        id=project.id,
        name=project.name,
        description=project.description,
        project_type=_project_type_to_info(project.project_type),
        metadata=project.metadata_,
        recording_count=recording_count,
        is_archived=project.is_archived,
        sort_order=project.sort_order,
        icon=project.icon,
        color=project.color,
        document_count=document_count,
        inherited_tags=inherited_tags,
        created_at=project.created_at.isoformat(),
        updated_at=project.updated_at.isoformat(),
    )


@router.delete("/{project_id}", response_model=MessageResponse)
async def delete_project(
    db: Annotated[AsyncSession, Depends(get_db)],
    project_id: str,
    delete_files: Annotated[bool, Query(description="Also trash all files in project")] = False,
) -> MessageResponse:
    """Soft-delete a project (move to trash).

    Args:
        project_id: The project ID to delete.
        delete_files: If True, also move all recordings/documents in this project to trash.
                     If False (default), detach items from the project (move to root).
    """
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()

    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    now = datetime.now(timezone.utc)

    if delete_files:
        # Also trash all recordings in this project
        rec_result = await db.execute(
            select(Recording).where(
                Recording.project_id == project_id,
                Recording.is_archived == False,
            )
        )
        for rec in rec_result.scalars():
            if rec.file_path:
                try:
                    new_path = await storage_service.move_to_trash(
                        rec.file_path, rec.storage_location_id
                    )
                    if new_path:
                        rec.file_path = str(new_path)
                except Exception as e:
                    logger.warning(f"Could not move file to trash for recording {rec.id}: {e}")
            rec.is_archived = True
            rec.deleted_at = now

        # Also trash all documents in this project
        doc_result = await db.execute(
            select(Document).where(
                Document.project_id == project_id,
                Document.is_archived == False,
            )
        )
        for doc in doc_result.scalars():
            if doc.file_path:
                try:
                    new_path = await storage_service.move_to_trash(
                        doc.file_path, doc.storage_location_id
                    )
                    if new_path:
                        doc.file_path = str(new_path)
                except Exception as e:
                    logger.warning(f"Could not move file to trash for document {doc.id}: {e}")
            doc.is_archived = True
            doc.deleted_at = now
    else:
        # Detach items from project (move to root)
        rec_result = await db.execute(
            select(Recording).where(Recording.project_id == project_id)
        )
        for rec in rec_result.scalars():
            if rec.file_path:
                try:
                    new_path = await storage_service.move_to_project(
                        rec.file_path, None, rec.storage_location_id
                    )
                    rec.file_path = str(new_path)
                except Exception as e:
                    logger.warning(f"Could not move file for recording {rec.id}: {e}")
            rec.project_id = None

        doc_result = await db.execute(
            select(Document).where(Document.project_id == project_id)
        )
        for doc in doc_result.scalars():
            if doc.file_path:
                try:
                    new_path = await storage_service.move_to_project(
                        doc.file_path, None, doc.storage_location_id
                    )
                    doc.file_path = str(new_path)
                except Exception as e:
                    logger.warning(f"Could not move file for document {doc.id}: {e}")
            doc.project_id = None

    # Soft-delete the project itself
    project.is_archived = True
    project.deleted_at = now
    await db.commit()
    await broadcast("projects", "deleted", project_id)

    return MessageResponse(message="Project moved to trash", id=project_id)


@router.delete("/{project_id}/permanent", response_model=MessageResponse)
async def permanently_delete_project(
    db: Annotated[AsyncSession, Depends(get_db)],
    project_id: str,
) -> MessageResponse:
    """Permanently delete a trashed project."""
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()

    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    if not project.is_archived:
        raise HTTPException(status_code=400, detail="Project must be in trash before permanent deletion")

    project_name = project.name

    # Permanently delete only trashed recordings still in this project
    rec_result = await db.execute(
        select(Recording).where(
            Recording.project_id == project_id,
            Recording.is_archived == True,
        )
    )
    for rec in rec_result.scalars():
        if rec.file_path:
            try:
                await storage_service.delete_file(rec.file_path, rec.storage_location_id)
            except Exception as e:
                logger.warning(f"Could not delete file for recording {rec.id}: {e}")
        await db.delete(rec)

    # Detach any non-trashed recordings from the project
    live_rec_result = await db.execute(
        select(Recording).where(
            Recording.project_id == project_id,
            Recording.is_archived == False,
        )
    )
    for rec in live_rec_result.scalars():
        rec.project_id = None

    # Permanently delete only trashed documents still in this project
    doc_result = await db.execute(
        select(Document).where(
            Document.project_id == project_id,
            Document.is_archived == True,
        )
    )
    for doc in doc_result.scalars():
        if doc.file_path:
            try:
                await storage_service.delete_file(doc.file_path, doc.storage_location_id)
            except Exception as e:
                logger.warning(f"Could not delete file for document {doc.id}: {e}")
        await db.delete(doc)

    # Detach any non-trashed documents from the project
    live_doc_result = await db.execute(
        select(Document).where(
            Document.project_id == project_id,
            Document.is_archived == False,
        )
    )
    for doc in live_doc_result.scalars():
        doc.project_id = None

    # Drop the cached vocab-retrieval context vector for this project.
    # The cache is otherwise content-addressed by hash, but a permanently
    # deleted project will never be re-queried, so the row is dead weight.
    try:
        await db.execute(
            sql_text("DELETE FROM project_context_embedding WHERE project_id = :pid"),
            {"pid": project_id},
        )
    except Exception as e:
        logger.debug(f"project_context_embedding cleanup skipped: {e}")

    await db.delete(project)
    await db.commit()
    await broadcast("projects", "deleted", project_id)

    # Clean up project folder
    try:
        await storage_service.delete_project_folder(project_name, delete_contents=True)
    except Exception as e:
        logger.warning(f"Could not delete folder for project: {e}")

    return MessageResponse(message="Project permanently deleted", id=project_id)


@router.post("/{project_id}/recordings/{recording_id}", response_model=MessageResponse)
async def add_recording_to_project(
    db: Annotated[AsyncSession, Depends(get_db)],
    project_id: str,
    recording_id: str,
) -> MessageResponse:
    """Add a recording to a project by setting its project_id and moving the file."""
    # Verify project exists
    project_result = await db.execute(select(Project).where(Project.id == project_id))
    project = project_result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # Verify recording exists
    recording_result = await db.execute(select(Recording).where(Recording.id == recording_id))
    recording = recording_result.scalar_one_or_none()
    if not recording:
        raise HTTPException(status_code=404, detail="Recording not found")

    # Check if already in this project
    if recording.project_id == project_id:
        return MessageResponse(message="Recording already in project", id=recording_id)

    # Move file to project folder (works for both local and cloud storage)
    if recording.file_path:
        try:
            new_path = await storage_service.move_to_project(
                recording.file_path, project.name, recording.storage_location_id
            )
            recording.file_path = str(new_path)
        except Exception as e:
            logger.warning(f"Could not move file for recording {recording_id}: {e}")

    # Set the project_id
    recording.project_id = project_id
    await db.commit()

    return MessageResponse(message="Recording added to project", id=recording_id)


@router.delete("/{project_id}/recordings/{recording_id}", response_model=MessageResponse)
async def remove_recording_from_project(
    db: Annotated[AsyncSession, Depends(get_db)],
    project_id: str,
    recording_id: str,
) -> MessageResponse:
    """Remove a recording from a project by clearing its project_id and moving file to root."""
    recording_result = await db.execute(
        select(Recording).where(
            Recording.id == recording_id,
            Recording.project_id == project_id,
        )
    )
    recording = recording_result.scalar_one_or_none()

    if not recording:
        raise HTTPException(status_code=404, detail="Recording not found in project")

    # Move file to root (works for both local and cloud storage)
    if recording.file_path:
        try:
            new_path = await storage_service.move_to_project(
                recording.file_path, None, recording.storage_location_id
            )
            recording.file_path = str(new_path)
        except Exception as e:
            logger.warning(f"Could not move file for recording {recording_id}: {e}")

    recording.project_id = None
    await db.commit()

    return MessageResponse(message="Recording removed from project", id=recording_id)


class ProjectRecordingResponse(BaseModel):
    """Response model for a recording in a project context."""

    id: str
    title: str
    file_name: str
    duration_seconds: float | None
    status: str
    created_at: str
    updated_at: str


class ProjectRecordingsResponse(BaseModel):
    """Response model for listing recordings in a project."""

    items: list[ProjectRecordingResponse]
    total: int


@router.get("/{project_id}/recordings", response_model=ProjectRecordingsResponse)
async def get_project_recordings(
    db: Annotated[AsyncSession, Depends(get_db)],
    project_id: str,
) -> ProjectRecordingsResponse:
    """Get all recordings for a project."""
    # Verify project exists
    project_result = await db.execute(select(Project).where(Project.id == project_id))
    if not project_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Project not found")

    # Get recordings by project_id FK
    result = await db.execute(
        select(Recording)
        .where(Recording.project_id == project_id)
        .order_by(Recording.created_at.desc())
    )
    recordings = result.scalars().all()

    items = [
        ProjectRecordingResponse(
            id=r.id,
            title=r.title,
            file_name=r.file_name,
            duration_seconds=r.duration_seconds,
            status=r.status,
            created_at=r.created_at.isoformat(),
            updated_at=r.updated_at.isoformat(),
        )
        for r in recordings
    ]

    return ProjectRecordingsResponse(items=items, total=len(items))


@router.patch("/{project_id}/archive", response_model=MessageResponse)
async def archive_project(
    db: Annotated[AsyncSession, Depends(get_db)],
    project_id: str,
) -> MessageResponse:
    """Archive a project (moves to trash)."""
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    project.is_archived = True
    project.deleted_at = project.deleted_at or datetime.now(timezone.utc)
    await db.commit()
    await broadcast("projects", "updated", project_id)
    return MessageResponse(message="Project moved to trash", id=project_id)


@router.patch("/{project_id}/unarchive", response_model=MessageResponse)
async def unarchive_project(
    db: Annotated[AsyncSession, Depends(get_db)],
    project_id: str,
) -> MessageResponse:
    """Restore a project from trash."""
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    project.is_archived = False
    project.deleted_at = None
    await db.commit()
    await broadcast("projects", "updated", project_id)
    return MessageResponse(message="Project restored", id=project_id)


class ProjectSections(BaseModel):
    """Content type counts for a project."""

    recordings: int = 0
    documents: int = 0
    notes: int = 0


@router.get("/{project_id}/sections", response_model=ProjectSections)
async def get_project_sections(
    db: Annotated[AsyncSession, Depends(get_db)],
    project_id: str,
) -> ProjectSections:
    """Get content type counts for a project (auto type-based sections)."""
    from persistence.models import Note

    result = await db.execute(select(Project).where(Project.id == project_id))
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Project not found")

    rec_count = await db.scalar(
        select(func.count(Recording.id)).where(Recording.project_id == project_id)
    ) or 0

    doc_count = await db.scalar(
        select(func.count(Document.id)).where(Document.project_id == project_id)
    ) or 0

    note_count = await db.scalar(
        select(func.count(Note.id)).where(
            (Note.recording_id.in_(
                select(Recording.id).where(Recording.project_id == project_id)
            )) |
            (Note.document_id.in_(
                select(Document.id).where(Document.project_id == project_id)
            ))
        )
    ) or 0

    return ProjectSections(
        recordings=rec_count,
        documents=doc_count,
        notes=note_count,
    )


class BulkMoveRequest(BaseModel):
    """Request to move multiple items to this project."""

    recording_ids: list[str] = []
    document_ids: list[str] = []


@router.post("/{project_id}/move-items", response_model=MessageResponse)
async def bulk_move_items(
    db: Annotated[AsyncSession, Depends(get_db)],
    project_id: str,
    data: BulkMoveRequest,
) -> MessageResponse:
    """Move recordings and documents into a project."""
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    moved = 0

    for rec_id in data.recording_ids:
        rec_result = await db.execute(select(Recording).where(Recording.id == rec_id))
        rec = rec_result.scalar_one_or_none()
        if rec and rec.project_id != project_id:
            rec.project_id = project_id
            moved += 1

    for doc_id in data.document_ids:
        doc_result = await db.execute(select(Document).where(Document.id == doc_id))
        doc = doc_result.scalar_one_or_none()
        if doc and doc.project_id != project_id:
            doc.project_id = project_id
            moved += 1

    await db.commit()
    await broadcast("projects", "updated", project_id)
    await broadcast("recordings", "updated")
    await broadcast("documents", "updated")
    return MessageResponse(message=f"Moved {moved} items to project", id=project_id)

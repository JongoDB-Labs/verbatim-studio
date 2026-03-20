"""Document management endpoints."""

import logging
from datetime import datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import distinct, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from persistence import get_db
from persistence.models import Document, DocumentTag, Project, StorageLocation
from services.storage import storage_service, get_active_storage_location
from storage.factory import get_adapter
from api.dependencies import get_active_project_id, get_active_project_ids
from api.routes.sync import broadcast
from core.events import emit as emit_event

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/documents", tags=["documents"])

# Allowed MIME types
ALLOWED_MIME_TYPES = {
    # Documents
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",  # DOCX
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",  # XLSX
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",  # PPTX
    "text/plain",
    "text/markdown",
    # Images
    "image/png",
    "image/jpeg",
    "image/tiff",
    "image/webp",
}


class DocumentResponse(BaseModel):
    """Response model for a document."""

    id: str
    title: str
    filename: str
    file_path: str
    mime_type: str
    file_size_bytes: int
    project_id: str | None
    project_ids: list[str] = []
    tag_ids: list[str] = []
    status: str
    error_message: str | None
    page_count: int | None
    metadata: dict
    created_at: str
    updated_at: str
    # Extracted content (only included when requested or for single doc)
    extracted_text: str | None = None
    extracted_markdown: str | None = None


class DocumentListResponse(BaseModel):
    """Response model for listing documents."""

    items: list[DocumentResponse]
    total: int
    page: int = 1
    page_size: int = 20
    total_pages: int = 1


class DocumentUpdate(BaseModel):
    """Request model for updating a document."""

    title: str | None = None
    project_id: str | None = None


class MessageResponse(BaseModel):
    """Generic message response."""

    message: str
    id: str | None = None


def _doc_to_response(
    doc: Document,
    include_content: bool = False,
    tag_ids: list[str] | None = None,
    project_ids: list[str] | None = None,
) -> DocumentResponse:
    """Convert Document model to response."""
    return DocumentResponse(
        id=doc.id,
        title=doc.title,
        filename=doc.filename,
        file_path=doc.file_path,
        mime_type=doc.mime_type,
        file_size_bytes=doc.file_size_bytes,
        project_id=doc.project_id,
        project_ids=project_ids if project_ids is not None else ([doc.project_id] if doc.project_id else []),
        tag_ids=tag_ids if tag_ids is not None else [],
        status=doc.status,
        error_message=doc.error_message,
        page_count=doc.page_count,
        metadata=doc.metadata_,
        created_at=doc.created_at.isoformat(),
        updated_at=doc.updated_at.isoformat(),
        extracted_text=doc.extracted_text if include_content else None,
        extracted_markdown=doc.extracted_markdown if include_content else None,
    )


@router.post("", response_model=DocumentResponse, status_code=status.HTTP_201_CREATED)
async def upload_document(
    db: Annotated[AsyncSession, Depends(get_db)],
    file: UploadFile = File(...),
    title: str = Form(None),
    project_id: str = Form(None),
    enable_ocr: str = Form("false"),  # String because FormData sends 'true'/'false'
) -> DocumentResponse:
    """Upload a new document.

    Args:
        file: The file to upload
        title: Optional title (defaults to filename)
        project_id: Optional project to assign to
        enable_ocr: If True, run OCR processing on the document
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="Filename required")

    # Validate MIME type
    mime_type = file.content_type or "application/octet-stream"
    if mime_type not in ALLOWED_MIME_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type: {mime_type}. Allowed: PDF, DOCX, XLSX, PPTX, TXT, MD, PNG, JPG, TIFF"
        )

    # Validate project exists if provided and get name for path
    project_name = None
    if project_id:
        project = await db.get(Project, project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        project_name = project.name

    # Read file content
    content = await file.read()
    file_size = len(content)

    # Validate file size
    MAX_FILE_SIZE = 10 * 1024 * 1024 * 1024  # 10 GB
    if file_size > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"File too large. Maximum size is {MAX_FILE_SIZE // (1024 * 1024 * 1024)} GB"
        )
    if file_size == 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    # Sanitize filename for security
    safe_filename = Path(file.filename.replace("\\", "/")).name
    if not safe_filename or safe_filename in (".", ".."):
        safe_filename = "unknown"

    # Generate document ID
    from persistence.models import generate_uuid
    doc_id = generate_uuid()

    # Determine title - strip extension if title ends with file extension
    file_extension = Path(safe_filename).suffix.lower()
    if title:
        # If user provided title ends with file extension, strip it
        if title.lower().endswith(file_extension):
            doc_title = title[:-len(file_extension)]
        else:
            doc_title = title
    else:
        doc_title = safe_filename.rsplit(".", 1)[0]

    # Save to storage with human-readable path
    try:
        file_path = await storage_service.save_upload(
            content=content,
            title=doc_title,
            filename=safe_filename,
            project_name=project_name,
        )
    except Exception as e:
        logger.exception("Failed to save uploaded document file")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to save uploaded file. Please try again.",
        )

    # Set storage_location_id AFTER saving, based on actual storage used.
    # save_upload returns Path for local, str for cloud.
    storage_location = await get_active_storage_location()
    storage_location_id = None
    if storage_location:
        if storage_location.type == "cloud" and isinstance(file_path, Path):
            # Cloud was intended but file was saved locally (fallback)
            pass
        else:
            storage_location_id = storage_location.id

    # Derive final title and filename from actual path
    # Handle both Path (local) and string (cloud) returns
    if isinstance(file_path, Path):
        final_title = file_path.stem  # filename without extension
        final_filename = file_path.name  # Actual filename (may have collision suffix)
    else:
        # Cloud storage returns relative path string
        filename = file_path.split("/")[-1]
        final_filename = filename
        if "." in filename:
            final_title = filename.rsplit(".", 1)[0]
        else:
            final_title = filename

    # Create document record with OCR preference in metadata
    # Convert string 'true'/'false' to bool (FormData sends strings)
    ocr_enabled = enable_ocr.lower() == 'true' if isinstance(enable_ocr, str) else bool(enable_ocr)
    doc = Document(
        id=doc_id,
        title=final_title,
        filename=final_filename,
        file_path=str(file_path),  # Full path or cloud path
        mime_type=mime_type,
        file_size_bytes=file_size,
        project_id=project_id,
        storage_location_id=storage_location_id,
        status="pending",
        metadata_={"enable_ocr": ocr_enabled},
    )
    db.add(doc)
    await db.commit()
    await db.refresh(doc)

    # Queue processing job
    from services.jobs import job_queue
    await job_queue.enqueue("process_document", {"document_id": doc.id})

    await broadcast("documents", "created", doc.id)
    await emit_event("document.uploaded", document_id=doc.id, filename=safe_filename)
    return _doc_to_response(doc)


@router.get("", response_model=DocumentListResponse)
async def list_documents(
    db: Annotated[AsyncSession, Depends(get_db)],
    page: Annotated[int, Query(ge=1, description="Page number")] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, description="Items per page")] = 50,
    project_id: Annotated[str | None, Query(description="Filter by project")] = None,
    status_filter: Annotated[str | None, Query(alias="status", description="Filter by status")] = None,
    search: Annotated[str | None, Query(description="Search by title or filename")] = None,
    sort_by: Annotated[str, Query(description="Sort field (created_at, title, file_size_bytes)")] = "created_at",
    sort_order: Annotated[str, Query(description="Sort order (asc, desc)")] = "desc",
    date_from: Annotated[str | None, Query(description="Filter from date (ISO 8601)")] = None,
    date_to: Annotated[str | None, Query(description="Filter to date (ISO 8601)")] = None,
    tag_ids: Annotated[str | None, Query(description="Comma-separated tag IDs to filter by")] = None,
    mime_type: Annotated[str | None, Query(description="Filter by MIME type")] = None,
    active_project_ids: Annotated[list[str], Depends(get_active_project_ids)] = [],
    all_projects: Annotated[bool, Query(alias="all", description="Return all projects (ignore active project)")] = False,
) -> DocumentListResponse:
    """List all documents with pagination and filtering."""
    query = select(Document)

    # Filter by active storage location (include unassigned docs for backward compat)
    active_location = await get_active_storage_location()
    if active_location:
        if active_location.type == "cloud":
            query = query.where(
                or_(
                    Document.storage_location_id == active_location.id,
                    Document.storage_location_id.is_(None),
                )
            )
        elif active_location.config.get("path"):
            active_path = active_location.config.get("path")
            query = query.where(
                or_(
                    Document.storage_location_id == active_location.id,
                    Document.storage_location_id.is_(None),
                    Document.file_path.startswith(active_path),
                )
            )

    # Project scoping: explicit project_id param takes precedence over header
    if project_id:
        query = query.where(Document.project_id == project_id)
    elif active_project_ids and not all_projects:
        query = query.where(Document.project_id.in_(active_project_ids))

    if status_filter:
        query = query.where(Document.status == status_filter)
    if search and search.strip():
        search_term = f"%{search.strip()}%"
        query = query.where(
            or_(
                Document.title.ilike(search_term),
                Document.filename.ilike(search_term),
            )
        )
    if mime_type:
        # Support prefix matching (e.g. "image/" matches "image/png", "image/jpeg")
        if mime_type.endswith("/"):
            query = query.where(Document.mime_type.startswith(mime_type))
        else:
            query = query.where(Document.mime_type == mime_type)

    # Date range filters
    if date_from:
        try:
            from_date = datetime.fromisoformat(date_from)
            query = query.where(Document.created_at >= from_date)
        except ValueError:
            pass

    if date_to:
        try:
            to_date = datetime.fromisoformat(date_to).replace(hour=23, minute=59, second=59)
            query = query.where(Document.created_at <= to_date)
        except ValueError:
            pass

    # Tag filter — documents must have ALL specified tags
    if tag_ids and tag_ids.strip():
        tag_id_list = [t.strip() for t in tag_ids.split(",") if t.strip()]
        if tag_id_list:
            query = query.where(
                Document.id.in_(
                    select(DocumentTag.document_id)
                    .where(DocumentTag.tag_id.in_(tag_id_list))
                    .group_by(DocumentTag.document_id)
                    .having(func.count(distinct(DocumentTag.tag_id)) == len(tag_id_list))
                )
            )

    # Get total count
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # Calculate pagination
    total_pages = (total + page_size - 1) // page_size if total > 0 else 1
    offset = (page - 1) * page_size

    # Apply sorting
    sort_column = {
        "created_at": Document.created_at,
        "title": Document.title,
        "file_size_bytes": Document.file_size_bytes,
    }.get(sort_by, Document.created_at)

    if sort_order == "asc":
        query = query.order_by(sort_column.asc())
    else:
        query = query.order_by(sort_column.desc())

    # Apply pagination
    query = query.offset(offset).limit(page_size)
    result = await db.execute(query)
    docs = result.scalars().all()

    # Batch-load tag IDs for all documents
    doc_ids = [d.id for d in docs]
    tag_map: dict[str, list[str]] = {did: [] for did in doc_ids}
    if doc_ids:
        tag_result = await db.execute(
            select(DocumentTag.document_id, DocumentTag.tag_id).where(
                DocumentTag.document_id.in_(doc_ids)
            )
        )
        for row in tag_result:
            tag_map[row.document_id].append(row.tag_id)

    return DocumentListResponse(
        items=[
            _doc_to_response(
                d,
                tag_ids=tag_map.get(d.id, []),
                project_ids=[d.project_id] if d.project_id else [],
            )
            for d in docs
        ],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


class BulkIdsRequest(BaseModel):
    """Request model for bulk operations."""

    ids: list[str] = Field(..., min_length=1)


class BulkAssignRequest(BaseModel):
    """Request model for bulk project assignment."""

    ids: list[str] = Field(..., min_length=1)
    project_id: str | None = Field(default=None, description="Project ID or null to unassign")


@router.post("/bulk-delete", response_model=MessageResponse)
async def bulk_delete_documents(
    body: BulkIdsRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> MessageResponse:
    """Delete multiple documents and their files."""
    result = await db.execute(select(Document).where(Document.id.in_(body.ids)))
    docs = result.scalars().all()

    for doc in docs:
        if doc.file_path:
            try:
                await storage_service.delete_file(doc.file_path, doc.storage_location_id)
            except Exception as e:
                logger.warning(f"Failed to delete file {doc.file_path}: {e}")
        await db.delete(doc)

    await db.commit()
    await broadcast("documents", "deleted")
    return MessageResponse(message=f"Deleted {len(docs)} document(s)")


@router.post("/bulk-assign", response_model=MessageResponse)
async def bulk_assign_documents(
    body: BulkAssignRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> MessageResponse:
    """Assign multiple documents to a project (or remove from project)."""
    result = await db.execute(select(Document).where(Document.id.in_(body.ids)))
    docs = result.scalars().all()

    if not docs:
        return MessageResponse(message="No documents found")

    new_project_name = None
    if body.project_id:
        project_result = await db.execute(select(Project).where(Project.id == body.project_id))
        project = project_result.scalar_one_or_none()
        if not project:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Project not found: {body.project_id}",
            )
        if project:
            new_project_name = project.name

    for doc in docs:
        if doc.project_id != body.project_id and doc.file_path:
            try:
                new_path = await storage_service.move_to_project(
                    doc.file_path, new_project_name, doc.storage_location_id
                )
                doc.file_path = str(new_path)
            except Exception as e:
                logger.warning("Could not move file for document %s: %s", doc.id, e)
        doc.project_id = body.project_id

    await db.commit()
    await broadcast("documents", "updated")
    return MessageResponse(message=f"Updated {len(docs)} document(s)")


@router.get("/{document_id}", response_model=DocumentResponse)
async def get_document(
    db: Annotated[AsyncSession, Depends(get_db)],
    document_id: str,
    include_content: Annotated[bool, Query(description="Include extracted text content")] = True,
) -> DocumentResponse:
    """Get a single document by ID."""
    doc = await db.get(Document, document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    return _doc_to_response(doc, include_content=include_content)


@router.patch("/{document_id}", response_model=DocumentResponse)
async def update_document(
    db: Annotated[AsyncSession, Depends(get_db)],
    document_id: str,
    update: DocumentUpdate,
) -> DocumentResponse:
    """Update a document's title or project assignment."""
    doc = await db.get(Document, document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    # Handle title change - rename file in storage
    if update.title is not None and update.title != doc.title:
        if doc.file_path:
            try:
                new_path = await storage_service.rename_item(
                    doc.file_path, update.title, doc.storage_location_id
                )
                if str(new_path) != doc.file_path:
                    doc.file_path = str(new_path)
                    if isinstance(new_path, Path):
                        doc.filename = new_path.name
                    else:
                        doc.filename = str(new_path).split("/")[-1]
            except Exception as e:
                logger.warning(f"Could not rename file for document {document_id}: {e}")
        doc.title = update.title

    # Handle project change - move file to new project folder
    if update.project_id is not None and update.project_id != doc.project_id:
        new_project_name = None
        if update.project_id:
            project = await db.get(Project, update.project_id)
            if not project:
                raise HTTPException(status_code=404, detail="Project not found")
            new_project_name = project.name

        if doc.file_path:
            try:
                new_path = await storage_service.move_to_project(
                    doc.file_path, new_project_name, doc.storage_location_id
                )
                doc.file_path = str(new_path)
            except Exception as e:
                logger.warning(f"Could not move file for document {document_id}: {e}")

        doc.project_id = update.project_id or None

    await db.commit()
    await db.refresh(doc)
    await broadcast("documents", "updated", doc.id)
    return _doc_to_response(doc)


@router.delete("/{document_id}", response_model=MessageResponse)
async def delete_document(
    db: Annotated[AsyncSession, Depends(get_db)],
    document_id: str,
) -> MessageResponse:
    """Delete a document and its file."""
    doc = await db.get(Document, document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    # Delete file from storage
    try:
        await storage_service.delete_file(doc.file_path, doc.storage_location_id)
    except Exception as e:
        logger.warning(f"Failed to delete file {doc.file_path}: {e}")

    await db.delete(doc)
    await db.commit()

    await broadcast("documents", "deleted", document_id)
    return MessageResponse(message="Document deleted", id=document_id)


class DocumentPropertiesResponse(BaseModel):
    """Response model for document file properties."""

    id: str
    title: str
    file_path: str
    filename: str
    file_size: int | None
    file_size_formatted: str
    file_exists: bool
    mime_type: str | None
    page_count: int | None
    status: str
    created_at: str
    updated_at: str
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


@router.get("/{document_id}/properties", response_model=DocumentPropertiesResponse)
async def get_document_properties(
    db: Annotated[AsyncSession, Depends(get_db)],
    document_id: str,
) -> DocumentPropertiesResponse:
    """Get detailed file properties for a document.

    Returns information useful for a Properties dialog including
    file path, size, whether file exists, etc.
    """
    from persistence.models import StorageLocation

    doc = await db.get(Document, document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    file_path = Path(doc.file_path) if doc.file_path else None
    file_exists = file_path.exists() if file_path else False

    # Get actual file size from disk if possible
    actual_size = doc.file_size_bytes
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

    return DocumentPropertiesResponse(
        id=doc.id,
        title=doc.title,
        file_path=doc.file_path or "",
        filename=doc.filename or "",
        file_size=actual_size,
        file_size_formatted=_format_file_size(actual_size),
        file_exists=file_exists,
        mime_type=doc.mime_type,
        page_count=doc.metadata_.get("page_count") if doc.metadata_ else None,
        status=doc.status,
        created_at=doc.created_at.isoformat() if doc.created_at else "",
        updated_at=doc.updated_at.isoformat() if doc.updated_at else "",
        storage_location=storage_location_name,
    )


@router.get("/{document_id}/file", response_model=None)
async def download_document_file(
    db: Annotated[AsyncSession, Depends(get_db)],
    document_id: str,
    inline: Annotated[bool, Query(description="Display inline instead of download")] = False,
):
    """Download or view the original document file.

    Use inline=true to display in browser (for iframe embedding).
    For Office documents (DOCX, XLSX, PPTX), returns the PDF preview when inline=true.
    """
    doc = await db.get(Document, document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    # Check if document is in cloud storage
    if doc.storage_location_id:
        location_result = await db.execute(
            select(StorageLocation).where(StorageLocation.id == doc.storage_location_id)
        )
        location = location_result.scalar_one_or_none()

        if location and location.type == "cloud":
            # Use storage adapter to read from cloud
            try:
                adapter = get_adapter(location)
                content = await adapter.read_file(doc.file_path)
                content_disposition = "inline" if inline else "attachment"
                return Response(
                    content=content,
                    media_type=doc.mime_type,
                    headers={
                        "Content-Disposition": f'{content_disposition}; filename="{doc.filename}"',
                    },
                )
            except Exception as e:
                logger.error(f"Failed to read cloud file: {e}")
                raise HTTPException(
                    status_code=404, detail="File not found in cloud storage"
                )

    # For inline viewing of Office documents, use the PDF preview if available
    if inline and doc.metadata_.get("preview_path"):
        preview_path = Path(doc.metadata_["preview_path"])
        if not preview_path.is_absolute():
            preview_path = storage_service.get_full_path(doc.metadata_["preview_path"])
        if preview_path.exists():
            return FileResponse(
                path=preview_path,
                filename=doc.filename.rsplit(".", 1)[0] + ".pdf",
                media_type="application/pdf",
                content_disposition_type="inline",
            )

    # File path handling - try multiple resolution strategies
    file_path = Path(doc.file_path)
    resolved_path = None

    # Strategy 1: Direct path (if absolute or exists relative to CWD)
    if file_path.exists():
        resolved_path = file_path
    # Strategy 2: For documents with storage_location_id, resolve relative to storage location
    elif doc.storage_location_id and not file_path.is_absolute():
        location_result = await db.execute(
            select(StorageLocation).where(StorageLocation.id == doc.storage_location_id)
        )
        location = location_result.scalar_one_or_none()
        if location and location.type == "local" and location.config.get("path"):
            storage_path = Path(location.config["path"])
            # If storage path itself is relative, the file might be at that relative location
            candidate = storage_path / doc.file_path.lstrip("/")
            if candidate.exists():
                resolved_path = candidate
            # Also try if file_path already includes the storage folder name
            elif "/" in doc.file_path:
                # file_path might be like "verbatim/file.png", storage path is "/Users/.../verbatim"
                # In this case, the file_path folder name should be stripped
                path_parts = doc.file_path.split("/", 1)
                if len(path_parts) > 1:
                    candidate = storage_path / path_parts[1]
                    if candidate.exists():
                        resolved_path = candidate
    # Strategy 3: Fallback to media_dir for legacy paths
    if not resolved_path and not file_path.is_absolute():
        fallback_path = storage_service.get_full_path(doc.file_path)
        if fallback_path.exists():
            resolved_path = fallback_path

    if not resolved_path:
        logger.warning(f"File not found for document {doc.id}: tried {doc.file_path}")
        raise HTTPException(status_code=404, detail="File not found on disk")

    # For inline viewing (iframe), use content-disposition: inline
    content_disposition = "inline" if inline else "attachment"

    return FileResponse(
        path=resolved_path,
        filename=doc.filename,
        media_type=doc.mime_type,
        content_disposition_type=content_disposition,
    )


@router.post("/{document_id}/process", response_model=MessageResponse)
async def reprocess_document(
    db: Annotated[AsyncSession, Depends(get_db)],
    document_id: str,
) -> MessageResponse:
    """Re-trigger document processing."""
    doc = await db.get(Document, document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    # Reset status and queue job
    doc.status = "pending"
    doc.error_message = None
    await db.commit()

    from services.jobs import job_queue
    await job_queue.enqueue("process_document", {"document_id": document_id})

    return MessageResponse(message="Processing queued", id=document_id)


@router.post("/{document_id}/ocr", response_model=MessageResponse)
async def run_ocr(
    db: Annotated[AsyncSession, Depends(get_db)],
    document_id: str,
) -> MessageResponse:
    """Run OCR processing on a document.

    This enables OCR for the document and triggers reprocessing.
    Can be used on documents that were uploaded without OCR enabled.
    """
    from sqlalchemy.orm.attributes import flag_modified

    doc = await db.get(Document, document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    # Enable OCR in metadata
    doc.metadata_["enable_ocr"] = True
    flag_modified(doc, "metadata_")

    # Reset status and queue job
    doc.status = "pending"
    doc.error_message = None
    doc.extracted_text = None
    doc.extracted_markdown = None
    await db.commit()

    from services.jobs import job_queue
    await job_queue.enqueue("process_document", {"document_id": document_id})

    return MessageResponse(message="OCR processing queued", id=document_id)


@router.post("/{document_id}/cancel", response_model=MessageResponse)
async def cancel_document_processing(
    db: Annotated[AsyncSession, Depends(get_db)],
    document_id: str,
) -> MessageResponse:
    """Cancel document processing.

    Cancels any running or queued processing job for this document.
    """
    from persistence.models import Job
    from services.jobs import job_queue

    doc = await db.get(Document, document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    if doc.status not in ("pending", "processing"):
        raise HTTPException(
            status_code=400,
            detail=f"Document is not being processed (status: {doc.status})"
        )

    # Find the active job for this document
    result = await db.execute(
        select(Job)
        .where(Job.job_type == "process_document")
        .where(Job.payload["document_id"].astext == document_id)
        .where(Job.status.in_(["queued", "running"]))
        .order_by(Job.created_at.desc())
        .limit(1)
    )
    job = result.scalar_one_or_none()

    if job:
        # Cancel the job
        cancelled = await job_queue.cancel_job(job.id)
        if cancelled:
            # Update document status
            doc.status = "cancelled"
            doc.error_message = "Processing was cancelled by user"
            await db.commit()
            return MessageResponse(message="Processing cancelled", id=document_id)
        else:
            raise HTTPException(status_code=500, detail="Failed to cancel job")
    else:
        # No job found, just update document status
        doc.status = "cancelled"
        doc.error_message = "Processing was cancelled by user"
        await db.commit()
        return MessageResponse(message="Processing cancelled (no active job found)", id=document_id)


@router.get("/{document_id}/content")
async def get_document_content(
    db: Annotated[AsyncSession, Depends(get_db)],
    document_id: str,
    format: Annotated[str, Query(description="Output format: text or markdown")] = "markdown",
) -> dict:
    """Get extracted text content from a document."""
    doc = await db.get(Document, document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    if doc.status != "completed":
        raise HTTPException(status_code=400, detail=f"Document not processed yet (status: {doc.status})")

    if format == "markdown" and doc.extracted_markdown:
        return {"content": doc.extracted_markdown, "format": "markdown"}
    elif doc.extracted_text:
        return {"content": doc.extracted_text, "format": "text"}
    else:
        raise HTTPException(status_code=404, detail="No extracted content available")

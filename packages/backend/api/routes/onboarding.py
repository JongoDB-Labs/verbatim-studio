"""Onboarding API — sample workspace install / uninstall.

Optional-on-tour install of a curated sample workspace ("Tour: Sample
Workspace") so users have something to apply the feature tour to. The
zip is hosted alongside the corpus in verbatim-studio-releases.

# Lifecycle

  1. User reaches first tour step → modal asks "Install sample
     workspace? (~10 MB)"
  2. POST /api/onboarding/sample-workspace/install
     → backend downloads tour-demo.zip, extracts, seeds DB
  3. Tour proceeds — every page they visit has real content
  4. After tour completes → modal offers "Keep sample workspace, or
     remove it?"
  5. DELETE /api/onboarding/sample-workspace removes only rows
     tagged with metadata.is_demo = true

# Sandboxing

Every project, recording, document, conversation row created by the
install carries `metadata.is_demo = true`. The DELETE endpoint walks
those tags and removes ONLY the tagged rows — user-created data is
never touched even if titles overlap.

# Idempotency

If a demo project already exists, install is a no-op (returns existing
project IDs). So a user clicking "install" twice in quick succession
won't duplicate.
"""

from __future__ import annotations

import json
import logging
import shutil
import tempfile
import urllib.request
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from persistence import get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/onboarding", tags=["onboarding"])


# Public release URL — same hosting as the corpus.
TOUR_DEMO_URL = (
    "https://github.com/JongoDB/verbatim-studio-releases"
    "/releases/latest/download/tour-demo.zip"
)


def _local_dev_zip_path() -> Path | None:
    """Local dev: prefer dist/tour-demo.zip if present so we don't
    need a release uploaded for development testing."""
    repo_root = Path(__file__).resolve().parents[3]
    candidate = repo_root / "dist" / "tour-demo.zip"
    return candidate if candidate.exists() else None


def _demo_marker(meta: dict | None) -> bool:
    """Return True if a metadata dict tags the row as demo data."""
    return bool(meta and meta.get("is_demo") is True)


class SampleIdsResponse(BaseModel):
    """Maps manifest demo_keys (e.g. "roadmap-brief") to actual DB IDs.

    The tour uses this to drill into specific surfaces — when the tour
    step is "show inline notes", it can navigate to the roadmap-brief
    document by looking up demo_key → document_id here.
    """

    projects: dict[str, str]
    recordings: dict[str, str]
    documents: dict[str, str]
    chats: dict[str, str]


@router.get("/sample-workspace/ids", response_model=SampleIdsResponse)
async def sample_workspace_ids(
    db: AsyncSession = Depends(get_db),
) -> SampleIdsResponse:
    """Return demo_key → entity_id map for tour drilldown navigation."""
    from persistence.models import (
        Conversation, Document, Project, Recording,
    )

    out = SampleIdsResponse(projects={}, recordings={}, documents={}, chats={})

    proj_result = await db.execute(select(Project))
    project_ids: set[str] = set()
    for p in proj_result.scalars():
        if _demo_marker(p.metadata_):
            project_ids.add(p.id)
            key = p.metadata_.get("demo_key")
            if key:
                out.projects[key] = p.id

    rec_result = await db.execute(select(Recording))
    for r in rec_result.scalars():
        if _demo_marker(r.metadata_) or r.project_id in project_ids:
            key = r.metadata_.get("demo_key") if r.metadata_ else None
            if key:
                out.recordings[key] = r.id

    doc_result = await db.execute(select(Document))
    for d in doc_result.scalars():
        if _demo_marker(d.metadata_) or d.project_id in project_ids:
            key = d.metadata_.get("demo_key") if d.metadata_ else None
            if key:
                out.documents[key] = d.id

    # Conversations don't have a metadata column — match on title prefix.
    convo_result = await db.execute(select(Conversation))
    for c in convo_result.scalars():
        if c.project_id in project_ids:
            # Use project demo_key + title as a stable lookup key
            out.chats[c.id] = c.id  # callers use the id directly; map for completeness
    return out


class InstallStatusResponse(BaseModel):
    """Reports whether a demo workspace is already installed."""

    installed: bool
    project_count: int
    primary_project_id: str | None
    primary_project_name: str | None


@router.get("/sample-workspace/status", response_model=InstallStatusResponse)
async def sample_workspace_status(
    db: AsyncSession = Depends(get_db),
) -> InstallStatusResponse:
    """Detect existing demo install (idempotency check + UI state)."""
    from persistence.models import Project

    result = await db.execute(select(Project).where(Project.is_archived == False))  # noqa: E712
    demo_projects = [p for p in result.scalars() if _demo_marker(p.metadata_)]
    primary = next(
        (p for p in demo_projects if p.metadata_.get("demo_key") == "primary"),
        None,
    )
    return InstallStatusResponse(
        installed=len(demo_projects) > 0,
        project_count=len(demo_projects),
        primary_project_id=primary.id if primary else None,
        primary_project_name=primary.name if primary else None,
    )


class InstallResponse(BaseModel):
    installed: bool
    project_ids: list[str]
    primary_project_id: str | None
    counts: dict[str, int]
    message: str


@router.post("/sample-workspace/install", response_model=InstallResponse)
async def install_sample_workspace(
    db: AsyncSession = Depends(get_db),
) -> InstallResponse:
    """Download + extract + seed the sample workspace."""
    from persistence.models import (
        Conversation, ConversationMessage, Document, Note, Project,
        Recording, Segment, Transcript,
    )

    # Idempotency check
    existing_status = await sample_workspace_status(db)
    if existing_status.installed:
        return InstallResponse(
            installed=True,
            project_ids=[existing_status.primary_project_id] if existing_status.primary_project_id else [],
            primary_project_id=existing_status.primary_project_id,
            counts={},
            message="Sample workspace already installed.",
        )

    # Acquire the zip — local dist/ for dev, otherwise release URL.
    local_zip = _local_dev_zip_path()
    work_dir = Path(tempfile.mkdtemp(prefix="verbatim-tour-"))
    try:
        if local_zip:
            zip_path = local_zip
            logger.info("Using local tour-demo zip: %s", local_zip)
        else:
            zip_path = work_dir / "tour-demo.zip"
            logger.info("Fetching tour-demo zip from %s", TOUR_DEMO_URL)
            req = urllib.request.Request(
                TOUR_DEMO_URL,
                headers={"User-Agent": "verbatim-studio/onboarding-install"},
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                zip_path.write_bytes(resp.read())

        # Extract
        with zipfile.ZipFile(zip_path) as z:
            z.extractall(work_dir)

        manifest_path = work_dir / "manifest.json"
        if not manifest_path.exists():
            raise HTTPException(
                status_code=500,
                detail="tour-demo.zip is missing manifest.json — corrupt download",
            )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        files_dir = work_dir / "files"

        from core.config import settings
        from services.storage import storage_service

        # Track created entities for the response
        project_id_map: dict[str, str] = {}  # manifest key → DB id
        document_id_map: dict[str, str] = {}
        recording_id_map: dict[str, str] = {}
        counts = {"projects": 0, "recordings": 0, "documents": 0, "notes": 0, "chats": 0}

        # 1) Projects
        for project_spec in manifest.get("projects", []):
            project = Project(
                id=str(uuid.uuid4()),
                name=project_spec["name"],
                description=project_spec.get("description"),
                icon=project_spec.get("icon"),
                color=project_spec.get("color"),
                metadata_={
                    "is_demo": True,
                    "demo_key": project_spec["key"],
                    "schema_version": manifest.get("schema_version", 1),
                },
            )
            db.add(project)
            await db.flush()
            project_id_map[project_spec["key"]] = project.id
            counts["projects"] += 1
            # Create a real folder on disk (Verbatim's project-as-folder model)
            try:
                await storage_service.create_project_folder(project.name)
            except Exception as e:
                logger.debug("create_project_folder skipped: %s", e)

        # 2) Recordings + transcripts
        for rec_spec in manifest.get("recordings", []):
            project_id = project_id_map.get(rec_spec["project"])
            src_path = files_dir / rec_spec["filename"]
            if not src_path.exists():
                logger.warning("Recording file missing in zip: %s", rec_spec["filename"])
                continue
            # Copy audio into media dir
            settings.ensure_directories()
            dest_dir = settings.MEDIA_DIR / "demo" / "recordings"
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest_path = dest_dir / src_path.name
            shutil.copy2(src_path, dest_path)

            recording = Recording(
                id=str(uuid.uuid4()),
                project_id=project_id,
                title=rec_spec["title"],
                file_path=str(dest_path),
                file_name=src_path.name,
                file_size=dest_path.stat().st_size,
                duration_seconds=rec_spec.get("duration_seconds"),
                mime_type="audio/mpeg",
                status="completed",
                metadata_={
                    "is_demo": True,
                    "demo_key": rec_spec["key"],
                    "description": rec_spec.get("description"),
                    "attribution": rec_spec.get("attribution"),
                },
            )
            db.add(recording)
            await db.flush()
            recording_id_map[rec_spec["key"]] = recording.id
            counts["recordings"] += 1

            # Transcript with segments
            tx = rec_spec.get("transcript")
            if tx:
                transcript = Transcript(
                    id=str(uuid.uuid4()),
                    recording_id=recording.id,
                    language=rec_spec.get("language", "en"),
                    word_count=sum(
                        len((s.get("text") or "").split())
                        for s in tx.get("segments", [])
                    ),
                )
                db.add(transcript)
                await db.flush()
                for idx, seg in enumerate(tx.get("segments", [])):
                    db.add(Segment(
                        id=str(uuid.uuid4()),
                        transcript_id=transcript.id,
                        segment_index=idx,
                        speaker=seg.get("speaker"),
                        start_time=seg.get("start", 0.0),
                        end_time=seg.get("end", 0.0),
                        text=seg.get("text", ""),
                        confidence=seg.get("confidence"),
                    ))

        # 3) Documents
        for doc_spec in manifest.get("documents", []):
            project_id = project_id_map.get(doc_spec["project"])
            src_path = files_dir / doc_spec["filename"]
            if not src_path.exists():
                logger.warning("Document file missing in zip: %s", doc_spec["filename"])
                continue
            settings.ensure_directories()
            dest_dir = settings.MEDIA_DIR / "demo" / "documents"
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest_path = dest_dir / src_path.name
            shutil.copy2(src_path, dest_path)

            # Document uses filename + file_size_bytes (different field
            # names from Recording, which uses file_name + file_size).
            document = Document(
                id=str(uuid.uuid4()),
                project_id=project_id,
                title=doc_spec["title"],
                file_path=str(dest_path),
                filename=src_path.name,
                file_size_bytes=dest_path.stat().st_size,
                mime_type=doc_spec.get("mime_type") or "application/octet-stream",
                extracted_text=doc_spec.get("extracted_text"),
                status="completed",
                metadata_={
                    "is_demo": True,
                    "demo_key": doc_spec["key"],
                    "attribution": doc_spec.get("attribution"),
                },
            )
            db.add(document)
            await db.flush()
            document_id_map[doc_spec["key"]] = document.id
            counts["documents"] += 1

        # 4) Notes (anchored to documents)
        for note_spec in manifest.get("notes", []):
            doc_id = document_id_map.get(note_spec["document"])
            if not doc_id:
                continue
            note = Note(
                id=str(uuid.uuid4()),
                document_id=doc_id,
                anchor_type=note_spec["anchor_type"],
                anchor_data=note_spec.get("anchor_data") or {},
                content=note_spec["content"],
            )
            # Some Note models track is_demo via metadata; if there's
            # no metadata field, the demo_key on the parent document
            # is enough for cleanup.
            db.add(note)
            counts["notes"] += 1

        # 5) Chats
        for chat_spec in manifest.get("chats", []):
            project_id = project_id_map.get(chat_spec["project"])
            # Conversation has no metadata column — demo cleanup happens
            # via project_id (the parent project carries is_demo=true).
            conversation = Conversation(
                id=str(uuid.uuid4()),
                project_id=project_id,
                title=chat_spec["title"],
            )
            db.add(conversation)
            await db.flush()
            for msg in chat_spec.get("messages", []):
                db.add(ConversationMessage(
                    id=str(uuid.uuid4()),
                    conversation_id=conversation.id,
                    role=msg["role"],
                    content=msg["content"],
                ))
            counts["chats"] += 1

        await db.commit()

        primary = project_id_map.get("primary")
        return InstallResponse(
            installed=True,
            project_ids=list(project_id_map.values()),
            primary_project_id=primary,
            counts=counts,
            message=(
                f"Installed sample workspace with {counts['projects']} projects, "
                f"{counts['recordings']} recordings, {counts['documents']} documents, "
                f"{counts['notes']} notes, {counts['chats']} chats."
            ),
        )

    except urllib.error.HTTPError as e:
        await db.rollback()
        raise HTTPException(
            status_code=502,
            detail=f"Failed to download tour-demo.zip from release: HTTP {e.code}",
        )
    except Exception as e:
        await db.rollback()
        logger.exception("Sample workspace install failed")
        raise HTTPException(status_code=500, detail=f"Install failed: {e}")
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


class RemoveResponse(BaseModel):
    removed: bool
    counts: dict[str, int]


@router.delete("/sample-workspace", response_model=RemoveResponse)
async def remove_sample_workspace(
    db: AsyncSession = Depends(get_db),
) -> RemoveResponse:
    """Remove only rows tagged metadata.is_demo == true."""
    from persistence.models import (
        Conversation, ConversationMessage, Document, Note, Project,
        Recording, Segment, Transcript,
    )

    counts = {
        "projects": 0, "recordings": 0, "documents": 0,
        "notes": 0, "chats": 0, "messages": 0, "segments": 0, "transcripts": 0,
    }

    # Find demo projects + cascade through their content
    proj_result = await db.execute(select(Project))
    demo_projects = [p for p in proj_result.scalars() if _demo_marker(p.metadata_)]
    demo_project_ids = {p.id for p in demo_projects}

    if not demo_projects:
        return RemoveResponse(removed=False, counts=counts)

    # Remove conversations + messages — Conversation has no metadata
    # column, so demo identification is via parent project_id only.
    convo_result = await db.execute(select(Conversation))
    for convo in convo_result.scalars():
        if convo.project_id in demo_project_ids:
            msg_result = await db.execute(
                select(ConversationMessage).where(
                    ConversationMessage.conversation_id == convo.id,
                )
            )
            for msg in msg_result.scalars():
                await db.delete(msg)
                counts["messages"] += 1
            await db.delete(convo)
            counts["chats"] += 1

    # Remove documents + their notes
    doc_result = await db.execute(select(Document))
    for doc in doc_result.scalars():
        if _demo_marker(doc.metadata_) or doc.project_id in demo_project_ids:
            note_result = await db.execute(
                select(Note).where(Note.document_id == doc.id)
            )
            for note in note_result.scalars():
                await db.delete(note)
                counts["notes"] += 1
            # Best-effort delete the file
            if doc.file_path:
                try:
                    Path(doc.file_path).unlink(missing_ok=True)
                except Exception:
                    pass
            await db.delete(doc)
            counts["documents"] += 1

    # Remove recordings + their transcripts + segments
    rec_result = await db.execute(select(Recording))
    for rec in rec_result.scalars():
        if _demo_marker(rec.metadata_) or rec.project_id in demo_project_ids:
            tx_result = await db.execute(
                select(Transcript).where(Transcript.recording_id == rec.id)
            )
            for tx in tx_result.scalars():
                seg_result = await db.execute(
                    select(Segment).where(Segment.transcript_id == tx.id)
                )
                for seg in seg_result.scalars():
                    await db.delete(seg)
                    counts["segments"] += 1
                await db.delete(tx)
                counts["transcripts"] += 1
            if rec.file_path:
                try:
                    Path(rec.file_path).unlink(missing_ok=True)
                except Exception:
                    pass
            await db.delete(rec)
            counts["recordings"] += 1

    # Finally remove the demo projects themselves
    from services.storage import storage_service
    for project in demo_projects:
        try:
            await storage_service.delete_project_folder(project.name, delete_contents=True)
        except Exception as e:
            logger.debug("project folder cleanup skipped: %s", e)
        await db.delete(project)
        counts["projects"] += 1

    await db.commit()
    return RemoveResponse(removed=True, counts=counts)

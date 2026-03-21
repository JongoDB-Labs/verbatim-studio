"""Organization tools — create_project, tag_recordings, get_recording_info, system_status.

create_project: Creates a new Project record.
tag_recordings: Creates tags if needed and assigns them to recordings.
get_recording_info: Queries recording metadata or lists recent recordings.
system_status: Returns formatted system status with content counts.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import select, func as sa_func

from services.tool_registry import ToolContext, ToolDef, ToolResult

logger = logging.getLogger(__name__)


# ── create_project ────────────────────────────────────────────────────


async def handle_create_project(args: dict, ctx: ToolContext) -> ToolResult:
    """Create a new Project record."""
    name = args.get("name", "").strip()
    description = args.get("description", "")

    if not name:
        return ToolResult(content="Project name is required.")

    from persistence.models import Project

    try:
        project = Project(
            id=str(uuid.uuid4()),
            name=name,
            description=description or None,
        )
        ctx.db.add(project)
        await ctx.db.commit()
        await ctx.db.refresh(project)

        return ToolResult(
            content=f"Created project \"{name}\" (id: {project.id})."
        )
    except Exception as e:
        logger.exception("Failed to create project")
        return ToolResult(content=f"Failed to create project: {e}")


# ── tag_recordings ────────────────────────────────────────────────────


async def handle_tag_recordings(args: dict, ctx: ToolContext) -> ToolResult:
    """Create tags if needed and assign them to recordings."""
    recording_ids = args.get("recording_ids", [])
    tag_names = args.get("tag_names", [])

    if not recording_ids:
        return ToolResult(content="No recording IDs provided.")

    if not tag_names:
        return ToolResult(content="No tag names provided.")

    from persistence.models import Recording, Tag, RecordingTag

    try:
        # Resolve or create tags
        tags: list = []
        for tag_name in tag_names:
            result = await ctx.db.execute(
                select(Tag).where(Tag.name == tag_name)
            )
            tag = result.scalar_one_or_none()
            if tag is None:
                tag = Tag(id=str(uuid.uuid4()), name=tag_name)
                ctx.db.add(tag)
            tags.append(tag)

        # Assign tags to recordings
        tagged_count = 0
        for rec_id in recording_ids:
            result = await ctx.db.execute(
                select(Recording).where(Recording.id == rec_id)
            )
            recording = result.scalar_one_or_none()
            if recording is None:
                continue

            existing_tag_ids = {t.id for t in recording.tags}
            for tag in tags:
                if tag.id not in existing_tag_ids:
                    recording.tags.append(tag)
                    tagged_count += 1

        await ctx.db.commit()

        tag_list = ", ".join(tag_names)
        return ToolResult(
            content=f"Tagged {len(recording_ids)} recording(s) with: {tag_list}. "
            f"({tagged_count} new tag assignment(s) created.)"
        )
    except Exception as e:
        logger.exception("Failed to tag recordings")
        return ToolResult(content=f"Failed to tag recordings: {e}")


# ── get_recording_info ────────────────────────────────────────────────


async def handle_get_recording_info(args: dict, ctx: ToolContext) -> ToolResult:
    """Query recording metadata or list recent recordings."""
    recording_id = args.get("recording_id")
    limit = args.get("limit", 10)

    from persistence.models import Recording

    try:
        if recording_id:
            # Get specific recording
            result = await ctx.db.execute(
                select(Recording).where(Recording.id == recording_id)
            )
            recording = result.scalar_one_or_none()
            if recording is None:
                return ToolResult(content=f"Recording not found: {recording_id}")

            duration_str = "N/A"
            if recording.duration_seconds is not None:
                mins = int(recording.duration_seconds // 60)
                secs = int(recording.duration_seconds % 60)
                duration_str = f"{mins}m {secs}s"

            file_size_str = "N/A"
            if recording.file_size is not None:
                mb = recording.file_size / (1024 * 1024)
                file_size_str = f"{mb:.1f} MB"

            tag_names = [t.name for t in recording.tags] if recording.tags else []
            tags_str = ", ".join(tag_names) if tag_names else "none"

            lines = [
                f"Recording: {recording.title}",
                f"  ID: {recording.id}",
                f"  Status: {recording.status}",
                f"  Duration: {duration_str}",
                f"  File size: {file_size_str}",
                f"  Created: {recording.created_at.isoformat()}",
                f"  Tags: {tags_str}",
            ]
            return ToolResult(content="\n".join(lines))
        else:
            # List recent recordings
            query = select(Recording).order_by(Recording.created_at.desc()).limit(limit)
            if ctx.project_id:
                query = query.where(Recording.project_id == ctx.project_id)

            result = await ctx.db.execute(query)
            recordings = result.scalars().all()

            if not recordings:
                return ToolResult(content="No recordings found.")

            lines = [f"Recent recordings ({len(recordings)}):\n"]
            for r in recordings:
                lines.append(f"- {r.title} (id: {r.id}, status: {r.status})")
            return ToolResult(content="\n".join(lines))
    except Exception as e:
        logger.exception("Failed to get recording info")
        return ToolResult(content=f"Failed to get recording info: {e}")


# ── system_status ─────────────────────────────────────────────────────


async def handle_system_status(args: dict, ctx: ToolContext) -> ToolResult:
    """Return formatted system status with content counts."""
    from persistence.models import Recording, Transcript, Project

    try:
        rec_result = await ctx.db.execute(
            select(sa_func.count()).select_from(Recording)
        )
        recording_count = rec_result.scalar()

        trans_result = await ctx.db.execute(
            select(sa_func.count()).select_from(Transcript)
        )
        transcript_count = trans_result.scalar()

        proj_result = await ctx.db.execute(
            select(sa_func.count()).select_from(Project)
        )
        project_count = proj_result.scalar()

        lines = [
            "System Status:",
            f"  Projects: {project_count}",
            f"  Recordings: {recording_count}",
            f"  Transcripts: {transcript_count}",
        ]
        return ToolResult(content="\n".join(lines))
    except Exception as e:
        logger.exception("Failed to get system status")
        return ToolResult(content=f"Failed to get system status: {e}")


# ── Tool definitions ─────────────────────────────────────────────────


create_project_tool = ToolDef(
    name="create_project",
    description="Create a new project workspace for organizing recordings and documents.",
    parameters={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Project name"},
            "description": {"type": "string", "description": "Optional project description"},
        },
        "required": ["name"],
    },
    handler=handle_create_project,
    project_scoped=False,
)

tag_recordings_tool = ToolDef(
    name="tag_recordings",
    description="Create tags and assign them to recordings for organization.",
    parameters={
        "type": "object",
        "properties": {
            "recording_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Recording IDs to tag",
            },
            "tag_names": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Tag names to assign (created if they don't exist)",
            },
        },
        "required": ["recording_ids", "tag_names"],
    },
    handler=handle_tag_recordings,
    project_scoped=True,
)

get_recording_info_tool = ToolDef(
    name="get_recording_info",
    description="Get detailed info about a specific recording, or list recent recordings.",
    parameters={
        "type": "object",
        "properties": {
            "recording_id": {"type": "string", "description": "Specific recording ID (omit to list recent)"},
            "limit": {"type": "integer", "description": "Max recordings to list (default 10)"},
        },
    },
    handler=handle_get_recording_info,
    project_scoped=True,
)

system_status_tool = ToolDef(
    name="system_status",
    description="Show system status including counts of projects, recordings, and transcripts.",
    parameters={
        "type": "object",
        "properties": {},
    },
    handler=handle_system_status,
    project_scoped=False,
)

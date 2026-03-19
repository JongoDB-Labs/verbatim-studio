# Projects & Workspaces Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Implement isolated project workspaces with auto-generated type sections, project-scoped AI context, and soft boundary cross-project search.

**Architecture:** Elevate the existing `Project` model from a flat grouping to a workspace container. Add `project_id` FK to `Conversation`. Scope all list/search endpoints via an `X-Active-Project` header read by FastAPI middleware. Frontend gets a global project selector in the sidebar and passes the active project on every API call. Max's context scoping uses automatic semantic search within the active project.

**Tech Stack:** Python/FastAPI/SQLAlchemy (backend), React/TypeScript/TanStack Query (frontend), SQLite with custom migrations, Zustand for active project state.

**Design doc:** `docs/plans/2026-03-17-projects-workspaces-design.md`

---

## Phase 1: Data Foundation

### Task 1: Database Migration — Project columns + Conversation FK

**Files:**
- Create: `packages/backend/migrations/add_project_workspace_columns.py`
- Modify: `packages/backend/persistence/database.py:141-184`

**Step 1: Write the migration file**

Create `packages/backend/migrations/add_project_workspace_columns.py`:

```python
"""Add workspace columns to projects and project_id to conversations."""

import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)


def migrate(db_path: Path) -> None:
    """Add is_archived, sort_order, icon, color to projects; project_id to conversations."""
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Check if projects table exists
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='projects'")
        if not cursor.fetchone():
            logger.info("projects table does not exist — skipping")
            conn.close()
            return

        # Add new columns to projects (idempotent)
        cursor.execute("PRAGMA table_info(projects)")
        project_columns = [col[1] for col in cursor.fetchall()]

        if "is_archived" not in project_columns:
            cursor.execute("ALTER TABLE projects ADD COLUMN is_archived BOOLEAN DEFAULT 0 NOT NULL")
            logger.info("Added is_archived column to projects")

        if "sort_order" not in project_columns:
            cursor.execute("ALTER TABLE projects ADD COLUMN sort_order INTEGER DEFAULT 0 NOT NULL")
            logger.info("Added sort_order column to projects")

        if "icon" not in project_columns:
            cursor.execute("ALTER TABLE projects ADD COLUMN icon VARCHAR(50)")
            logger.info("Added icon column to projects")

        if "color" not in project_columns:
            cursor.execute("ALTER TABLE projects ADD COLUMN color VARCHAR(7)")
            logger.info("Added color column to projects")

        # Add project_id FK to conversations table
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='conversations'")
        if cursor.fetchone():
            cursor.execute("PRAGMA table_info(conversations)")
            conv_columns = [col[1] for col in cursor.fetchall()]

            if "project_id" not in conv_columns:
                cursor.execute(
                    "ALTER TABLE conversations ADD COLUMN project_id VARCHAR(36) "
                    "REFERENCES projects(id) ON DELETE SET NULL"
                )
                logger.info("Added project_id column to conversations")

        conn.commit()
        conn.close()
        logger.info("Project workspace migration completed")

    except sqlite3.Error as e:
        logger.error(f"Database error during project workspace migration: {e}")
        raise
```

**Step 2: Register the migration in database.py**

In `packages/backend/persistence/database.py`, add at the end of `_run_migrations()` (after line 184):

```python
    # Add workspace columns to projects and project_id to conversations
    from migrations.add_project_workspace_columns import migrate as migrate_project_workspace
    await conn.run_sync(lambda _: migrate_project_workspace(db_path))
```

**Step 3: Run the app to verify migration executes**

Run: `cd packages/backend && python -m uvicorn api.app:app --port 52780`
Expected: Log output includes "Project workspace migration completed" (or columns already exist).

**Step 4: Commit**

```bash
git add packages/backend/migrations/add_project_workspace_columns.py packages/backend/persistence/database.py
git commit -m "feat: add project workspace migration (archived, sort_order, icon, color, conversation project_id)"
```

---

### Task 2: Update SQLAlchemy Models

**Files:**
- Modify: `packages/backend/persistence/models.py:53-69` (Project model)
- Modify: `packages/backend/persistence/models.py:432-445` (Conversation model)

**Step 1: Update the Project model**

In `packages/backend/persistence/models.py`, update the `Project` class (lines 53-69) to add the new columns and relationships:

```python
class Project(Base):
    """Project model — isolated workspace for organizing content."""

    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    project_type_id: Mapped[str | None] = mapped_column(
        ForeignKey("project_types.id", ondelete="SET NULL")
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    icon: Mapped[str | None] = mapped_column(String(50))
    color: Mapped[str | None] = mapped_column(String(7))
    created_at: Mapped[datetime] = mapped_column(default=func.now())
    updated_at: Mapped[datetime] = mapped_column(default=func.now(), onupdate=func.now())

    project_type: Mapped[ProjectType | None] = relationship(back_populates="projects")
    recordings: Mapped[list["Recording"]] = relationship(back_populates="project")
    documents: Mapped[list["Document"]] = relationship(back_populates="project")
    conversations: Mapped[list["Conversation"]] = relationship(back_populates="project")
```

**Step 2: Update the Conversation model**

In `packages/backend/persistence/models.py`, update the `Conversation` class (lines 432-445):

```python
class Conversation(Base):
    """Saved chat conversation."""

    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    title: Mapped[str | None] = mapped_column(String(255))
    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL")
    )
    compressed_memory: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(default=func.now())
    updated_at: Mapped[datetime] = mapped_column(default=func.now(), onupdate=func.now())

    project: Mapped["Project | None"] = relationship(back_populates="conversations")
    messages: Mapped[list["ConversationMessage"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan", order_by="ConversationMessage.created_at"
    )
```

**Step 3: Update the Document model relationship**

The Document model (line 375) already has `project: Mapped["Project | None"] = relationship()` but it needs `back_populates` added to match the new Project.documents relationship:

```python
    project: Mapped["Project | None"] = relationship(back_populates="documents")
```

**Step 4: Verify the app starts without errors**

Run: `cd packages/backend && python -m uvicorn api.app:app --port 52780`
Expected: App starts successfully, no import errors.

**Step 5: Commit**

```bash
git add packages/backend/persistence/models.py
git commit -m "feat: update Project and Conversation models with workspace fields"
```

---

### Task 3: Active Project Setting + Seed Default Project

**Files:**
- Modify: `packages/backend/persistence/database.py:75-122` (seed_defaults)
- Modify: `packages/backend/api/routes/projects.py`

**Step 1: Add default project seeding**

In `packages/backend/persistence/database.py`, add a `seed_default_project()` call inside `init_db()`, after the `seed_defaults(session)` call (line 138):

```python
async def init_db() -> None:
    """Initialize database tables and seed defaults."""
    from .models import Base

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Run schema migrations for changes that create_all won't handle
    async with engine.begin() as conn:
        await _run_migrations(conn)

    # Auto-seed defaults on startup
    async with async_session() as session:
        await seed_defaults(session)
        await _seed_default_project(session)


async def _seed_default_project(session: AsyncSession) -> None:
    """Ensure a 'General' default project exists for unscoped content."""
    from sqlalchemy import select
    from .models import Project, Setting

    # Check if default project setting exists
    result = await session.execute(
        select(Setting).where(Setting.key == "default_project_id")
    )
    existing_setting = result.scalar_one_or_none()
    if existing_setting:
        return  # Already seeded

    # Check if a 'General' project already exists
    result = await session.execute(
        select(Project).where(Project.name == "General")
    )
    general = result.scalar_one_or_none()

    if not general:
        general = Project(
            name="General",
            description="Default project for unorganized content",
            sort_order=0,
        )
        session.add(general)
        await session.flush()

    # Store the default project ID as a setting
    setting = Setting(key="default_project_id", value={"id": general.id})
    session.add(setting)
    await session.commit()
```

**Step 2: Add active project endpoints to projects.py**

Add these endpoints at the end of `packages/backend/api/routes/projects.py`:

```python
@router.patch("/{project_id}/archive", response_model=MessageResponse)
async def archive_project(
    db: Annotated[AsyncSession, Depends(get_db)],
    project_id: str,
) -> MessageResponse:
    """Archive a project (soft-hide from sidebar)."""
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    project.is_archived = True
    await db.commit()
    await broadcast("projects", "updated", project_id)
    return MessageResponse(message="Project archived", id=project_id)


@router.patch("/{project_id}/unarchive", response_model=MessageResponse)
async def unarchive_project(
    db: Annotated[AsyncSession, Depends(get_db)],
    project_id: str,
) -> MessageResponse:
    """Unarchive a project."""
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    project.is_archived = False
    await db.commit()
    await broadcast("projects", "updated", project_id)
    return MessageResponse(message="Project unarchived", id=project_id)


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

    # Count recordings
    rec_count = await db.scalar(
        select(func.count(Recording.id)).where(Recording.project_id == project_id)
    ) or 0

    # Count documents
    doc_count = await db.scalar(
        select(func.count(Document.id)).where(Document.project_id == project_id)
    ) or 0

    # Count notes (via their parent recording or document in this project)
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
```

**Step 3: Update list_projects to filter archived by default**

In `packages/backend/api/routes/projects.py`, update the `list_projects` endpoint (line 144-205). Add an `include_archived` query parameter and filter by `is_archived`:

```python
@router.get("", response_model=ProjectListResponse)
async def list_projects(
    db: Annotated[AsyncSession, Depends(get_db)],
    search: Annotated[str | None, Query(description="Search by name")] = None,
    project_type_id: Annotated[str | None, Query(description="Filter by project type")] = None,
    tag: Annotated[str | None, Query(description="Filter by tag in metadata.tags")] = None,
    include_archived: Annotated[bool, Query(description="Include archived projects")] = False,
) -> ProjectListResponse:
```

Add this filter after the existing `if search:` block (around line 162):

```python
    if not include_archived:
        query = query.where(Project.is_archived == False)
```

**Step 4: Update ProjectResponse to include new fields**

In the `ProjectResponse` model (line 60-71), add:

```python
class ProjectResponse(BaseModel):
    """Response model for a project."""

    id: str
    name: str
    description: str | None
    project_type: ProjectTypeInfo | None
    metadata: dict
    is_archived: bool
    sort_order: int
    icon: str | None
    color: str | None
    recording_count: int
    document_count: int
    inherited_tags: list[InheritedTag]
    created_at: str
    updated_at: str
```

Update all places that construct `ProjectResponse` to include the new fields:
- `create_project` (line 251): add `is_archived=False, sort_order=0, icon=None, color=None, document_count=0`
- `get_project` (line 292): add `is_archived=project.is_archived, sort_order=project.sort_order, icon=project.icon, color=project.color` and compute `document_count`
- `list_projects` (line 183): same additions
- `update_project` (line 403): same additions

For `document_count`, add a count query alongside the recording count:

```python
    doc_count_result = await db.execute(
        select(func.count(Document.id)).where(Document.project_id == project.id)
    )
    document_count = doc_count_result.scalar() or 0
```

**Step 5: Update ProjectUpdate model**

Add new fields to `ProjectUpdate` (line 33-38):

```python
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
```

In the `update_project` handler, add handling for new fields (after line 379):

```python
    if data.is_archived is not None:
        project.is_archived = data.is_archived
    if data.sort_order is not None:
        project.sort_order = data.sort_order
    if data.icon is not None:
        project.icon = data.icon
    if data.color is not None:
        project.color = data.color
```

**Step 6: Add active project settings endpoints**

Add a settings route for active project. In `packages/backend/api/routes/projects.py`:

```python
from persistence.models import Document, Project, ProjectType, Recording, RecordingTag, Setting, Tag


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
    """Set the active project. Pass {"project_id": null} to clear (All Projects mode)."""
    project_id = data.get("project_id")

    # Validate project exists if setting one
    if project_id:
        result = await db.execute(select(Project).where(Project.id == project_id))
        if not result.scalar_one_or_none():
            raise HTTPException(status_code=404, detail="Project not found")

    # Upsert setting
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
```

**Step 7: Verify endpoints work**

Run: `cd packages/backend && python -m uvicorn api.app:app --port 52780`
Test: `curl http://localhost:52780/api/projects/active/current`
Expected: `{"active_project_id": null}`

**Step 8: Commit**

```bash
git add packages/backend/persistence/database.py packages/backend/api/routes/projects.py
git commit -m "feat: add project archive, sections, and active project endpoints"
```

---

## Phase 2: API Scoping

### Task 4: Project Scope Dependency

**Files:**
- Create: `packages/backend/api/dependencies.py`
- Modify: `packages/backend/api/routes/recordings.py`
- Modify: `packages/backend/api/routes/documents.py`
- Modify: `packages/backend/api/routes/conversations.py`
- Modify: `packages/backend/api/routes/search.py`

**Step 1: Create the project scope dependency**

Create `packages/backend/api/dependencies.py`:

```python
"""Shared API dependencies."""

from fastapi import Header


async def get_active_project_id(
    x_active_project: str | None = Header(None, alias="X-Active-Project"),
) -> str | None:
    """Extract the active project ID from the X-Active-Project header.

    Returns None when in 'All Projects' mode (no header or empty value).
    """
    if x_active_project and x_active_project.strip():
        return x_active_project.strip()
    return None
```

**Step 2: Scope the recordings list endpoint**

In `packages/backend/api/routes/recordings.py`, find the `list_recordings` endpoint. Add the dependency:

```python
from api.dependencies import get_active_project_id

@router.get("", response_model=RecordingListResponse)
async def list_recordings(
    db: Annotated[AsyncSession, Depends(get_db)],
    active_project_id: Annotated[str | None, Depends(get_active_project_id)] = None,
    # ... existing params ...
    all: Annotated[bool, Query(description="Return all projects (ignore active project)")] = False,
) -> RecordingListResponse:
```

After the existing filters are applied, add project scoping:

```python
    # Project scoping (from X-Active-Project header)
    if active_project_id and not all:
        query = query.where(Recording.project_id == active_project_id)
    elif project_id:
        # Existing project_id query param still works
        query = query.where(Recording.project_id == project_id)
```

Note: The existing `project_id` query parameter already exists in this endpoint — the new `active_project_id` from the header takes precedence when present.

**Step 3: Scope the documents list endpoint**

In `packages/backend/api/routes/documents.py`, find the list endpoint and add the same pattern:

```python
from api.dependencies import get_active_project_id

# In the list endpoint signature, add:
    active_project_id: Annotated[str | None, Depends(get_active_project_id)] = None,
    all: Annotated[bool, Query(description="Return all projects")] = False,

# In the query building, add:
    if active_project_id and not all:
        query = query.where(Document.project_id == active_project_id)
```

**Step 4: Scope the conversations list endpoint**

In `packages/backend/api/routes/conversations.py`, update `list_conversations`:

```python
from api.dependencies import get_active_project_id

@router.get("", response_model=ConversationListResponse)
async def list_conversations(
    db: Annotated[AsyncSession, Depends(get_db)],
    active_project_id: Annotated[str | None, Depends(get_active_project_id)] = None,
    all: Annotated[bool, Query(description="Return all projects")] = False,
) -> ConversationListResponse:
    """List all saved conversations, most recent first."""
    query = (
        select(
            Conversation,
            func.count(ConversationMessage.id).label("message_count"),
        )
        .outerjoin(ConversationMessage)
        .group_by(Conversation.id)
        .order_by(Conversation.updated_at.desc())
    )

    # Project scoping
    if active_project_id and not all:
        query = query.where(Conversation.project_id == active_project_id)

    # ... rest of the function unchanged
```

Also update `create_conversation` to accept and store `project_id`:

```python
class ConversationCreate(BaseModel):
    """Request to create a new conversation."""
    title: str | None = None
    messages: list[MessageCreate] = []
    compressed_memory: str | None = None
    project_id: str | None = None
```

In the handler:

```python
    conv = Conversation(
        title=title,
        compressed_memory=data.compressed_memory,
        project_id=data.project_id,
    )
```

**Step 5: Scope global search**

In `packages/backend/api/routes/search.py`, update `global_search` (line 498):

```python
from api.dependencies import get_active_project_id

@router.get("/global", response_model=GlobalSearchResponse)
async def global_search(
    db: Annotated[AsyncSession, Depends(get_db)],
    q: Annotated[str, Query(min_length=1, description="Search query")],
    active_project_id: Annotated[str | None, Depends(get_active_project_id)] = None,
    all: Annotated[bool, Query(description="Search all projects")] = False,
    limit: Annotated[int, Query(ge=1, le=50, description="Maximum results")] = 20,
    semantic: Annotated[bool, Query(description="Include semantic search results")] = True,
    save_history: Annotated[bool, Query(description="Save to search history")] = True,
) -> GlobalSearchResponse:
```

Then add project filtering to each sub-query. For recordings search (line 528):

```python
    recording_query = select(Recording).where(Recording.title.ilike(f"%{q}%"))
    if active_project_id and not all:
        recording_query = recording_query.where(Recording.project_id == active_project_id)
    recording_query = recording_query.order_by(Recording.created_at.desc()).limit(keyword_limit)
```

Apply the same pattern to segments (filter via `Recording.project_id` join), document chunks (filter via `Document.project_id` join), notes (filter via parent's project_id), and conversations (filter via `Conversation.project_id`).

For semantic search, pass the `active_project_id` to `_semantic_search()` and `_semantic_search_documents()` and add a WHERE clause on the join.

**Step 6: Add bulk move endpoint**

In `packages/backend/api/routes/projects.py`:

```python
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
    # Verify project exists
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    moved = 0

    # Move recordings
    for rec_id in data.recording_ids:
        rec_result = await db.execute(select(Recording).where(Recording.id == rec_id))
        rec = rec_result.scalar_one_or_none()
        if rec and rec.project_id != project_id:
            # Move file to project folder
            if rec.file_path:
                try:
                    new_path = await storage_service.move_to_project(
                        rec.file_path, project.name, rec.storage_location_id
                    )
                    rec.file_path = str(new_path)
                except Exception as e:
                    logger.warning(f"Could not move file for recording {rec_id}: {e}")
            rec.project_id = project_id
            moved += 1

    # Move documents
    for doc_id in data.document_ids:
        doc_result = await db.execute(select(Document).where(Document.id == doc_id))
        doc = doc_result.scalar_one_or_none()
        if doc and doc.project_id != project_id:
            if doc.file_path:
                try:
                    new_path = await storage_service.move_to_project(
                        doc.file_path, project.name, doc.storage_location_id
                    )
                    doc.file_path = str(new_path)
                except Exception as e:
                    logger.warning(f"Could not move file for document {doc_id}: {e}")
            doc.project_id = project_id
            moved += 1

    await db.commit()
    await broadcast("projects", "updated", project_id)
    await broadcast("recordings", "updated")
    await broadcast("documents", "updated")
    return MessageResponse(message=f"Moved {moved} items to project", id=project_id)
```

**Step 7: Commit**

```bash
git add packages/backend/api/dependencies.py packages/backend/api/routes/recordings.py packages/backend/api/routes/documents.py packages/backend/api/routes/conversations.py packages/backend/api/routes/search.py packages/backend/api/routes/projects.py
git commit -m "feat: add project scoping to all list/search endpoints via X-Active-Project header"
```

---

## Phase 3: Frontend — Project Switcher & Scoped Views

### Task 5: Active Project Store

**Files:**
- Create: `packages/frontend/src/stores/projectStore.ts`
- Modify: `packages/frontend/src/lib/queryKeys.ts`

**Step 1: Create the active project Zustand store**

Create `packages/frontend/src/stores/projectStore.ts`:

```typescript
import { create } from 'zustand';

interface ActiveProject {
  id: string;
  name: string;
  color?: string | null;
  icon?: string | null;
}

interface ProjectStore {
  activeProject: ActiveProject | null; // null = "All Projects" mode
  setActiveProject: (project: ActiveProject | null) => void;
}

export const useProjectStore = create<ProjectStore>((set) => ({
  activeProject: null,
  setActiveProject: (project) => {
    set({ activeProject: project });
    // Persist to localStorage
    if (project) {
      localStorage.setItem('active-project', JSON.stringify(project));
    } else {
      localStorage.removeItem('active-project');
    }
  },
}));

// Initialize from localStorage on module load
const stored = localStorage.getItem('active-project');
if (stored) {
  try {
    const parsed = JSON.parse(stored);
    useProjectStore.setState({ activeProject: parsed });
  } catch {
    localStorage.removeItem('active-project');
  }
}
```

**Step 2: Update queryKeys with active project awareness**

In `packages/frontend/src/lib/queryKeys.ts`, update the query key factories to include active project context so caches are scoped:

```typescript
// Add at the top:
import { useProjectStore } from '@/stores/projectStore';

// Helper to get current active project ID for query keys
function activeProjectScope(): string | undefined {
  return useProjectStore.getState().activeProject?.id;
}

// Update the recordings and documents keys:
export const queryKeys = {
  recordings: {
    all: ['recordings'] as const,
    list: (filters?: RecordingFilters) => ['recordings', 'list', { ...filters, _scope: activeProjectScope() }] as const,
    detail: (id: string) => ['recordings', 'detail', id] as const,
  },
  documents: {
    all: ['documents'] as const,
    list: (filters?: DocumentFilters) => ['documents', 'list', { ...filters, _scope: activeProjectScope() }] as const,
    detail: (id: string) => ['documents', 'detail', id] as const,
  },
  conversations: {
    all: ['conversations'] as const,
    list: () => ['conversations', 'list', { _scope: activeProjectScope() }] as const,
    detail: (id: string) => ['conversations', 'detail', id] as const,
  },
  // ... rest unchanged
};
```

**Step 3: Commit**

```bash
git add packages/frontend/src/stores/projectStore.ts packages/frontend/src/lib/queryKeys.ts
git commit -m "feat: add active project store and scoped query keys"
```

---

### Task 6: API Client — X-Active-Project Header

**Files:**
- Modify: `packages/frontend/src/lib/api.ts`

**Step 1: Inject the X-Active-Project header into all requests**

In `packages/frontend/src/lib/api.ts`, find the `request<T>()` method. Add the active project header injection. Look for where headers are constructed (inside the `request` method):

```typescript
import { useProjectStore } from '@/stores/projectStore';

// Inside the request<T>() method, where headers are built:
    const activeProject = useProjectStore.getState().activeProject;
    if (activeProject) {
      headers['X-Active-Project'] = activeProject.id;
    }
```

This ensures every API call automatically includes the active project scope.

**Step 2: Add new API methods for project workspace features**

In the `api.projects` namespace of the ApiClient, add:

```typescript
  // In api.projects:
  getActiveProject: () => this.request<{ active_project_id: string | null }>('/api/projects/active/current'),
  setActiveProject: (projectId: string | null) =>
    this.request<{ active_project_id: string | null }>('/api/projects/active/current', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ project_id: projectId }),
    }),
  archive: (id: string) =>
    this.request<{ message: string; id: string }>(`/api/projects/${id}/archive`, { method: 'PATCH' }),
  unarchive: (id: string) =>
    this.request<{ message: string; id: string }>(`/api/projects/${id}/unarchive`, { method: 'PATCH' }),
  getSections: (id: string) =>
    this.request<{ recordings: number; documents: number; notes: number }>(`/api/projects/${id}/sections`),
  moveItems: (id: string, data: { recording_ids: string[]; document_ids: string[] }) =>
    this.request<{ message: string; id: string }>(`/api/projects/${id}/move-items`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    }),
```

**Step 3: Commit**

```bash
git add packages/frontend/src/lib/api.ts
git commit -m "feat: inject X-Active-Project header and add project workspace API methods"
```

---

### Task 7: Project Selector Component

**Files:**
- Create: `packages/frontend/src/components/layout/ProjectSelector.tsx`
- Modify: `packages/frontend/src/components/layout/Sidebar.tsx`

**Step 1: Create the ProjectSelector component**

Create `packages/frontend/src/components/layout/ProjectSelector.tsx`:

```tsx
import { useState, useRef, useEffect } from 'react';
import { useProjectStore } from '@/stores/projectStore';
import { api } from '@/lib/api';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { queryKeys } from '@/lib/queryKeys';

interface ProjectSelectorProps {
  collapsed: boolean;
}

export function ProjectSelector({ collapsed }: ProjectSelectorProps) {
  const { activeProject, setActiveProject } = useProjectStore();
  const [isOpen, setIsOpen] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);
  const queryClient = useQueryClient();

  const { data: projectsData } = useQuery({
    queryKey: queryKeys.projects.list({ includeArchived: false }),
    queryFn: () => api.projects.list(),
  });

  const { data: archivedData } = useQuery({
    queryKey: queryKeys.projects.list({ includeArchived: true }),
    queryFn: () => api.projects.list({ include_archived: true }),
  });

  // Close dropdown when clicking outside
  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setIsOpen(false);
      }
    }
    if (isOpen) document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [isOpen]);

  const handleSelect = async (project: { id: string; name: string; color?: string | null; icon?: string | null } | null) => {
    setActiveProject(project);
    setIsOpen(false);
    // Persist to backend
    await api.projects.setActiveProject(project?.id ?? null);
    // Invalidate all scoped queries so they refetch with new scope
    queryClient.invalidateQueries({ queryKey: queryKeys.recordings.all });
    queryClient.invalidateQueries({ queryKey: queryKeys.documents.all });
    queryClient.invalidateQueries({ queryKey: queryKeys.conversations.all });
    queryClient.invalidateQueries({ queryKey: queryKeys.search.history });
    queryClient.invalidateQueries({ queryKey: queryKeys.dashboard.stats });
  };

  const projects = projectsData?.items ?? [];
  const archived = (archivedData?.items ?? []).filter(p => p.is_archived);

  return (
    <div className="relative" ref={dropdownRef}>
      <button
        onClick={() => setIsOpen(!isOpen)}
        className="w-full flex items-center gap-2 px-3 py-2 rounded-lg text-sm font-medium
          bg-zinc-100 dark:bg-zinc-800 hover:bg-zinc-200 dark:hover:bg-zinc-700
          transition-colors text-left"
        title={activeProject?.name ?? 'All Projects'}
      >
        {activeProject?.color && (
          <span
            className="w-3 h-3 rounded-full flex-shrink-0"
            style={{ backgroundColor: activeProject.color }}
          />
        )}
        {!activeProject && (
          <svg className="w-4 h-4 flex-shrink-0 text-zinc-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
            <path strokeLinecap="round" strokeLinejoin="round" d="M4 6h16M4 12h16M4 18h16" />
          </svg>
        )}
        {!collapsed && (
          <>
            <span className="truncate flex-1">
              {activeProject?.name ?? 'All Projects'}
            </span>
            <svg className={`w-4 h-4 flex-shrink-0 transition-transform ${isOpen ? 'rotate-180' : ''}`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
              <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
            </svg>
          </>
        )}
      </button>

      {isOpen && (
        <div className="absolute top-full left-0 right-0 mt-1 z-50 bg-white dark:bg-zinc-900
          border border-zinc-200 dark:border-zinc-700 rounded-lg shadow-lg overflow-hidden min-w-[200px]">
          {/* All Projects option */}
          <button
            onClick={() => handleSelect(null)}
            className={`w-full flex items-center gap-2 px-3 py-2 text-sm hover:bg-zinc-100 dark:hover:bg-zinc-800
              ${!activeProject ? 'bg-zinc-100 dark:bg-zinc-800 font-medium' : ''}`}
          >
            <svg className="w-4 h-4 text-zinc-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
              <path strokeLinecap="round" strokeLinejoin="round" d="M4 6h16M4 12h16M4 18h16" />
            </svg>
            All Projects
          </button>

          {projects.length > 0 && (
            <div className="border-t border-zinc-200 dark:border-zinc-700" />
          )}

          {/* Active projects */}
          {projects.filter(p => !p.is_archived).map((project) => (
            <button
              key={project.id}
              onClick={() => handleSelect({
                id: project.id,
                name: project.name,
                color: project.color,
                icon: project.icon,
              })}
              className={`w-full flex items-center gap-2 px-3 py-2 text-sm hover:bg-zinc-100 dark:hover:bg-zinc-800
                ${activeProject?.id === project.id ? 'bg-zinc-100 dark:bg-zinc-800 font-medium' : ''}`}
            >
              {project.color ? (
                <span className="w-3 h-3 rounded-full flex-shrink-0" style={{ backgroundColor: project.color }} />
              ) : (
                <span className="w-3 h-3 rounded-full flex-shrink-0 bg-zinc-400" />
              )}
              <span className="truncate">{project.name}</span>
            </button>
          ))}

          {/* Archived section */}
          {archived.length > 0 && (
            <>
              <div className="border-t border-zinc-200 dark:border-zinc-700" />
              <div className="px-3 py-1.5 text-xs text-zinc-500 uppercase tracking-wider">Archived</div>
              {archived.map((project) => (
                <button
                  key={project.id}
                  onClick={() => handleSelect({
                    id: project.id,
                    name: project.name,
                    color: project.color,
                    icon: project.icon,
                  })}
                  className="w-full flex items-center gap-2 px-3 py-2 text-sm text-zinc-500 hover:bg-zinc-100 dark:hover:bg-zinc-800"
                >
                  <span className="w-3 h-3 rounded-full flex-shrink-0 bg-zinc-300 dark:bg-zinc-600" />
                  <span className="truncate">{project.name}</span>
                </button>
              ))}
            </>
          )}
        </div>
      )}
    </div>
  );
}
```

**Step 2: Add ProjectSelector to Sidebar**

In `packages/frontend/src/components/layout/Sidebar.tsx`, import and render the ProjectSelector above the nav items. Add the import:

```typescript
import { ProjectSelector } from './ProjectSelector';
```

Then render it in the sidebar, after the logo/title area and before the nav items list. Find where `NAV_ITEMS.map(...)` is rendered and add above it:

```tsx
{/* Project Selector */}
<div className="px-2 mb-2">
  <ProjectSelector collapsed={collapsed} />
</div>
```

**Step 3: Commit**

```bash
git add packages/frontend/src/components/layout/ProjectSelector.tsx packages/frontend/src/components/layout/Sidebar.tsx
git commit -m "feat: add project selector dropdown to sidebar"
```

---

### Task 8: Project Home Page

**Files:**
- Create: `packages/frontend/src/pages/projects/ProjectHomePage.tsx`
- Modify: `packages/frontend/src/app/App.tsx`

**Step 1: Create the ProjectHomePage component**

Create `packages/frontend/src/pages/projects/ProjectHomePage.tsx`:

```tsx
import { useQuery } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { queryKeys } from '@/lib/queryKeys';
import { useProjectStore } from '@/stores/projectStore';

interface ProjectHomePageProps {
  projectId: string;
  onNavigateToRecordings: () => void;
  onNavigateToDocuments: () => void;
  onViewTranscript: (recordingId: string) => void;
  onViewDocument: (documentId: string) => void;
}

export function ProjectHomePage({
  projectId,
  onNavigateToRecordings,
  onNavigateToDocuments,
  onViewTranscript,
  onViewDocument,
}: ProjectHomePageProps) {
  const { activeProject } = useProjectStore();

  const { data: project } = useQuery({
    queryKey: queryKeys.projects.detail(projectId),
    queryFn: () => api.projects.get(projectId),
  });

  const { data: sections } = useQuery({
    queryKey: ['projects', projectId, 'sections'],
    queryFn: () => api.projects.getSections(projectId),
  });

  const { data: recordings } = useQuery({
    queryKey: queryKeys.recordings.list({ projectId, sortBy: 'created_at', sortOrder: 'desc' }),
    queryFn: () => api.recordings.list({ projectId, sortBy: 'created_at', sortOrder: 'desc', pageSize: 4 }),
  });

  const { data: documents } = useQuery({
    queryKey: queryKeys.documents.list({ projectId, sortBy: 'created_at', sortOrder: 'desc' }),
    queryFn: () => api.documents.list({ project_id: projectId, sort_by: 'created_at', sort_order: 'desc', page_size: 4 }),
  });

  if (!project) return null;

  return (
    <div className="max-w-5xl mx-auto p-6 space-y-6">
      {/* Project Header */}
      <div>
        <div className="flex items-center gap-3 mb-2">
          {project.color && (
            <span className="w-4 h-4 rounded-full" style={{ backgroundColor: project.color }} />
          )}
          <h1 className="text-2xl font-bold text-zinc-900 dark:text-zinc-100">
            {project.name}
          </h1>
        </div>
        {project.description && (
          <p className="text-zinc-600 dark:text-zinc-400">{project.description}</p>
        )}
      </div>

      {/* Type Section Cards */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {/* Recordings Section */}
        {(sections?.recordings ?? 0) > 0 && (
          <div className="bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700 rounded-lg p-4">
            <div className="flex items-center justify-between mb-3">
              <h2 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">
                Recordings ({sections?.recordings})
              </h2>
              <button
                onClick={onNavigateToRecordings}
                className="text-xs text-blue-600 dark:text-blue-400 hover:underline"
              >
                View all
              </button>
            </div>
            <div className="space-y-2">
              {recordings?.items?.slice(0, 4).map((rec) => (
                <button
                  key={rec.id}
                  onClick={() => onViewTranscript(rec.id)}
                  className="w-full text-left px-2 py-1.5 rounded text-sm hover:bg-zinc-100 dark:hover:bg-zinc-800 truncate text-zinc-700 dark:text-zinc-300"
                >
                  {rec.title}
                </button>
              ))}
            </div>
          </div>
        )}

        {/* Documents Section */}
        {(sections?.documents ?? 0) > 0 && (
          <div className="bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700 rounded-lg p-4">
            <div className="flex items-center justify-between mb-3">
              <h2 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">
                Documents ({sections?.documents})
              </h2>
              <button
                onClick={onNavigateToDocuments}
                className="text-xs text-blue-600 dark:text-blue-400 hover:underline"
              >
                View all
              </button>
            </div>
            <div className="space-y-2">
              {documents?.items?.slice(0, 4).map((doc) => (
                <button
                  key={doc.id}
                  onClick={() => onViewDocument(doc.id)}
                  className="w-full text-left px-2 py-1.5 rounded text-sm hover:bg-zinc-100 dark:hover:bg-zinc-800 truncate text-zinc-700 dark:text-zinc-300"
                >
                  {doc.title}
                </button>
              ))}
            </div>
          </div>
        )}

        {/* Notes Section */}
        {(sections?.notes ?? 0) > 0 && (
          <div className="bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700 rounded-lg p-4">
            <h2 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100 mb-3">
              Notes ({sections?.notes})
            </h2>
            <p className="text-xs text-zinc-500">Notes are accessible from their parent recordings and documents.</p>
          </div>
        )}
      </div>

      {/* Empty State */}
      {(sections?.recordings ?? 0) === 0 && (sections?.documents ?? 0) === 0 && (
        <div className="text-center py-12 text-zinc-500">
          <p className="text-lg mb-2">This project is empty</p>
          <p className="text-sm">Upload recordings or documents, or move existing content into this project.</p>
        </div>
      )}
    </div>
  );
}
```

**Step 2: Wire ProjectHomePage into App.tsx**

In `packages/frontend/src/app/App.tsx`, when the active project changes via the sidebar selector, navigating to the project detail page should show the ProjectHomePage. The existing `project-detail` navigation type already maps to `ProjectDetailPage`. We can either replace `ProjectDetailPage` with `ProjectHomePage` when it's the active project, or add the project home as the landing view when switching projects.

The simplest approach: when the user selects a project from the selector, navigate to `{ type: 'project-detail', projectId }`. The `ProjectDetailPage` already handles this route — we just need to ensure it shows the new section cards layout. This is handled by modifying the existing `ProjectDetailPage` or rendering `ProjectHomePage` instead.

In the switch/case in App.tsx that renders pages (find the `navigation.type === 'project-detail'` case):

```tsx
import { ProjectHomePage } from '@/pages/projects/ProjectHomePage';

// In the render switch:
case 'project-detail':
  return (
    <ProjectHomePage
      projectId={navigation.projectId}
      onNavigateToRecordings={handleNavigateToRecordings}
      onNavigateToDocuments={handleNavigateToDocuments}
      onViewTranscript={handleViewTranscript}
      onViewDocument={handleViewDocument}
    />
  );
```

**Step 3: Commit**

```bash
git add packages/frontend/src/pages/projects/ProjectHomePage.tsx packages/frontend/src/app/App.tsx
git commit -m "feat: add project home page with auto type-section cards"
```

---

### Task 9: Scoped Breadcrumbs

**Files:**
- Modify: `packages/frontend/src/pages/recordings/RecordingsPage.tsx`
- Modify: `packages/frontend/src/pages/documents/DocumentsPage.tsx`
- Modify: `packages/frontend/src/pages/chats/ChatsPage.tsx`

**Step 1: Add scope indicator to RecordingsPage**

In `packages/frontend/src/pages/recordings/RecordingsPage.tsx`, add a scope badge below the page header:

```tsx
import { useProjectStore } from '@/stores/projectStore';

// Inside the component:
const { activeProject } = useProjectStore();

// In the JSX, after the page title:
{activeProject && (
  <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-xs bg-zinc-100 dark:bg-zinc-800 text-zinc-600 dark:text-zinc-400">
    {activeProject.color && (
      <span className="w-2 h-2 rounded-full" style={{ backgroundColor: activeProject.color }} />
    )}
    {activeProject.name}
  </span>
)}
```

**Step 2: Same for DocumentsPage and ChatsPage**

Apply the identical pattern to `DocumentsPage.tsx` and `ChatsPage.tsx`.

**Step 3: Commit**

```bash
git add packages/frontend/src/pages/recordings/RecordingsPage.tsx packages/frontend/src/pages/documents/DocumentsPage.tsx packages/frontend/src/pages/chats/ChatsPage.tsx
git commit -m "feat: add project scope badges to list pages"
```

---

## Phase 4: Max Context Scoping

### Task 10: Project-Aware System Prompt

**Files:**
- Modify: `packages/backend/api/routes/ai.py`

**Step 1: Add project context to the multi-chat endpoint**

In `packages/backend/api/routes/ai.py`, update the `chat_multi_stream` function (line 753). Add the project scope dependency and inject project context into the system prompt:

```python
from api.dependencies import get_active_project_id

@router.post("/chat/multi", response_class=StreamingResponse)
async def chat_multi_stream(
    request: MultiChatRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    active_project_id: Annotated[str | None, Depends(get_active_project_id)] = None,
) -> StreamingResponse:
```

After the model is loaded and before building context (around line 769), add project context injection:

```python
    # Project context injection
    project_context_preamble = ""
    if active_project_id:
        project_result = await db.execute(
            select(Project).where(Project.id == active_project_id)
        )
        project = project_result.scalar_one_or_none()
        if project:
            # Count content in project
            rec_count = await db.scalar(
                select(func.count(Recording.id)).where(Recording.project_id == active_project_id)
            ) or 0
            doc_count = await db.scalar(
                select(func.count(Document.id)).where(Document.project_id == active_project_id)
            ) or 0

            project_context_preamble = (
                f"\n\nYou are currently working within the project '{project.name}'. "
                f"This project contains {rec_count} recording(s) and {doc_count} document(s). "
                "When answering questions, prioritize content from this project. "
                "The user may ask you to search beyond this project — do so when explicitly requested."
            )
```

Then prepend `project_context_preamble` to the system prompt when building messages. Find where `system_prompt` is constructed and append the preamble:

```python
    system_prompt = MAX_SYSTEM_PROMPT + project_context_preamble
    # (or MAX_SYSTEM_PROMPT_GENERAL + project_context_preamble if general_mode)
```

**Step 2: Auto semantic search within project**

After the existing manual attachment context building (around line 825), add automatic project-scoped semantic search when no manual attachments are provided:

```python
    # Auto-inject project context via semantic search (when no manual attachments)
    if active_project_id and not request.recording_ids and not request.document_ids:
        try:
            from services.embedding import embedding_service, bytes_to_embedding
            if embedding_service.is_available():
                query_embedding = await embedding_service.embed_query(request.message)

                # Search segments in project recordings
                seg_query = (
                    select(SegmentEmbedding, Segment.text, Recording.title)
                    .join(Segment, SegmentEmbedding.segment_id == Segment.id)
                    .join(Transcript, Segment.transcript_id == Transcript.id)
                    .join(Recording, Transcript.recording_id == Recording.id)
                    .where(Recording.project_id == active_project_id)
                )
                seg_result = await db.execute(seg_query)
                seg_rows = seg_result.all()

                # Score and rank
                import math
                scored_segments = []
                for seg_emb, seg_text, rec_title in seg_rows:
                    emb = bytes_to_embedding(seg_emb.embedding)
                    dot = sum(a * b for a, b in zip(query_embedding, emb))
                    norm_a = math.sqrt(sum(a * a for a in query_embedding))
                    norm_b = math.sqrt(sum(b * b for b in emb))
                    score = dot / (norm_a * norm_b) if norm_a and norm_b else 0
                    if score > 0.35:
                        scored_segments.append((score, seg_text, rec_title))

                scored_segments.sort(key=lambda x: x[0], reverse=True)

                # Inject top matches as context
                if scored_segments:
                    auto_context_parts = []
                    for score, text, title in scored_segments[:5]:
                        auto_context_parts.append(f"[From '{title}']: {text}")
                    project_auto_context = "\n\n=== Relevant content from this project ===\n" + "\n\n".join(auto_context_parts)
                    context_parts.append(project_auto_context)
        except Exception as e:
            logger.warning("Project auto-context failed: %s", e)
```

**Step 3: Commit**

```bash
git add packages/backend/api/routes/ai.py
git commit -m "feat: add project-aware system prompt and auto semantic search for Max"
```

---

### Task 11: Chat Panel Scope Badge

**Files:**
- Modify: `packages/frontend/src/components/ai/ChatPanel.tsx`
- Modify: `packages/frontend/src/components/ai/ChatHeader.tsx`

**Step 1: Add scope badge to ChatHeader**

In `packages/frontend/src/components/ai/ChatHeader.tsx`, add the active project scope indicator:

```tsx
import { useProjectStore } from '@/stores/projectStore';

// Inside the component:
const { activeProject } = useProjectStore();

// In the JSX, below the header title area:
<div className="flex items-center gap-1.5 text-xs text-zinc-500">
  <span>Scoped to:</span>
  {activeProject ? (
    <span className="inline-flex items-center gap-1">
      {activeProject.color && (
        <span className="w-2 h-2 rounded-full" style={{ backgroundColor: activeProject.color }} />
      )}
      {activeProject.name}
    </span>
  ) : (
    <span>All Projects</span>
  )}
</div>
```

**Step 2: Pass project_id when saving conversations**

In `packages/frontend/src/components/ai/ChatPanel.tsx`, find where conversations are saved (the save handler that calls `api.conversations.create`). Add the active project ID:

```tsx
import { useProjectStore } from '@/stores/projectStore';

// Inside ChatPanel:
const { activeProject } = useProjectStore();

// When saving a conversation:
const result = await api.conversations.create({
  title: saveTitle || undefined,
  messages: messages.map(m => ({ role: m.role, content: m.content })),
  compressed_memory: compressedMemory,
  project_id: activeProject?.id ?? undefined,
});
```

**Step 3: Commit**

```bash
git add packages/frontend/src/components/ai/ChatPanel.tsx packages/frontend/src/components/ai/ChatHeader.tsx
git commit -m "feat: add project scope badge to chat panel and scope saved conversations"
```

---

## Phase 5: Polish

### Task 12: Integration Wiring and Edge Cases

**Files:**
- Modify: `packages/frontend/src/app/App.tsx` (project switch triggers navigation)
- Modify: `packages/frontend/src/hooks/useConversations.ts` (scope-aware)

**Step 1: Project switching triggers query invalidation**

In `App.tsx`, subscribe to the project store and invalidate queries when the active project changes. Add a useEffect:

```tsx
import { useProjectStore } from '@/stores/projectStore';

// Inside AppContent:
const activeProject = useProjectStore(state => state.activeProject);

useEffect(() => {
  // When active project changes, invalidate all scoped queries
  queryClient.invalidateQueries({ queryKey: queryKeys.recordings.all });
  queryClient.invalidateQueries({ queryKey: queryKeys.documents.all });
  queryClient.invalidateQueries({ queryKey: queryKeys.conversations.all });
  queryClient.invalidateQueries({ queryKey: queryKeys.dashboard.stats });
}, [activeProject?.id]);
```

**Step 2: Clear chat when switching projects**

When the active project changes, clear the current chat to prevent context bleed:

```tsx
useEffect(() => {
  // Clear chat state when project changes
  setChatMessages([]);
  setChatAttachments([]);
  setChatCompressedMemory(null);
  setIsChatOpen(false);
}, [activeProject?.id]);
```

**Step 3: Navigate to project home on selection**

When the user selects a project from the ProjectSelector, navigate to the project detail view:

In `ProjectSelector.tsx`, accept an `onNavigate` prop:

```tsx
interface ProjectSelectorProps {
  collapsed: boolean;
  onNavigate?: (projectId: string | null) => void;
}
```

In `handleSelect`, call it:

```tsx
    if (project) {
      onNavigate?.(project.id);
    }
```

Wire this in `Sidebar.tsx`:

```tsx
<ProjectSelector
  collapsed={collapsed}
  onNavigate={(projectId) => {
    if (projectId) {
      onNavigate(`project-detail-${projectId}`);
      // Or use the existing navigation callback pattern
    }
  }}
/>
```

**Step 4: Commit**

```bash
git add packages/frontend/src/app/App.tsx packages/frontend/src/components/layout/ProjectSelector.tsx packages/frontend/src/components/layout/Sidebar.tsx packages/frontend/src/hooks/useConversations.ts
git commit -m "feat: wire project switching with query invalidation and context clearing"
```

---

## Summary

| Phase | Tasks | What ships |
|-------|-------|-----------|
| **Phase 1** | Tasks 1-3 | DB migration, model updates, archive/sections/active-project endpoints |
| **Phase 2** | Task 4 | All list/search endpoints scoped via X-Active-Project header |
| **Phase 3** | Tasks 5-9 | Project selector, home page, scoped views, breadcrumbs |
| **Phase 4** | Tasks 10-11 | Max project-aware prompts, auto semantic search, chat scope badge |
| **Phase 5** | Task 12 | Query invalidation on project switch, context clearing, navigation wiring |

Each phase is independently deployable. Phases 1-3 give full organizational power. Phase 4 adds AI magic. Phase 5 polishes the experience.

# Projects & Workspaces Design

**Date:** 2026-03-17
**Status:** Approved

## Overview

Implement a Projects system that creates isolated workspaces within Verbatim. Each project scopes the Verbatim Assistant's (Max) context to only the files, transcripts, and data within that workspace. Users can create, rename, and archive projects, and switch between them without context bleed.

Each project auto-generates type-based sections (Recordings, Documents, Notes) based on what content exists within it — no manual section management required.

## Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Content sections | Automatic type-based bins | Sections appear/disappear based on content types present. Zero friction, no user configuration. |
| AI boundary | Soft boundary with default scope | Max defaults to the active project's content but can search across projects when the user explicitly asks. |
| Project switching | Global project selector | Persistent switcher in the sidebar. Entire app scopes to the active project — recordings, documents, search, and Max. |
| Archiving | Soft archive | Archived projects hidden from sidebar but content remains searchable. Max can reference archived content when explicitly asked. |
| Content types | Recordings, Documents, Notes | Design around the three existing content types. Extensible if new types are added later. |

## Data Model

### Project table (evolve existing)

New columns on the existing `Project` model:

- `is_archived: bool` — default false
- `sort_order: int` — sidebar ordering
- `icon: str` — optional, visual identity
- `color: str` — optional, accent color

The `ProjectType` relationship is deprecated. Projects become freeform workspaces rather than template-driven containers.

### Active project

Stored as a `Setting` entry (`active_project_id`). Persisted across app restarts via the existing settings system. A null value means "All Projects" mode (unscoped).

### Content ownership

Every content entity gets a required `project_id` foreign key:

- `Recording` — already has a project association; make it a proper required FK
- `Document` — add `project_id` (new column)
- `Note` — add `project_id` (new column)
- `Conversation` — add `project_id` (chat history scoped to project)

### Type sections

Virtual — no database table. The frontend queries content type counts for a project and renders sections dynamically. A project with only recordings shows one section; add a PDF and "Documents" appears automatically.

### No folder entity

The type bins are the organizational structure. No premature folder abstraction.

## API Scoping

### Header-based scoping

The frontend sets an `X-Active-Project` header on every API request when a project is active. FastAPI middleware reads this header and injects the project scope into the request context, keeping scoping logic centralized.

```
GET /api/recordings          → recordings in active project
GET /api/recordings?all=true → recordings across all projects
GET /api/documents           → scoped
GET /api/search              → scoped (with option to search globally)
GET /api/conversations       → scoped
```

### New endpoints

- `PATCH /api/projects/:id/archive` — set `is_archived = true`
- `PATCH /api/projects/:id/unarchive` — reverse
- `PUT /api/settings/active_project` — set the active project
- `GET /api/projects/:id/sections` — content type counts (e.g., `{ recordings: 12, documents: 3, notes: 7 }`)
- `POST /api/projects/:id/move-items` — bulk move with `{ recording_ids: [], document_ids: [], note_ids: [] }`

### Content movement

- `PATCH /api/recordings/:id` with `{ project_id: "new-id" }` — move between projects
- Same pattern for documents and notes
- Bulk move via the dedicated endpoint above

### Key rule

No endpoint returns cross-project results by default when a project is active. The `?all=true` escape hatch exists for the "All Projects" view and for Max's soft-boundary searches.

## Max Context Scoping

### Current behavior

The `/api/ai/chat/multi` endpoint takes explicit `recording_ids[]` and `document_ids[]`. The `ContextManager` allocates token budget across system prompt, history, memory, and attached content.

### New behavior

#### Default context injection

When a project is active and the user sends a message, Max automatically has access to the project's content without manual file attachment:

1. **Semantic search within project** — The user's message is embedded and matched against `SegmentEmbedding` and `DocumentEmbedding` rows belonging to the active project's content. Top-k relevant chunks are injected as context.

2. **Project-aware system prompt** — Max's system prompt gains a preamble:
   > "You are currently working within the project '[Project Name]'. This project contains [N] recordings, [N] documents, and [N] notes. When answering questions, prioritize content from this project. The user may ask you to search beyond this project — do so when explicitly requested."

#### Soft boundary implementation

- **Default:** semantic search scoped to `WHERE project_id = active_project`
- **User says "search all my projects" or "check my other projects":** intent detected via keyword matching (like the existing help-intent detection) → search widens to all non-archived projects
- **User says "also check the [Project Name] project":** search targets that specific project
- **Cross-project citations:** Max always identifies when referencing content outside the active project: *"From your 'Client Research' project: ..."*

#### Manual attachment still works

Users can still explicitly attach specific recordings/documents via the `AttachmentPicker`. These override the automatic context and can cross project boundaries (consistent with the soft boundary model).

#### Context budget impact

The `ContextManager` gets a new budget consumer: `project_context` (auto-retrieved semantic matches), slotted between `history` and `context` in priority. If the user manually attaches content, that takes precedence and `project_context` shrinks accordingly.

## UI Changes

### Sidebar — Project Switcher

At the top of the sidebar, above the current navigation, add a project selector dropdown:

- Active project name + icon/color
- Click to expand: list of non-archived projects sorted by `sort_order`
- "All Projects" option at the top (unscoped mode)
- "Archived" section at the bottom (collapsed by default)
- "+ New Project" button at the bottom

When no project is active ("All Projects"), the app behaves like it does today.

### Project Home Page (`/projects/:id`)

When a project is selected, it gets an overview page showing:

- Project name, description (editable inline)
- Type sections as cards — one card per content type with items:
  - "Recordings (12)" — 3-4 most recent, click to go to scoped Recordings page
  - "Documents (3)" — same pattern
  - "Notes (7)" — same pattern
- Sections with zero items don't render (automatic type bins)
- Recent activity feed (latest transcriptions, edits, uploads)

### Scoped list pages

Existing Recordings, Documents, and Search pages work as they do now, filtered to the active project. Subtle breadcrumb or badge: `Recordings · Client Research Project`

### Max chat panel

- Small badge below chat header: `Scoped to: Client Research`
- Cross-project citations get distinct visual treatment (different background, project name label)
- "All Projects" mode shows: `Scoped to: All Projects`

## Migration & Default Behavior

### Existing users

1. **Create a "General" default project** — On first launch after the update, auto-create a system project called "General" (user can rename). All existing content gets assigned `project_id = general_project_id`.

2. **Start in "All Projects" mode** — Don't force users into the General project. The app starts unscoped so existing users see no change until they choose to create a project.

### Database migration (single Alembic migration)

1. Add `is_archived`, `sort_order`, `icon`, `color` columns to `Project`
2. Add `project_id` FK to `Document`, `Note`, `Conversation` tables (nullable initially)
3. Create the "General" default project
4. Backfill: `UPDATE documents SET project_id = :general_id WHERE project_id IS NULL` (same for notes, conversations)
5. Make `project_id` non-nullable after backfill
6. Add `active_project_id` setting entry (null = "All Projects")

### New users

First launch shows "All Projects" with an empty state prompt: *"Create your first project to organize your recordings, documents, and notes into focused workspaces."* Until they create one, everything lives in the implicit global space — identical to the current app behavior.

### Key principle

The feature is additive. Users who never create a project experience zero change. The moment they create one, the organizational power unlocks.

## Implementation Sequence

Each phase is independently shippable and testable.

### Phase 1: Data foundation

- Alembic migration: new columns, default "General" project, backfill
- Update SQLAlchemy models (Project, Document, Note, Conversation)
- `active_project_id` in settings
- New project CRUD endpoints (create, rename, archive/unarchive, reorder)

### Phase 2: API scoping

- `X-Active-Project` header middleware in FastAPI
- Scope all list endpoints (recordings, documents, notes, conversations, search)
- `?all=true` escape hatch for global queries
- `/api/projects/:id/sections` endpoint (content type counts)
- Bulk move endpoint

### Phase 3: Frontend — project switcher & scoped views

- Sidebar project selector component
- API client sends `X-Active-Project` header on every request
- Project home page with type-section cards
- Scoped breadcrumbs on list pages
- Settings to manage projects (rename, reorder, archive)

### Phase 4: Max context scoping

- Modify `/api/ai/chat/multi` to accept project scope from header
- Auto semantic search within project (new `project_context` budget consumer in ContextManager)
- Project-aware system prompt injection
- Soft boundary intent detection ("search all projects", "check [name]")
- Cross-project citation labeling in responses
- Scope badge in chat panel UI

### Phase 5: Polish

- "All Projects" default view for new/existing users
- Empty state prompts
- Project-scoped conversation history
- Archive/unarchive flow with UI feedback
- Migration testing across SQLite and PostgreSQL

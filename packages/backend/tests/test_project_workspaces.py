"""Tests for Project Workspaces feature (Phases 1-3).

Covers:
- Phase 1: Model fields, workspace columns, default project seeding
- Phase 2: X-Active-Project header scoping on recordings, documents, conversations, search
- Phase 3: Sections, archive/unarchive, active project, move-items endpoints
"""

from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from persistence.models import Project, Recording, Transcript, Conversation, Document, Setting


@pytest.fixture(autouse=True)
def mock_storage_location():
    """Bypass storage_locations table lookup in recordings/documents list.

    get_active_storage_location() uses its own session factory (not the
    test-injected one), so it hits a DB that doesn't have the table.
    Returning None is safe — it just skips the storage-location filter.
    """
    async def _return_none():
        return None

    with patch("api.routes.recordings.get_active_storage_location", new=_return_none), \
         patch("api.routes.documents.get_active_storage_location", new=_return_none):
        yield


# ── Helpers ──────────────────────────────────────────────────────────────

async def create_project(client: AsyncClient, name: str, **kwargs) -> dict:
    """Create a project and return the response data."""
    resp = await client.post("/api/projects", json={"name": name, **kwargs})
    assert resp.status_code == 201, f"Failed to create project: {resp.text}"
    return resp.json()


async def create_recording_in_project(db: AsyncSession, title: str, project_id: str | None) -> str:
    """Create a recording directly in the DB and return its ID."""
    import uuid
    rec_id = str(uuid.uuid4())
    rec = Recording(
        id=rec_id,
        title=title,
        project_id=project_id,
        status="completed",
        file_path="/tmp/test.wav",
        file_name="test.wav",
    )
    db.add(rec)
    await db.commit()
    return rec_id


async def create_document_in_project(db: AsyncSession, title: str, project_id: str | None) -> str:
    """Create a document directly in the DB and return its ID."""
    import uuid
    doc_id = str(uuid.uuid4())
    doc = Document(
        id=doc_id,
        title=title,
        filename="test.pdf",
        project_id=project_id,
        file_path="/tmp/test.pdf",
        mime_type="application/pdf",
        file_size_bytes=1024,
        status="completed",
    )
    db.add(doc)
    await db.commit()
    return doc_id


async def create_conversation_in_project(db: AsyncSession, title: str, project_id: str | None) -> str:
    """Create a conversation directly in the DB and return its ID."""
    import uuid
    conv_id = str(uuid.uuid4())
    conv = Conversation(id=conv_id, title=title, project_id=project_id)
    db.add(conv)
    await db.commit()
    return conv_id


# ══════════════════════════════════════════════════════════════════════════
# Phase 1: Model fields and workspace columns
# ══════════════════════════════════════════════════════════════════════════


class TestPhase1ModelFields:
    """Test that workspace columns exist on models."""

    @pytest.mark.asyncio
    async def test_project_has_workspace_fields(self, client: AsyncClient):
        """Project response includes is_archived, sort_order, icon, color."""
        data = await create_project(client, "Workspace Test")
        assert data["is_archived"] is False
        assert data["sort_order"] == 0
        assert "color" in data
        assert "icon" in data

    @pytest.mark.asyncio
    async def test_project_has_document_count(self, client: AsyncClient):
        """Project response includes document_count."""
        data = await create_project(client, "Doc Count Test")
        assert "document_count" in data
        assert data["document_count"] == 0

    @pytest.mark.asyncio
    async def test_update_project_color_and_icon(self, client: AsyncClient):
        """Set color and icon via PATCH update (not available on create)."""
        project = await create_project(client, "Full Fields", description="A test project")
        resp = await client.patch(
            f"/api/projects/{project['id']}",
            json={"color": "#00FF00", "icon": "folder"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["color"] == "#00FF00"
        assert data["icon"] == "folder"

    @pytest.mark.asyncio
    async def test_update_project_workspace_fields(self, client: AsyncClient):
        """Update workspace fields on a project."""
        project = await create_project(client, "Update Fields")
        resp = await client.patch(
            f"/api/projects/{project['id']}",
            json={"color": "#0000FF", "icon": "star", "sort_order": 5},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["color"] == "#0000FF"
        assert data["icon"] == "star"
        assert data["sort_order"] == 5

    @pytest.mark.asyncio
    async def test_conversation_project_id(self, client: AsyncClient, db_session: AsyncSession):
        """Conversations can be created with project_id."""
        project = await create_project(client, "Conv Project")
        conv_id = await create_conversation_in_project(db_session, "Test Conv", project["id"])

        resp = await client.get(f"/api/conversations/{conv_id}")
        assert resp.status_code == 200


# ══════════════════════════════════════════════════════════════════════════
# Phase 2: X-Active-Project header scoping
# ══════════════════════════════════════════════════════════════════════════


class TestPhase2HeaderScoping:
    """Test that list endpoints respect X-Active-Project header."""

    @pytest.mark.asyncio
    async def test_recordings_scoped_by_header(self, client: AsyncClient, db_session: AsyncSession):
        """Recordings list is filtered when X-Active-Project header is set."""
        p1 = await create_project(client, "Project A")
        p2 = await create_project(client, "Project B")

        await create_recording_in_project(db_session, "Rec in A", p1["id"])
        await create_recording_in_project(db_session, "Rec in B", p2["id"])
        await create_recording_in_project(db_session, "Rec unassigned", None)

        # Scoped to Project A
        resp = await client.get(
            "/api/recordings",
            headers={"X-Active-Project": p1["id"]},
        )
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert len(items) == 1
        assert items[0]["title"] == "Rec in A"

        # Scoped to Project B
        resp = await client.get(
            "/api/recordings",
            headers={"X-Active-Project": p2["id"]},
        )
        items = resp.json()["items"]
        assert len(items) == 1
        assert items[0]["title"] == "Rec in B"

    @pytest.mark.asyncio
    async def test_recordings_no_header_returns_all(self, client: AsyncClient, db_session: AsyncSession):
        """Without header, recordings list returns everything."""
        p1 = await create_project(client, "Project C")
        await create_recording_in_project(db_session, "Rec C1", p1["id"])
        await create_recording_in_project(db_session, "Rec C2", None)

        resp = await client.get("/api/recordings")
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert len(items) >= 2

    @pytest.mark.asyncio
    async def test_recordings_all_projects_escape_hatch(self, client: AsyncClient, db_session: AsyncSession):
        """The all=true param bypasses header scoping."""
        p1 = await create_project(client, "Project D")
        await create_recording_in_project(db_session, "Rec D1", p1["id"])
        await create_recording_in_project(db_session, "Rec D2", None)

        resp = await client.get(
            "/api/recordings?all=true",
            headers={"X-Active-Project": p1["id"]},
        )
        assert resp.status_code == 200
        items = resp.json()["items"]
        # Should return all recordings, not just project D's
        assert len(items) >= 2

    @pytest.mark.asyncio
    async def test_documents_scoped_by_header(self, client: AsyncClient, db_session: AsyncSession):
        """Documents list is filtered by X-Active-Project header."""
        p1 = await create_project(client, "Doc Project E")
        p2 = await create_project(client, "Doc Project F")

        await create_document_in_project(db_session, "Doc in E", p1["id"])
        await create_document_in_project(db_session, "Doc in F", p2["id"])

        resp = await client.get(
            "/api/documents",
            headers={"X-Active-Project": p1["id"]},
        )
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert len(items) == 1
        assert items[0]["title"] == "Doc in E"

    @pytest.mark.asyncio
    async def test_conversations_scoped_by_header(self, client: AsyncClient, db_session: AsyncSession):
        """Conversations list is filtered by X-Active-Project header."""
        p1 = await create_project(client, "Conv Project G")
        p2 = await create_project(client, "Conv Project H")

        await create_conversation_in_project(db_session, "Conv in G", p1["id"])
        await create_conversation_in_project(db_session, "Conv in H", p2["id"])

        resp = await client.get(
            "/api/conversations",
            headers={"X-Active-Project": p1["id"]},
        )
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert len(items) == 1
        assert items[0]["title"] == "Conv in G"

    @pytest.mark.asyncio
    async def test_conversations_create_with_project_id(self, client: AsyncClient):
        """Creating a conversation with project_id associates it correctly."""
        p1 = await create_project(client, "Conv Create Project")

        resp = await client.post(
            "/api/conversations",
            json={
                "title": "My Chat",
                "messages": [{"role": "user", "content": "Hello"}],
                "project_id": p1["id"],
            },
        )
        assert resp.status_code == 200
        conv_id = resp.json()["id"]

        # Verify it shows up when scoped
        list_resp = await client.get(
            "/api/conversations",
            headers={"X-Active-Project": p1["id"]},
        )
        conv_ids = [c["id"] for c in list_resp.json()["items"]]
        assert conv_id in conv_ids


# ══════════════════════════════════════════════════════════════════════════
# Phase 3: Sections, archive, active project, move-items
# ══════════════════════════════════════════════════════════════════════════


class TestPhase3Sections:
    """Test project sections endpoint."""

    @pytest.mark.asyncio
    async def test_sections_empty_project(self, client: AsyncClient):
        """Sections for empty project returns all zeros."""
        project = await create_project(client, "Empty Sections")
        resp = await client.get(f"/api/projects/{project['id']}/sections")
        assert resp.status_code == 200
        data = resp.json()
        assert data["recordings"] == 0
        assert data["documents"] == 0
        assert data["notes"] == 0

    @pytest.mark.asyncio
    async def test_sections_with_content(self, client: AsyncClient, db_session: AsyncSession):
        """Sections counts recordings and documents correctly."""
        project = await create_project(client, "Content Sections")
        await create_recording_in_project(db_session, "Section Rec 1", project["id"])
        await create_recording_in_project(db_session, "Section Rec 2", project["id"])
        await create_document_in_project(db_session, "Section Doc 1", project["id"])

        resp = await client.get(f"/api/projects/{project['id']}/sections")
        assert resp.status_code == 200
        data = resp.json()
        assert data["recordings"] == 2
        assert data["documents"] == 1

    @pytest.mark.asyncio
    async def test_sections_not_found(self, client: AsyncClient):
        """Sections for non-existent project returns 404."""
        resp = await client.get("/api/projects/nonexistent-id/sections")
        assert resp.status_code == 404


class TestPhase3Archive:
    """Test archive/unarchive endpoints."""

    @pytest.mark.asyncio
    async def test_archive_project(self, client: AsyncClient):
        """Archive a project."""
        project = await create_project(client, "To Archive")
        assert project["is_archived"] is False

        resp = await client.patch(f"/api/projects/{project['id']}/archive")
        assert resp.status_code == 200
        data = resp.json()
        assert data["message"] == "Project archived"

        # Verify via GET that is_archived is now True
        get_resp = await client.get(f"/api/projects/{project['id']}")
        assert get_resp.json()["is_archived"] is True

    @pytest.mark.asyncio
    async def test_unarchive_project(self, client: AsyncClient):
        """Unarchive a previously archived project."""
        project = await create_project(client, "To Unarchive")

        # Archive it
        await client.patch(f"/api/projects/{project['id']}/archive")

        # Unarchive it
        resp = await client.patch(f"/api/projects/{project['id']}/unarchive")
        assert resp.status_code == 200
        data = resp.json()
        assert data["message"] == "Project unarchived"

        # Verify via GET that is_archived is now False
        get_resp = await client.get(f"/api/projects/{project['id']}")
        assert get_resp.json()["is_archived"] is False

    @pytest.mark.asyncio
    async def test_list_excludes_archived_by_default(self, client: AsyncClient):
        """Archived projects don't appear in default listing."""
        p1 = await create_project(client, "Active Project")
        p2 = await create_project(client, "Archived Project")
        await client.patch(f"/api/projects/{p2['id']}/archive")

        resp = await client.get("/api/projects")
        assert resp.status_code == 200
        names = [p["name"] for p in resp.json()["items"]]
        assert "Active Project" in names
        assert "Archived Project" not in names

    @pytest.mark.asyncio
    async def test_list_includes_archived_when_requested(self, client: AsyncClient):
        """Archived projects appear when include_archived=true."""
        p1 = await create_project(client, "Active P2")
        p2 = await create_project(client, "Archived P2")
        await client.patch(f"/api/projects/{p2['id']}/archive")

        resp = await client.get("/api/projects?include_archived=true")
        assert resp.status_code == 200
        names = [p["name"] for p in resp.json()["items"]]
        assert "Active P2" in names
        assert "Archived P2" in names


class TestPhase3ActiveProject:
    """Test active project get/set endpoints."""

    @pytest.mark.asyncio
    async def test_get_active_project_default(self, client: AsyncClient):
        """Default active project is null."""
        resp = await client.get("/api/projects/active/current")
        assert resp.status_code == 200
        data = resp.json()
        assert data["active_project_id"] is None

    @pytest.mark.asyncio
    async def test_set_and_get_active_project(self, client: AsyncClient):
        """Set active project and retrieve it."""
        project = await create_project(client, "Active Test")

        set_resp = await client.put(
            "/api/projects/active/current",
            json={"project_id": project["id"]},
        )
        assert set_resp.status_code == 200

        get_resp = await client.get("/api/projects/active/current")
        data = get_resp.json()
        assert data["active_project_id"] == project["id"]

    @pytest.mark.asyncio
    async def test_clear_active_project(self, client: AsyncClient):
        """Clear active project by setting to null."""
        project = await create_project(client, "Clear Active Test")

        # Set it
        await client.put(
            "/api/projects/active/current",
            json={"project_id": project["id"]},
        )

        # Clear it
        resp = await client.put(
            "/api/projects/active/current",
            json={"project_id": None},
        )
        assert resp.status_code == 200

        get_resp = await client.get("/api/projects/active/current")
        assert get_resp.json()["active_project_id"] is None

    @pytest.mark.asyncio
    async def test_active_project_route_not_captured_as_id(self, client: AsyncClient):
        """Ensure /active/current doesn't get captured by /{project_id}."""
        # This should NOT return 404 (which would happen if "active" was treated as a project_id)
        resp = await client.get("/api/projects/active/current")
        assert resp.status_code == 200


class TestPhase3MoveItems:
    """Test bulk move-items endpoint."""

    @pytest.mark.asyncio
    async def test_move_recording_to_project(self, client: AsyncClient, db_session: AsyncSession):
        """Move a recording into a project."""
        project = await create_project(client, "Move Target")
        rec_id = await create_recording_in_project(db_session, "Movable Rec", None)

        resp = await client.post(
            f"/api/projects/{project['id']}/move-items",
            json={"recording_ids": [rec_id]},
        )
        assert resp.status_code == 200

        # Verify recording is now in project
        list_resp = await client.get(
            "/api/recordings",
            headers={"X-Active-Project": project["id"]},
        )
        rec_ids = [r["id"] for r in list_resp.json()["items"]]
        assert rec_id in rec_ids

    @pytest.mark.asyncio
    async def test_move_document_to_project(self, client: AsyncClient, db_session: AsyncSession):
        """Move a document into a project."""
        project = await create_project(client, "Move Doc Target")
        doc_id = await create_document_in_project(db_session, "Movable Doc", None)

        resp = await client.post(
            f"/api/projects/{project['id']}/move-items",
            json={"document_ids": [doc_id]},
        )
        assert resp.status_code == 200

        # Verify document is now in project
        list_resp = await client.get(
            "/api/documents",
            headers={"X-Active-Project": project["id"]},
        )
        doc_ids = [d["id"] for d in list_resp.json()["items"]]
        assert doc_id in doc_ids

"""Tests for archive/unarchive endpoints on recordings and documents.

Covers:
- PATCH /recordings/{id}/archive and /unarchive
- PATCH /documents/{id}/archive and /unarchive
- GET /recordings/archived and /documents/archived
- Default listing excludes archived items
"""

import uuid
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from persistence.models import Document, Project, Recording


@pytest.fixture(autouse=True)
def mock_storage_location():
    """Bypass storage_locations table lookup in recordings/documents list.

    get_active_storage_location() uses its own session factory (not the
    test-injected one), so it hits a DB that doesn't have the table.
    Returning None is safe -- it just skips the storage-location filter.
    """
    async def _return_none():
        return None

    with patch("api.routes.recordings.get_active_storage_location", new=_return_none), \
         patch("api.routes.documents.get_active_storage_location", new=_return_none):
        yield


# -- Helpers ------------------------------------------------------------------

async def _create_project(client: AsyncClient, name: str) -> dict:
    """Create a project and return the response data."""
    resp = await client.post("/api/projects", json={"name": name})
    assert resp.status_code == 201, f"Failed to create project: {resp.text}"
    return resp.json()


async def _create_recording(db: AsyncSession, title: str, project_id: str | None) -> str:
    """Insert a recording directly in the DB and return its ID."""
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


async def _create_document(db: AsyncSession, title: str, project_id: str | None) -> str:
    """Insert a document directly in the DB and return its ID."""
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


# == Recording archive tests ==================================================


class TestRecordingArchive:
    """Archive / unarchive lifecycle for recordings."""

    @pytest.mark.asyncio
    async def test_archive_recording(self, client: AsyncClient, db_session: AsyncSession):
        """PATCH /recordings/{id}/archive sets is_archived=True."""
        project = await _create_project(client, "Rec Archive Proj")
        rec_id = await _create_recording(db_session, "Archivable Rec", project["id"])

        resp = await client.patch(f"/api/recordings/{rec_id}/archive")
        assert resp.status_code == 200
        data = resp.json()
        assert data["message"] == "Recording archived"
        assert data["id"] == rec_id

        # Confirm via GET that is_archived is now True
        get_resp = await client.get(f"/api/recordings/{rec_id}")
        assert get_resp.status_code == 200
        assert get_resp.json()["is_archived"] is True

    @pytest.mark.asyncio
    async def test_unarchive_recording(self, client: AsyncClient, db_session: AsyncSession):
        """PATCH /recordings/{id}/unarchive sets is_archived=False."""
        project = await _create_project(client, "Rec Unarchive Proj")
        rec_id = await _create_recording(db_session, "Unarchivable Rec", project["id"])

        # Archive first
        await client.patch(f"/api/recordings/{rec_id}/archive")

        # Unarchive
        resp = await client.patch(f"/api/recordings/{rec_id}/unarchive")
        assert resp.status_code == 200
        data = resp.json()
        assert data["message"] == "Recording unarchived"

        # Confirm via GET
        get_resp = await client.get(f"/api/recordings/{rec_id}")
        assert get_resp.status_code == 200
        assert get_resp.json()["is_archived"] is False

    @pytest.mark.asyncio
    async def test_list_recordings_excludes_archived_by_default(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """GET /recordings excludes archived recordings when include_archived is not set."""
        project = await _create_project(client, "Rec Exclude Proj")
        active_id = await _create_recording(db_session, "Active Rec", project["id"])
        archived_id = await _create_recording(db_session, "Archived Rec", project["id"])

        # Archive one recording
        await client.patch(f"/api/recordings/{archived_id}/archive")

        resp = await client.get(
            "/api/recordings",
            headers={"X-Active-Project": project["id"]},
        )
        assert resp.status_code == 200
        titles = [r["title"] for r in resp.json()["items"]]
        assert "Active Rec" in titles
        assert "Archived Rec" not in titles

    @pytest.mark.asyncio
    async def test_archived_recordings_endpoint(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """GET /recordings/archived returns only archived recordings."""
        project = await _create_project(client, "Rec Archived List Proj")
        active_id = await _create_recording(db_session, "Still Active", project["id"])
        archived_id = await _create_recording(db_session, "In Archive", project["id"])

        await client.patch(f"/api/recordings/{archived_id}/archive")

        resp = await client.get(
            "/api/recordings/archived",
            headers={"X-Active-Project": project["id"]},
        )
        assert resp.status_code == 200
        items = resp.json()["items"]
        titles = [r["title"] for r in items]
        assert "In Archive" in titles
        assert "Still Active" not in titles

    @pytest.mark.asyncio
    async def test_recording_response_has_is_archived_field(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """RecordingResponse includes is_archived field."""
        project = await _create_project(client, "Rec Field Proj")
        rec_id = await _create_recording(db_session, "Field Test Rec", project["id"])

        resp = await client.get(f"/api/recordings/{rec_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert "is_archived" in data
        assert data["is_archived"] is False


# == Document archive tests ====================================================


class TestDocumentArchive:
    """Archive / unarchive lifecycle for documents."""

    @pytest.mark.asyncio
    async def test_archive_document(self, client: AsyncClient, db_session: AsyncSession):
        """PATCH /documents/{id}/archive sets is_archived=True."""
        project = await _create_project(client, "Doc Archive Proj")
        doc_id = await _create_document(db_session, "Archivable Doc", project["id"])

        resp = await client.patch(f"/api/documents/{doc_id}/archive")
        assert resp.status_code == 200
        data = resp.json()
        assert data["message"] == "Document archived"
        assert data["id"] == doc_id

        # Confirm via GET that is_archived is now True
        get_resp = await client.get(f"/api/documents/{doc_id}")
        assert get_resp.status_code == 200
        assert get_resp.json()["is_archived"] is True

    @pytest.mark.asyncio
    async def test_unarchive_document(self, client: AsyncClient, db_session: AsyncSession):
        """PATCH /documents/{id}/unarchive sets is_archived=False."""
        project = await _create_project(client, "Doc Unarchive Proj")
        doc_id = await _create_document(db_session, "Unarchivable Doc", project["id"])

        # Archive first
        await client.patch(f"/api/documents/{doc_id}/archive")

        # Unarchive
        resp = await client.patch(f"/api/documents/{doc_id}/unarchive")
        assert resp.status_code == 200
        data = resp.json()
        assert data["message"] == "Document unarchived"

        # Confirm via GET
        get_resp = await client.get(f"/api/documents/{doc_id}")
        assert get_resp.status_code == 200
        assert get_resp.json()["is_archived"] is False

    @pytest.mark.asyncio
    async def test_list_documents_excludes_archived_by_default(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """GET /documents excludes archived documents when include_archived is not set."""
        project = await _create_project(client, "Doc Exclude Proj")
        active_id = await _create_document(db_session, "Active Doc", project["id"])
        archived_id = await _create_document(db_session, "Archived Doc", project["id"])

        # Archive one document
        await client.patch(f"/api/documents/{archived_id}/archive")

        resp = await client.get(
            "/api/documents",
            headers={"X-Active-Project": project["id"]},
        )
        assert resp.status_code == 200
        titles = [d["title"] for d in resp.json()["items"]]
        assert "Active Doc" in titles
        assert "Archived Doc" not in titles

    @pytest.mark.asyncio
    async def test_archived_documents_endpoint(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """GET /documents/archived returns only archived documents."""
        project = await _create_project(client, "Doc Archived List Proj")
        active_id = await _create_document(db_session, "Still Active Doc", project["id"])
        archived_id = await _create_document(db_session, "In Doc Archive", project["id"])

        await client.patch(f"/api/documents/{archived_id}/archive")

        resp = await client.get(
            "/api/documents/archived",
            headers={"X-Active-Project": project["id"]},
        )
        assert resp.status_code == 200
        items = resp.json()["items"]
        titles = [d["title"] for d in items]
        assert "In Doc Archive" in titles
        assert "Still Active Doc" not in titles

    @pytest.mark.asyncio
    async def test_document_response_has_is_archived_field(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """DocumentResponse includes is_archived field."""
        project = await _create_project(client, "Doc Field Proj")
        doc_id = await _create_document(db_session, "Field Test Doc", project["id"])

        resp = await client.get(f"/api/documents/{doc_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert "is_archived" in data
        assert data["is_archived"] is False

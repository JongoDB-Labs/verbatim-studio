"""Trash service — auto-purge and settings for the recycle-bin feature."""

import asyncio
import logging
from datetime import datetime, timezone, timedelta

from sqlalchemy import select

logger = logging.getLogger(__name__)

TRASH_SETTINGS_KEY = "trash"
DEFAULT_AUTO_PURGE_DAYS = 30
VALID_PURGE_OPTIONS = (0, 30, 60, 90)  # 0 = never


async def get_trash_settings() -> dict:
    """Return trash settings from the DB, merged with defaults."""
    from persistence.database import get_session_factory
    from persistence.models import Setting

    defaults = {"auto_purge_days": DEFAULT_AUTO_PURGE_DAYS}

    async with get_session_factory()() as session:
        result = await session.execute(
            select(Setting).where(Setting.key == TRASH_SETTINGS_KEY)
        )
        setting = result.scalar_one_or_none()
        if setting and isinstance(setting.value, dict):
            defaults.update(setting.value)

    return defaults


async def save_trash_settings(updates: dict) -> dict:
    """Persist trash settings to the DB."""
    from persistence.database import get_session_factory
    from persistence.models import Setting

    async with get_session_factory()() as session:
        result = await session.execute(
            select(Setting).where(Setting.key == TRASH_SETTINGS_KEY)
        )
        setting = result.scalar_one_or_none()

        current = setting.value.copy() if setting and isinstance(setting.value, dict) else {}
        current.update(updates)

        if setting:
            setting.value = current
        else:
            session.add(Setting(key=TRASH_SETTINGS_KEY, value=current))

        await session.commit()

    return await get_trash_settings()


async def _purge_expired_items() -> int:
    """Permanently delete all trashed items past the auto-purge window.

    Returns the number of items purged.
    """
    from persistence.database import get_session_factory
    from persistence.models import Recording, Document, Project
    from services.storage import StorageService

    settings = await get_trash_settings()
    days = settings.get("auto_purge_days", DEFAULT_AUTO_PURGE_DAYS)

    if days == 0:
        return 0  # Auto-purge disabled

    # Use naive UTC to match SQLite's naive datetime storage
    cutoff = datetime.utcnow() - timedelta(days=days)
    storage = StorageService()
    purged = 0

    async with get_session_factory()() as session:
        # Purge recordings
        result = await session.execute(
            select(Recording).where(
                Recording.is_archived == True,
                Recording.deleted_at != None,
                Recording.deleted_at < cutoff,
            )
        )
        for recording in result.scalars().all():
            if recording.file_path:
                try:
                    await storage.delete_file(recording.file_path, recording.storage_location_id)
                except Exception as e:
                    logger.warning("Failed to delete file for purged recording %s: %s", recording.id, e)
            await session.delete(recording)
            purged += 1

        # Purge documents
        result = await session.execute(
            select(Document).where(
                Document.is_archived == True,
                Document.deleted_at != None,
                Document.deleted_at < cutoff,
            )
        )
        for doc in result.scalars().all():
            if doc.file_path:
                try:
                    await storage.delete_file(doc.file_path, doc.storage_location_id)
                except Exception as e:
                    logger.warning("Failed to delete file for purged document %s: %s", doc.id, e)
            await session.delete(doc)
            purged += 1

        # Purge projects (no files to delete for projects themselves)
        result = await session.execute(
            select(Project).where(
                Project.is_archived == True,
                Project.deleted_at != None,
                Project.deleted_at < cutoff,
            )
        )
        for project in result.scalars().all():
            await session.delete(project)
            purged += 1

        if purged:
            await session.commit()
            logger.info("Auto-purge: permanently deleted %d expired trash items", purged)

            # Broadcast invalidation so frontends refresh
            try:
                from api.routes.sync import broadcast
                await broadcast("recordings", "deleted")
                await broadcast("documents", "deleted")
                await broadcast("projects", "deleted")
            except Exception:
                pass  # Non-critical

    return purged


async def auto_purge_loop() -> None:
    """Background loop that runs trash purge on startup and then hourly."""
    try:
        # Run immediately on startup
        await _purge_expired_items()
    except Exception:
        logger.warning("Initial trash purge failed", exc_info=True)

    while True:
        await asyncio.sleep(3600)  # Every hour
        try:
            await _purge_expired_items()
        except Exception:
            logger.warning("Periodic trash purge failed", exc_info=True)

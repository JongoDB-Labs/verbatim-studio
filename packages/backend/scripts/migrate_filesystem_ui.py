#!/usr/bin/env python3
"""Migration script to convert UUID-based file paths to human-readable paths.

This script migrates existing recordings and documents from the old structure:
    media/recordings/{uuid}/{filename}
    media/documents/{uuid}/{filename}

To the new filesystem-as-UI structure:
    media/{project_name}/{title}.{ext}       (if in a project)
    media/{title}.{ext}                       (if not in a project)

Run from the backend directory:
    python scripts/migrate_filesystem_ui.py

Options:
    --dry-run   Show what would be done without making changes
    --verbose   Show detailed progress
"""

import argparse
import asyncio
import logging
import shutil
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.config import settings
from persistence.database import get_session_factory
from persistence.models import Document, Project, Recording
from services.path_manager import PathManager
from sqlalchemy import select
from sqlalchemy.orm import selectinload

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

path_manager = PathManager()


async def migrate_recordings(
    storage_root: Path, dry_run: bool = False, verbose: bool = False
) -> tuple[int, int]:
    """Migrate recordings to new path structure.

    Args:
        storage_root: The storage root directory.
        dry_run: If True, show what would be done without making changes.
        verbose: If True, show detailed progress.

    Returns:
        Tuple of (migrated_count, skipped_count).
    """
    migrated = 0
    skipped = 0

    async with get_session_factory()() as session:
        result = await session.execute(
            select(Recording).options(selectinload(Recording.project))
        )
        recordings = result.scalars().all()

        for rec in recordings:
            old_path = Path(rec.file_path) if rec.file_path else None

            # Skip if no file path or file doesn't exist
            if not old_path or not old_path.exists():
                if verbose:
                    logger.info(f"Skipping recording {rec.id}: file not found")
                skipped += 1
                continue

            # Check if already migrated (not in recordings/ subdirectory)
            try:
                relative = old_path.relative_to(storage_root)
                if relative.parts[0] != "recordings":
                    if verbose:
                        logger.info(f"Skipping recording {rec.id}: already migrated")
                    skipped += 1
                    continue
            except ValueError:
                # Path not relative to storage root - skip
                skipped += 1
                continue

            # Compute new path
            project_name = rec.project.name if rec.project else None
            extension = old_path.suffix
            new_path = path_manager.get_item_path(
                storage_root, project_name, rec.title, extension
            )

            # Handle collisions
            actual_path = path_manager.generate_unique_path(new_path.parent, new_path.name)

            logger.info(f"Migrate: {old_path.name} -> {actual_path.relative_to(storage_root)}")

            if not dry_run:
                # Ensure parent directory exists
                actual_path.parent.mkdir(parents=True, exist_ok=True)

                # Move file
                shutil.move(str(old_path), str(actual_path))

                # Update database
                rec.file_path = str(actual_path)
                rec.file_name = actual_path.name

            migrated += 1

        if not dry_run:
            await session.commit()

    return migrated, skipped


async def migrate_documents(
    storage_root: Path, dry_run: bool = False, verbose: bool = False
) -> tuple[int, int]:
    """Migrate documents to new path structure.

    Args:
        storage_root: The storage root directory.
        dry_run: If True, show what would be done without making changes.
        verbose: If True, show detailed progress.

    Returns:
        Tuple of (migrated_count, skipped_count).
    """
    migrated = 0
    skipped = 0

    async with get_session_factory()() as session:
        result = await session.execute(
            select(Document).options(selectinload(Document.project))
        )
        documents = result.scalars().all()

        for doc in documents:
            # Documents stored relative paths, need to resolve
            if not doc.file_path:
                skipped += 1
                continue

            # Handle both absolute and relative paths
            old_path = Path(doc.file_path)
            if not old_path.is_absolute():
                old_path = storage_root / doc.file_path

            if not old_path.exists():
                if verbose:
                    logger.info(f"Skipping document {doc.id}: file not found")
                skipped += 1
                continue

            # Check if already migrated (not in documents/ subdirectory)
            try:
                relative = old_path.relative_to(storage_root)
                if relative.parts[0] != "documents":
                    if verbose:
                        logger.info(f"Skipping document {doc.id}: already migrated")
                    skipped += 1
                    continue
            except ValueError:
                # Path not relative to storage root - skip
                skipped += 1
                continue

            # Compute new path
            project_name = doc.project.name if doc.project else None
            extension = old_path.suffix
            new_path = path_manager.get_item_path(
                storage_root, project_name, doc.title, extension
            )

            # Handle collisions
            actual_path = path_manager.generate_unique_path(new_path.parent, new_path.name)

            logger.info(f"Migrate: {old_path.name} -> {actual_path.relative_to(storage_root)}")

            if not dry_run:
                # Ensure parent directory exists
                actual_path.parent.mkdir(parents=True, exist_ok=True)

                # Move file
                shutil.move(str(old_path), str(actual_path))

                # Update database
                doc.file_path = str(actual_path)
                doc.filename = actual_path.name

            migrated += 1

        if not dry_run:
            await session.commit()

    return migrated, skipped


async def cleanup_empty_directories(storage_root: Path, dry_run: bool = False) -> int:
    """Clean up empty UUID directories.

    Args:
        storage_root: The storage root directory.
        dry_run: If True, show what would be done without making changes.

    Returns:
        Number of directories removed.
    """
    removed = 0

    for subdir_name in ["recordings", "documents"]:
        subdir = storage_root / subdir_name
        if not subdir.exists():
            continue

        # Look for UUID directories
        for uuid_dir in list(subdir.iterdir()):
            if not uuid_dir.is_dir():
                continue

            # Check if empty
            if not any(uuid_dir.iterdir()):
                logger.info(f"Remove empty directory: {uuid_dir.relative_to(storage_root)}")
                if not dry_run:
                    uuid_dir.rmdir()
                removed += 1

        # Remove the recordings/documents directory if empty
        if not any(subdir.iterdir()):
            logger.info(f"Remove empty directory: {subdir.relative_to(storage_root)}")
            if not dry_run:
                subdir.rmdir()
            removed += 1

    return removed


async def main():
    """Run the migration."""
    parser = argparse.ArgumentParser(description="Migrate files to filesystem-as-UI structure")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done without making changes")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show detailed progress")
    args = parser.parse_args()

    storage_root = settings.MEDIA_DIR

    if args.dry_run:
        logger.info("DRY RUN - no changes will be made")

    logger.info(f"Storage root: {storage_root}")
    logger.info("")

    # Initialize database
    from persistence.database import init_db
    await init_db()

    # Migrate recordings
    logger.info("Migrating recordings...")
    rec_migrated, rec_skipped = await migrate_recordings(
        storage_root, dry_run=args.dry_run, verbose=args.verbose
    )
    logger.info(f"  Migrated: {rec_migrated}, Skipped: {rec_skipped}")
    logger.info("")

    # Migrate documents
    logger.info("Migrating documents...")
    doc_migrated, doc_skipped = await migrate_documents(
        storage_root, dry_run=args.dry_run, verbose=args.verbose
    )
    logger.info(f"  Migrated: {doc_migrated}, Skipped: {doc_skipped}")
    logger.info("")

    # Cleanup empty directories
    logger.info("Cleaning up empty directories...")
    removed = await cleanup_empty_directories(storage_root, dry_run=args.dry_run)
    logger.info(f"  Removed: {removed}")
    logger.info("")

    logger.info("Migration complete!")


if __name__ == "__main__":
    asyncio.run(main())

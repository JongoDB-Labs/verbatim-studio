"""Storage service for file management.

Implements filesystem-as-UI pattern where:
- Projects are folders on disk
- Recordings/documents are files in those folders
- Files without a project live at the storage root

Supports multiple storage backends through adapters (local, cloud, network).
"""

import logging
from pathlib import Path

import aiofiles
import aiofiles.os

from core.config import settings
from services.path_manager import PathManager, path_manager as default_path_manager

logger = logging.getLogger(__name__)


async def get_active_storage_location():
    """Get the default active storage location from database.

    Prioritizes the default storage location, falling back to any active location.

    Returns:
        StorageLocation model or None if no active location.
    """
    from persistence.database import get_session_factory
    from persistence.models import StorageLocation
    from sqlalchemy import select

    async with get_session_factory()() as session:
        # First try to get the default storage location
        result = await session.execute(
            select(StorageLocation).where(
                StorageLocation.is_default == True,
                StorageLocation.is_active == True,
            )
        )
        location = result.scalar_one_or_none()
        if location:
            return location

        # Fall back to any active storage location
        result = await session.execute(
            select(StorageLocation).where(StorageLocation.is_active == True).limit(1)
        )
        return result.scalar_one_or_none()


async def get_storage_adapter():
    """Get the appropriate storage adapter for the active location.

    Returns:
        StorageAdapter instance for the active storage location,
        or a LocalAdapter for the default media directory.
    """
    from storage.factory import get_adapter
    from storage.adapters.local import LocalAdapter

    location = await get_active_storage_location()

    if location:
        try:
            return get_adapter(location), location.type, location.subtype
        except Exception as e:
            logger.error(
                f"Failed to get adapter for {location.type}/{location.subtype} "
                f"location '{location.name}' ({location.id}): {e}. "
                f"Falling back to local storage."
            )
            # Fall back to local

    # Default to local storage
    return LocalAdapter({"path": str(settings.MEDIA_DIR)}), "local", None


async def get_active_storage_path() -> Path:
    """Get the path of the currently active storage location.

    Returns the configured storage location path from the database,
    or falls back to settings.MEDIA_DIR if none is configured.

    Note: For cloud storage, this returns None as there's no local path.
    """
    location = await get_active_storage_location()

    if location and location.config:
        if location.type == "local":
            path_str = location.config.get("path")
            if path_str:
                path = Path(path_str)
                # Warn if path is not absolute (legacy entries before fix)
                if not path.is_absolute():
                    logger.warning(
                        f"Storage location has relative path '{path_str}'. "
                        "Please update to an absolute path in Settings."
                    )
                path.mkdir(parents=True, exist_ok=True)
                return path
        # For cloud storage, return None - callers should use adapters
        elif location.type == "cloud":
            return None

    return settings.MEDIA_DIR


class StorageService:
    """Service for managing file storage operations.

    Uses human-readable paths based on project names and item titles.
    Supports multiple storage backends through adapters.
    """

    def __init__(
        self,
        media_dir: Path | None = None,
        pm: PathManager | None = None,
    ):
        """Initialize storage service.

        Args:
            media_dir: Override for media directory. Uses settings.MEDIA_DIR by default.
            pm: PathManager instance. Uses default if not provided.
        """
        self.media_dir = media_dir or settings.MEDIA_DIR
        self._pm = pm or default_path_manager

    async def _get_adapter_info(self):
        """Get the current storage adapter and type info."""
        return await get_storage_adapter()

    async def _resolve_adapter(self, storage_location_id: str | None = None):
        """Resolve adapter for an existing file by its storage_location_id.

        Falls back to current default if location_id is None or resolution fails.
        """
        if storage_location_id:
            from persistence.database import get_session_factory
            from persistence.models import StorageLocation
            from storage.factory import get_adapter

            async with get_session_factory()() as session:
                location = await session.get(StorageLocation, storage_location_id)
                if location:
                    try:
                        return get_adapter(location), location.type, location.subtype
                    except Exception as e:
                        logger.error(
                            "Failed to get adapter for location %s: %s",
                            storage_location_id,
                            e,
                        )
        return await get_storage_adapter()

    async def _is_cloud_storage(self) -> bool:
        """Check if current storage is cloud-based."""
        _, storage_type, _ = await self._get_adapter_info()
        return storage_type == "cloud"

    def get_item_path(
        self,
        title: str,
        filename: str,
        project_name: str | None = None,
    ) -> Path:
        """Get the path for a recording or document file.

        New pattern: <project_name>/<title>.<ext> or <title>.<ext>

        Args:
            title: The item title.
            filename: Original filename (used for extension).
            project_name: Project name if item belongs to a project.

        Returns:
            Full path where the file should be stored.
        """
        extension = Path(filename).suffix
        return self._pm.get_item_path(self.media_dir, project_name, title, extension)

    def _get_relative_path(
        self,
        title: str,
        filename: str,
        project_name: str | None = None,
    ) -> str:
        """Get relative path for cloud storage.

        Args:
            title: The item title.
            filename: Original filename (used for extension).
            project_name: Project name if item belongs to a project.

        Returns:
            Relative path string for cloud storage.
        """
        extension = Path(filename).suffix
        safe_title = self._pm.sanitize_name(title)
        if project_name:
            safe_project = self._pm.sanitize_name(project_name)
            return f"{safe_project}/{safe_title}{extension}"
        return f"{safe_title}{extension}"

    # Legacy method for backwards compatibility during migration
    def get_recording_path(self, recording_id: str, filename: str) -> Path:
        """Get the full path for a recording file (legacy UUID-based path).

        Args:
            recording_id: The recording's unique ID.
            filename: Original filename with extension.

        Returns:
            Full path where the recording file should be stored.

        Raises:
            ValueError: If filename contains path traversal attempts.
        """
        # Defense in depth: sanitize filename to prevent path traversal
        normalized_filename = filename.replace("\\", "/")
        safe_filename = Path(normalized_filename).name
        if not safe_filename or safe_filename in (".", ".."):
            safe_filename = "unknown"

        # Also sanitize recording_id to prevent path traversal
        normalized_recording_id = recording_id.replace("\\", "/")
        safe_recording_id = Path(normalized_recording_id).name
        if not safe_recording_id or safe_recording_id in (".", ".."):
            raise ValueError("Invalid recording ID")

        # Create a subdirectory based on recording ID
        recording_dir = self.media_dir / "recordings" / safe_recording_id
        result_path = recording_dir / safe_filename

        # Final safety check
        try:
            result_path.resolve().relative_to(self.media_dir.resolve())
        except ValueError:
            raise ValueError("Path traversal detected in filename or recording_id")

        return result_path

    async def save_upload(
        self,
        content: bytes,
        title: str,
        filename: str,
        project_name: str | None = None,
    ) -> Path | str:
        """Save an uploaded file to storage with human-readable path.

        Args:
            content: File content as bytes.
            title: The item title (used for filename).
            filename: Original filename (used for extension).
            project_name: Project name if item belongs to a project.

        Returns:
            Path where the file was saved (Path for local, string for cloud).
        """
        adapter, storage_type, _ = await self._get_adapter_info()

        if storage_type == "cloud":
            # Use cloud adapter
            try:
                relative_path = self._get_relative_path(title, filename, project_name)

                # Ensure directory exists
                if project_name:
                    await adapter.ensure_directory(self._pm.sanitize_name(project_name))

                # Write file through adapter
                await adapter.write_file(relative_path, content)

                logger.info(f"Saved file to cloud storage: {relative_path}")
                return relative_path
            except Exception as e:
                logger.error("Cloud storage write failed: %s. Falling back to local.", e)
                # Fall through to local storage below

        # Local storage (or cloud fallback)
        storage_root = await get_active_storage_path()
        if storage_root is None:
            storage_root = self.media_dir

        extension = Path(filename).suffix
        desired_path = self._pm.get_item_path(storage_root, project_name, title, extension)

        # Ensure parent directory exists
        await aiofiles.os.makedirs(desired_path.parent, exist_ok=True)

        # Generate unique path to handle collisions
        actual_path = self._pm.generate_unique_path(desired_path.parent, desired_path.name)

        # Ensure directory exists (in case unique path has different parent)
        await aiofiles.os.makedirs(actual_path.parent, exist_ok=True)

        # Write the file
        async with aiofiles.open(actual_path, "wb") as f:
            await f.write(content)

        return actual_path

    async def move_to_project(
        self,
        current_path: Path | str,
        new_project_name: str | None,
        storage_location_id: str | None = None,
    ) -> Path | str:
        """Move a file when project assignment changes.

        Args:
            current_path: Current file path (Path for local, string for cloud).
            new_project_name: New project name, or None to move to root.
            storage_location_id: Storage location ID of the existing file.

        Returns:
            New path where the file is now located.
        """
        adapter, storage_type, _ = await self._resolve_adapter(storage_location_id)

        if storage_type == "cloud":
            # For cloud storage: read file, write to new location, delete old
            old_path = str(current_path)
            filename = old_path.split("/")[-1]

            if new_project_name:
                safe_project = self._pm.sanitize_name(new_project_name)
                await adapter.ensure_directory(safe_project)
                new_path = f"{safe_project}/{filename}"
            else:
                new_path = filename

            if old_path != new_path:
                # Read old file content
                content = await adapter.read_file(old_path)
                # Write to new location
                await adapter.write_file(new_path, content)
                # Delete old file
                await adapter.delete_file(old_path)
                logger.info(f"Moved cloud file from {old_path} to {new_path}")

            return new_path
        else:
            # Local storage - use filesystem operations
            storage_root = await get_active_storage_path()
            if storage_root is None:
                storage_root = self.media_dir

            if new_project_name:
                new_parent = storage_root / self._pm.sanitize_name(new_project_name)
            else:
                new_parent = storage_root

            # Resolve the source path - try direct path first, then media_dir fallback
            source_path = Path(current_path)
            if not source_path.exists() and not source_path.is_absolute():
                source_path = self.get_full_path(str(current_path))

            return await self._pm.move_file(source_path, new_parent)

    async def rename_item(
        self,
        current_path: Path | str,
        new_title: str,
        storage_location_id: str | None = None,
    ) -> Path | str:
        """Rename a file when its title changes.

        Args:
            current_path: Current file path (Path for local, string for cloud).
            new_title: New title for the item.
            storage_location_id: Storage location ID of the existing file.

        Returns:
            New path with renamed file.
        """
        adapter, storage_type, _ = await self._resolve_adapter(storage_location_id)

        if storage_type == "cloud":
            # For cloud storage: read file, write with new name, delete old
            old_path = str(current_path)
            # Get extension from old path
            if "." in old_path.split("/")[-1]:
                extension = "." + old_path.rsplit(".", 1)[1]
            else:
                extension = ""

            # Determine new path (keep same directory)
            path_parts = old_path.rsplit("/", 1)
            if len(path_parts) == 2:
                directory = path_parts[0]
                new_path = f"{directory}/{self._pm.sanitize_name(new_title)}{extension}"
            else:
                new_path = f"{self._pm.sanitize_name(new_title)}{extension}"

            if old_path != new_path:
                # Read old file content
                content = await adapter.read_file(old_path)
                # Write with new name
                await adapter.write_file(new_path, content)
                # Delete old file
                await adapter.delete_file(old_path)
                logger.info(f"Renamed cloud file from {old_path} to {new_path}")

            return new_path
        else:
            # Local storage - resolve path first
            path = Path(current_path)
            if not path.exists() and not path.is_absolute():
                path = self.get_full_path(str(current_path))
            extension = path.suffix
            new_name = f"{self._pm.sanitize_name(new_title)}{extension}"
            return await self._pm.rename_file(path, new_name)

    async def delete_file(
        self,
        file_path: Path | str,
        storage_location_id: str | None = None,
    ) -> bool:
        """Delete a file from storage.

        Args:
            file_path: Path to the file to delete (Path for local, string for cloud).
            storage_location_id: Storage location ID of the existing file.

        Returns:
            True if file was deleted, False if it didn't exist.
        """
        adapter, storage_type, _ = await self._resolve_adapter(storage_location_id)

        if storage_type == "cloud":
            # For cloud storage, file_path is a relative path string
            relative_path = str(file_path)
            try:
                await adapter.delete_file(relative_path)
                logger.info(f"Deleted file from cloud storage: {relative_path}")
                return True
            except Exception as e:
                logger.error(f"Failed to delete cloud file {relative_path}: {e}")
                return False
        else:
            # Local storage - resolve path first
            path = Path(file_path)
            if not path.exists() and not path.is_absolute():
                path = self.get_full_path(str(file_path))
            try:
                await aiofiles.os.remove(path)
                # Try to remove parent directory if empty (cleanup project folder)
                await self._pm.delete_folder_if_empty(path.parent)
                return True
            except FileNotFoundError:
                return False

    async def move_to_trash(
        self,
        file_path: Path | str,
        storage_location_id: str | None = None,
    ) -> Path | str | None:
        """Move a file to the _trash folder, preserving its relative path.

        Returns the new path, or None if the file didn't exist.
        """
        adapter, storage_type, _ = await self._resolve_adapter(storage_location_id)

        if storage_type == "cloud":
            old_path = str(file_path)
            new_path = f"_trash/{old_path}"
            try:
                content = await adapter.read_file(old_path)
                parts = new_path.rsplit("/", 1)
                if len(parts) == 2:
                    await adapter.ensure_directory(parts[0])
                await adapter.write_file(new_path, content)
                await adapter.delete_file(old_path)
                logger.info("Moved cloud file to trash: %s -> %s", old_path, new_path)
                return new_path
            except Exception as e:
                logger.warning("Failed to move cloud file to trash %s: %s", old_path, e)
                return None
        else:
            storage_root = await get_active_storage_path()
            if storage_root is None:
                storage_root = self.media_dir

            source = Path(file_path)
            if not source.exists() and not source.is_absolute():
                source = self.get_full_path(str(file_path))

            if not source.exists():
                logger.warning("File not found for trash move: %s", source)
                return None

            # Compute relative path from storage root, then put under _trash/
            try:
                relative = source.relative_to(storage_root)
            except ValueError:
                relative = Path(source.name)

            trash_dest = storage_root / "_trash" / relative
            await aiofiles.os.makedirs(trash_dest.parent, exist_ok=True)

            # Handle collision
            dest = trash_dest
            counter = 1
            while dest.exists():
                dest = trash_dest.with_name(f"{trash_dest.stem}_{counter}{trash_dest.suffix}")
                counter += 1

            await aiofiles.os.rename(source, dest)
            # Clean up empty parent directory
            await self._pm.delete_folder_if_empty(source.parent)
            logger.info("Moved file to trash: %s -> %s", source, dest)
            return dest

    async def restore_from_trash(
        self,
        trash_path: Path | str,
        storage_location_id: str | None = None,
    ) -> Path | str | None:
        """Restore a file from _trash back to its original location.

        Returns the restored path, or None if the trash file didn't exist.
        """
        adapter, storage_type, _ = await self._resolve_adapter(storage_location_id)

        if storage_type == "cloud":
            old_path = str(trash_path)
            if old_path.startswith("_trash/"):
                new_path = old_path[len("_trash/"):]
            else:
                new_path = old_path
            try:
                content = await adapter.read_file(old_path)
                parts = new_path.rsplit("/", 1)
                if len(parts) == 2:
                    await adapter.ensure_directory(parts[0])
                await adapter.write_file(new_path, content)
                await adapter.delete_file(old_path)
                logger.info("Restored cloud file from trash: %s -> %s", old_path, new_path)
                return new_path
            except Exception as e:
                logger.warning("Failed to restore cloud file from trash %s: %s", old_path, e)
                return None
        else:
            storage_root = await get_active_storage_path()
            if storage_root is None:
                storage_root = self.media_dir

            source = Path(trash_path)
            if not source.exists() and not source.is_absolute():
                source = self.get_full_path(str(trash_path))

            if not source.exists():
                logger.warning("Trash file not found for restore: %s", source)
                return None

            # Compute the original location by stripping _trash/ prefix
            trash_root = storage_root / "_trash"
            try:
                relative = source.relative_to(trash_root)
            except ValueError:
                # File was never moved to _trash — it's still at its original location
                logger.info("File not in _trash, no restore needed: %s", source)
                return source

            restored_dest = storage_root / relative
            await aiofiles.os.makedirs(restored_dest.parent, exist_ok=True)

            # Handle collision
            dest = restored_dest
            counter = 1
            while dest.exists():
                dest = restored_dest.with_stem(f"{restored_dest.stem}_{counter}")
                counter += 1

            await aiofiles.os.rename(source, dest)
            await self._pm.delete_folder_if_empty(source.parent)
            logger.info("Restored file from trash: %s -> %s", source, dest)
            return dest

    async def get_file_size(self, file_path: Path | str) -> int | None:
        """Get the size of a file in bytes.

        Args:
            file_path: Path to the file (Path for local, string for cloud).

        Returns:
            File size in bytes, or None if file doesn't exist.
        """
        adapter, storage_type, _ = await self._get_adapter_info()

        if storage_type == "cloud":
            # For cloud storage, use adapter's get_file_info
            relative_path = str(file_path)
            try:
                info = await adapter.get_file_info(relative_path)
                return info.get("size") if info else None
            except Exception as e:
                logger.error(f"Failed to get cloud file size for {relative_path}: {e}")
                return None
        else:
            # Local storage
            path = Path(file_path)
            try:
                stat_result = await aiofiles.os.stat(path)
                return stat_result.st_size
            except FileNotFoundError:
                return None

    async def file_exists(self, file_path: Path | str) -> bool:
        """Check if a file exists.

        Args:
            file_path: Path to the file (Path for local, string for cloud).

        Returns:
            True if file exists, False otherwise.
        """
        adapter, storage_type, _ = await self._get_adapter_info()

        if storage_type == "cloud":
            # For cloud storage, use adapter's exists method
            relative_path = str(file_path)
            try:
                return await adapter.exists(relative_path)
            except Exception as e:
                logger.error(f"Failed to check cloud file existence for {relative_path}: {e}")
                return False
        else:
            # Local storage
            path = Path(file_path)
            return await aiofiles.os.path.exists(path)

    async def read_file(self, file_path: Path | str) -> bytes | None:
        """Read file content from storage.

        Args:
            file_path: Path to the file (Path for local, string for cloud).

        Returns:
            File content as bytes, or None if file doesn't exist.
        """
        adapter, storage_type, _ = await self._get_adapter_info()

        if storage_type == "cloud":
            # For cloud storage, use adapter's read_file
            relative_path = str(file_path)
            try:
                return await adapter.read_file(relative_path)
            except Exception as e:
                logger.error(f"Failed to read cloud file {relative_path}: {e}")
                return None
        else:
            # Local storage
            path = Path(file_path)
            try:
                async with aiofiles.open(path, "rb") as f:
                    return await f.read()
            except FileNotFoundError:
                return None

    async def save_file(self, relative_path: str, content: bytes) -> Path:
        """Save a file to a relative path under media directory.

        Args:
            relative_path: Relative path like "documents/{id}/{filename}"
            content: File content as bytes.

        Returns:
            Full path where the file was saved.
        """
        # Sanitize path to prevent traversal
        parts = relative_path.replace("\\", "/").split("/")
        safe_parts = []
        for part in parts:
            safe_part = Path(part).name
            if safe_part and safe_part not in (".", ".."):
                safe_parts.append(safe_part)

        if not safe_parts:
            raise ValueError("Invalid file path")

        file_path = self.media_dir.joinpath(*safe_parts)

        # Safety check: ensure within media_dir
        try:
            file_path.resolve().relative_to(self.media_dir.resolve())
        except ValueError:
            raise ValueError("Path traversal detected")

        # Ensure directory exists
        await aiofiles.os.makedirs(file_path.parent, exist_ok=True)

        # Write file
        async with aiofiles.open(file_path, "wb") as f:
            await f.write(content)

        return file_path

    def get_full_path(self, relative_path: str) -> Path:
        """Get full path for a relative path under media directory.

        Args:
            relative_path: Relative path like "documents/{id}/{filename}"

        Returns:
            Full Path object.
        """
        # Sanitize and construct path
        parts = relative_path.replace("\\", "/").split("/")
        safe_parts = []
        for part in parts:
            safe_part = Path(part).name
            if safe_part and safe_part not in (".", ".."):
                safe_parts.append(safe_part)

        if not safe_parts:
            raise ValueError("Invalid file path")

        full_path = self.media_dir.joinpath(*safe_parts)

        # Safety check
        try:
            full_path.resolve().relative_to(self.media_dir.resolve())
        except ValueError:
            raise ValueError("Path traversal detected")

        return full_path

    async def copy_file(self, source_path: Path | str, dest_relative_path: str) -> Path:
        """Copy a file to a new location.

        Args:
            source_path: Full path to source file
            dest_relative_path: Relative destination path like "recordings/{id}/{filename}"

        Returns:
            Full path where the file was copied.
        """
        source = Path(source_path)
        if not source.exists():
            raise FileNotFoundError(f"Source file not found: {source}")

        # Get destination full path using existing method
        dest_path = self.get_full_path(dest_relative_path)

        # Ensure directory exists
        await aiofiles.os.makedirs(dest_path.parent, exist_ok=True)

        # Copy file content
        async with aiofiles.open(source, "rb") as src:
            content = await src.read()
        async with aiofiles.open(dest_path, "wb") as dst:
            await dst.write(content)

        return dest_path

    async def ensure_project_folder(self, project_name: str) -> Path | str:
        """Ensure a project folder exists.

        Args:
            project_name: The project name.

        Returns:
            Path to the project folder (Path for local, string for cloud).
        """
        adapter, storage_type, _ = await self._get_adapter_info()
        safe_name = self._pm.sanitize_name(project_name)

        if storage_type == "cloud":
            await adapter.ensure_directory(safe_name)
            return safe_name
        else:
            storage_root = await get_active_storage_path()
            if storage_root is None:
                storage_root = self.media_dir
            folder_path = storage_root / safe_name
            await self._pm.ensure_folder(folder_path)
            return folder_path

    async def rename_project_folder(self, old_name: str, new_name: str) -> Path | str:
        """Rename a project folder.

        Args:
            old_name: Current project name.
            new_name: New project name.

        Returns:
            New folder path (Path for local, string for cloud).
        """
        adapter, storage_type, _ = await self._get_adapter_info()
        old_safe = self._pm.sanitize_name(old_name)
        new_safe = self._pm.sanitize_name(new_name)

        if storage_type == "cloud":
            # For cloud: list files in old folder, move each to new folder, delete old
            try:
                files = await adapter.list_files(old_safe)
                if files:
                    await adapter.ensure_directory(new_safe)
                    for file_info in files:
                        old_path = file_info.get("path", "")
                        if old_path:
                            filename = old_path.split("/")[-1]
                            new_path = f"{new_safe}/{filename}"
                            content = await adapter.read_file(old_path)
                            await adapter.write_file(new_path, content)
                            await adapter.delete_file(old_path)
                    logger.info(f"Renamed cloud folder from {old_safe} to {new_safe}")
                return new_safe
            except Exception as e:
                logger.warning(f"Could not rename cloud folder {old_safe}: {e}")
                return await self.ensure_project_folder(new_name)
        else:
            storage_root = await get_active_storage_path()
            if storage_root is None:
                storage_root = self.media_dir
            old_path = storage_root / old_safe

            if not old_path.exists():
                # Folder doesn't exist yet, just ensure new one exists
                return await self.ensure_project_folder(new_name)

            return await self._pm.rename_folder(old_path, new_name)

    async def delete_project_folder_if_empty(self, project_name: str) -> bool:
        """Delete a project folder if it's empty.

        Args:
            project_name: The project name.

        Returns:
            True if folder was deleted, False otherwise.
        """
        adapter, storage_type, _ = await self._get_adapter_info()
        safe_name = self._pm.sanitize_name(project_name)

        if storage_type == "cloud":
            # For cloud: use adapter's delete_folder method
            try:
                if hasattr(adapter, "delete_folder"):
                    return await adapter.delete_folder(safe_name, delete_contents=False)
                else:
                    # Fallback for adapters without delete_folder
                    files = await adapter.list_files(safe_name)
                    if not files:
                        logger.info(f"Cloud folder {safe_name} is empty")
                        return True
                    return False
            except Exception as e:
                logger.warning(f"Could not delete cloud folder {safe_name}: {e}")
                return False
        else:
            storage_root = await get_active_storage_path()
            if storage_root is None:
                storage_root = self.media_dir
            folder_path = storage_root / safe_name
            return await self._pm.delete_folder_if_empty(folder_path)

    async def delete_project_folder(
        self, project_name: str, delete_contents: bool = False
    ) -> bool:
        """Delete a project folder.

        Args:
            project_name: The project name.
            delete_contents: If True, delete folder and all contents.
                           If False, only delete if empty.

        Returns:
            True if folder was deleted, False otherwise.
        """
        adapter, storage_type, _ = await self._get_adapter_info()
        safe_name = self._pm.sanitize_name(project_name)

        if storage_type == "cloud":
            try:
                if hasattr(adapter, "delete_folder"):
                    return await adapter.delete_folder(safe_name, delete_contents=delete_contents)
                else:
                    logger.warning(f"Adapter does not support delete_folder")
                    return False
            except Exception as e:
                logger.warning(f"Could not delete cloud folder {safe_name}: {e}")
                return False
        else:
            storage_root = await get_active_storage_path()
            if storage_root is None:
                storage_root = self.media_dir
            folder_path = storage_root / safe_name

            if delete_contents:
                # Delete folder and all contents
                import shutil
                try:
                    if folder_path.exists():
                        shutil.rmtree(folder_path)
                        logger.info(f"Deleted folder with contents: {folder_path}")
                        return True
                    return True
                except Exception as e:
                    logger.warning(f"Could not delete folder {folder_path}: {e}")
                    return False
            else:
                return await self._pm.delete_folder_if_empty(folder_path)

    async def move_project_files_to_root(self, project_name: str) -> int:
        """Move all files from a project folder to the storage root.

        Args:
            project_name: The project name.

        Returns:
            Number of files moved.
        """
        adapter, storage_type, _ = await self._get_adapter_info()
        safe_name = self._pm.sanitize_name(project_name)

        if storage_type == "cloud":
            try:
                if hasattr(adapter, "move_files_to_parent"):
                    return await adapter.move_files_to_parent(safe_name)
                else:
                    logger.warning(f"Adapter does not support move_files_to_parent")
                    return 0
            except Exception as e:
                logger.warning(f"Could not move files from cloud folder {safe_name}: {e}")
                return 0
        else:
            storage_root = await get_active_storage_path()
            if storage_root is None:
                storage_root = self.media_dir
            folder_path = storage_root / safe_name

            moved_count = 0
            if folder_path.exists():
                for item in folder_path.iterdir():
                    if item.is_file():
                        try:
                            new_path = storage_root / item.name
                            # Handle name conflicts
                            counter = 1
                            while new_path.exists():
                                stem = item.stem
                                suffix = item.suffix
                                new_path = storage_root / f"{stem}_{counter}{suffix}"
                                counter += 1
                            item.rename(new_path)
                            moved_count += 1
                        except Exception as e:
                            logger.warning(f"Could not move file {item}: {e}")
            return moved_count


# Default storage service instance
storage_service = StorageService()

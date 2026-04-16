"""Path management service for filesystem-as-UI pattern.

Handles path sanitization, collision detection, and file operations
to keep the filesystem structure in sync with the database.
"""

import asyncio
import re
import shutil
from pathlib import Path

import aiofiles
import aiofiles.os


class PathManager:
    """Manages filesystem paths and operations for the filesystem-as-UI pattern.

    Projects become folders, recordings/documents become files in those folders.
    Files without a project live at the storage root.
    """

    # Characters not allowed in filenames (Windows + macOS + Linux restrictions)
    INVALID_CHARS_PATTERN = re.compile(r'[/\\:*?"<>|]')

    # Max filename length (most filesystems support 255 bytes)
    MAX_NAME_LENGTH = 200  # Leave room for collision suffix

    def sanitize_name(self, name: str) -> str:
        """Sanitize a name for use as a filename or folder name.

        Args:
            name: The raw name (e.g., project name, recording title).

        Returns:
            A sanitized name safe for filesystem use.
        """
        if not name:
            return "untitled"

        # Replace invalid characters with underscore
        sanitized = self.INVALID_CHARS_PATTERN.sub("_", name)

        # Replace multiple consecutive underscores/spaces with single space
        sanitized = re.sub(r"[_\s]+", " ", sanitized)

        # Trim leading/trailing whitespace
        sanitized = sanitized.strip()

        # Don't allow names that are just dots (. and ..)
        if sanitized in (".", ".."):
            sanitized = "untitled"

        # Truncate to max length
        if len(sanitized) > self.MAX_NAME_LENGTH:
            sanitized = sanitized[: self.MAX_NAME_LENGTH].rstrip()

        # Ensure we have something
        if not sanitized:
            sanitized = "untitled"

        return sanitized

    def generate_unique_path(self, parent: Path, desired_name: str) -> Path:
        """Generate a unique path with numeric suffix for collisions.

        Args:
            parent: The parent directory.
            desired_name: The desired filename (including extension).

        Returns:
            A unique path. If desired_name exists, returns name (1).ext, name (2).ext, etc.
        """
        target = parent / desired_name

        if not target.exists():
            return target

        # Split name and extension
        stem = Path(desired_name).stem
        suffix = Path(desired_name).suffix

        # Try numeric suffixes
        counter = 1
        while True:
            new_name = f"{stem} ({counter}){suffix}"
            target = parent / new_name
            if not target.exists():
                return target
            counter += 1
            # Safety limit to prevent infinite loop
            if counter > 10000:
                raise ValueError(f"Too many files with name {stem}")

    def get_item_path(
        self,
        storage_root: Path,
        project_name: str | None,
        title: str,
        extension: str,
    ) -> Path:
        """Get the desired filesystem path for an item.

        Args:
            storage_root: The base storage directory.
            project_name: The project name (None if no project).
            title: The item title.
            extension: The file extension (including dot, e.g., ".mp3").

        Returns:
            The desired path (may need uniqueness check before use).
        """
        safe_title = self.sanitize_name(title)
        filename = f"{safe_title}{extension}"

        if project_name:
            safe_project = self.sanitize_name(project_name)
            return storage_root / safe_project / filename
        else:
            return storage_root / filename

    async def ensure_folder(self, path: Path) -> None:
        """Create a folder if it doesn't exist.

        Args:
            path: The folder path to create.
        """
        await aiofiles.os.makedirs(path, exist_ok=True)

    async def rename_file(self, old_path: Path, new_name: str) -> Path:
        """Rename a file in place, handling collisions.

        Args:
            old_path: Current file path.
            new_name: New filename (including extension).

        Returns:
            The actual new path (may have collision suffix).

        Raises:
            FileNotFoundError: If old_path doesn't exist.
        """
        if not old_path.exists():
            raise FileNotFoundError(f"File not found: {old_path}")

        # Sanitize the new name's stem but preserve extension
        stem = Path(new_name).stem
        extension = Path(new_name).suffix
        safe_name = f"{self.sanitize_name(stem)}{extension}"

        new_path = self.generate_unique_path(old_path.parent, safe_name)

        if new_path != old_path:
            # Use run_in_executor for blocking rename
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, shutil.move, str(old_path), str(new_path))

        return new_path

    async def move_file(self, old_path: Path, new_parent: Path) -> Path:
        """Move a file to a new folder, handling collisions.

        Args:
            old_path: Current file path.
            new_parent: Destination folder.

        Returns:
            The actual new path (may have collision suffix).

        Raises:
            FileNotFoundError: If old_path doesn't exist.
        """
        if not old_path.exists():
            raise FileNotFoundError(f"File not found: {old_path}")

        # Ensure destination folder exists
        await self.ensure_folder(new_parent)

        # Generate unique path in new location
        new_path = self.generate_unique_path(new_parent, old_path.name)

        if new_path != old_path:
            # Use run_in_executor for blocking move
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, shutil.move, str(old_path), str(new_path))

        return new_path

    async def rename_folder(self, old_path: Path, new_name: str) -> Path:
        """Rename a folder, handling collisions.

        Args:
            old_path: Current folder path.
            new_name: New folder name.

        Returns:
            The actual new path (may have collision suffix).

        Raises:
            FileNotFoundError: If old_path doesn't exist.
            NotADirectoryError: If old_path is not a directory.
        """
        if not old_path.exists():
            raise FileNotFoundError(f"Folder not found: {old_path}")

        if not old_path.is_dir():
            raise NotADirectoryError(f"Not a directory: {old_path}")

        safe_name = self.sanitize_name(new_name)
        new_path = self.generate_unique_path(old_path.parent, safe_name)

        if new_path != old_path:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, shutil.move, str(old_path), str(new_path))

        return new_path

    async def delete_folder_if_empty(self, path: Path) -> bool:
        """Delete a folder if it's empty.

        Args:
            path: The folder path.

        Returns:
            True if folder was deleted, False otherwise.
        """
        if not path.exists() or not path.is_dir():
            return False

        try:
            # Check if empty
            if not any(path.iterdir()):
                await aiofiles.os.rmdir(path)
                return True
        except OSError:
            pass

        return False


# Default instance
path_manager = PathManager()

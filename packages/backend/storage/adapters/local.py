# packages/backend/storage/adapters/local.py
"""Local filesystem storage adapter."""

import mimetypes
from datetime import datetime
from pathlib import Path

import aiofiles
import aiofiles.os

from storage.base import StorageAdapter, FileInfo
from storage.exceptions import (
    StorageNotFoundError,
    StoragePermissionError,
    StorageUnavailableError,
)


class LocalAdapter(StorageAdapter):
    """Storage adapter for local filesystem."""

    def __init__(self, config: dict):
        """Initialize with config containing 'path'."""
        self.base_path = Path(config["path"])

    def _resolve_path(self, path: str) -> Path:
        """Resolve relative path to absolute, preventing traversal."""
        if not path:
            return self.base_path
        resolved = (self.base_path / path).resolve()
        if not resolved.is_relative_to(self.base_path.resolve()):
            raise StoragePermissionError(f"Path traversal not allowed: {path}")
        return resolved

    async def test_connection(self) -> bool:
        """Verify base path exists and is writable."""
        if not self.base_path.exists():
            raise StorageUnavailableError(f"Path does not exist: {self.base_path}")
        if not self.base_path.is_dir():
            raise StorageUnavailableError(f"Path is not a directory: {self.base_path}")
        test_file = self.base_path / ".write_test"
        try:
            test_file.touch()
            test_file.unlink()
        except PermissionError:
            raise StoragePermissionError(f"Cannot write to: {self.base_path}")
        return True

    async def list_files(self, path: str = "") -> list[FileInfo]:
        """List files in directory."""
        dir_path = self._resolve_path(path)
        if not dir_path.exists():
            raise StorageNotFoundError(f"Directory not found: {path}")
        if not dir_path.is_dir():
            raise StorageNotFoundError(f"Not a directory: {path}")

        files = []
        for item in dir_path.iterdir():
            stat = item.stat()
            mime_type = None
            if item.is_file():
                mime_type, _ = mimetypes.guess_type(str(item))
            files.append(FileInfo(
                name=item.name,
                path=str(item.relative_to(self.base_path)),
                size=stat.st_size if item.is_file() else 0,
                is_directory=item.is_dir(),
                modified_at=datetime.fromtimestamp(stat.st_mtime),
                mime_type=mime_type,
            ))
        return files

    async def read_file(self, path: str) -> bytes:
        """Read file contents."""
        file_path = self._resolve_path(path)
        if not file_path.exists():
            raise StorageNotFoundError(f"File not found: {path}")
        if not file_path.is_file():
            raise StorageNotFoundError(f"Not a file: {path}")
        async with aiofiles.open(file_path, "rb") as f:
            return await f.read()

    async def write_file(self, path: str, data: bytes) -> None:
        """Write data to file, creating directories as needed."""
        file_path = self._resolve_path(path)
        await aiofiles.os.makedirs(file_path.parent, exist_ok=True)
        async with aiofiles.open(file_path, "wb") as f:
            await f.write(data)

    async def delete_file(self, path: str) -> None:
        """Delete a file."""
        file_path = self._resolve_path(path)
        if not file_path.exists():
            raise StorageNotFoundError(f"File not found: {path}")
        await aiofiles.os.remove(file_path)

    async def exists(self, path: str) -> bool:
        """Check if path exists."""
        return self._resolve_path(path).exists()

    async def get_file_info(self, path: str) -> FileInfo:
        """Get file metadata."""
        file_path = self._resolve_path(path)
        if not file_path.exists():
            raise StorageNotFoundError(f"File not found: {path}")
        stat = file_path.stat()
        mime_type = None
        if file_path.is_file():
            mime_type, _ = mimetypes.guess_type(str(file_path))
        return FileInfo(
            name=file_path.name,
            path=path,
            size=stat.st_size if file_path.is_file() else 0,
            is_directory=file_path.is_dir(),
            modified_at=datetime.fromtimestamp(stat.st_mtime),
            mime_type=mime_type,
        )

    async def ensure_directory(self, path: str) -> None:
        """Create directory and parents."""
        dir_path = self._resolve_path(path)
        await aiofiles.os.makedirs(dir_path, exist_ok=True)

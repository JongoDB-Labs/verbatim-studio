"""Shared API dependencies."""

from fastapi import Header


async def get_active_project_id(
    x_active_project: str | None = Header(None, alias="X-Active-Project"),
) -> str | None:
    """Extract the active project ID from the X-Active-Project header.

    Returns None when in 'All Projects' mode (no header or empty value).

    .. deprecated::
        Use :func:`get_active_project_ids` instead, which supports
        comma-separated multi-project selection.
    """
    if x_active_project and x_active_project.strip():
        return x_active_project.strip()
    return None


async def get_active_project_ids(
    x_active_project: str | None = Header(None, alias="X-Active-Project"),
) -> list[str]:
    """Extract active project IDs from the X-Active-Project header.

    Returns empty list when in 'All Projects' mode (no header or empty value).
    Supports comma-separated IDs for multi-project selection.
    """
    if x_active_project and x_active_project.strip():
        return [pid.strip() for pid in x_active_project.split(",") if pid.strip()]
    return []

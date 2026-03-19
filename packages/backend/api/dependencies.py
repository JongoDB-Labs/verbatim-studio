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

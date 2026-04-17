"""Calendar integration API endpoints (read-only)."""

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from services.calendar_integration import CalendarService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/calendar", tags=["calendar"])


async def _get_google_access_token() -> str:
    """Retrieve a valid Google OAuth access_token.

    The frontend stores tokens after the OAuth flow completes.  Here we
    check the in-memory OAuth state for a completed Google flow.  If no
    token is available we raise 401.
    """
    from services.oauth import oauth_states

    # Look for a completed Google OAuth session with tokens
    for _state, info in oauth_states.items():
        if (
            info.get("provider") == "gdrive"
            and info.get("status") == "complete"
            and info.get("tokens", {}).get("access_token")
        ):
            return info["tokens"]["access_token"]

    # Return 422 (not 401) — missing OAuth is a configuration issue, not
    # an authentication failure.  A 401 here triggers the global auth
    # interceptor in the frontend and shows the enterprise login page.
    raise HTTPException(
        status_code=422,
        detail="No Google OAuth credentials configured. "
        "Please connect your Google account in Settings > Integrations.",
    )


@router.get("/events")
async def get_upcoming_events(
    max_results: int = Query(default=10, ge=1, le=50, description="Max events to return"),
    access_token: str | None = Query(default=None, description="Google OAuth access token (optional override)"),
) -> dict[str, Any]:
    """Fetch upcoming meetings from Google Calendar.

    Returns a list of upcoming events with meeting links, attendees,
    and other metadata.  Requires a valid Google OAuth token with
    ``calendar.events.readonly`` scope.
    """
    token = access_token or await _get_google_access_token()

    svc = CalendarService(token)

    try:
        events = await svc.get_upcoming_events(max_results=max_results)
    except PermissionError:
        raise HTTPException(
            status_code=422,
            detail="Google OAuth token is expired or invalid. Please re-authenticate.",
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    return {
        "events": [e.to_dict() for e in events],
        "count": len(events),
    }

"""Google Calendar read-only integration.

Fetches upcoming meetings from the user's primary Google Calendar
using the Calendar API v3.  Only requires an OAuth access_token with
the ``calendar.events.readonly`` scope.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

import httpx

logger = logging.getLogger(__name__)

GOOGLE_CALENDAR_API = "https://www.googleapis.com/calendar/v3/calendars/primary/events"


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class CalendarEvent:
    id: str
    title: str
    start_time: datetime | None
    end_time: datetime | None
    meeting_url: str | None = None
    has_video_link: bool = False
    attendees: list[str] | None = None
    description: str | None = None

    def to_dict(self) -> dict:
        """Serialise for JSON responses."""
        d = asdict(self)
        # ISO-format datetimes for JSON
        for key in ("start_time", "end_time"):
            val = d.get(key)
            if isinstance(val, datetime):
                d[key] = val.isoformat()
        return d


# ---------------------------------------------------------------------------
# Pure parser
# ---------------------------------------------------------------------------

def _parse_datetime(dt_dict: dict | None) -> datetime | None:
    """Parse a Google Calendar start/end dict into a datetime.

    Google returns either ``{"dateTime": "..."}`` (timed event) or
    ``{"date": "YYYY-MM-DD"}`` (all-day event).
    """
    if dt_dict is None:
        return None
    if "dateTime" in dt_dict:
        raw = dt_dict["dateTime"]
        return datetime.fromisoformat(raw)
    if "date" in dt_dict:
        # All-day event — treat as midnight UTC
        return datetime.strptime(dt_dict["date"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return None


def _extract_meeting_url(event: dict) -> str | None:
    """Extract a video meeting URL from the event.

    Priority: hangoutLink (Google Meet) > conferenceData entryPoints (Zoom, Teams, etc).
    """
    # Google Meet link
    hangout = event.get("hangoutLink")
    if hangout:
        return hangout

    # Generic conference entry points (Zoom, Teams, Webex, ...)
    conf = event.get("conferenceData") or {}
    for ep in conf.get("entryPoints", []):
        if ep.get("entryPointType") == "video":
            return ep.get("uri")

    return None


def parse_google_events(
    raw_events: list[dict],
    after: datetime | None = None,
) -> list[CalendarEvent]:
    """Convert raw Google Calendar API event dicts to CalendarEvent objects.

    Args:
        raw_events: List of event resources from the Calendar API.
        after: If provided, exclude events whose start_time is before this.

    Returns:
        Parsed and (optionally) filtered list of CalendarEvent.
    """
    results: list[CalendarEvent] = []
    for raw in raw_events:
        start = _parse_datetime(raw.get("start"))
        end = _parse_datetime(raw.get("end"))

        # Filter out events before the cutoff
        if after is not None and start is not None:
            # Make comparison timezone-aware
            cmp_after = after if after.tzinfo else after.replace(tzinfo=timezone.utc)
            cmp_start = start if start.tzinfo else start.replace(tzinfo=timezone.utc)
            if cmp_start < cmp_after:
                continue

        meeting_url = _extract_meeting_url(raw)

        # Attendees: prefer displayName, fall back to email
        raw_attendees = raw.get("attendees")
        attendees: list[str] | None = None
        if raw_attendees:
            attendees = [
                a.get("displayName") or a.get("email", "")
                for a in raw_attendees
            ]

        results.append(CalendarEvent(
            id=raw.get("id", ""),
            title=raw.get("summary", "(No title)"),
            start_time=start,
            end_time=end,
            meeting_url=meeting_url,
            has_video_link=meeting_url is not None,
            attendees=attendees,
            description=raw.get("description"),
        ))

    return results


# ---------------------------------------------------------------------------
# Async service
# ---------------------------------------------------------------------------

class CalendarService:
    """Read-only Google Calendar client."""

    def __init__(self, access_token: str):
        self._token = access_token

    async def get_upcoming_events(
        self,
        max_results: int = 10,
        time_min: datetime | None = None,
    ) -> list[CalendarEvent]:
        """Fetch upcoming events from the user's primary calendar.

        Args:
            max_results: Maximum number of events to return.
            time_min: Only return events starting at or after this time.
                      Defaults to *now* (UTC).

        Returns:
            List of parsed CalendarEvent objects.
        """
        if time_min is None:
            time_min = datetime.now(timezone.utc)

        params = {
            "timeMin": time_min.isoformat(),
            "maxResults": max_results,
            "singleEvents": "true",
            "orderBy": "startTime",
        }

        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/json",
        }

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(GOOGLE_CALENDAR_API, params=params, headers=headers)

        if resp.status_code == 401:
            logger.warning("Google Calendar API returned 401 — token may be expired")
            raise PermissionError("Google OAuth token is expired or invalid")

        if resp.status_code != 200:
            logger.error(
                "Google Calendar API error %d: %s",
                resp.status_code,
                resp.text[:500],
            )
            raise RuntimeError(
                f"Google Calendar API returned {resp.status_code}"
            )

        data = resp.json()
        raw_events = data.get("items", [])
        return parse_google_events(raw_events)

"""Tests for Google Calendar integration — parse_google_events pure function."""

from datetime import datetime, timezone

from services.calendar_integration import CalendarEvent, parse_google_events


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _event(
    event_id: str = "evt1",
    summary: str = "Team Standup",
    start_dt: str | None = "2026-04-18T09:00:00-04:00",
    end_dt: str | None = "2026-04-18T09:30:00-04:00",
    all_day_start: str | None = None,
    all_day_end: str | None = None,
    hangout_link: str | None = None,
    conference_data: dict | None = None,
    attendees: list[dict] | None = None,
    description: str | None = None,
) -> dict:
    """Build a raw Google Calendar API event dict."""
    evt: dict = {"id": event_id, "summary": summary}
    if start_dt:
        evt["start"] = {"dateTime": start_dt}
    elif all_day_start:
        evt["start"] = {"date": all_day_start}
    if end_dt:
        evt["end"] = {"dateTime": end_dt}
    elif all_day_end:
        evt["end"] = {"date": all_day_end}
    if hangout_link:
        evt["hangoutLink"] = hangout_link
    if conference_data:
        evt["conferenceData"] = conference_data
    if attendees:
        evt["attendees"] = attendees
    if description is not None:
        evt["description"] = description
    return evt


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestParseGoogleEvents:
    """Tests for the pure parse_google_events function."""

    def test_parses_event_with_datetime(self):
        raw = [_event()]
        result = parse_google_events(raw)
        assert len(result) == 1
        evt = result[0]
        assert isinstance(evt, CalendarEvent)
        assert evt.id == "evt1"
        assert evt.title == "Team Standup"
        assert evt.start_time is not None
        assert evt.start_time.year == 2026
        assert evt.start_time.month == 4
        assert evt.start_time.day == 18

    def test_parses_all_day_event(self):
        raw = [_event(
            start_dt=None, end_dt=None,
            all_day_start="2026-04-20", all_day_end="2026-04-21",
        )]
        result = parse_google_events(raw)
        assert len(result) == 1
        evt = result[0]
        assert evt.start_time is not None
        assert evt.start_time.hour == 0
        assert evt.start_time.minute == 0

    def test_extracts_google_meet_link(self):
        raw = [_event(hangout_link="https://meet.google.com/abc-defg-hij")]
        result = parse_google_events(raw)
        assert len(result) == 1
        evt = result[0]
        assert evt.meeting_url == "https://meet.google.com/abc-defg-hij"
        assert evt.has_video_link is True

    def test_no_video_link(self):
        raw = [_event()]
        result = parse_google_events(raw)
        evt = result[0]
        assert evt.meeting_url is None
        assert evt.has_video_link is False

    def test_extracts_zoom_from_conference_data(self):
        conf = {
            "entryPoints": [
                {"entryPointType": "phone", "uri": "tel:+1234567890"},
                {"entryPointType": "video", "uri": "https://zoom.us/j/123456789"},
            ]
        }
        raw = [_event(conference_data=conf)]
        result = parse_google_events(raw)
        evt = result[0]
        assert evt.meeting_url == "https://zoom.us/j/123456789"
        assert evt.has_video_link is True

    def test_extracts_teams_from_conference_data(self):
        conf = {
            "entryPoints": [
                {"entryPointType": "video", "uri": "https://teams.microsoft.com/l/meetup-join/abc"},
            ]
        }
        raw = [_event(conference_data=conf)]
        result = parse_google_events(raw)
        evt = result[0]
        assert evt.meeting_url == "https://teams.microsoft.com/l/meetup-join/abc"
        assert evt.has_video_link is True

    def test_hangout_link_takes_precedence_over_conference_data(self):
        conf = {
            "entryPoints": [
                {"entryPointType": "video", "uri": "https://zoom.us/j/999"},
            ]
        }
        raw = [_event(
            hangout_link="https://meet.google.com/abc",
            conference_data=conf,
        )]
        result = parse_google_events(raw)
        evt = result[0]
        # hangoutLink is the primary Google Meet link
        assert evt.meeting_url == "https://meet.google.com/abc"

    def test_extracts_attendees_with_display_name(self):
        attendees = [
            {"displayName": "Alice", "email": "alice@example.com"},
            {"email": "bob@example.com"},
        ]
        raw = [_event(attendees=attendees)]
        result = parse_google_events(raw)
        evt = result[0]
        assert evt.attendees == ["Alice", "bob@example.com"]

    def test_filters_past_events(self):
        past = _event(
            event_id="past",
            start_dt="2020-01-01T09:00:00Z",
            end_dt="2020-01-01T10:00:00Z",
        )
        future = _event(
            event_id="future",
            start_dt="2030-06-15T14:00:00Z",
            end_dt="2030-06-15T15:00:00Z",
        )
        after = datetime(2025, 1, 1, tzinfo=timezone.utc)
        result = parse_google_events([past, future], after=after)
        assert len(result) == 1
        assert result[0].id == "future"

    def test_empty_list_returns_empty(self):
        result = parse_google_events([])
        assert result == []

    def test_missing_summary_uses_no_title(self):
        raw = [{"id": "e1", "start": {"dateTime": "2026-04-18T10:00:00Z"}}]
        result = parse_google_events(raw)
        assert len(result) == 1
        assert result[0].title == "(No title)"

    def test_missing_start_end_handled_gracefully(self):
        raw = [{"id": "e2", "summary": "Broken event"}]
        result = parse_google_events(raw)
        assert len(result) == 1
        assert result[0].start_time is None
        assert result[0].end_time is None

    def test_description_is_captured(self):
        raw = [_event(description="Discuss Q2 roadmap")]
        result = parse_google_events(raw)
        assert result[0].description == "Discuss Q2 roadmap"

    def test_conference_data_without_entry_points(self):
        conf = {"conferenceSolution": {"name": "Zoom"}}
        raw = [_event(conference_data=conf)]
        result = parse_google_events(raw)
        assert result[0].meeting_url is None
        assert result[0].has_video_link is False

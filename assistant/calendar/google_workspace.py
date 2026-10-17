"""Read-only Google Calendar adapter over the bundled Workspace client.

The child process bounds SDK/auth latency and isolates the skill's import-time
profile globals. No discovery of accounts, setup, installs or event writes.
Existing Workspace OAuth refresh may update its own token file.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, UTC
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from .provider import (
    CalendarEvent, CalendarProviderError, CalendarQuery, CalendarSnapshot,
    CalendarSourceMetadata, Freshness,
)

_MAX_BYTES = 2 * 1024 * 1024


def _load_workspace_client():
    import hermes_constants
    from hermes_constants import get_bundled_skills_dir

    path = get_bundled_skills_dir(Path(hermes_constants.__file__).resolve().parent / "skills") / "productivity/google-workspace/scripts/google_api.py"
    spec = importlib.util.spec_from_file_location("_calendar_workspace_client", path)
    if spec is None or spec.loader is None:
        raise CalendarProviderError("calendar unavailable: bundled Workspace client missing")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _list_events(calendar_id: str, query: CalendarQuery) -> dict[str, Any]:
    """Execute only events.list through the existing authenticated SDK seam."""
    client = _load_workspace_client()
    service = client.build_service("calendar", "v3")
    try:
        result = service.events().list(
            calendarId=calendar_id,
            timeMin=query.start.isoformat(),
            timeMax=query.end.isoformat(),
            maxResults=query.max_results,
            singleEvents=True,
            orderBy="startTime",
            showDeleted=False,
            fields="items(id,summary,start,end,location,status),timeZone,nextPageToken",
        ).execute(num_retries=0)
        # A partial page is not a complete snapshot. No hidden auto-pagination
        # (or unbounded latency) in the fast Photon context path.
        if not isinstance(result, dict) or result.get("nextPageToken"):
            raise CalendarProviderError("calendar unavailable: incomplete event window")
        return result
    finally:
        service.close()


def _boundary(value: Any, timezone_name: str) -> datetime:
    if not isinstance(value, dict):
        raise ValueError("invalid event boundary")
    if "dateTime" in value:
        parsed = datetime.fromisoformat(value["dateTime"])
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("event datetime lacks timezone")
        return parsed
    # Google all-day end dates are exclusive, in the calendar's timezone.
    raw = value["date"]
    parsed = datetime.strptime(raw, "%Y-%m-%d")
    return parsed.replace(tzinfo=ZoneInfo(timezone_name))


def _normalize(payload: Any, query: CalendarQuery, calendar_id: str) -> CalendarSnapshot:
    try:
        if not isinstance(payload, dict) or payload.get("nextPageToken") or "error" in payload:
            raise ValueError("incomplete response")
        items = payload["items"]
        if not isinstance(items, list) or len(items) > query.max_results:
            raise ValueError("invalid items")
        timezone_name = payload["timeZone"]
        ZoneInfo(timezone_name)
        events = []
        for item in items:
            if item.get("status") == "cancelled":
                continue
            event_id = item["id"]
            title = item.get("summary", "(no title)")
            location = item.get("location")
            if not isinstance(event_id, str) or not isinstance(title, str):
                raise ValueError("invalid event text")
            if location is not None and not isinstance(location, str):
                raise ValueError("invalid location")
            event = CalendarEvent(
                event_id=event_id, title=title,
                start=_boundary(item["start"], timezone_name),
                end=_boundary(item["end"], timezone_name),
                timezone_name=timezone_name, location=location,
            )
            if event.end > query.start and event.start < query.end:
                events.append(event)
        return CalendarSnapshot(
            events=tuple(sorted(events, key=lambda event: (event.start, event.event_id))),
            query=query,
            source=CalendarSourceMetadata(
                source=f"google-workspace/calendar:{calendar_id}",
                fetched_at=datetime.now(UTC),
                freshness=Freshness.LIVE, is_live=True,
            ),
        )
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise CalendarProviderError("calendar unavailable: invalid Workspace response") from exc


@dataclass(frozen=True)
class GoogleWorkspaceCalendarProvider:
    """One explicitly selected calendar using the active profile's existing OAuth."""

    scope: Any
    profile_home: Path
    calendar_id: str

    def upcoming(self, query: CalendarQuery) -> CalendarSnapshot:
        from hermes_constants import get_hermes_home
        from .configured import _calendar_section, _scope_is_granted

        # Do not retain authority when a provider survives a profile switch or
        # a trusted configuration edit between resolution and execution.
        section = _calendar_section()
        if (
            get_hermes_home().resolve() != self.profile_home.resolve()
            or not isinstance(section, Mapping)
            or section.get("enabled") is not True
            or section.get("approval_granted") is not True
            or not _scope_is_granted(section, self.scope)
            or section.get("provider") != "google_workspace"
            or section.get("calendar_id") != self.calendar_id
        ):
            raise CalendarProviderError("calendar access is disabled or approval changed")
        if (
            type(query.max_results) is not int or not 0 < query.max_results <= 1000
            or query.end - query.start > timedelta(days=31)
        ):
            raise CalendarProviderError("calendar unavailable: query exceeds read bounds")
        request = {
            "calendar_id": self.calendar_id,
            "chat_id": self.scope.chat_id, "user_id": self.scope.user_id,
            "start": query.start.isoformat(), "end": query.end.isoformat(),
            "max_results": query.max_results,
        }
        env = os.environ.copy()
        env["HERMES_HOME"] = str(self.profile_home)
        try:
            result = subprocess.run(
                [sys.executable, "-m", "assistant.calendar.google_workspace"],
                input=json.dumps(request), capture_output=True, text=True,
                encoding="utf-8", env=env, timeout=8, check=False,
                cwd=Path(__file__).resolve().parents[2],
            )
            if result.returncode or len(result.stdout.encode("utf-8")) > _MAX_BYTES:
                raise CalendarProviderError("calendar unavailable: Workspace read failed or incomplete")
            payload = json.loads(result.stdout)
        except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
            # Never forward SDK errors, stderr, credentials, or partial events.
            raise CalendarProviderError("calendar unavailable: Workspace read failed or timed out") from exc
        return _normalize(payload, query, self.calendar_id)


def _main() -> int:
    try:
        request = json.loads(sys.stdin.read(8192))
        query = CalendarQuery(
            datetime.fromisoformat(request["start"]),
            datetime.fromisoformat(request["end"]), request["max_results"],
        )
        from ..coordinator import PhotonConversationScope
        from .configured import resolve_calendar_access
        from .google_workspace import GoogleWorkspaceCalendarProvider as Provider

        policy, provider = resolve_calendar_access(PhotonConversationScope(
            chat_id=request["chat_id"], user_id=request["user_id"],
        ))
        if (
            not policy.allows_read
            or not isinstance(provider, Provider)
            or provider.calendar_id != request["calendar_id"]
            or type(query.max_results) is not int or not 0 < query.max_results <= 1000
            or query.end - query.start > timedelta(days=31)
        ):
            raise CalendarProviderError("calendar access is disabled or query exceeds bounds")
        result = _list_events(request["calendar_id"], query)
        # Google omits items for a successful empty calendar response.
        result.setdefault("items", [])
        print(json.dumps(result))
        return 0
    except (Exception, SystemExit):
        print("calendar unavailable: existing Workspace client/auth required", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(_main())

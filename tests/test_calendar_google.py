"""Offline production-path contracts; never use real OAuth or calendar data."""
import json
import subprocess
import sys
from datetime import datetime, timedelta, UTC
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import Mock

import pytest

from assistant.calendar import CalendarProviderError, CalendarQuery, Freshness, resolve_calendar_access
from assistant.calendar import google_workspace as google
from assistant import PhotonConversationScope

SCOPE = PhotonConversationScope(chat_id="any;-;+15555550123", user_id="test-user")
START = datetime(2026, 10, 12, tzinfo=UTC)
QUERY = CalendarQuery(START, START + timedelta(days=2))
PAYLOAD = {"timeZone": "America/New_York", "items": [
    {"id": "all-day", "summary": "Fictional day", "start": {"date": "2026-10-12"},
     "end": {"date": "2026-10-13"}},
    {"id": "timed", "summary": "Fictional call", "start": {"dateTime": "2026-10-13T09:00:00-04:00"},
     "end": {"dateTime": "2026-10-13T10:00:00-04:00"}},
]}


@pytest.fixture
def configured(tmp_path, monkeypatch):
    home = tmp_path / "profile"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    section = {
        "enabled": True, "approval_granted": True, "provider": "google_workspace",
        "calendar_id": "fictional@example.invalid",
        "approved_scopes": [{"chat_id": SCOPE.chat_id, "user_id": SCOPE.user_id}],
    }

    def save(**changes):
        section.update(changes)
        (home / "config.yaml").write_text(json.dumps({
            "platforms": {"photon": {"extra": {"calendar": section}}},
        }))
    save()
    return home, save


@pytest.mark.parametrize("changes", [
    {"enabled": False}, {"enabled": "true"}, {"approval_granted": False},
    {"approved_scopes": [{"chat_id": SCOPE.chat_id, "user_id": "another-user"}]},
    {"approved_scopes": [{"chat_id": "*", "user_id": SCOPE.user_id}]},
    {"provider": "unknown"}, {"calendar_id": ""},
])
def test_resolver_never_launches_for_denied_or_invalid_config(configured, monkeypatch, changes):
    launch = Mock(side_effect=AssertionError("must not launch"))
    monkeypatch.setattr(google.subprocess, "run", launch)
    _, save = configured
    save(**changes)
    _, provider = resolve_calendar_access(SCOPE)
    assert provider is None
    launch.assert_not_called()


def test_real_child_protocol_with_synthetic_workspace_client(configured, tmp_path, monkeypatch):
    home, _ = configured
    bundle = tmp_path / "skills"
    script = bundle / "productivity/google-workspace/scripts/google_api.py"
    script.parent.mkdir(parents=True)
    # No real token file or service endpoint. Exercise real module loading,
    # subprocess protocol, bound profile, query contract, and normalization.
    script.write_text(f'''import os
class Service:
    def events(self): return self
    def list(self, **params):
        assert os.environ["HERMES_HOME"] == {str(home.resolve())!r}
        assert params["calendarId"] == "fictional@example.invalid"
        assert params["singleEvents"] is True and params["showDeleted"] is False
        assert params["orderBy"] == "startTime" and params["maxResults"] == 100
        assert params["timeMin"] == {QUERY.start.isoformat()!r}
        assert params["timeMax"] == {QUERY.end.isoformat()!r}
        return self
    def execute(self, num_retries):
        assert num_retries == 0
        return {PAYLOAD!r}
    def close(self): pass
def build_service(api, version):
    assert (api, version) == ("calendar", "v3")
    return Service()
''')
    monkeypatch.setenv("HERMES_BUNDLED_SKILLS", str(bundle))
    policy, provider = resolve_calendar_access(SCOPE)
    assert policy.allows_read
    result = provider.upcoming(QUERY)
    assert [event.event_id for event in result.events] == ["all-day", "timed"]
    assert result.events[0].start.hour == 4
    assert result.events[0].end == START + timedelta(days=1, hours=4)
    assert result.source.freshness is Freshness.LIVE
    assert result.source.is_live
    assert result.source.source == "google-workspace/calendar:fictional@example.invalid"
    assert not (home / "google_token.json").exists()


def test_existing_bundled_workspace_sdk_seam(monkeypatch):
    monkeypatch.setenv("HERMES_BUNDLED_SKILLS", str(Path(__import__("hermes_constants").__file__).resolve().parent / "skills"))
    client = google._load_workspace_client()
    credentials = object()
    service = Mock()
    service.events.return_value.list.return_value.execute.return_value = PAYLOAD
    build = Mock(return_value=service)
    monkeypatch.setitem(sys.modules, "googleapiclient.discovery", SimpleNamespace(build=build))
    monkeypatch.setattr(client, "get_credentials", lambda: credentials)
    monkeypatch.setattr(google, "_load_workspace_client", lambda: client)
    assert google._list_events("fictional-calendar", QUERY) == PAYLOAD
    build.assert_called_once_with("calendar", "v3", credentials=credentials)
    service.events.return_value.list.return_value.execute.assert_called_once_with(num_retries=0)
    service.close.assert_called_once()


@pytest.mark.parametrize("mode", ["revoke", "profile"])
def test_stale_provider_denied_before_io(configured, tmp_path, monkeypatch, mode):
    _, save = configured
    _, provider = resolve_calendar_access(SCOPE)
    if mode == "revoke":
        save(approval_granted=False)
    else:
        other = tmp_path / "other"
        other.mkdir()
        monkeypatch.setenv("HERMES_HOME", str(other))
    launch = Mock(side_effect=AssertionError("must not launch"))
    monkeypatch.setattr(google.subprocess, "run", launch)
    with pytest.raises(CalendarProviderError, match="approval changed"):
        provider.upcoming(QUERY)
    launch.assert_not_called()


@pytest.mark.parametrize("payload", [
    {**PAYLOAD, "nextPageToken": "more"}, {"error": "private provider text"},
    {"timeZone": "Bad/Zone", "items": []},
    {"timeZone": "UTC", "items": [{"id": "broken"}]},
])
def test_malformed_or_partial_results_unavailable(payload):
    with pytest.raises(CalendarProviderError, match="invalid Workspace response"):
        google._normalize(payload, QUERY, "fictional-calendar")


def test_timeout_is_sanitized_and_no_retry(configured, monkeypatch):
    _, provider = resolve_calendar_access(SCOPE)
    launch = Mock(side_effect=subprocess.TimeoutExpired("private-command", 8, output="private-data"))
    monkeypatch.setattr(google.subprocess, "run", launch)
    with pytest.raises(CalendarProviderError) as exc:
        provider.upcoming(QUERY)
    assert "private" not in str(exc.value)
    launch.assert_called_once()
    assert launch.call_args.kwargs["timeout"] == 8


def test_child_entry_refuses_unapproved_scope(configured, monkeypatch):
    import io

    request = {
        "calendar_id": "fictional@example.invalid", "chat_id": SCOPE.chat_id,
        "user_id": "unapproved-user", "start": QUERY.start.isoformat(),
        "end": QUERY.end.isoformat(), "max_results": 100,
    }
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(request)))
    listing = Mock(side_effect=AssertionError("must not read account"))
    monkeypatch.setattr(google, "_list_events", listing)
    assert google._main() == 1
    listing.assert_not_called()


def test_empty_live_snapshot_and_oversized_query(configured, monkeypatch):
    result = google._normalize({"items": [], "timeZone": "UTC"}, QUERY, "fictional-calendar")
    assert result.events == () and result.source.is_live
    _, provider = resolve_calendar_access(SCOPE)
    launch = Mock(side_effect=AssertionError("must not launch"))
    monkeypatch.setattr(google.subprocess, "run", launch)
    with pytest.raises(CalendarProviderError, match="bounds"):
        provider.upcoming(CalendarQuery(START, START + timedelta(days=32)))
    launch.assert_not_called()

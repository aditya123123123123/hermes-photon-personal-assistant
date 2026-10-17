"""Behavior contracts for the profile-local offline Photon calendar source."""

import json
from datetime import datetime, timedelta, UTC

import pytest

from assistant.calendar import (
    CachedJsonCalendarProvider,
    CalendarAccessPolicy,
    CalendarProviderError,
    CalendarQuery,
    Freshness,
    resolve_calendar_access,
)
from assistant import PhotonConversationScope

NOW = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)
SCOPE = PhotonConversationScope(chat_id="any;-;+15555555555", user_id="user-1")
OTHER_SCOPE = PhotonConversationScope(chat_id="any;-;+19999999999", user_id="user-1")


def _write_config(home, calendar):
    (home / "config.yaml").write_text(
        json.dumps({"platforms": {"photon": {"extra": {"calendar": calendar}}}}),
        encoding="utf-8",
    )


def _write_snapshot(path, *, fetched_at=NOW - timedelta(minutes=5), **metadata):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({
            "metadata": {
                "source": "offline-export",
                "fetched_at": fetched_at.isoformat() if isinstance(fetched_at, datetime) else fetched_at,
                "coverage_start": "2026-10-12T00:00:00Z",
                "coverage_end": "2026-10-13T00:00:00Z",
                "freshness": "cached",
                "is_live": False,
                **metadata,
            },
            "events": [
                {
                    "event_id": "later",
                    "title": "Later",
                    "start": "2026-10-12T15:00:00Z",
                    "end": "2026-10-12T16:00:00Z",
                },
                {
                    "event_id": "inside",
                    "title": "Inside",
                    "start": "2026-10-12T12:30:00Z",
                    "end": "2026-10-12T13:30:00Z",
                },
            ],
        }),
        encoding="utf-8",
    )


def _calendar_config(snapshot, **overrides):
    value = {
        "enabled": True,
        "approval_granted": True,
        "approved_scopes": [{"chat_id": SCOPE.chat_id, "user_id": SCOPE.user_id}],
        "snapshot_path": str(snapshot),
        "max_age_seconds": 3600,
    }
    value.update(overrides)
    return value


def test_cached_json_provider_filters_and_preserves_provenance(tmp_path):
    snapshot_path = tmp_path / "calendar.json"
    _write_snapshot(snapshot_path)
    provider = CachedJsonCalendarProvider(
        snapshot_path,
        max_age_seconds=3600,
        clock=lambda: NOW,
    )

    result = provider.upcoming(CalendarQuery(NOW, NOW + timedelta(hours=2)))

    assert [event.event_id for event in result.events] == ["inside"]
    assert result.source.source == "offline-export"
    assert result.source.fetched_at == NOW - timedelta(minutes=5)
    assert result.source.freshness is Freshness.CACHED
    assert result.source.is_live is False


@pytest.mark.parametrize(
    "metadata, expected",
    [
        ({"freshness": "live"}, "freshness must be cached"),
        ({"is_live": True}, "cannot be marked live"),
        ({"fetched_at": (NOW + timedelta(minutes=1)).isoformat()}, "in the future"),
        ({"fetched_at": (NOW - timedelta(hours=2)).isoformat()}, "stale"),
    ],
)
def test_cached_json_provider_rejects_unusable_provenance(tmp_path, metadata, expected):
    snapshot_path = tmp_path / "calendar.json"
    _write_snapshot(snapshot_path, **metadata)
    provider = CachedJsonCalendarProvider(snapshot_path, max_age_seconds=3600, clock=lambda: NOW)

    with pytest.raises(CalendarProviderError, match=expected):
        provider.upcoming(CalendarQuery(NOW, NOW + timedelta(hours=1)))


def test_resolver_is_disabled_without_exact_profile_scope_and_does_not_read_source(tmp_path, monkeypatch):
    home = tmp_path / "profile"
    home.mkdir()
    snapshot = home / "missing.json"
    _write_config(home, _calendar_config(snapshot))
    monkeypatch.setenv("HERMES_HOME", str(home))

    policy, provider = resolve_calendar_access(OTHER_SCOPE)

    assert policy == CalendarAccessPolicy(enabled=True, approval_granted=False)
    assert provider is None
    assert not snapshot.exists()


def test_resolver_returns_real_offline_provider_after_profile_and_scope_gates(tmp_path, monkeypatch):
    home = tmp_path / "profile"
    home.mkdir()
    snapshot = home / "calendar.json"
    _write_snapshot(snapshot)
    _write_snapshot(snapshot, fetched_at=datetime.now(UTC) - timedelta(seconds=5))
    _write_config(home, _calendar_config("calendar.json", source="ignored-config-label"))
    monkeypatch.setenv("HERMES_HOME", str(home))

    policy, provider = resolve_calendar_access(SCOPE)

    assert policy.allows_read is True
    assert isinstance(provider, CachedJsonCalendarProvider)
    assert provider.path == snapshot
    result = provider.upcoming(CalendarQuery(NOW, NOW + timedelta(hours=2)))
    assert result.source.source == "offline-export"


def test_resolver_rejects_string_booleans_and_missing_bounded_age(tmp_path, monkeypatch):
    home = tmp_path / "profile"
    home.mkdir()
    snapshot = home / "calendar.json"
    _write_snapshot(snapshot)
    monkeypatch.setenv("HERMES_HOME", str(home))

    _write_config(home, _calendar_config(snapshot, enabled="true"))
    policy, provider = resolve_calendar_access(SCOPE)
    assert policy == CalendarAccessPolicy()
    assert provider is None

    _write_config(home, _calendar_config(snapshot, max_age_seconds=None))
    policy, provider = resolve_calendar_access(SCOPE)
    assert policy == CalendarAccessPolicy(enabled=True, approval_granted=True)
    assert provider is None

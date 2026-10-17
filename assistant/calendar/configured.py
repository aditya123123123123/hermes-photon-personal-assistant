"""Profile-local approval gates for cached and live read-only calendar sources.

Provider selection, paths and approvals come only from trusted configuration,
never from conversation text. Missing settings leave account access disabled.
"""

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from datetime import datetime, timedelta, UTC
from pathlib import Path
from typing import Any, Callable, Mapping

from ..calendar.provider import (
    CalendarAccessPolicy,
    CalendarEvent,
    CalendarProvider,
    CalendarProviderError,
    CalendarQuery,
    CalendarSnapshot,
    CalendarSourceMetadata,
    Freshness,
)


_MAX_AGE_SECONDS = 7 * 24 * 60 * 60
_MAX_SNAPSHOT_BYTES = 8 * 1024 * 1024


def _parse_datetime(value: Any, field_name: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise CalendarProviderError(f"calendar snapshot unavailable: {field_name} is required")
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError as exc:
        raise CalendarProviderError(
            f"calendar snapshot unavailable: invalid {field_name}"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise CalendarProviderError(
            f"calendar snapshot unavailable: {field_name} must include a timezone"
        )
    return parsed.astimezone(UTC)


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise CalendarProviderError(f"calendar snapshot unavailable: {label} must be an object")
    return value


def _snapshot_metadata(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    nested = payload.get("metadata")
    if nested is None:
        return payload
    return _mapping(nested, "metadata")


@dataclass(frozen=True)
class CachedJsonCalendarProvider:
    """Read and validate one profile-configured cached JSON snapshot per query."""

    path: Path
    max_age_seconds: int
    source_label: str | None = None
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)

    def __post_init__(self) -> None:
        if type(self.max_age_seconds) is not int or not 0 < self.max_age_seconds <= _MAX_AGE_SECONDS:
            raise ValueError("max_age_seconds must be an integer between 1 and 604800")
        object.__setattr__(self, "path", Path(self.path).expanduser())

    def upcoming(self, query: CalendarQuery) -> CalendarSnapshot:
        payload = self._read_payload()
        metadata = _snapshot_metadata(payload)
        source = metadata.get("source")
        if not isinstance(source, str) or not source.strip():
            raise CalendarProviderError(
                "calendar snapshot unavailable: source provenance is required"
            )
        freshness = metadata.get("freshness", Freshness.CACHED.value)
        if freshness != Freshness.CACHED.value:
            raise CalendarProviderError(
                "calendar snapshot unavailable: source freshness must be cached"
            )
        if metadata.get("is_live", False) is not False:
            raise CalendarProviderError(
                "calendar snapshot unavailable: cached source cannot be marked live"
            )
        fetched_at = _parse_datetime(metadata.get("fetched_at"), "fetched_at")
        now = self._now()
        if now.tzinfo is None or now.utcoffset() is None:
            raise CalendarProviderError("calendar snapshot unavailable: clock is not timezone-aware")
        now = now.astimezone(UTC)
        if fetched_at > now:
            raise CalendarProviderError(
                "calendar snapshot unavailable: fetched_at is in the future"
            )
        if now - fetched_at > timedelta(seconds=self.max_age_seconds):
            raise CalendarProviderError(
                "calendar snapshot unavailable: cached snapshot is stale"
            )

        coverage_start = _parse_datetime(metadata.get("coverage_start"), "coverage_start")
        coverage_end = _parse_datetime(metadata.get("coverage_end"), "coverage_end")
        if not timedelta(0) < coverage_end - coverage_start <= timedelta(days=366):
            raise CalendarProviderError("calendar snapshot unavailable: invalid coverage window")
        if query.start < coverage_start or query.end > coverage_end:
            raise CalendarProviderError("calendar snapshot unavailable: query outside snapshot coverage")
        if type(query.max_results) is not int or not 0 < query.max_results <= 1000:
            raise CalendarProviderError("calendar snapshot unavailable: invalid result limit")

        raw_events = payload.get("events")
        if not isinstance(raw_events, list) or len(raw_events) > 10000:
            raise CalendarProviderError(
                "calendar snapshot unavailable: events must be an array"
            )
        events = tuple(self._event(item, index) for index, item in enumerate(raw_events))
        matches = tuple(
            sorted(
                (
                    event
                    for event in events
                    if event.end > query.start and event.start < query.end
                ),
                key=lambda event: (event.start, event.event_id),
            )[: query.max_results]
        )
        return CalendarSnapshot(
            events=matches,
            query=query,
            source=CalendarSourceMetadata(
                source=source.strip(),
                fetched_at=fetched_at,
                freshness=Freshness.CACHED,
                is_live=False,
            ),
        )

    def _now(self) -> datetime:
        try:
            return self.clock()
        except Exception as exc:
            raise CalendarProviderError(
                "calendar snapshot unavailable: could not determine current time"
            ) from exc

    def _read_payload(self) -> Mapping[str, Any]:
        try:
            fd = os.open(self.path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
            with os.fdopen(fd, "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode):
                    raise CalendarProviderError("calendar snapshot unavailable: not a regular file")
                data = stream.read(_MAX_SNAPSHOT_BYTES + 1)
            if len(data) > _MAX_SNAPSHOT_BYTES:
                raise CalendarProviderError("calendar snapshot unavailable: snapshot exceeds size limit")
            text = data.decode("utf-8")
        except CalendarProviderError:
            raise
        except (OSError, UnicodeError) as exc:
            raise CalendarProviderError(
                "calendar snapshot unavailable: configured file could not be read"
            ) from exc
        try:
            payload = json.loads(text)
        except (ValueError, RecursionError) as exc:
            raise CalendarProviderError(
                "calendar snapshot unavailable: invalid JSON"
            ) from exc
        return _mapping(payload, "snapshot")

    @staticmethod
    def _event(value: Any, index: int) -> CalendarEvent:
        item = _mapping(value, f"event {index}")
        event_id = item.get("event_id", item.get("id"))
        title = item.get("title")
        for field in ("timezone_name", "location"):
            if item.get(field) is not None and not isinstance(item[field], str):
                raise CalendarProviderError(f"calendar snapshot unavailable: invalid event {index}")
        if not isinstance(event_id, str) or not isinstance(title, str):
            raise CalendarProviderError(
                f"calendar snapshot unavailable: invalid event {index}"
            )
        try:
            return CalendarEvent(
                event_id=event_id,
                title=title,
                start=_parse_datetime(item.get("start"), f"event {index} start"),
                end=_parse_datetime(item.get("end"), f"event {index} end"),
                timezone_name=item.get("timezone_name"),
                location=item.get("location"),
            )
        except CalendarProviderError:
            raise
        except (TypeError, ValueError, AttributeError) as exc:
            raise CalendarProviderError(
                f"calendar snapshot unavailable: invalid event {index}"
            ) from exc


# Descriptive alias for callers that prefer the source type in the name.
FileCalendarProvider = CachedJsonCalendarProvider


def _strict_bool(section: Mapping[str, Any], key: str) -> tuple[bool, bool]:
    value = section.get(key, False)
    return (value, True) if isinstance(value, bool) else (False, False)


def _scope_is_granted(section: Mapping[str, Any], scope: Any) -> bool:
    grants = section.get("approved_scopes", ())
    if not isinstance(grants, list):
        return False
    for grant in grants:
        if not isinstance(grant, Mapping):
            continue
        # Exact equality is intentional: no wildcard, prefix, or chat-only grant.
        if grant.get("chat_id") == scope.chat_id and grant.get("user_id") == scope.user_id:
            return True
    return False


def _calendar_section() -> Mapping[str, Any] | None:
    """Read trusted policy without accepting last-known-good approval fallback."""
    try:
        from hermes_cli.config import load_config_readonly
        from hermes_cli.config_read_errors import FailedConfigRead

        config = load_config_readonly()
        if isinstance(config, FailedConfigRead) or not isinstance(config, Mapping):
            return None
    except Exception:
        return None
    from ..settings import setting
    configured = setting("calendar")
    if configured is not None:
        return configured if isinstance(configured, Mapping) else None
    platforms = config.get("platforms")
    photon = platforms.get("photon") if isinstance(platforms, Mapping) else None
    if not isinstance(photon, Mapping):
        return None
    extra = photon.get("extra")
    calendar = extra.get("calendar") if isinstance(extra, Mapping) else None
    return calendar if isinstance(calendar, Mapping) else None


def resolve_calendar_access(scope: Any) -> tuple[CalendarAccessPolicy, CalendarProvider | None]:
    """Resolve read approval and a configured provider for one exact Photon scope.

    Configuration is profile-local because the normal config loader follows the
    active ``HERMES_HOME``.  A malformed or absent configuration is fail-closed.
    The snapshot file is not opened until the returned provider is queried.
    """
    section = _calendar_section()
    if section is None:
        return CalendarAccessPolicy(), None

    enabled, enabled_valid = _strict_bool(section, "enabled")
    approved, approval_valid = _strict_bool(section, "approval_granted")
    if not enabled_valid or not approval_valid:
        return CalendarAccessPolicy(), None
    if not enabled:
        return CalendarAccessPolicy(enabled=False, approval_granted=False), None

    try:
        from ..coordinator import PhotonConversationScope

        if not isinstance(scope, PhotonConversationScope):
            return CalendarAccessPolicy(), None
    except (ImportError, TypeError):
        return CalendarAccessPolicy(), None

    approval_granted = approved and _scope_is_granted(section, scope)
    policy = CalendarAccessPolicy(enabled=True, approval_granted=approval_granted)
    if not policy.allows_read:
        return policy, None

    return _configured_provider(section, scope, policy)


def _configured_provider(section, scope, policy):
    provider_kind = section.get("provider", "cached_json")
    if provider_kind == "google_workspace":
        from hermes_constants import get_hermes_home
        from .google_workspace import GoogleWorkspaceCalendarProvider

        calendar_id = section.get("calendar_id")
        if (
            not isinstance(calendar_id, str) or not calendar_id.strip()
            or calendar_id != calendar_id.strip() or len(calendar_id) > 1024
            or any(ord(char) < 32 for char in calendar_id)
        ):
            return policy, None
        return policy, GoogleWorkspaceCalendarProvider(
            scope=scope, profile_home=get_hermes_home().resolve(), calendar_id=calendar_id,
        )
    if provider_kind != "cached_json":
        return policy, None

    snapshot_path = section.get("snapshot_path")
    max_age = section.get("max_age_seconds")
    if not isinstance(snapshot_path, str) or not snapshot_path.strip():
        return policy, None
    if isinstance(max_age, bool) or not isinstance(max_age, int) or not 0 < max_age <= _MAX_AGE_SECONDS:
        return policy, None
    try:
        from hermes_constants import get_hermes_home

        path = Path(snapshot_path).expanduser()
        if not path.is_absolute():
            path = get_hermes_home() / path
        provider = CachedJsonCalendarProvider(
            path=path,
            max_age_seconds=max_age,
            source_label=section.get("source") if isinstance(section.get("source"), str) else None,
        )
    except (OSError, TypeError, ValueError):
        return policy, None
    return policy, provider


__all__ = [
    "CachedJsonCalendarProvider",
    "FileCalendarProvider",
    "resolve_calendar_access",
]

"""Small, read-only calendar provider contract.

This module deliberately stops at a provider seam.  It does not discover
accounts, read credentials, make network requests, or expose event mutation
operations. A configured provider must be injected explicitly and pass both the
enabled and approval-gated policy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, UTC
from enum import Enum
from typing import Protocol, Sequence, runtime_checkable


class CalendarProviderError(RuntimeError):
    """Raised when calendar access is unavailable or denied."""


class Freshness(str, Enum):
    """How current a calendar snapshot is known to be."""

    LIVE = "live"
    CACHED = "cached"
    SYNTHETIC = "synthetic"
    UNKNOWN = "unknown"


def _utc(value: datetime, *, field_name: str) -> datetime:
    """Require an aware datetime and normalize it to UTC."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


@dataclass(frozen=True)
class CalendarEvent:
    """A normalized calendar event with UTC boundaries."""

    event_id: str
    title: str
    start: datetime
    end: datetime
    timezone_name: str | None = None
    location: str | None = None

    def __post_init__(self) -> None:
        if not self.event_id.strip():
            raise ValueError("event_id must not be empty")
        if not self.title.strip():
            raise ValueError("title must not be empty")
        start = _utc(self.start, field_name="start")
        end = _utc(self.end, field_name="end")
        if end <= start:
            raise ValueError("end must be after start")
        object.__setattr__(self, "start", start)
        object.__setattr__(self, "end", end)


@dataclass(frozen=True)
class CalendarQuery:
    """A bounded, timezone-aware read window for upcoming events."""

    start: datetime
    end: datetime
    max_results: int = 100

    def __post_init__(self) -> None:
        start = _utc(self.start, field_name="query start")
        end = _utc(self.end, field_name="query end")
        if end <= start:
            raise ValueError("query end must be after query start")
        if self.max_results <= 0:
            raise ValueError("max_results must be positive")
        object.__setattr__(self, "start", start)
        object.__setattr__(self, "end", end)


@dataclass(frozen=True)
class CalendarSourceMetadata:
    """Provenance and freshness attached to every returned snapshot."""

    source: str
    fetched_at: datetime
    freshness: Freshness
    is_live: bool = False

    def __post_init__(self) -> None:
        if not self.source.strip():
            raise ValueError("source must not be empty")
        object.__setattr__(
            self,
            "fetched_at",
            _utc(self.fetched_at, field_name="fetched_at"),
        )
        if self.is_live and self.freshness is not Freshness.LIVE:
            raise ValueError("live sources must use LIVE freshness")
        if not self.is_live and self.freshness is Freshness.LIVE:
            raise ValueError("LIVE freshness requires is_live=True")


@dataclass(frozen=True)
class CalendarSnapshot:
    """Events plus explicit query and provenance for one read."""

    events: tuple[CalendarEvent, ...]
    query: CalendarQuery
    source: CalendarSourceMetadata

    def __post_init__(self) -> None:
        object.__setattr__(self, "events", tuple(self.events))
        if any(not isinstance(event, CalendarEvent) for event in self.events):
            raise TypeError("events must contain CalendarEvent values")


@runtime_checkable
class CalendarProvider(Protocol):
    """Read-only calendar provider interface.

    The absence of create/update/delete methods is intentional.  Providers are
    only queried for events that overlap a bounded upcoming window.
    """

    def upcoming(self, query: CalendarQuery) -> CalendarSnapshot:
        """Return events overlapping ``query``, ordered by start time."""
        ...


@dataclass(frozen=True)
class CalendarAccessPolicy:
    """Explicit opt-in and approval gate for calendar reads."""

    enabled: bool = False
    approval_granted: bool = False

    @property
    def allows_read(self) -> bool:
        return self.enabled and self.approval_granted


@dataclass(frozen=True)
class DisabledCalendarProvider:
    """Fail-closed provider used when calendar access is absent or not approved."""

    reason: str = "calendar access is disabled or unconfigured"

    def upcoming(self, query: CalendarQuery) -> CalendarSnapshot:
        del query
        raise CalendarProviderError(self.reason)


@dataclass(frozen=True)
class SyntheticCalendarProvider:
    """Deterministic in-memory provider for tests and local contract fixtures."""

    events: tuple[CalendarEvent, ...] = field(default_factory=tuple)
    fetched_at: datetime | None = None
    source: str = "synthetic-calendar-fixture"

    def __post_init__(self) -> None:
        object.__setattr__(self, "events", tuple(self.events))
        if any(not isinstance(event, CalendarEvent) for event in self.events):
            raise TypeError("events must contain CalendarEvent values")
        if self.fetched_at is not None:
            object.__setattr__(
                self,
                "fetched_at",
                _utc(self.fetched_at, field_name="fetched_at"),
            )

    def upcoming(self, query: CalendarQuery) -> CalendarSnapshot:
        matches = tuple(
            sorted(
                (
                    event
                    for event in self.events
                    if event.end > query.start and event.start < query.end
                ),
                key=lambda event: (event.start, event.event_id),
            )[: query.max_results]
        )
        fetched_at = self.fetched_at or datetime.now(UTC)
        return CalendarSnapshot(
            events=matches,
            query=query,
            source=CalendarSourceMetadata(
                source=self.source,
                fetched_at=fetched_at,
                freshness=Freshness.SYNTHETIC,
                is_live=False,
            ),
        )


def get_calendar_provider(
    policy: CalendarAccessPolicy | None = None,
    *,
    configured_provider: CalendarProvider | None = None,
    synthetic_events: Sequence[CalendarEvent] | None = None,
) -> CalendarProvider:
    """Resolve a provider without ever enabling calendar access by default.

    ``configured_provider`` is the approved integration seam.  Synthetic events
    are accepted only for explicit tests; neither path is reachable without
    both policy flags being true.
    """
    effective_policy = policy or CalendarAccessPolicy()
    if not effective_policy.allows_read:
        return DisabledCalendarProvider()
    if configured_provider is not None:
        return configured_provider
    if synthetic_events is not None:
        return SyntheticCalendarProvider(tuple(synthetic_events))
    return DisabledCalendarProvider("calendar provider is not configured")


__all__ = [
    "CalendarAccessPolicy",
    "CalendarEvent",
    "CalendarProvider",
    "CalendarProviderError",
    "CalendarQuery",
    "CalendarSnapshot",
    "CalendarSourceMetadata",
    "DisabledCalendarProvider",
    "Freshness",
    "SyntheticCalendarProvider",
    "get_calendar_provider",
]

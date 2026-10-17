"""Read-only calendar contracts and approval-gated profile-local sources."""

from .configured import CachedJsonCalendarProvider, FileCalendarProvider, resolve_calendar_access
from .provider import (
    CalendarAccessPolicy,
    CalendarEvent,
    CalendarProvider,
    CalendarProviderError,
    CalendarQuery,
    CalendarSnapshot,
    CalendarSourceMetadata,
    DisabledCalendarProvider,
    Freshness,
    SyntheticCalendarProvider,
    get_calendar_provider,
)

__all__ = [
    "CachedJsonCalendarProvider",
    "CalendarAccessPolicy",
    "CalendarEvent",
    "CalendarProvider",
    "CalendarProviderError",
    "CalendarQuery",
    "CalendarSnapshot",
    "CalendarSourceMetadata",
    "DisabledCalendarProvider",
    "FileCalendarProvider",
    "Freshness",
    "SyntheticCalendarProvider",
    "get_calendar_provider",
    "resolve_calendar_access",
]

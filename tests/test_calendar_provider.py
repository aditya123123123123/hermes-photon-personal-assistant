from datetime import datetime, timedelta, UTC

import pytest

from assistant.calendar import (
    CalendarAccessPolicy,
    CalendarEvent,
    CalendarProviderError,
    CalendarQuery,
    Freshness,
    get_calendar_provider,
)


def test_calendar_is_disabled_by_default_and_reads_only_after_approval():
    start = datetime(2026, 10, 12, 9, tzinfo=UTC)
    query = CalendarQuery(start, start + timedelta(days=1))
    event = CalendarEvent("event-1", "Synthetic review", start, start + timedelta(hours=1))

    with pytest.raises(CalendarProviderError):
        get_calendar_provider().upcoming(query)

    provider = get_calendar_provider(
        CalendarAccessPolicy(enabled=True, approval_granted=True),
        synthetic_events=[event],
    )
    snapshot = provider.upcoming(query)

    assert [item.event_id for item in snapshot.events] == ["event-1"]
    assert snapshot.source.freshness is Freshness.SYNTHETIC
    assert snapshot.source.is_live is False

import argparse
from datetime import datetime, timedelta

from gateway.session_context import clear_session_vars, set_session_vars
from assistant.calendar import CalendarAccessPolicy, CalendarEvent, CalendarSnapshot, CalendarSourceMetadata, Freshness
from assistant.personal_assistant import _on_pre_llm_call, follow_up_context


class _Calendar:
    def upcoming(self, query):
        return CalendarSnapshot(
            events=(CalendarEvent("event-1", "Planning", query.start, query.start + timedelta(hours=1)),),
            query=query,
            source=CalendarSourceMetadata(
                source="offline-test", fetched_at=query.start,
                freshness=Freshness.CACHED,
            ),
        )


def test_follow_up_recall_is_scoped_and_ranked(monkeypatch):
    proposal = {
        "id": "proposal-1",
        "platform": "photon",
        "chat_id": "any;-;+1234567890",
        "proposed_at": datetime.now().astimezone().isoformat(),
        "title": "Review the travel plan",
        "explanation": "The travel thread has no reply.",
        "commitment_id": "commitment-1",
    }
    monkeypatch.setattr(
        "assistant.dropped_thread_detector.list_follow_up_proposals",
        lambda status=None: [proposal] if status == "pending" else [],
    )
    monkeypatch.setattr(
        "assistant.store.get_commitment",
        lambda commitment_id: {"id": commitment_id, "status": "proposed"},
    )
    set_session_vars(
        platform="photon", chat_id="any;-;+1234567890", chat_type="dm",
        user_id="+1234567890", session_id="session-1",
    )
    try:
        context = follow_up_context(
            platform="photon", session_id="session-1", user_message="travel plan",
        )
    finally:
        clear_session_vars([])

    assert "proposal-1" in context
    assert "Unverified follow-up proposals" in context




def test_follow_up_recall_does_not_cross_photon_group_scope(monkeypatch):
    monkeypatch.setattr(
        "assistant.dropped_thread_detector.list_follow_up_proposals",
        lambda status=None: [{"id": "proposal-1", "platform": "photon"}],
    )
    set_session_vars(
        platform="photon", chat_id="group-1", chat_type="group",
        user_id="+1234567890", session_id="session-1",
    )
    try:
        assert follow_up_context(
            platform="photon", session_id="session-1", user_message="travel plan",
        ) == ""
    finally:
        clear_session_vars([])

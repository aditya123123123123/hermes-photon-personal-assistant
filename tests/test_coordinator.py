"""Behavior contracts for the read-only Photon coordination facade."""

from datetime import datetime, timedelta, UTC
from types import MappingProxyType

import pytest

from assistant.calendar import CalendarAccessPolicy, CalendarEvent, CalendarQuery, CalendarSnapshot, CalendarSourceMetadata, Freshness
from assistant import (
    PhotonConversationScope,
    PhotonCoordinator,
    observe_photon_context,
    propose_photon_coordination,
)


NOW = datetime(2026, 10, 11, 16, 0, tzinfo=UTC)
SCOPE = PhotonConversationScope(chat_id="any;-;+15555554567", user_id="user-1")


class ReadOnlyCalendar:
    def __init__(self) -> None:
        self.queries: list[CalendarQuery] = []

    def upcoming(self, query: CalendarQuery) -> CalendarSnapshot:
        self.queries.append(query)
        return CalendarSnapshot(
            events=(CalendarEvent("event-1", "Focus", query.start + timedelta(hours=1), query.start + timedelta(hours=2)),),
            query=query,
            source=CalendarSourceMetadata(
                source="test-calendar",
                fetched_at=NOW,
                freshness=Freshness.CACHED,
            ),
        )


def _task(*, task_id: str = "task-1", chat_id: str = SCOPE.chat_id) -> dict:
    return {
        "id": task_id,
        "name": "Accepted task",
        "prompt": "private prompt must not escape",
        "enabled": True,
        "state": "scheduled",
        "origin": {"platform": "photon", "chat_type": "dm", "chat_id": chat_id, "user_id": SCOPE.user_id},
        "schedule_display": "every day",
        "next_run_at": "2026-10-12T09:00:00+00:00",
    }


def _pending(*, proposal_id: str = "proposal-1", chat_id: str = SCOPE.chat_id) -> dict:
    return {
        "id": proposal_id,
        "proposal_key": "dropped-thread:abc",
        "proposal_type": "follow_up",
        "source": "dropped_thread",
        "platform": "photon",
        "chat_id": chat_id,
        "thread_id": "thread-1",
        "user_message_id": "message-1",
        "status": "pending",
        "attempt": 1,
        "proposed_at": "2026-10-11T12:00:00+00:00",
        "next_eligible_at": "2026-10-18T12:00:00+00:00",
        "explanation": "unanswered user turn",
        "reasons": [],
    }


def test_exact_scope_rejects_groups_other_platforms_and_non_photon_ids() -> None:
    with pytest.raises(ValueError):
        PhotonConversationScope(chat_id="group-1", user_id="user-1")
    with pytest.raises(ValueError):
        PhotonConversationScope(chat_id="any;-;+15555554567", user_id="", chat_type="dm")
    with pytest.raises(ValueError):
        PhotonConversationScope(chat_id="any;-;+15555554567", user_id="user-1", platform="telegram")


def test_observation_combines_scoped_tasks_proposals_calendar_and_conversation_without_prompt() -> None:
    calendar = ReadOnlyCalendar()
    state = {"last_resolved_model": "model-a", "nested": {"turn": 3}}
    observation = observe_photon_context(
        SCOPE,
        now=NOW,
        accepted_tasks=(_task(), _task(task_id="other", chat_id="any;-;+19999999999")),
        pending_proposals=(_pending(), _pending(proposal_id="other", chat_id="any;-;+19999999999")),
        calendar_policy=CalendarAccessPolicy(enabled=True, approval_granted=True),
        calendar_provider=calendar,
        calendar_window=timedelta(hours=6),
        conversation_state=state,
    )

    assert [item["id"] for item in observation.accepted_tasks] == ["task-1"]
    assert [item["id"] for item in observation.pending_dropped_thread_proposals] == ["proposal-1"]
    assert observation.calendar is not None
    assert calendar.queries == [CalendarQuery(NOW, NOW + timedelta(hours=6), 100)]
    assert observation.conversation_state == state
    assert "prompt" not in observation.as_dict()["accepted_tasks"][0]
    assert all(item.component in {"scope", "effects", "accepted_tasks", "pending_proposals", "calendar", "conversation"} for item in observation.audit)
    assert any(item.detail == "read_only_no_send_no_calendar_write" for item in observation.audit)
    assert observation.accepted_tasks[0]["id"] == "task-1"
    assert isinstance(observation.accepted_tasks[0], MappingProxyType)


def test_calendar_is_disabled_by_default_and_proposals_are_pure_and_restart_deterministic() -> None:
    provider = ReadOnlyCalendar()
    observation = observe_photon_context(
        SCOPE,
        now=NOW,
        accepted_tasks=(_task(),),
        pending_proposals=(_pending(),),
        calendar_provider=provider,
        conversation_state={"turn": 1},
    )
    assert observation.calendar is None
    assert provider.queries == []

    first = propose_photon_coordination(observation)
    second = propose_photon_coordination(observation)
    assert first.as_dict() == second.as_dict()
    assert len(first.proposals) == 1
    proposal = first.proposals[0]
    assert proposal.kind == "review_dropped_thread"
    assert proposal.requires_user_approval is True
    assert proposal.effect == "none"
    assert proposal.evidence["proposal_key"] == "dropped-thread:abc"


def test_facade_can_use_injected_task_loader_and_does_not_mutate_inputs() -> None:
    source = {"turn": {"count": 1}}
    commitment = {
        "id": "commitment-1",
        "source": "photon",
        "status": "active",
        "title": "Call back",
        "metadata": {"chat_id": SCOPE.chat_id, "chat_type": "dm", "user_id": SCOPE.user_id},
    }
    coordinator = PhotonCoordinator(task_loader=lambda: (_task(), commitment))
    observation = coordinator.observe(
        SCOPE,
        now=NOW,
        pending_proposals=(),
        conversation_state=source,
    )
    source["turn"]["count"] = 9
    assert observation.conversation_state["turn"]["count"] == 1
    assert [task["id"] for task in observation.accepted_tasks] == ["commitment-1", "task-1"]
    assert coordinator.propose(observation).proposals == ()


def test_observation_requires_aware_now_and_positive_calendar_window() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        observe_photon_context(SCOPE, now=datetime(2026, 10, 11, 16, 0), accepted_tasks=(), pending_proposals=())
    with pytest.raises(ValueError, match="positive"):
        observe_photon_context(SCOPE, now=NOW, calendar_window=timedelta(0), accepted_tasks=(), pending_proposals=())


def test_task_context_requires_exact_sender_not_only_matching_chat() -> None:
    wrong_sender = _task(task_id="wrong-sender")
    wrong_sender["origin"]["user_id"] = "other-user"
    missing_sender = _task(task_id="missing-sender")
    del missing_sender["origin"]["user_id"]
    observation = observe_photon_context(
        SCOPE, now=NOW, accepted_tasks=(_task(), wrong_sender, missing_sender),
        pending_proposals=(),
    )
    assert [item["id"] for item in observation.accepted_tasks] == ["task-1"]


def test_default_loader_keeps_blocked_captures_scoped_and_drops_completed(monkeypatch, tmp_path):
    from assistant import store as suggestions

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    records = {}
    for label, user in (("owned", SCOPE.user_id), ("other", "other-user")):
        record = suggestions.create_commitment(
            title=label, semantic_key=label, status="active", now=NOW,
            metadata={"chat_id": SCOPE.chat_id, "chat_type": "dm", "user_id": user},
        )
        records[label] = suggestions.transition_commitment(record["id"], "blocked", now=NOW)

    observation = observe_photon_context(SCOPE, now=NOW, pending_proposals=())
    assert [(row["id"], row["status"]) for row in observation.accepted_tasks] == [
        (records["owned"]["id"], "blocked"),
    ]
    suggestions.transition_commitment(records["owned"]["id"], "completed", now=NOW)
    assert observe_photon_context(SCOPE, now=NOW, pending_proposals=()).accepted_tasks == ()

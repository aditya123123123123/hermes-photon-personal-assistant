"""Behaviour contracts for the proposal-only dropped-thread detector."""

from datetime import datetime, timedelta, UTC
import importlib
import json
import multiprocessing

import pytest


def _concurrent_propose(home: str, barrier) -> None:
    import os
    os.environ["HERMES_HOME"] = home
    import assistant.dropped_thread_detector as detector
    barrier.wait()
    detector.propose_follow_up({
        "thread_id": "concurrent-thread",
        "chat_id": "any;-;+1555554567",
        "last_user_at": "2026-10-09T16:00:00+00:00",
        "last_user_message_id": "concurrent-message",
        "platform": "photon",
        "is_dm": True,
        "state": "open",
    }, now=datetime(2026, 10, 11, 16, 0, tzinfo=UTC))



NOW = datetime(2026, 10, 11, 16, 0, tzinfo=UTC)


@pytest.fixture
def detector(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    import hermes_constants
    importlib.reload(hermes_constants)
    import assistant.dropped_thread_detector as module
    importlib.reload(module)
    return module


def snapshot(detector, *, user_at=None, assistant_at=None, chat_id="any;-;+15551234567", **changes):
    value = {
        "thread_id": "thread-1",
        "chat_id": chat_id,
        "last_user_at": user_at or NOW - timedelta(days=2),
        "last_user_message_id": "message-1",
        "last_assistant_at": assistant_at,
        "platform": "photon",
        "is_dm": True,
        "state": "open",
    }
    value.update(changes)
    return detector.ThreadSnapshot.from_mapping(value)


class TestEvaluation:
    def test_quiet_period_and_answer_are_explainable(self, detector):
        recent = snapshot(detector, user_at=NOW - timedelta(hours=1))
        decision = detector.evaluate_dropped_thread(recent, now=NOW)
        assert decision.eligible is False
        assert decision.reasons == ("quiet_period_not_elapsed",)
        assert decision.proposal_key.startswith("dropped-thread:")

        answered = snapshot(detector, assistant_at=NOW - timedelta(hours=1))
        decision = detector.evaluate_dropped_thread(answered, now=NOW)
        assert decision.eligible is False
        assert "assistant_replied_after_user" in decision.reasons

    @pytest.mark.parametrize(
        "changes,reason",
        [
            ({"chat_id": "any;-;group-1"}, "not_exact_photon_dm"),
            ({"is_dm": False}, "not_direct_message"),
            ({"platform": "telegram"}, "not_photon"),
            ({"state": "completed"}, "terminal_state:completed"),
        ],
    )
    def test_only_exact_open_photon_dms_are_candidates(self, detector, changes, reason):
        decision = detector.evaluate_dropped_thread(snapshot(detector, **changes), now=NOW)
        assert decision.eligible is False
        assert reason in decision.reasons


class TestDurableProposals:
    def test_proposal_is_pending_only_and_duplicate_suppressed(self, detector):
        candidate = snapshot(detector)
        first = detector.propose_follow_up(candidate, now=NOW)
        assert first["status"] == "pending"
        assert first["source"] == "dropped_thread"
        assert first["proposal_type"] == "follow_up"
        assert first["commitment_id"]
        from assistant.store import get_commitment
        commitment = get_commitment(first["commitment_id"])
        assert commitment["status"] == "proposed"
        assert commitment["metadata"]["requires_user_approval"] is True
        assert commitment["metadata"]["effect"] == "none"
        assert detector.propose_follow_up(candidate, now=NOW + timedelta(days=30)) is None
        assert len(detector.list_follow_up_proposals()) == 1
        from assistant.store import list_commitments
        assert len(list_commitments()) == 1

    def test_approval_syncs_the_real_commitment_lifecycle_without_creating_a_job(self, detector):
        candidate = snapshot(detector)
        proposal = detector.propose_follow_up(candidate, now=NOW)
        accepted = detector.accept_follow_up_proposal(proposal["id"])
        assert accepted["status"] == "accepted"
        from assistant.store import get_commitment
        commitment = get_commitment(proposal["commitment_id"])
        assert commitment["status"] == "active"
        assert commitment["task_created"] is False
        assert not (detector._current_file().parent / "jobs.json").exists()

    def test_rejection_syncs_as_dismissed_and_remains_latched(self, detector):
        proposal = detector.propose_follow_up(snapshot(detector), now=NOW)
        rejected = detector.reject_follow_up_proposal(proposal["id"])
        assert rejected["status"] == "rejected"
        from assistant.store import get_commitment
        assert get_commitment(proposal["commitment_id"])["status"] == "dismissed"
        assert detector.accept_follow_up_proposal(proposal["id"]) is None

    def test_completion_before_approval_does_not_activate_or_create_work(self, detector):
        proposal = detector.propose_follow_up(snapshot(detector), now=NOW)
        completed = detector.complete_follow_up_proposal(proposal["id"])
        assert completed["status"] == "completed"
        from assistant.store import get_commitment
        commitment = get_commitment(proposal["commitment_id"])
        assert commitment["status"] == "proposed"
        assert commitment["task_created"] is False

    def test_restart_reads_same_profile_store_and_preserves_other_suggestions(self, detector, tmp_path):
        path = tmp_path / ".hermes" / "photon-personal-assistant" / "store.json"
        path.parent.mkdir()
        path.write_text(json.dumps({"suggestions": [{"id": "keep-me"}]}), encoding="utf-8")
        candidate = snapshot(detector)
        created = detector.propose_follow_up(candidate, now=NOW)
        assert json.loads(path.read_text(encoding="utf-8"))["suggestions"] == [{"id": "keep-me"}]

        reloaded = importlib.reload(detector)
        assert reloaded.list_follow_up_proposals()[0]["id"] == created["id"]
        assert reloaded.get_hermes_home().resolve() / "photon-personal-assistant" / "store.json" == path

    def test_profile_switches_do_not_share_proposals(self, detector, tmp_path):
        from hermes_constants import reset_hermes_home_override, set_hermes_home_override

        home_a = tmp_path / "profile-a"
        home_b = tmp_path / "profile-b"
        token = set_hermes_home_override(home_a)
        try:
            assert detector.propose_follow_up(snapshot(detector), now=NOW) is not None
        finally:
            reset_hermes_home_override(token)
        token = set_hermes_home_override(home_b)
        try:
            assert detector.list_follow_up_proposals() == []
            assert detector.propose_follow_up(snapshot(detector), now=NOW) is not None
        finally:
            reset_hermes_home_override(token)
        assert (home_a / "photon-personal-assistant" / "store.json").exists()
        assert (home_b / "photon-personal-assistant" / "store.json").exists()

    def test_terminal_outcomes_latch_and_deferred_backoff_is_restart_safe(self, detector):
        candidate = snapshot(detector)
        created = detector.propose_follow_up(candidate, now=NOW)
        assert detector.reject_follow_up_proposal(created["id"])["status"] == "rejected"
        assert detector.propose_follow_up(candidate, now=NOW + timedelta(days=90)) is None

        other = snapshot(detector, thread_id="thread-2", last_user_message_id="message-2")
        proposal = detector.propose_follow_up(other, now=NOW)
        assert detector.defer_follow_up_proposal(proposal["id"])["status"] == "deferred"
        # Default cadence is seven days; a deferred proposal is not retried early.
        assert detector.propose_follow_up(other, now=NOW + timedelta(days=6, hours=23)) is None
        retry = detector.propose_follow_up(other, now=NOW + timedelta(days=7))
        assert retry is not None
        assert retry["attempt"] == 2
        assert len(detector.list_follow_up_proposals()) == 3

    def test_completion_and_cancellation_are_terminal(self, detector):
        completed = detector.propose_follow_up(snapshot(detector), now=NOW)
        assert detector.complete_follow_up_proposal(completed["id"])["status"] == "completed"
        assert detector.propose_follow_up(snapshot(detector), now=NOW + timedelta(days=30)) is None

        cancelled = detector.propose_follow_up(
            snapshot(detector, thread_id="thread-2", last_user_message_id="message-2"), now=NOW
        )
        assert detector.cancel_follow_up_proposal(cancelled["id"])["status"] == "cancelled"
        assert detector.propose_follow_up(
            snapshot(detector, thread_id="thread-2", last_user_message_id="message-2"),
            now=NOW + timedelta(days=30),
        ) is None

    def test_invalid_outcome_and_non_pending_refs_are_safe(self, detector):
        with pytest.raises(ValueError):
            detector.record_follow_up_outcome("missing", "sent")
        assert detector.complete_follow_up_proposal("missing") is None


class TestProductionObservation:
    def test_photon_inbound_observation_creates_proposal_from_normalized_event(self, detector):
        event = {
            "messageId": "event-message-1",
            "timestamp": (NOW - timedelta(days=2)).isoformat(),
            "space": {"id": "any;-;+1555554567", "type": "dm"},
            "content": {"type": "text", "text": "hello"},
        }
        proposal = detector.observe_photon_inbound(event, now=NOW)
        assert proposal is not None
        assert proposal["chat_id"] == "any;-;+1555554567"
        assert proposal["user_message_id"] == "event-message-1"
        assert proposal["status"] == "pending"
        assert not (detector._current_file().parent / "jobs.json").exists()

    @pytest.mark.parametrize("space_type", ["group", "channel", ""])
    def test_observation_rejects_non_dm_spaces(self, detector, space_type):
        event = {
            "messageId": "event-message-1",
            "timestamp": (NOW - timedelta(days=2)).isoformat(),
            "space": {"id": "any;-;+1555554567", "type": space_type},
            "content": {"type": "text", "text": "hello"},
        }
        assert detector.observe_photon_inbound(event, now=NOW) is None
        assert detector.list_follow_up_proposals() == []


class TestProcessSafePersistence:
    def test_concurrent_processes_append_once_and_preserve_commitment(self, detector, tmp_path):
        home = tmp_path / ".hermes"
        barrier = multiprocessing.Barrier(2)
        processes = [multiprocessing.Process(target=_concurrent_propose, args=(str(home), barrier)) for _ in range(2)]
        for process in processes:
            process.start()
        for process in processes:
            process.join(timeout=10)
            assert process.exitcode == 0
        import assistant.dropped_thread_detector as reloaded
        from assistant import store as suggestions
        reloaded.FOLLOW_UP_PROPOSALS_FILE = home / "photon-personal-assistant" / "store.json"
        suggestions.SUGGESTIONS_FILE = home / "photon-personal-assistant" / "store.json"
        proposals = reloaded.list_follow_up_proposals()
        assert len(proposals) == 1
        commitments = suggestions.list_commitments()
        assert len(commitments) == 1
        assert proposals[0]["commitment_id"] == commitments[0]["id"]


class TestBatch:
    def test_batch_keeps_input_order_and_deduplicates(self, detector):
        first = snapshot(detector)
        second = snapshot(detector, thread_id="thread-2", last_user_message_id="message-2")
        created = detector.propose_follow_ups([first, second, first], now=NOW)
        assert [item["thread_id"] for item in created] == ["thread-1", "thread-2"]

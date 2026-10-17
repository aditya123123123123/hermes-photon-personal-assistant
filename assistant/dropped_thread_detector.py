"""Proposal-only detection of quiet, unanswered Photon direct messages.

This module deliberately stops at a durable proposal.  It does not inspect gateway adapters,
send messages, create cron jobs, or wake a conversation.  The proposal rows live in the existing
``photon-personal-assistant/store.json`` store under a separate ``follow_up_proposals`` namespace so this feature
does not introduce a second general-purpose task database.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, UTC
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

from hermes_constants import get_hermes_home
from hermes_time import now as _hermes_now
from utils import atomic_json_write

__all__ = [
    "CandidateEvaluation",
    "DetectorPolicy",
    "FollowUpProposal",
    "ThreadSnapshot",
    "accept_follow_up_proposal",
    "approve_follow_up_proposal",
    "cancel_follow_up_proposal",
    "complete_follow_up_proposal",
    "defer_follow_up_proposal",
    "evaluate_dropped_thread",
    "list_follow_up_proposals",
    "load_follow_up_proposals",
    "observe_photon_inbound",
    "propose_follow_up",
    "propose_follow_ups",
    "record_follow_up_outcome",
    "reject_follow_up_proposal",
]

# This is the adapter-native Photon DM identifier, not a permissive "looks like a chat id" check.
# Keeping the exact shape here prevents group/space ids from becoming proactive candidates.
_PHOTON_DM_CHAT_RE = re.compile(r"^any;-;\+\d{6,}$")
_TERMINAL_STATES = frozenset({"accepted", "cancelled", "completed", "rejected", "suppressed"})
_RETRYABLE_STATES = frozenset({"deferred"})

# The JSON file is intentionally overridable for embedders/tests, but normal calls resolve the
# active profile at call time.  This follows the plugin commitment store and is important for multiplexed homes.
FOLLOW_UP_PROPOSALS_FILE: Optional[Path] = None
_store_lock = threading.RLock()


@dataclass(frozen=True)
class DetectorPolicy:
    """Pure policy knobs for quiet-period and retry cadence decisions."""

    quiet_period: timedelta = timedelta(hours=24)
    cadence: timedelta = timedelta(days=7)
    backoff_factor: float = 2.0
    max_attempts: int = 3

    def __post_init__(self) -> None:
        if self.quiet_period < timedelta(0):
            raise ValueError("quiet_period must be non-negative")
        if self.cadence <= timedelta(0):
            raise ValueError("cadence must be positive")
        if self.backoff_factor < 1:
            raise ValueError("backoff_factor must be at least 1")
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be positive")


@dataclass(frozen=True)
class ThreadSnapshot:
    """The minimum normalized evidence needed to evaluate one conversation."""

    thread_id: str
    chat_id: str
    last_user_at: datetime
    last_user_message_id: str
    platform: str = "photon"
    is_dm: bool = True
    last_assistant_at: Optional[datetime] = None
    state: str = "open"

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> ThreadSnapshot:
        """Build a snapshot from a connector-neutral mapping without retaining message text."""
        def first(*names: str, default: Any = None) -> Any:
            for name in names:
                if name in value and value[name] is not None:
                    return value[name]
            return default

        user_at = first("last_user_at", "user_at", "last_user_timestamp")
        if user_at is None:
            raise ValueError("last_user_at is required")
        assistant_at = first("last_assistant_at", "assistant_at", "last_assistant_timestamp")
        return cls(
            thread_id=str(first("thread_id", "id", default="")).strip(),
            chat_id=str(first("chat_id", "conversation_id", default="")).strip(),
            last_user_at=_as_utc(user_at),
            last_user_message_id=str(first("last_user_message_id", "user_message_id", default="")).strip(),
            platform=str(first("platform", "channel", default="photon")).strip().lower(),
            is_dm=bool(first("is_dm", "direct_message", default=True)),
            last_assistant_at=_as_utc(assistant_at) if assistant_at is not None else None,
            state=str(first("state", "status", default="open")).strip().lower(),
        )


@dataclass(frozen=True)
class CandidateEvaluation:
    eligible: bool
    proposal_key: str
    reasons: tuple[str, ...]
    explanation: str
    eligible_at: Optional[datetime] = None


@dataclass(frozen=True)
class FollowUpProposal:
    """Read-only view of a durable proposal record."""

    id: str
    proposal_key: str
    thread_id: str
    chat_id: str
    user_message_id: str
    status: str
    attempt: int
    proposed_at: datetime
    next_eligible_at: Optional[datetime]
    explanation: str
    reasons: tuple[str, ...]
    platform: str = "photon"

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> FollowUpProposal:
        return cls(
            id=str(record["id"]),
            proposal_key=str(record["proposal_key"]),
            thread_id=str(record["thread_id"]),
            chat_id=str(record["chat_id"]),
            user_message_id=str(record["user_message_id"]),
            status=str(record["status"]),
            attempt=int(record.get("attempt", 1)),
            proposed_at=_as_utc(record["proposed_at"]),
            next_eligible_at=(
                _as_utc(record["next_eligible_at"])
                if record.get("next_eligible_at") else None
            ),
            explanation=str(record.get("explanation", "")),
            reasons=tuple(str(item) for item in record.get("reasons", ())),
            platform=str(record.get("platform", "photon")),
        )


def _as_utc(value: Any) -> datetime:
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str):
        result = datetime.fromisoformat(value)
    else:
        raise TypeError("timestamps must be datetime objects or ISO-8601 strings")
    if result.tzinfo is None:
        return result.replace(tzinfo=UTC)
    return result.astimezone(UTC)


def _now(value: Optional[datetime]) -> datetime:
    return _as_utc(value if value is not None else _hermes_now())


def _policy(policy: Optional[DetectorPolicy]) -> DetectorPolicy:
    return policy if policy is not None else DetectorPolicy()


def _proposal_key(snapshot: ThreadSnapshot) -> str:
    # IDs are opaque and may contain separators; hash only the stable identity and keep no message text.
    identity = "\x1f".join((snapshot.platform, snapshot.chat_id, snapshot.thread_id, snapshot.last_user_message_id))
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
    return f"dropped-thread:{digest}"


def evaluate_dropped_thread(
    snapshot: ThreadSnapshot | Mapping[str, Any],
    *,
    now: Optional[datetime] = None,
    policy: Optional[DetectorPolicy] = None,
    prior_records: Sequence[Mapping[str, Any]] = (),
) -> CandidateEvaluation:
    """Evaluate one snapshot deterministically and return explainable reason codes.

    No filesystem or network access occurs here.  ``prior_records`` is normally loaded by
    :func:`propose_follow_up`; accepting it explicitly keeps this decision function easy to audit.
    """
    candidate = snapshot if isinstance(snapshot, ThreadSnapshot) else ThreadSnapshot.from_mapping(snapshot)
    current = _now(now)
    rules = _policy(policy)
    key = _proposal_key(candidate)
    reasons: list[str] = []
    eligible_at: Optional[datetime] = None

    if candidate.platform != "photon":
        reasons.append("not_photon")
    if not candidate.is_dm:
        reasons.append("not_direct_message")
    if not _PHOTON_DM_CHAT_RE.fullmatch(candidate.chat_id):
        reasons.append("not_exact_photon_dm")
    if not candidate.thread_id:
        reasons.append("missing_thread_id")
    if not candidate.last_user_message_id:
        reasons.append("missing_user_message_id")
    if candidate.state in _TERMINAL_STATES:
        reasons.append(f"terminal_state:{candidate.state}")
    if candidate.last_assistant_at is not None and candidate.last_assistant_at >= candidate.last_user_at:
        reasons.append("assistant_replied_after_user")

    eligible_at = candidate.last_user_at + rules.quiet_period
    if current < eligible_at:
        reasons.append("quiet_period_not_elapsed")

    matching = [r for r in prior_records if r.get("proposal_key") == key]
    latest = max(matching, key=lambda r: (_as_utc(r.get("proposed_at", "1970-01-01T00:00:00+00:00")), str(r.get("id", ""))), default=None)
    if latest is not None:
        status = str(latest.get("status", "pending"))
        if status in _TERMINAL_STATES:
            reasons.append(f"terminal_proposal:{status}")
        elif status == "pending":
            reasons.append("proposal_pending")
        elif status in _RETRYABLE_STATES:
            attempt = int(latest.get("attempt", 1))
            if attempt >= rules.max_attempts:
                reasons.append("max_attempts_reached")
            else:
                proposed_at = _as_utc(latest["proposed_at"])
                delay = rules.cadence * (rules.backoff_factor ** max(0, attempt - 1))
                retry_at = proposed_at + delay
                if current < retry_at:
                    reasons.append("backoff_not_elapsed")
                    eligible_at = max(eligible_at, retry_at)
                # This is a positive rule result, not a blocking reason.  Keeping only
                # blockers in ``reasons`` makes ``eligible`` mechanically auditable.
        else:
            reasons.append(f"unknown_proposal_state:{status}")

    eligible = not reasons
    if eligible:
        explanation = "Photon DM has an unanswered user turn older than the quiet period; no active or terminal proposal blocks it."
    else:
        explanation = "; ".join(reasons)
    return CandidateEvaluation(eligible, key, tuple(reasons), explanation, eligible_at)


def _current_file() -> Path:
    return FOLLOW_UP_PROPOSALS_FILE or (get_hermes_home().resolve() / "photon-personal-assistant" / "store.json")


def _read_payload() -> dict[str, Any]:
    if FOLLOW_UP_PROPOSALS_FILE is None:
        from .store import load_store_payload
        raw = load_store_payload()
    else:
        path = _current_file()
        if not path.exists():
            raw = {"suggestions": []}
        else:
            try:
                raw = json.loads(path.read_text(encoding="utf-8-sig"))
            except (OSError, json.JSONDecodeError):
                raw = {}
        if not isinstance(raw, dict):
            raw = {"suggestions": raw if isinstance(raw, list) else []}
    proposals = raw.get("follow_up_proposals", [])
    return {**raw, "follow_up_proposals": proposals if isinstance(proposals, list) else []}


def _write_payload(payload: Mapping[str, Any]) -> None:
    if FOLLOW_UP_PROPOSALS_FILE is None:
        from .store import save_store_payload
        save_store_payload(dict(payload))
        return
    path = _current_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_json_write(path, dict(payload), mode=0o600)


def load_follow_up_proposals() -> list[dict[str, Any]]:
    """Load all proposal records, including terminal records retained for deduplication."""
    with _store_lock:
        return list(_read_payload()["follow_up_proposals"])


def list_follow_up_proposals(*, status: Optional[str] = None) -> list[dict[str, Any]]:
    rows = load_follow_up_proposals()
    return [row for row in rows if status is None or row.get("status") == status]


def _new_id(key: str, attempt: int) -> str:
    return hashlib.sha256(f"{key}\x1f{attempt}".encode()).hexdigest()[:16]


def propose_follow_up(
    snapshot: ThreadSnapshot | Mapping[str, Any],
    *,
    now: Optional[datetime] = None,
    policy: Optional[DetectorPolicy] = None,
) -> Optional[dict[str, Any]]:
    """Persist one proposal if eligible; never sends or schedules anything."""
    current = _now(now)
    from .store import store_transaction, create_commitment
    with store_transaction():
        with _store_lock:
            payload = _read_payload()
            records = payload["follow_up_proposals"]
            decision = evaluate_dropped_thread(snapshot, now=current, policy=policy, prior_records=records)
            if not decision.eligible:
                return None
            candidate = snapshot if isinstance(snapshot, ThreadSnapshot) else ThreadSnapshot.from_mapping(snapshot)
            matching = [r for r in records if r.get("proposal_key") == decision.proposal_key]
            attempt = max((int(r.get("attempt", 0)) for r in matching), default=0) + 1
            next_at = current + _policy(policy).cadence * (_policy(policy).backoff_factor ** max(0, attempt - 1))
            record = {
                "id": _new_id(decision.proposal_key, attempt),
                "proposal_key": decision.proposal_key,
                "source": "dropped_thread",
                "proposal_type": "follow_up",
                "platform": candidate.platform,
                "thread_id": candidate.thread_id,
                "chat_id": candidate.chat_id,
                "user_message_id": candidate.last_user_message_id,
                "status": "pending",
                "attempt": attempt,
                "proposed_at": current.isoformat(),
                "next_eligible_at": next_at.isoformat(),
                "reasons": list(decision.reasons),
                "explanation": decision.explanation,
                "title": "Follow up on an unanswered Photon conversation",
                "description": "A follow-up is available for review. Hermes will not send it automatically.",
            }
            commitment = create_commitment(
                title=record["title"], description=record["description"], kind="photon_follow_up",
                status="proposed", source="dropped_thread", target=candidate.chat_id,
                semantic_key=decision.proposal_key,
                metadata={"proposal_id": record["id"], "proposal_key": decision.proposal_key,
                          "platform": candidate.platform, "chat_id": candidate.chat_id,
                          "thread_id": candidate.thread_id, "user_message_id": candidate.last_user_message_id,
                          "requires_user_approval": True, "effect": "none"}, now=current)
            record["commitment_id"] = commitment["id"]
            # Re-read/revalidate immediately before append while the process lock remains held.
            payload = _read_payload()
            records = payload["follow_up_proposals"]
            decision = evaluate_dropped_thread(snapshot, now=current, policy=policy, prior_records=records)
            if not decision.eligible:
                return None
            records.append(record)
            _write_payload(payload)
            return dict(record)


def observe_photon_inbound(event: Mapping[str, Any], *, now: Optional[datetime] = None,
                           policy: Optional[DetectorPolicy] = None) -> Optional[dict[str, Any]]:
    """Observe one authorized Photon inbound event and create only a proposal when eligible.

    This is the production observation boundary: it consumes normalized adapter data, filters exact
    one-to-one Photon chats, and never sends or schedules outbound work.
    """
    space = event.get("space") or {}
    content = event.get("content") or {}
    if str(space.get("type") or "").strip().lower() != "dm":
        return None
    if str(content.get("type") or "").strip().lower() not in {"text", "attachment", "voice", "poll_option"}:
        return None
    timestamp = event.get("timestamp")
    if timestamp is None:
        return None
    snapshot = ThreadSnapshot.from_mapping({
        "thread_id": str(space.get("id") or ""),
        "chat_id": str(space.get("id") or ""),
        "last_user_at": timestamp,
        "last_user_message_id": str(event.get("messageId") or ""),
        "platform": "photon",
        "is_dm": True,
        "state": "open",
    })
    return propose_follow_up(snapshot, now=now, policy=policy)


def propose_follow_ups(
    snapshots: Iterable[ThreadSnapshot | Mapping[str, Any]],
    *,
    now: Optional[datetime] = None,
    policy: Optional[DetectorPolicy] = None,
) -> list[dict[str, Any]]:
    """Evaluate a batch in input order and persist only newly eligible proposals."""
    created = []
    for snapshot in snapshots:
        proposal = propose_follow_up(snapshot, now=now, policy=policy)
        if proposal is not None:
            created.append(proposal)
    return created


def _resolve_ref(records: Sequence[Mapping[str, Any]], ref: str) -> Optional[Mapping[str, Any]]:
    for record in records:
        if record.get("id") == ref or record.get("proposal_key") == ref:
            return record
    if ref.isdigit():
        pending = [r for r in records if r.get("status") == "pending"]
        index = int(ref) - 1
        if 0 <= index < len(pending):
            return pending[index]
    return None


def record_follow_up_outcome(ref: str, outcome: str) -> Optional[dict[str, Any]]:
    """Record a user/worker disposition; no disposition performs outbound work."""
    normalized = str(outcome).strip().lower()
    if normalized not in _TERMINAL_STATES | _RETRYABLE_STATES:
        raise ValueError(f"unknown follow-up outcome: {outcome!r}")
    with _store_lock:
        payload = _read_payload()
        target = _resolve_ref(payload["follow_up_proposals"], ref)
        if target is None or target.get("status") != "pending":
            return None
        _sync_commitment_outcome(target, normalized)
        payload = _read_payload()
        for record in payload["follow_up_proposals"]:
            if record.get("id") == target.get("id"):
                record["status"] = normalized
                record["resolved_at"] = _hermes_now().isoformat()
                _write_payload(payload)
                return dict(record)
    return None


def reject_follow_up_proposal(ref: str) -> Optional[dict[str, Any]]:
    return record_follow_up_outcome(ref, "rejected")


def _sync_commitment_outcome(record: Mapping[str, Any], outcome: str) -> None:
    """Reflect a proposal disposition without making any external change."""
    commitment_id = record.get("commitment_id")
    if not commitment_id:
        return
    from .store import (
        approve_commitment,
        cancel_commitment,
        complete_commitment,
        dismiss_commitment,
        get_commitment,
    )

    commitment = get_commitment(str(commitment_id))
    if commitment is None:
        return
    if outcome == "accepted":
        approve_commitment(str(commitment_id))
    elif outcome == "rejected":
        dismiss_commitment(str(commitment_id))
    elif outcome == "cancelled":
        cancel_commitment(str(commitment_id))
    elif outcome == "completed" and commitment.get("status") == "active":
        complete_commitment(str(commitment_id))


def accept_follow_up_proposal(ref: str) -> Optional[dict[str, Any]]:
    """Approve a pending proposal; this only activates its durable commitment."""
    return record_follow_up_outcome(ref, "accepted")


def approve_follow_up_proposal(ref: str) -> Optional[dict[str, Any]]:
    return accept_follow_up_proposal(ref)


def cancel_follow_up_proposal(ref: str) -> Optional[dict[str, Any]]:
    return record_follow_up_outcome(ref, "cancelled")


def complete_follow_up_proposal(ref: str) -> Optional[dict[str, Any]]:
    return record_follow_up_outcome(ref, "completed")


def defer_follow_up_proposal(ref: str) -> Optional[dict[str, Any]]:
    return record_follow_up_outcome(ref, "deferred")

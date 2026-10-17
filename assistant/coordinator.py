"""Read-only, restart-safe coordination for an authorized Photon DM.

The coordinator is deliberately a facade, not a worker.  It reads durable cron
jobs and dropped-thread proposals, optionally reads an explicitly approved
calendar provider, and accepts a caller-owned conversation-state snapshot.  It
never sends, schedules, accepts, completes, or mutates anything.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, UTC
from types import MappingProxyType
from typing import Any, Callable, Mapping, Sequence

from .calendar import (
    CalendarAccessPolicy,
    CalendarProvider,
    CalendarProviderError,
    CalendarQuery,
    CalendarSnapshot,
    get_calendar_provider,
)


_PHOTON_DM_CHAT_RE = re.compile(r"^any;-;\+\d{6,}$")


def _utc(value: datetime, field_name: str = "timestamp") -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _iso(value: Any) -> Any:
    if isinstance(value, datetime):
        return _utc(value).isoformat()
    return value


def _copy_jsonish(value: Any) -> Any:
    """Copy caller/store data without retaining mutable references or private objects."""
    if isinstance(value, Mapping):
        return {str(key): _copy_jsonish(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, (list, tuple)):
        return [_copy_jsonish(item) for item in value]
    return _iso(value)


def _freeze_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze_value(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_value(item) for item in value)
    return value


def _freeze_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    frozen = _freeze_value(value)
    if not isinstance(frozen, Mapping):
        raise TypeError("expected a mapping")
    return frozen


@dataclass(frozen=True, slots=True)
class PhotonConversationScope:
    """The exact one-to-one Photon scope permitted to receive this context."""

    chat_id: str
    user_id: str
    chat_type: str = "dm"
    platform: str = "photon"

    def __post_init__(self) -> None:
        platform = str(self.platform).strip().lower()
        chat_type = str(self.chat_type).strip().lower()
        chat_id = str(self.chat_id).strip()
        user_id = str(self.user_id).strip()
        if platform != "photon":
            raise ValueError("coordination is Photon-only")
        if chat_type != "dm":
            raise ValueError("coordination requires a direct-message scope")
        if not _PHOTON_DM_CHAT_RE.fullmatch(chat_id):
            raise ValueError("chat_id is not an exact Photon DM identifier")
        if not user_id:
            raise ValueError("user_id is required")
        object.__setattr__(self, "platform", platform)
        object.__setattr__(self, "chat_type", chat_type)
        object.__setattr__(self, "chat_id", chat_id)
        object.__setattr__(self, "user_id", user_id)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> PhotonConversationScope:
        return cls(
            platform=str(value.get("platform", "photon")),
            chat_type=str(value.get("chat_type", value.get("type", "dm"))),
            chat_id=str(value.get("chat_id", "")),
            user_id=str(value.get("user_id", "")),
        )

    def as_dict(self) -> dict[str, str]:
        return {
            "platform": self.platform,
            "chat_type": self.chat_type,
            "chat_id": self.chat_id,
            "user_id": self.user_id,
        }


class RestartAcceptanceError(ValueError):
    """Raised when a restart snapshot is not accepted for this Photon scope."""


@dataclass(frozen=True, slots=True)
class PhotonRestartAcceptance:
    """Immutable proof that a restart may be observed for one exact DM scope."""

    restart_id: str
    scope: PhotonConversationScope
    accepted_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        restart_id = str(self.restart_id).strip()
        if not restart_id:
            raise RestartAcceptanceError("restart_id is required")
        accepted_at = _utc(self.accepted_at, "accepted_at")
        expires_at = _utc(self.expires_at, "expires_at")
        if expires_at <= accepted_at:
            raise RestartAcceptanceError("restart acceptance expiry must be after acceptance")
        object.__setattr__(self, "restart_id", restart_id)
        object.__setattr__(self, "accepted_at", accepted_at)
        object.__setattr__(self, "expires_at", expires_at)

    def as_dict(self) -> dict[str, Any]:
        return {
            "restart_id": self.restart_id,
            "scope": self.scope.as_dict(),
            "accepted_at": self.accepted_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
        }


def _parse_restart_time(value: Any, field_name: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise RestartAcceptanceError(f"restart state requires {field_name}")
    try:
        return _utc(datetime.fromisoformat(value), field_name)
    except ValueError as exc:
        raise RestartAcceptanceError(f"restart state {field_name} is invalid") from exc


def validate_photon_restart_acceptance(
    scope: PhotonConversationScope | Mapping[str, Any],
    restart_state: PhotonRestartAcceptance | Mapping[str, Any],
    *,
    now: datetime,
) -> PhotonRestartAcceptance:
    """Validate an accepted, unexpired restart snapshot without external writes."""
    resolved_scope = scope if isinstance(scope, PhotonConversationScope) else PhotonConversationScope.from_mapping(scope)
    observed_at = _utc(now, "now")
    if isinstance(restart_state, PhotonRestartAcceptance):
        accepted = restart_state
    else:
        if str(restart_state.get("status", "")).strip().lower() != "accepted":
            raise RestartAcceptanceError("restart state is not accepted")
        raw_scope = restart_state.get("scope")
        if not isinstance(raw_scope, Mapping):
            raise RestartAcceptanceError("restart state has no exact scope")
        try:
            accepted = PhotonRestartAcceptance(
                restart_id=str(restart_state.get("restart_id", "")),
                scope=PhotonConversationScope.from_mapping(raw_scope),
                accepted_at=_parse_restart_time(restart_state.get("accepted_at"), "accepted_at"),
                expires_at=_parse_restart_time(restart_state.get("expires_at"), "expires_at"),
            )
        except RestartAcceptanceError:
            raise
        except (TypeError, ValueError) as exc:
            raise RestartAcceptanceError(f"invalid restart state: {exc}") from exc
    if accepted.scope != resolved_scope:
        raise RestartAcceptanceError("restart state belongs to a different Photon scope")
    if observed_at < accepted.accepted_at:
        raise RestartAcceptanceError("restart state is from the future")
    if observed_at >= accepted.expires_at:
        raise RestartAcceptanceError("restart state is stale")
    return accepted


@dataclass(frozen=True, slots=True)
class AuditRecord:
    """One deterministic, non-secret read receipt for a coordinator component."""

    component: str
    source: str
    status: str
    count: int = 0
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "component": self.component,
            "source": self.source,
            "status": self.status,
            "count": self.count,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class CoordinatorObservation:
    """A complete point-in-time observation; all fields are read-only snapshots."""

    scope: PhotonConversationScope
    observed_at: datetime
    accepted_tasks: tuple[Mapping[str, Any], ...]
    pending_dropped_thread_proposals: tuple[Mapping[str, Any], ...]
    calendar: CalendarSnapshot | None
    conversation_state: Mapping[str, Any]
    audit: tuple[AuditRecord, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "observed_at", _utc(self.observed_at, "observed_at"))
        object.__setattr__(self, "accepted_tasks", tuple(self.accepted_tasks))
        object.__setattr__(self, "pending_dropped_thread_proposals", tuple(self.pending_dropped_thread_proposals))
        object.__setattr__(self, "conversation_state", _freeze_mapping(self.conversation_state))
        object.__setattr__(self, "audit", tuple(self.audit))

    def as_dict(self) -> dict[str, Any]:
        return {
            "scope": self.scope.as_dict(),
            "observed_at": self.observed_at.isoformat(),
            "accepted_tasks": [_copy_jsonish(item) for item in self.accepted_tasks],
            "pending_dropped_thread_proposals": [
                _copy_jsonish(item) for item in self.pending_dropped_thread_proposals
            ],
            "calendar": _calendar_as_dict(self.calendar),
            "conversation_state": _copy_jsonish(self.conversation_state),
            "audit": [item.as_dict() for item in self.audit],
        }


@dataclass(frozen=True, slots=True)
class CoordinatorProposal:
    """A review-only recommendation.  It contains no executable operation."""

    proposal_id: str
    kind: str
    reason: str
    evidence: Mapping[str, Any]
    requires_user_approval: bool = True
    effect: str = "none"

    def __post_init__(self) -> None:
        if self.effect != "none":
            raise ValueError("coordination proposals cannot carry an effect")
        object.__setattr__(self, "evidence", _freeze_mapping(self.evidence))

    def as_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "kind": self.kind,
            "reason": self.reason,
            "evidence": _copy_jsonish(self.evidence),
            "requires_user_approval": self.requires_user_approval,
            "effect": self.effect,
        }


@dataclass(frozen=True, slots=True)
class CoordinationResult:
    """Observation plus deterministic review proposals."""

    observation: CoordinatorObservation
    proposals: tuple[CoordinatorProposal, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        return {
            "observation": self.observation.as_dict(),
            "proposals": [proposal.as_dict() for proposal in self.proposals],
        }


def _scope_matches(record: Mapping[str, Any], scope: PhotonConversationScope) -> bool:
    origin = record.get("origin")
    if not isinstance(origin, Mapping):
        metadata = record.get("metadata")
        origin = metadata if isinstance(metadata, Mapping) else {}
    platform = origin.get("platform", record.get("source", ""))
    return (
        str(platform).strip().lower() == "photon"
        and str(origin.get("chat_type", "dm")).strip().lower() == "dm"
        and str(origin.get("chat_id", "")).strip() == scope.chat_id
        and origin.get("user_id") == scope.user_id
    )


def _task_summary(record: Mapping[str, Any]) -> Mapping[str, Any]:
    """Expose task scheduling metadata, never its prompt or delivery credentials."""
    keys = (
        "id", "name", "title", "kind", "source", "status", "state", "enabled", "schedule_display",
        "next_run_at", "last_run_at", "last_status", "due_at",
    )
    return _freeze_mapping({key: _copy_jsonish(record[key]) for key in keys if key in record})


def _proposal_summary(record: Mapping[str, Any]) -> Mapping[str, Any]:
    keys = (
        "id", "proposal_key", "proposal_type", "source", "platform", "thread_id", "chat_id",
        "user_message_id", "status", "attempt", "proposed_at", "next_eligible_at", "reasons", "explanation",
    )
    return _freeze_mapping({key: _copy_jsonish(record[key]) for key in keys if key in record})


def _calendar_as_dict(snapshot: CalendarSnapshot | None) -> dict[str, Any] | None:
    if snapshot is None:
        return None
    return {
        "events": [
            {
                "event_id": event.event_id,
                "title": event.title,
                "start": event.start.isoformat(),
                "end": event.end.isoformat(),
                "timezone_name": event.timezone_name,
                "location": event.location,
            }
            for event in snapshot.events
        ],
        "query": {
            "start": snapshot.query.start.isoformat(),
            "end": snapshot.query.end.isoformat(),
            "max_results": snapshot.query.max_results,
        },
        "source": {
            "source": snapshot.source.source,
            "fetched_at": snapshot.source.fetched_at.isoformat(),
            "freshness": snapshot.source.freshness.value,
            "is_live": snapshot.source.is_live,
        },
    }


def _load_tasks() -> Sequence[Mapping[str, Any]]:
    """Read accepted scheduled jobs and accepted durable commitments."""
    from cron.jobs import list_jobs
    from .store import list_commitments

    jobs = list_jobs(include_disabled=True)
    commitments = [
        record for record in list_commitments()
        if record.get("status") in {"active", "blocked"}
    ]
    return (*jobs, *commitments)


def _load_proposals() -> Sequence[Mapping[str, Any]]:
    from .dropped_thread_detector import list_follow_up_proposals

    return list_follow_up_proposals(status="pending")


def observe_photon_context(
    scope: PhotonConversationScope | Mapping[str, Any],
    *,
    now: datetime,
    conversation_state: Mapping[str, Any] | None = None,
    accepted_tasks: Sequence[Mapping[str, Any]] | None = None,
    pending_proposals: Sequence[Mapping[str, Any]] | None = None,
    calendar_policy: CalendarAccessPolicy | None = None,
    calendar_provider: CalendarProvider | None = None,
    calendar_window: timedelta = timedelta(days=1),
    calendar_max_results: int = 100,
    restart_state: PhotonRestartAcceptance | Mapping[str, Any] | None = None,
) -> CoordinatorObservation:
    """Read the Photon-scoped context without performing any external or durable write.

    ``accepted_tasks`` and ``pending_proposals`` are injectable snapshots for gateway/cron
    callers that already loaded them.  When omitted, the existing profile-local cron stores
    are read.  The caller supplies ``now`` so retries and tests remain deterministic.
    """
    resolved_scope = scope if isinstance(scope, PhotonConversationScope) else PhotonConversationScope.from_mapping(scope)
    observed_at = _utc(now, "now")
    audits: list[AuditRecord] = [
        AuditRecord("scope", "caller", "accepted", 1, "exact_photon_dm"),
        AuditRecord("effects", "coordinator", "disabled", 0, "read_only_no_send_no_calendar_write"),
    ]
    if restart_state is not None:
        accepted_restart = validate_photon_restart_acceptance(resolved_scope, restart_state, now=observed_at)
        audits.append(AuditRecord("restart", "caller", "accepted", 1, accepted_restart.restart_id))

    raw_tasks = list(_load_tasks() if accepted_tasks is None else accepted_tasks)
    tasks = tuple(
        sorted(
            (_task_summary(record) for record in raw_tasks if _scope_matches(record, resolved_scope)),
            key=lambda item: (str(item.get("id", "")), str(item.get("name", ""))),
        )
    )
    audits.append(AuditRecord("accepted_tasks", "cron.jobs", "read", len(tasks), "origin_scoped"))

    raw_proposals = list(_load_proposals() if pending_proposals is None else pending_proposals)
    proposals = tuple(
        sorted(
            (
                _proposal_summary(record)
                for record in raw_proposals
                if str(record.get("platform", "")).lower() == "photon"
                and str(record.get("chat_id", "")) == resolved_scope.chat_id
            ),
            key=lambda item: (str(item.get("proposed_at", "")), str(item.get("id", ""))),
        )
    )
    audits.append(AuditRecord("pending_proposals", "photon-personal-assistant.store.follow_up_proposals", "read", len(proposals), "pending_only"))

    calendar: CalendarSnapshot | None = None
    if calendar_window <= timedelta(0):
        raise ValueError("calendar_window must be positive")
    query = CalendarQuery(observed_at, observed_at + calendar_window, calendar_max_results)
    provider = get_calendar_provider(
        calendar_policy,
        configured_provider=calendar_provider,
    )
    if calendar_policy is not None and calendar_policy.allows_read and calendar_provider is not None:
        try:
            calendar = provider.upcoming(query)
            audits.append(AuditRecord("calendar", "injected_read_only_provider", "read", len(calendar.events), "approved_bounded_window"))
        except CalendarProviderError as exc:
            audits.append(AuditRecord("calendar", "injected_read_only_provider", "unavailable", 0, type(exc).__name__))
    else:
        audits.append(AuditRecord("calendar", "photon-personal-assistant.calendar.provider", "disabled", 0, "disabled_by_default_or_not_configured"))

    state = _copy_jsonish(conversation_state or {})
    if not isinstance(state, Mapping):
        raise TypeError("conversation_state must be a mapping")
    audits.append(AuditRecord("conversation", "caller_snapshot", "read", len(state), "not_persisted"))
    return CoordinatorObservation(
        scope=resolved_scope,
        observed_at=observed_at,
        accepted_tasks=tasks,
        pending_dropped_thread_proposals=proposals,
        calendar=calendar,
        conversation_state=state,
        audit=tuple(audits),
    )


def _proposal_id(scope: PhotonConversationScope, kind: str, evidence: Mapping[str, Any]) -> str:
    canonical = repr((scope.chat_id, kind, sorted((str(k), repr(v)) for k, v in evidence.items())))
    return "coordination:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


def propose_photon_coordination(
    observation: CoordinatorObservation,
) -> CoordinationResult:
    """Turn an observation into deterministic, approval-gated review proposals.

    This function is pure: proposal generation does not persist a proposal, create a task,
    send a message, read a calendar again, or update conversation state.
    """
    generated: list[CoordinatorProposal] = []
    for record in observation.pending_dropped_thread_proposals:
        evidence = {
            "proposal_key": record.get("proposal_key", ""),
            "thread_id": record.get("thread_id", ""),
            "source_status": record.get("status", "pending"),
        }
        generated.append(
            CoordinatorProposal(
                proposal_id=_proposal_id(observation.scope, "review_dropped_thread", evidence),
                kind="review_dropped_thread",
                reason=str(record.get("explanation", "Review the pending dropped-thread proposal.")),
                evidence=evidence,
            )
        )
    return CoordinationResult(observation=observation, proposals=tuple(generated))


class PhotonCoordinator:
    """Small injectable facade for future gateway and cron callers."""

    def __init__(self, *, task_loader: Callable[[], Sequence[Mapping[str, Any]]] | None = None) -> None:
        self._task_loader = task_loader

    def observe(self, scope: PhotonConversationScope | Mapping[str, Any], **kwargs: Any) -> CoordinatorObservation:
        if "restart_state" in kwargs:
            validate_photon_restart_acceptance(scope, kwargs["restart_state"], now=kwargs["now"])
        if self._task_loader is not None and "accepted_tasks" not in kwargs:
            kwargs["accepted_tasks"] = self._task_loader()
        return observe_photon_context(scope, **kwargs)

    def observe_after_restart(
        self,
        scope: PhotonConversationScope | Mapping[str, Any],
        *,
        restart_state: PhotonRestartAcceptance | Mapping[str, Any],
        **kwargs: Any,
    ) -> CoordinatorObservation:
        """Observe only after the caller supplies accepted, unexpired restart proof."""
        kwargs["restart_state"] = restart_state
        return self.observe(scope, **kwargs)

    def propose(self, observation: CoordinatorObservation) -> CoordinationResult:
        return propose_photon_coordination(observation)


__all__ = [
    "AuditRecord",
    "CoordinationResult",
    "CoordinatorObservation",
    "CoordinatorProposal",
    "PhotonConversationScope",
    "PhotonCoordinator",
    "PhotonRestartAcceptance",
    "RestartAcceptanceError",
    "observe_photon_context",
    "propose_photon_coordination",
    "validate_photon_restart_acceptance",
]

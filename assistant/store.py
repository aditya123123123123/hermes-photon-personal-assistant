"""Suggested cron jobs and durable commitment/reminder proposals."""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import unicodedata
import uuid
from contextlib import contextmanager
from datetime import datetime, UTC

from hermes_cli.active_sessions import _FileLock
from pathlib import Path
from typing import Any, Mapping, Optional
from zoneinfo import ZoneInfo

from hermes_constants import get_hermes_home
from hermes_time import get_timezone_name as _hermes_timezone_name
from hermes_time import now as _hermes_now
from utils import atomic_json_write

logger = logging.getLogger(__name__)
SUGGESTIONS_FILE: Optional[Path] = None
_suggestions_lock = threading.RLock()
MAX_PENDING = 5
VALID_SOURCES = frozenset({"catalog", "blueprint", "usage", "integration"})
_STATUS_PENDING = "pending"
_STATUS_ACCEPTED = "accepted"
_STATUS_DISMISSED = "dismissed"
COMMITMENT_STATES = frozenset({"tentative", "proposed", "awaiting_approval", "active", "blocked", "completed", "dismissed", "cancelled", "expired", "stale"})
_COMMITMENT_TERMINAL_STATES = frozenset({"completed", "dismissed", "cancelled", "expired", "stale"})
_COMMITMENT_TRANSITIONS = {
    "tentative": frozenset({"proposed", "awaiting_approval", "active", "dismissed", "cancelled", "expired", "stale"}),
    "proposed": frozenset({"awaiting_approval", "active", "dismissed", "cancelled", "expired", "stale"}),
    "awaiting_approval": frozenset({"active", "dismissed", "cancelled", "expired", "stale"}),
    "active": frozenset({"blocked", "completed", "dismissed", "cancelled", "expired", "stale"}),
    "blocked": frozenset({"completed", "cancelled", "expired", "stale"}),
}
_COMMITMENT_STATUS_ALIASES = {"accepted": "active", "approved": "active"}
_COMMITMENT_NAMESPACE = "commitments"
_store_transaction_state = threading.local()


@contextmanager
def store_transaction(lock_path: Optional[Path] = None):
    """Serialize one store read/decision/write transaction across processes."""
    del lock_path
    depth = getattr(_store_transaction_state, "depth", 0)
    if depth:
        _store_transaction_state.depth = depth + 1
        try:
            yield
        finally:
            _store_transaction_state.depth = depth
        return
    with _suggestions_lock:
        with _FileLock(_current_suggestions_file().with_suffix(".lock")):
            _store_transaction_state.depth = 1
            try:
                yield
            finally:
                _store_transaction_state.depth = 0


def _in_store_transaction() -> bool:
    return bool(getattr(_store_transaction_state, "depth", 0))


def _normalize_commitment_status(status: Any) -> str:
    value = str(status or "").strip().lower().replace("-", "_").replace(" ", "_")
    value = _COMMITMENT_STATUS_ALIASES.get(value, value)
    if value not in COMMITMENT_STATES:
        raise ValueError(f"unknown commitment status: {status!r}")
    return value


def _commitment_timezone(value: Any) -> tuple[str, ZoneInfo]:
    name = str(value or "").strip() or _hermes_timezone_name() or "UTC"
    try:
        return name, ZoneInfo(name)
    except (KeyError, ValueError) as exc:
        raise ValueError(f"invalid commitment timezone: {name!r}") from exc


def _commitment_instant(value: Any, tz: ZoneInfo) -> Optional[datetime]:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str):
        try:
            result = datetime.fromisoformat(value.strip())
        except ValueError as exc:
            raise ValueError(f"invalid commitment timestamp: {value!r}") from exc
    else:
        raise TypeError("commitment timestamps must be datetime objects or ISO-8601 strings")
    if result.tzinfo is None:
        result = result.replace(tzinfo=tz)
    return result.astimezone(UTC)


def _commitment_iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value is not None else None


def _semantic_text(value: Any) -> str:
    return unicodedata.normalize("NFKC", str(value or "")).strip().casefold()


def _commitment_semantic_key(*, kind: Any, title: Any, due_at: Optional[datetime], timezone_name: str, target: Any, source: Any, semantic_key: Any) -> str:
    explicit = _semantic_text(semantic_key)
    basis = {"explicit": explicit} if explicit else {"kind": _semantic_text(kind) or "commitment", "title": _semantic_text(title), "due_at": _commitment_iso(due_at) or "", "timezone": timezone_name, "target": _semantic_text(target), "source": _semantic_text(source)}
    encoded = json.dumps(basis, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "commitment:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:32]


def _commitment_records(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    records = payload.get(_COMMITMENT_NAMESPACE, [])
    return records if isinstance(records, list) else []


def _current_suggestions_file() -> Path:
    return SUGGESTIONS_FILE or (get_hermes_home().resolve() / "photon-personal-assistant" / "store.json")


def _ensure_dir() -> None:
    _current_suggestions_file().parent.mkdir(parents=True, exist_ok=True, mode=0o700)


def _load_raw() -> dict[str, Any]:
    suggestions_file = _current_suggestions_file()
    if not suggestions_file.exists():
        return {"suggestions": []}
    try:
        with open(suggestions_file, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("suggestions.json unreadable (%s); starting empty", e)
        return {"suggestions": []}
    if isinstance(data, dict) and isinstance(data.get("suggestions"), list):
        return data
    if isinstance(data, list):
        return {"suggestions": data}
    logger.warning("suggestions.json malformed; starting empty")
    return {"suggestions": []}


def _save_store_payload(payload: dict[str, Any]) -> None:
    _ensure_dir()
    payload = dict(payload)
    payload["updated_at"] = _hermes_now().isoformat()
    atomic_json_write(_current_suggestions_file(), payload, mode=0o600)


def load_store_payload() -> dict[str, Any]:
    with _suggestions_lock:
        return _load_raw()


def save_store_payload(payload: dict[str, Any]) -> None:
    if not isinstance(payload, dict) or not isinstance(payload.get("suggestions", []), list):
        raise ValueError("suggestion store payload must contain a suggestions list")
    with _suggestions_lock:
        _save_store_payload(payload)






















def _commitment_now(value: Any, tz: ZoneInfo) -> datetime:
    result = _commitment_instant(value if value is not None else _hermes_now(), tz)
    assert result is not None
    return result


def _copy_commitment(record: Mapping[str, Any]) -> dict[str, Any]:
    return dict(record)


def _resolve_commitment(records: list[dict[str, Any]], ref: str) -> Optional[dict[str, Any]]:
    wanted = str(ref or "").strip()
    for record in records:
        if wanted in {str(record.get("id", "")), str(record.get("semantic_key", ""))}:
            return record
    if wanted.isdigit() and 0 <= int(wanted) - 1 < len(records):
        return records[int(wanted) - 1]
    return None


def create_commitment(*, title: Any = "", description: Any = "", kind: Any = "commitment", status: str = "tentative", due_at: Any = None, expires_at: Any = None, stale_at: Any = None, timezone: Any = None, target: Any = "", source: Any = "photon", semantic_key: Any = "", task_spec: Optional[Mapping[str, Any]] = None, metadata: Optional[Mapping[str, Any]] = None, now: Any = None) -> dict[str, Any]:
    normalized_status = _normalize_commitment_status(status)
    timezone_name, tz = _commitment_timezone(timezone)
    due, expires, stale = (_commitment_instant(v, tz) for v in (due_at, expires_at, stale_at))
    current = _commitment_now(now, tz)
    semantic = _commitment_semantic_key(kind=kind, title=title, due_at=due, timezone_name=timezone_name, target=target, source=source, semantic_key=semantic_key)
    def _create() -> dict[str, Any]:
        payload = _load_raw()
        records = _commitment_records(payload)
        for existing in records:
            if existing.get("semantic_key") == semantic:
                return _copy_commitment(existing)
        record = {"id": hashlib.sha256(semantic.encode("utf-8")).hexdigest()[:20], "semantic_key": semantic, "source": _semantic_text(source) or "photon", "kind": _semantic_text(kind) or "commitment", "title": str(title or "").strip(), "description": str(description or "").strip(), "target": str(target or "").strip(), "timezone": timezone_name, "due_at": _commitment_iso(due), "expires_at": _commitment_iso(expires), "stale_at": _commitment_iso(stale), "status": normalized_status, "state": normalized_status, "created_at": _commitment_iso(current), "updated_at": _commitment_iso(current), "task_spec": dict(task_spec) if isinstance(task_spec, Mapping) else {}, "metadata": dict(metadata) if isinstance(metadata, Mapping) else {}, "task_created": False}
        records.append(record)
        payload[_COMMITMENT_NAMESPACE] = records
        _save_store_payload(payload)
        return _copy_commitment(record)
    if _in_store_transaction():
        return _create()
    with store_transaction():
        return _create()


def list_commitments(*, status: Optional[str] = None) -> list[dict[str, Any]]:
    normalized = _normalize_commitment_status(status) if status is not None else None
    with _suggestions_lock:
        return [_copy_commitment(row) for row in _commitment_records(_load_raw()) if normalized is None or row.get("status") == normalized]


def get_commitment(ref: str) -> Optional[dict[str, Any]]:
    with _suggestions_lock:
        record = _resolve_commitment(_commitment_records(_load_raw()), ref)
        return _copy_commitment(record) if record is not None else None


def _stop_commitment_task(record: Mapping[str, Any]) -> None:
    """Remove scheduler work when a durable commitment becomes terminal.

    A cancelled/completed commitment must not leave an already-approved reminder
    runnable. Missing jobs are harmless because the durable commitment remains
    authoritative and the operation is idempotent.
    """
    job_id = record.get("task_job_id")
    if not job_id:
        return
    from cron.jobs import remove_job
    remove_job(str(job_id))


def transition_commitment(ref: str, status: str, *, now: Any = None, evidence: Optional[Mapping[str, Any]] = None) -> Optional[dict[str, Any]]:
    target_status = _normalize_commitment_status(status)
    with store_transaction():
        payload = _load_raw()
        records = _commitment_records(payload)
        record = _resolve_commitment(records, ref)
        if record is None:
            return None
        current_status = _normalize_commitment_status(record.get("status", "tentative"))
        entry = dict(evidence) if evidence is not None else None
        history = record.get("lifecycle_evidence", [])
        if current_status == target_status and (entry is None or entry in history):
            return _copy_commitment(record)
        if current_status == target_status and current_status in _COMMITMENT_TERMINAL_STATES:
            raise ValueError("Terminal commitment evidence cannot be overwritten")
        if current_status != target_status and target_status not in _COMMITMENT_TRANSITIONS.get(current_status, frozenset()):
            raise ValueError(f"cannot transition commitment from {current_status!r} to {target_status!r}")
        timezone_name, tz = _commitment_timezone(record.get("timezone"))
        timestamp = _commitment_now(now, tz)
        record["status"] = record["state"] = target_status
        record["timezone"] = timezone_name
        record["updated_at"] = _commitment_iso(timestamp)
        record[f"{target_status}_at"] = _commitment_iso(timestamp)
        if entry is not None:
            record["lifecycle_evidence"] = [*history, entry]
        _save_store_payload(payload)
        result = _copy_commitment(record)
        if target_status in _COMMITMENT_TERMINAL_STATES:
            _stop_commitment_task(result)
        return result


def _approved_delivery(task_spec, origin):
    import re
    deliver = task_spec.get("deliver") or "local"
    if deliver != "local":
        chat_id = str(origin.get("chat_id") or "")
        if (origin.get("platform") != "photon"
                or not re.fullmatch(r"any;-;\+\d{6,}", chat_id)
                or deliver != f"photon:{chat_id}"):
            raise ValueError("reminder delivery must match its exact Photon DM origin")
    return deliver


def _ensure_commitment_task(record: dict[str, Any]) -> None:
    """Materialize an approved reminder through the canonical cron scheduler once.

    Extraction is durable and silent; only an explicit transition to ``active`` may
    create work.  The commitment id is carried in ``origin`` so a crash after cron
    creation but before the commitment write can safely recover without a duplicate.
    Empty task specs intentionally remain durable commitments with no scheduled work.
    """
    task_spec = record.get("task_spec")
    if record.get("task_created") or not isinstance(task_spec, Mapping) or not task_spec:
        return
    from cron.jobs import load_jobs
    from cron.scheduler import create_job_with_scheduler_registration

    commitment_id = str(record.get("id", ""))
    origin = dict(record.get("metadata") or {})
    origin.update({
        "source": "commitment",
        "commitment_id": commitment_id,
        "platform": origin.get("platform", ""),
        "chat_id": origin.get("chat_id") or record.get("target", ""),
    })
    existing = next(
        (job for job in load_jobs()
         if isinstance(job.get("origin"), Mapping)
         and str(job["origin"].get("commitment_id", "")) == commitment_id),
        None,
    )
    if existing is None:
        schedule = task_spec.get("schedule") or record.get("due_at")
        prompt = task_spec.get("prompt") or record.get("description") or record.get("title")
        if not schedule or not isinstance(prompt, str) or not prompt.strip():
            return
        deliver = _approved_delivery(task_spec, origin)
        existing = create_job_with_scheduler_registration(
            prompt=prompt.strip(), schedule=str(schedule),
            name=task_spec.get("name") or record.get("title") or f"commitment-{commitment_id}",
            repeat=1, deliver=deliver, origin=origin,
            enabled_toolsets=[], attach_to_session=False,
        )
    else:
        # A prior process may have died after saving the job, before registration
        # or the commitment marker. Re-register that same job, never create another.
        from cron.scheduler_provider import resolve_cron_scheduler
        if existing.get("enabled", True):
            resolve_cron_scheduler().register_job(existing)
    record["task_created"] = True
    record["task_job_id"] = existing.get("id")


def approve_commitment(ref: str, *, now: Any = None) -> Optional[dict[str, Any]]:
    """Approve a commitment and materialize its reminder exactly once.

    Approval is the only transition that creates scheduler work.  The same
    cross-process store lock covers the status change, cron lookup, and durable
    task-created marker, so retries recover an existing job instead of emitting
    a duplicate reminder.
    """
    with store_transaction():
        payload = _load_raw()
        records = _commitment_records(payload)
        record = _resolve_commitment(records, ref)
        if record is None:
            return None
        current_status = _normalize_commitment_status(record.get("status", "tentative"))
        if current_status != "active":
            if "active" not in _COMMITMENT_TRANSITIONS.get(current_status, frozenset()):
                raise ValueError(
                    f"cannot transition commitment from {current_status!r} to 'active'"
                )
            timezone_name, tz = _commitment_timezone(record.get("timezone"))
            timestamp = _commitment_now(now, tz)
            record["status"] = record["state"] = "active"
            record["timezone"] = timezone_name
            record["updated_at"] = _commitment_iso(timestamp)
            record["active_at"] = _commitment_iso(timestamp)
        # Persist explicit approval before scheduler creation; failed registration
        # is recoverable through the job's commitment_id without re-creating it.
        _save_store_payload(payload)
        _ensure_commitment_task(record)
        _save_store_payload(payload)
        return _copy_commitment(record)


def accept_commitment(ref: str, *, now: Any = None) -> Optional[dict[str, Any]]:
    return approve_commitment(ref, now=now)


def complete_commitment(ref: str, *, now: Any = None) -> Optional[dict[str, Any]]:
    return transition_commitment(ref, "completed", now=now)


def dismiss_commitment(ref: str, *, now: Any = None) -> Optional[dict[str, Any]]:
    return transition_commitment(ref, "dismissed", now=now)


def cancel_commitment(ref: str, *, now: Any = None) -> Optional[dict[str, Any]]:
    return transition_commitment(ref, "cancelled", now=now)


def expire_commitment(ref: str, *, now: Any = None) -> Optional[dict[str, Any]]:
    return transition_commitment(ref, "expired", now=now)


def mark_commitment_stale(ref: str, *, now: Any = None) -> Optional[dict[str, Any]]:
    return transition_commitment(ref, "stale", now=now)


def expire_stale_commitments(*, now: Any = None) -> list[dict[str, Any]]:
    changed: list[dict[str, Any]] = []
    with store_transaction():
        payload = _load_raw()
        records = _commitment_records(payload)
        for record in records:
            current_status = str(record.get("status", ""))
            if current_status in _COMMITMENT_TERMINAL_STATES:
                continue
            timezone_name, tz = _commitment_timezone(record.get("timezone"))
            current = _commitment_now(now, tz)
            expires_at = _commitment_instant(record.get("expires_at"), tz)
            stale_at = _commitment_instant(record.get("stale_at"), tz)
            target = "expired" if expires_at is not None and expires_at <= current else ("stale" if stale_at is not None and stale_at <= current else None)
            if target is not None and target in _COMMITMENT_TRANSITIONS.get(current_status, frozenset()):
                record["status"] = record["state"] = target
                record["timezone"] = timezone_name
                record["updated_at"] = _commitment_iso(current)
                record[f"{target}_at"] = _commitment_iso(current)
                changed.append(_copy_commitment(record))
        if changed:
            _save_store_payload(payload)
    return changed

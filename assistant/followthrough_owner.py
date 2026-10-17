"""One profile-local follow-through owner, never selected by message text.

The legacy adapter cannot enforce this seam. Native activation therefore refuses
while that executable remains installed. Selection changes no tasks, jobs or skills.
"""
from __future__ import annotations

import sys

from gateway.session_context import get_session_env
from hermes_constants import get_hermes_home

OWNERS = ("auto", "legacy", "native", "disabled")


def workflow_owner() -> str:
    from hermes_cli.config import load_config_readonly, require_readable_config_before_write

    # Validate without the config loader's recovery-to-default behavior: corrupt
    # selection must not silently enable native writes. This guard reads only.
    try:
        require_readable_config_before_write()
    except Exception as exc:
        raise ValueError("Profile configuration unreadable; workflow selection disabled") from exc
    config = load_config_readonly().get("photon_followthrough", {})
    if not isinstance(config, dict):
        raise ValueError("Invalid profile-local photon_followthrough selection")
    from .settings import setting
    selected = config.get("owner", setting("owner", "auto"))
    if selected not in OWNERS:
        raise ValueError("Unknown profile-local follow-through owner")
    home = get_hermes_home()
    legacy = (home / "scripts" / "messages_followthrough.py").exists()
    # Installed skill instructions also establish an existing workflow. Merely
    # shipping this plugin must not silently supersede them.
    installed_skill = any((home / "skills").glob("**/messaging-personal-assistant/SKILL.md"))
    if selected == "auto":
        return "legacy" if legacy or installed_skill else "native"
    if selected == "native" and legacy:
        raise ValueError("Legacy adapter is still installed; retire it before native selection")
    return selected


def _require_quiescent() -> None:
    """Refuse cutover/rollback while either workflow still owns unresolved work.

    No adoption, migration, cancellation or scheduler execution is implicit in
    selecting an owner. Operator reconciles records through their existing owner.
    """
    import sqlite3

    from . import store as suggestions
    from cron.jobs import load_jobs

    for job in load_jobs():
        legacy = str(job.get("prompt", "")).startswith("MESSAGES_FOLLOWTHROUGH_V1 ")
        native = (job.get("origin") or {}).get("source") == "commitment"
        if (legacy or native) and job.get("enabled", True):
            raise ValueError("Resolve existing follow-through reminders before switching owners")
    if any(row.get("source") == "photon_conversation_cli" and row.get("status") not in
           {"completed", "cancelled", "expired", "dismissed", "stale"}
           for row in suggestions.list_commitments()):
        raise ValueError("Resolve native captures/proposals before switching owners")
    board = get_hermes_home() / "kanban.db"
    if board.exists():
        with sqlite3.connect(board.resolve().as_uri() + "?mode=ro", uri=True) as conn:
            if conn.execute("SELECT 1 FROM tasks WHERE created_by=? AND status NOT IN ('done','archived') LIMIT 1",
                            ("messages-followthrough",)).fetchone():
                raise ValueError("Resolve legacy captures before switching owners")


def require_native_owner() -> None:
    if workflow_owner() != "native":
        raise ValueError("Native follow-through is not the selected owner; use the existing workflow, never both")


def select_owner(owner: str, *, expected: str) -> dict:
    """Operator-only reversible selection; not a conversational approval escape.

    Native selection requires separately retiring the legacy entry point. Rollback
    selects legacy before restoring it. No record migration or job activation.
    """
    from hermes_cli.active_sessions import _FileLock
    from . import store as suggestions
    from hermes_cli.config import require_readable_config_before_write, _write_user_config

    if (not sys.stdin.isatty() or get_session_env("HERMES_SESSION_ID")
            or get_session_env("HERMES_CRON_SESSION") == "1"):
        raise ValueError("Workflow selection requires a local operator TTY outside agent sessions")
    if owner not in OWNERS:
        raise ValueError("Unknown workflow owner")
    home = get_hermes_home()
    with _FileLock(home / ".photon-followthrough-selection.lock"), suggestions.store_transaction():
        previous = workflow_owner()
        if previous != expected:
            raise ValueError("Workflow owner changed; inspect owner status before selecting")
        if owner == "native" and (home / "scripts" / "messages_followthrough.py").exists():
            raise ValueError("Legacy adapter is still installed; retire it before native selection")
        path = home / "config.yaml"
        raw = require_readable_config_before_write(path)
        section = raw.setdefault("photon_followthrough", {})
        if not isinstance(section, dict):
            raise ValueError("Invalid profile-local photon_followthrough selection")
        before = section.get("owner", "auto")
        if owner != previous and owner != "disabled":
            _require_quiescent()
        section["owner"] = owner
        _write_user_config(path, raw)
        selected = workflow_owner()
        if owner != "auto" and selected != owner:
            # A managed override must not leave a misleading partial selection.
            section["owner"] = before
            _write_user_config(path, raw)
            raise ValueError("Managed configuration prevented workflow selection")
        return {"owner": selected, "previous_owner": previous,
                "previous_selection": before, "selection": owner,
                "activation": "fresh session required; records and jobs unchanged"}

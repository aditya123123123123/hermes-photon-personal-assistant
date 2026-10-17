"""Evidence-backed transitions on existing commitment records, never a new ledger."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import stat

from . import store as suggestions


def _evidence(args) -> dict:
    path = Path(args.artifact)
    if not path.is_absolute() or not re.fullmatch(r"[a-f0-9]{64}", args.sha256):
        raise ValueError("Absolute artifact path and literal lowercase SHA256 required")
    if not args.contains.strip() or not args.summary.strip():
        raise ValueError("Expected artifact content and outcome summary are required")
    # Open once, refuse links/devices/FIFOs, bound the read, and hash those bytes.
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > 2_000_000:
            raise ValueError("Evidence must be a regular file up to 2 MB")
        raw = stream.read(2_000_001)
    if (len(raw) > 2_000_000 or hashlib.sha256(raw).hexdigest() != args.sha256
            or args.contains.encode() not in raw):
        raise ValueError("Evidence readback did not match hash and expected content")
    return {"artifact": str(path), "sha256": args.sha256, "contains": args.contains}


def transition(record, args, event):
    if record.get("task_spec") or record.get("kind") != "photon_commitment":
        raise ValueError("Evidence transitions apply only to accepted general captures, not reminder execution")
    if not (record.get("metadata") or {}).get("acceptance"):
        raise ValueError("Only an explicitly accepted capture can be blocked or completed")
    proof = _evidence(args)
    target = "blocked" if args.commitment_command == "block" else "completed"
    if target == "blocked" and not args.next_action.strip():
        raise ValueError("A blocked capture requires a concrete next action")
    if target == "completed" and any(
        row.get("status") == "blocked" and row.get("evidence", {}).get("sha256") == proof["sha256"]
        for row in record.get("lifecycle_evidence", [])
    ):
        raise ValueError("Blocker evidence is not completion evidence; verify a successful outcome artifact")
    entry = {"status": target, "summary": args.summary.strip(), "evidence": proof,
             "next_action": args.next_action.strip() if target == "blocked" else None,
             "source_event": {k: event[k] for k in ("session_id", "message_id", "id")}}
    return suggestions.transition_commitment(record["id"], target, evidence=entry)

"""Offline subprocess acceptance: registered Photon CLI -> durable cron -> hook."""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, UTC

import pytest

ROOT = Path(__file__).resolve().parents[1]
CHAT = "any;-;+15555550123"
USER = "+15555550123"  # A real sidecar sender is NOT an any;-; chat identifier.
QUOTE = "Remind me in 2h to review the report"
BOOT = '''
import argparse, json, socket, sys
# No provider, sidecar, calendar, Gmail, or model calls may escape this fixture.
def refuse(*a, **k): raise AssertionError("offline test attempted network")
socket.socket.connect = refuse
if sys.argv[1] == "context":
    from assistant.personal_assistant import _on_pre_llm_call
    print(json.dumps(_on_pre_llm_call(platform="photon", session_id="fixture-session", user_message="report")))
else:
    from hermes_cli.plugins import get_plugin_manager
    pm = get_plugin_manager()
    pm.discover_and_load()
    command = pm._cli_commands["assistant"]
    parser = argparse.ArgumentParser()
    command["setup_fn"](parser)
    args = parser.parse_args(sys.argv[1:])
    raise SystemExit(args.func(args))
'''


@pytest.fixture
def bound(tmp_path):
    home = tmp_path / "profile"
    home.mkdir()
    (home / "plugins").mkdir()
    (home / "plugins" / "photon-personal-assistant").symlink_to(ROOT, target_is_directory=True)
    (home / "config.yaml").write_text("plugins:\n  enabled: [photon-platform, photon-personal-assistant]\n")
    env = {k: v for k, v in os.environ.items() if not k.startswith(("HERMES_", "__HERMES", "_HERMES"))}
    env.update(HERMES_HOME=str(home), HERMES_TIMEZONE="UTC", HERMES_SESSION_ID="fixture-session",
               HERMES_SESSION_PLATFORM="photon", HERMES_SESSION_CHAT_TYPE="dm",
               HERMES_SESSION_CHAT_ID=CHAT, HERMES_SESSION_USER_ID=USER,
               HERMES_SESSION_MESSAGE_ID="fixture-event", HERMES_SESSION_KEY="fixture-key",
               HERMES_SESSION_PROFILE="default")
    # The real gateway persistence API/schema, not a hand-built stand-in table.
    from hermes_state import SessionDB
    db = SessionDB(db_path=home / "state.db")
    origin = dict(platform="photon", chat_id=CHAT, user_id=USER, chat_type="dm")
    db.create_session("fixture-session", source="photon", user_id=USER, chat_id=CHAT,
                      chat_type="dm", session_key="fixture-key", profile_name="default",
                      origin_json=json.dumps(origin))
    stamp = datetime.now(UTC) - timedelta(seconds=20)
    db.append_message("fixture-session", "user", QUOTE,
                      platform_message_id="fixture-event", timestamp=stamp.timestamp())
    db.close()
    return home, env, stamp


def call(bound, *args, ok=True):
    _, env, _ = bound
    run = subprocess.run([sys.executable, "-c", BOOT, *args], cwd=ROOT, env=env,
                         capture_output=True, text=True, timeout=30, check=False)
    assert run.returncode == (0 if ok else 1), run.stdout + run.stderr
    payload = json.loads(run.stdout)
    if args[0] != "context":
        assert payload["ok"] is ok
    return payload.get("result", payload)


def request(bound, command="request", **kwargs):
    args = ["commitment", command, "--title", "Report review", "--prompt", "Review the report", "--key", "report"]
    if command != "capture":
        args += ["--at", "in 2h"]
    if command != "propose":
        args += ["--accepted", "--acceptance", QUOTE]
    return call(bound, *args, **kwargs)


def test_request_restart_readback_context_cancel_and_tombstone(bound):
    first = request(bound)
    retry = request(bound)
    assert first["id"] == retry["id"]
    assert len(retry["jobs"]) == 1
    job = retry["jobs"][0]
    assert job["enabled"] and job["repeat"]["times"] == 1
    assert job["deliver"] == f"photon:{CHAT}"
    assert datetime.fromisoformat(job["next_run_at"]) == bound[2] + timedelta(hours=2)
    assert call(bound, "commitment", "show", first["id"])["task_job_id"] == job["id"]
    context = call(bound, "context")["context"]
    assert "Report review" in context and first["id"] in context
    cancelled = call(bound, "commitment", "cancel", first["id"])
    assert cancelled["status"] == "cancelled" and not cancelled["jobs"]
    assert not call(bound, "commitment", "show", first["id"])["jobs"]
    assert call(bound, "commitment", "cancel", first["id"])["status"] == "cancelled"
    assert request(bound)["status"] == "cancelled"
    assert "Report review" not in call(bound, "context")["context"]


def test_proposal_requires_acceptance_and_capture_never_dispatches(bound):
    proposed = request(bound, "propose")
    assert proposed["status"] == "proposed" and not proposed["jobs"]
    assert "Report review" not in call(bound, "context")["context"]
    call(bound, "commitment", "approve", proposed["id"], "--accepted", "--acceptance", "invented", ok=False)
    active = call(bound, "commitment", "approve", proposed["id"], "--accepted", "--acceptance", QUOTE)
    assert active["status"] == "active" and len(active["jobs"]) == 1
    bound[1]["HERMES_SESSION_MESSAGE_ID"] = "capture-event"
    with sqlite3.connect(bound[0] / "state.db") as conn:
        conn.execute("UPDATE messages SET platform_message_id='capture-event'")
    captured = request(bound, "capture")
    assert captured["status"] == "active" and not captured["jobs"]
    assert captured["execution"] == "not_dispatched"


@pytest.mark.parametrize("key,value", [
    ("HERMES_SESSION_PLATFORM", "cli"), ("HERMES_SESSION_CHAT_TYPE", "group"),
    ("HERMES_SESSION_USER_ID", "other-sender"), ("HERMES_SESSION_MESSAGE_ID", "wrong-event"),
    ("HERMES_SESSION_KEY", "wrong-key"), ("HERMES_SESSION_PROFILE", "other"),
    ("HERMES_CRON_SESSION", "1"), ("HERMES_DELEGATED_CHILD_CONTEXT", "1"),
])
def test_unbound_wrong_origin_and_background_refused(bound, key, value):
    bound[1][key] = value
    request(bound, ok=False)
    assert not (bound[0] / "cron" / "jobs.json").exists()


def test_missing_event_store_and_cross_dm_readback_refuse(bound):
    record = request(bound)
    bound[1]["HERMES_SESSION_CHAT_ID"] = "any;-;+15555550456"
    call(bound, "commitment", "show", record["id"], ok=False)
    bound[1]["HERMES_SESSION_CHAT_ID"] = CHAT
    with sqlite3.connect(bound[0] / "state.db") as conn:
        conn.execute("DELETE FROM messages")
    call(bound, "commitment", "cancel", record["id"], ok=False)


def test_concurrent_retries_only_one_job(bound):
    args = ["commitment", "request", "--title", "Report review", "--prompt", "Review the report",
            "--key", "report", "--at", "in 2h", "--accepted", "--acceptance", QUOTE]
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: call(bound, *args), range(2)))
    assert results[0]["task_job_id"] == results[1]["task_job_id"]
    assert len(call(bound, "commitment", "list")) == 1


def test_past_time_changed_intent_and_consumed_request_do_not_reschedule(bound):
    call(bound, "commitment", "request", "--title", "Old", "--prompt", "Old",
         "--at", "2000-01-01T00:00:00Z", "--accepted", "--acceptance", QUOTE, ok=False)
    first = request(bound)
    call(bound, "commitment", "request", "--title", "Changed", "--prompt", "Other",
         "--key", "report", "--at", "in 2h", "--accepted", "--acceptance", QUOTE, ok=False)
    # Cron consumes/removes a one-shot: marker remains a tombstone, not resend authority.
    path = bound[0] / "cron" / "jobs.json"
    payload = json.loads(path.read_text())
    payload["jobs"] = []
    path.write_text(json.dumps(payload))
    retried = request(bound)
    assert retried["task_job_id"] == first["task_job_id"] and not retried["jobs"]


def test_create_before_marker_recovery_reuses_existing_job(bound):
    first = request(bound)
    path = bound[0] / "photon-personal-assistant" / "store.json"
    payload = json.loads(path.read_text())
    row = payload["commitments"][0]
    row["task_created"] = False
    row.pop("task_job_id", None)
    path.write_text(json.dumps(payload))
    recovered = request(bound)
    assert recovered["task_job_id"] == first["task_job_id"]
    assert len(recovered["jobs"]) == 1


def test_capture_evidence_lifecycle_restart_and_no_dispatch(bound):
    import hashlib

    captured = request(bound, "capture")
    artifact = bound[0] / "result.txt"
    def change(command, text, summary, **kwargs):
        artifact.write_text(text)
        args = ["commitment", command, captured["id"], "--artifact", str(artifact),
                "--sha256", hashlib.sha256(text.encode()).hexdigest(), "--contains", text,
                "--summary", summary, "--accepted", "--acceptance", QUOTE]
        if command == "block":
            args += ["--next-action", "User signs in"]
        return call(bound, *args, **kwargs)

    blocked = change("block", "Login required", "Access unavailable")
    assert blocked["status"] == "blocked" and not blocked["jobs"]
    assert change("block", "Login required", "Access unavailable")["lifecycle_evidence"] == blocked["lifecycle_evidence"]
    change("complete", "Login required", "Done", ok=False)
    assert call(bound, "commitment", "show", captured["id"])["status"] == "blocked"
    done = change("complete", "Verified report artifact", "Report reviewed")
    assert done["status"] == "completed" and done["execution"] == "not_dispatched"
    assert len(done["lifecycle_evidence"]) == 2
    assert change("complete", "Verified report artifact", "Report reviewed")["lifecycle_evidence"] == done["lifecycle_evidence"]
    change("complete", "Different result", "Changed claim", ok=False)
    change("block", "New blocker", "Changed state", ok=False)
    assert request(bound, "capture")["status"] == "completed"
    assert not (bound[0] / "cron" / "jobs.json").exists()


def test_evidence_and_owner_refusals_write_nothing(bound):
    captured = request(bound, "capture")
    artifact = bound[0] / "result.txt"
    artifact.write_text("result")
    call(bound, "commitment", "complete", captured["id"], "--artifact", str(artifact),
         "--sha256", "0" * 64, "--contains", "result", "--summary", "Done",
         "--accepted", "--acceptance", QUOTE, ok=False)
    assert call(bound, "commitment", "show", captured["id"])["status"] == "active"
    legacy = bound[0] / "scripts" / "messages_followthrough.py"
    legacy.parent.mkdir()
    legacy.write_text("# synthetic installed owner, never executed")
    assert call(bound, "commitment", "owner")["owner"] == "legacy"
    request(bound, ok=False)
    call(bound, "commitment", "owner", "--select", "native", "--expected", "legacy", ok=False)
    assert (bound[0] / "config.yaml").read_text() == "plugins:\n  enabled: [photon-platform, photon-personal-assistant]\n"
    assert not (bound[0] / "cron" / "jobs.json").exists()

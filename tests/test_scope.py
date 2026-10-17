"""Production-path contracts for Photon personal-assistant event scoping."""
from __future__ import annotations

from typing import Any

import pytest

from gateway.config import Platform, PlatformConfig
from gateway.platforms.event import MessageEvent
from gateway.session import SessionSource
from assistant.adapter import PersonalAssistantPhotonAdapter as PhotonAdapter
from gateway.platforms.base import BasePlatformAdapter
from assistant.personal_assistant import (
    PHOTON_PERSONAL_ASSISTANT_PROMPT,
    is_photon_personal_assistant_scope,
)


CHAT_ID = "+155****4567"
SENDER_ID = "+155****4567"


def _make_adapter(monkeypatch: pytest.MonkeyPatch, extra: dict[str, Any] | None = None) -> PhotonAdapter:
    monkeypatch.setenv("PHOTON_PROJECT_ID", "test-project-id")
    monkeypatch.setenv("PHOTON_PROJECT_SECRET", "test-project-secret")
    monkeypatch.setenv("PHOTON_ALLOW_ALL_USERS", "true")
    adapter = PhotonAdapter(PlatformConfig(enabled=True, token="", extra=extra or {}))
    adapter.set_authorization_check(lambda *args: True)
    return adapter


def _payload(
    *,
    text: str = "hello",
    space_type: str = "dm",
    chat_id: str | None = CHAT_ID,
    sender_id: str | None = SENDER_ID,
    message_id: str = "scope-contract-1",
) -> dict[str, Any]:
    return {
        "messageId": message_id,
        "space": {"id": chat_id, "type": space_type, "phone": chat_id},
        "sender": {"id": sender_id},
        "content": {"type": "text", "text": text},
        "timestamp": "2026-05-14T19:06:32.000Z",
    }


async def _dispatch_capture(adapter: PhotonAdapter, monkeypatch: pytest.MonkeyPatch, payload: dict[str, Any]):
    captured: list[MessageEvent] = []

    async def capture(self, event: MessageEvent) -> None:
        captured.append(event)

    monkeypatch.setattr(BasePlatformAdapter, "handle_message", capture)
    await adapter._dispatch_inbound(payload)
    return captured


@pytest.mark.asyncio
async def test_fresh_photon_dm_event_gets_stable_prompt_override_and_skills(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _make_adapter(
        monkeypatch,
        {
            "channel_prompts": {CHAT_ID: "Configured household context."},
            "channel_skill_bindings": [{"id": CHAT_ID, "skills": ["calendar", "shopping"]}],
        },
    )

    first = (await _dispatch_capture(adapter, monkeypatch, _payload(message_id="dm-1")))[0]
    second = (await _dispatch_capture(adapter, monkeypatch, _payload(message_id="dm-2")))[0]

    expected = f"{PHOTON_PERSONAL_ASSISTANT_PROMPT}\n\nConfigured household context."
    assert first is not second
    assert first.channel_prompt is not None
    assert first.channel_prompt.startswith(expected + "\n\n")
    assert "Selected workflow owner: native" in first.channel_prompt
    assert second.channel_prompt == first.channel_prompt
    assert first.auto_skill == ["calendar", "shopping"]
    assert second.auto_skill == ["calendar", "shopping"]
    # The prompt is a stable per-event value: no message id, timestamp, or mutable history is added.
    assert "dm-1" not in first.channel_prompt
    assert "dm-2" not in second.channel_prompt
    assert first.channel_prompt.count(PHOTON_PERSONAL_ASSISTANT_PROMPT) == 1


@pytest.mark.asyncio
async def test_group_event_does_not_inherit_prompt_override_or_skills(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _make_adapter(
        monkeypatch,
        {
            "channel_prompts": {"group-guid": "Must not leak into group."},
            "channel_skill_bindings": [{"id": "group-guid", "skills": ["private-skill"]}],
        },
    )

    event = (await _dispatch_capture(
        adapter,
        monkeypatch,
        _payload(space_type="group", chat_id="group-guid", message_id="group-1"),
    ))[0]

    assert event.source.chat_type == "group"
    assert event.channel_prompt is None
    assert event.auto_skill is None


def test_non_photon_and_incomplete_identity_fail_closed() -> None:
    assert is_photon_personal_assistant_scope(
        SessionSource(platform=Platform.DISCORD, chat_id=CHAT_ID, chat_type="dm", user_id=SENDER_ID)
    ) is False
    assert is_photon_personal_assistant_scope(
        SessionSource(platform=Platform("photon"), chat_id=CHAT_ID, chat_type="dm", user_id=None)
    ) is False
    assert is_photon_personal_assistant_scope(
        SessionSource(platform=Platform("photon"), chat_id="", chat_type="dm", user_id=SENDER_ID)
    ) is False
    assert is_photon_personal_assistant_scope(
        SessionSource(platform=Platform("photon"), chat_id=CHAT_ID, chat_type="group", user_id=SENDER_ID)
    ) is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        _payload(sender_id=None, message_id="missing-sender"),
        _payload(space_type="channel", message_id="unknown-space"),
    ],
)
async def test_malformed_photon_identity_never_gets_personal_assistant_context(
    monkeypatch: pytest.MonkeyPatch, payload: dict[str, Any]
) -> None:
    adapter = _make_adapter(
        monkeypatch,
        {
            "channel_prompts": {CHAT_ID: "Configured context."},
            "channel_skill_bindings": [{"id": CHAT_ID, "skills": ["private-skill"]}],
        },
    )
    captured = await _dispatch_capture(adapter, monkeypatch, payload)

    assert len(captured) == 1
    assert captured[0].channel_prompt is None
    assert captured[0].auto_skill is None
    assert is_photon_personal_assistant_scope(captured[0].source) is False


@pytest.mark.asyncio
async def test_unauthorized_dm_never_gets_prompt_or_observed(monkeypatch):
    adapter = _make_adapter(monkeypatch)
    adapter.set_authorization_check(lambda *args: False)
    from unittest.mock import AsyncMock
    observer = AsyncMock()
    monkeypatch.setattr(adapter, "_observe", observer)
    events = await _dispatch_capture(adapter, monkeypatch, _payload())
    assert events[0].channel_prompt is None
    observer.assert_not_called()


@pytest.mark.asyncio
async def test_missing_chat_identity_is_dropped_before_event_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _make_adapter(monkeypatch, {"channel_prompts": {CHAT_ID: "Configured context."}})
    captured = await _dispatch_capture(adapter, monkeypatch, _payload(chat_id=None, message_id="missing-chat"))
    assert captured == []

"""Contract tests for the Photon-only model reaction tool."""

from __future__ import annotations

import json

import pytest

from gateway.session_context import clear_session_vars, set_session_vars
from assistant import reaction_tool


def _surface_for_platform(platform: str) -> set[str]:
    """Resolve a model surface through plugin discovery, platform config, and registry schemas."""
    from hermes_cli.config_defaults import DEFAULT_CONFIG
    from hermes_cli.plugins import discover_plugins
    from hermes_cli.tools_config import _get_platform_tools
    from gateway.platform_registry import platform_registry
    from model_tools import get_tool_definitions

    discover_plugins()
    # Materialize the deferred Photon adapter exactly as the gateway does before using its platform.
    assert platform_registry.get("photon") is not None
    enabled = _get_platform_tools(DEFAULT_CONFIG, platform)
    return {
        definition["function"]["name"]
        for definition in get_tool_definitions(
            enabled_toolsets=sorted(enabled), quiet_mode=True, skip_tool_search_assembly=True,
        )
    }


class _FakeAdapter:
    def __init__(self):
        self.calls = []

    async def add_reaction(self, **kwargs):
        self.calls.append(kwargs)
        return {"success": True, "message_id": kwargs["message_id"]}


@pytest.fixture
def photon_session():
    set_session_vars(
        platform="photon", chat_id="chat-123", message_id="inbound-456",
    )
    yield
    clear_session_vars([])


@pytest.mark.asyncio
async def test_reaction_tool_forwards_arbitrary_unicode_unchanged(
    photon_session, monkeypatch: pytest.MonkeyPatch,
):
    adapter = _FakeAdapter()
    monkeypatch.setattr(reaction_tool, "current_adapter", lambda: adapter)
    monkeypatch.setattr("hermes_cli.plugin_capabilities.plugin_capability_granted", lambda *a, **k: True)

    result = json.loads(await reaction_tool.photon_react_tool({"emoji": "🫠✨"}))

    assert result == {"success": True, "message_id": "inbound-456"}
    assert adapter.calls == [{
        "chat_id": "chat-123", "message_id": "inbound-456", "emoji": "🫠✨",
    }]


@pytest.mark.asyncio
async def test_reaction_tool_rejects_non_photon_session(monkeypatch: pytest.MonkeyPatch):
    set_session_vars(platform="telegram", chat_id="chat-123", message_id="msg-1")
    try:
        result = json.loads(await reaction_tool.photon_react_tool({"emoji": "🧠"}))
    finally:
        clear_session_vars([])

    assert result["error"]
    assert "only in a Photon session" in result["error"]


@pytest.mark.asyncio
async def test_reaction_tool_requires_consent_and_trigger(photon_session, monkeypatch):
    adapter = _FakeAdapter()
    monkeypatch.setattr(reaction_tool, "current_adapter", lambda: adapter)
    result = json.loads(await reaction_tool.photon_react_tool({"emoji": "❤️"}))
    assert "consent" in result["error"]
    set_session_vars(platform="photon", chat_id="chat-123", message_id="")
    result = json.loads(await reaction_tool.photon_react_tool({"emoji": "❤️"}))
    assert "triggering message" in result["error"]
    assert adapter.calls == []


@pytest.mark.asyncio
async def test_reaction_tool_requires_emoji(photon_session):
    result = json.loads(await reaction_tool.photon_react_tool({"emoji": "   "}))

    assert result["error"]
    assert "non-empty Unicode string" in result["error"]

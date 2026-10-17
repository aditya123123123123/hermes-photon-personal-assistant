"""Real directory discovery and model-tool assembly against unmodified upstream."""
import os
from pathlib import Path

import pytest
import yaml
from gateway.session_context import set_session_vars, clear_session_vars

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def installed():
    home = Path(os.environ["HERMES_HOME"])
    (home / "plugins").mkdir(parents=True)
    (home / "plugins" / "photon-personal-assistant").symlink_to(ROOT, target_is_directory=True)
    (home / "config.yaml").write_text(yaml.safe_dump({"plugins": {
        "enabled": ["photon-platform", "photon-personal-assistant"]}}))
    from hermes_cli.plugins import get_plugin_manager
    pm = get_plugin_manager()
    pm.discover_and_load()
    yield pm
    clear_session_vars([])
    pm.unload()


def surface(platform, *, raw=False):
    from hermes_cli.config import load_config_readonly
    from hermes_cli.tools_config import _get_platform_tools
    from model_tools import get_tool_definitions
    from toolsets import session_disabled_toolsets
    return get_tool_definitions(
        enabled_toolsets=sorted(_get_platform_tools(load_config_readonly(), platform)),
        disabled_toolsets=session_disabled_toolsets(None, platform),
        quiet_mode=True, skip_tool_search_assembly=raw)


def names(defs):
    return {d["function"]["name"] for d in defs}


def test_real_discovery_adapter_and_deferred_fallback(installed):
    from gateway.platform_registry import platform_registry
    from plugins.platforms.photon.adapter import PhotonAdapter
    from tools.tool_search import classify_tools
    entry = platform_registry.get("photon")
    assert entry.adapter_factory.__name__ == "PersonalAssistantPhotonAdapter"
    assert issubclass(entry.adapter_factory, PhotonAdapter)
    assert "assistant" in installed._cli_commands
    skill = installed.find_plugin_skill("photon-personal-assistant:texting-assistant")
    assert skill.resolve() == ROOT / "skills/texting-assistant/SKILL.md"
    assert not (Path(os.environ["HERMES_HOME"]) / "skills/texting-assistant").exists()
    from plugins.platforms.photon import cli
    assert installed._cli_commands["photon"]["setup_fn"] is cli.register_cli
    raw = surface("photon", raw=True)
    assert "photon_react" in names(raw)
    direct, deferred = classify_tools(raw)
    assert "photon_react" in names(deferred)
    assert "photon_react" not in names(surface("cli"))
    assert "photon_react" not in names(surface("telegram"))
    assert "photon_react" not in names(surface("photon"))


@pytest.mark.xfail(strict=True, reason="Upstream defers platform plugin tools; unapplied generic patch supplied")
def test_required_direct_visibility_on_stock_upstream(installed):
    assert "photon_react" in names(surface("photon"))


@pytest.mark.parametrize("case", ["dm", "group", "unknown", "missing_sender", "cron", "child", "mismatch", "kanban"])
def test_registered_hook_scope(installed, monkeypatch, case):
    from hermes_cli.plugins import invoke_hook
    binding = dict(platform="photon", chat_id="any;-;+12025550100", chat_type="dm",
                   user_id="test-user", session_id="test-session")
    kwargs = dict(platform="photon", session_id="test-session", user_message="report")
    if case in {"group", "unknown"}:
        binding["chat_type"] = case
    if case == "missing_sender":
        binding["user_id"] = ""
    if case == "cron":
        monkeypatch.setenv("HERMES_CRON_SESSION", "1")
    if case == "child":
        kwargs["parent_session_id"] = "parent"
    if case == "mismatch":
        kwargs["session_id"] = "other"
    if case == "kanban":
        monkeypatch.setattr("agent.delegation_context.owned_kanban_task", lambda: "task")
    set_session_vars(**binding)
    result = invoke_hook("pre_llm_call", **kwargs)
    assert bool(result) == (case == "dm")
    if result:
        assert "Read-only Photon assistant observation" in result[0]["context"]
        assert "system_prompt" not in result[0]


def test_disabling_tool_search_alone_is_not_a_safe_workaround(installed):
    home = Path(os.environ["HERMES_HOME"])
    config = yaml.safe_load((home / "config.yaml").read_text())
    config["tools"] = {"tool_search": {"enabled": "off"}}
    config["platform_toolsets"] = {"cli": ["terminal"], "telegram": ["terminal"],
                                   "photon": ["terminal", "photon"]}
    (home / "config.yaml").write_text(yaml.safe_dump(config))
    assert "photon_react" in names(surface("photon"))
    # Upstream auto-adds plugin toolsets even to explicit selections.
    assert "photon_react" in names(surface("cli"))
    assert "photon_react" in names(surface("telegram"))


def test_missing_dependency_keeps_diagnostics_and_gated_declarations(installed):
    home = Path(os.environ["HERMES_HOME"])
    (home / "config.yaml").write_text(yaml.safe_dump({"plugins": {
        "enabled": ["photon-personal-assistant"], "disabled": ["photon-platform"]}}))
    installed.discover_and_load(force=True)
    from tools.registry import registry
    entry = registry.get_entry("photon_react", scope=installed.scope_key)
    assert entry is not None
    assert "assistant" in installed._cli_commands
    import asyncio
    assert "missing or disabled" in asyncio.run(entry.handler({"emoji": "👍"}))
    from hermes_cli.plugins import invoke_hook
    assert not invoke_hook("pre_llm_call", platform="photon", session_id="test", user_message="test")

"""Offline setup diagnostics: no configuration bootstrap, writes or live probes."""
from dataclasses import asdict, dataclass
import json
import re
from pathlib import Path

import yaml
from hermes_constants import get_hermes_home
from hermes_cli.plugins_manifest import running_hermes_version, version_satisfies

from .settings import PLUGIN_ID

MIN_HERMES = ">=0.22.2"
SETTINGS = f"plugins.entries.{PLUGIN_ID}.settings"


@dataclass(frozen=True)
class Check:
    id: str
    status: str
    why: str
    fix: str


def mapping(value):
    return value if isinstance(value, dict) else {}


def read_config(home):
    # load_config_readonly() creates directories and config backups on a cold
    # cache: it is NOT suitable for a strictly non-mutating diagnostic.
    path = home / "config.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8-sig")) if path.exists() else {}
    if raw is not None and not isinstance(raw, dict):
        raise ValueError("configuration must be a mapping")
    from hermes_cli.config import _expand_env_vars
    from hermes_cli.managed_scope import apply_managed_overlay
    return apply_managed_overlay(_expand_env_vars(raw or {}))


def plugin_enabled(config, name, *, bundled=False):
    plugins = mapping(config.get("plugins"))
    aliases = {name, "platforms/photon"} if name == "photon-platform" else {name}
    if aliases.intersection(plugins.get("disabled") or []):
        return False
    return bundled or bool(aliases.intersection(plugins.get("enabled") or []))


def plugin_settings(config):
    plugins = mapping(config.get("plugins"))
    entry = mapping(mapping(plugins.get("entries")).get(PLUGIN_ID))
    return mapping(entry.get("settings", entry.get("config")))


def version_check():
    version = running_hermes_version()
    ok = bool(re.fullmatch(r"v?\d+\.\d+\.\d+(?:[-+].*)?", version)) and version_satisfies(MIN_HERMES, version)
    return Check("hermes_version", "ok" if ok else "fail",
                 f"Hermes {version} {'meets' if ok else 'does not establish'} the {MIN_HERMES} floor.",
                 "hermes update")


def env_presence(home):
    # Hermes CLI installs the profile secret scope; never open credential files.
    from agent.secret_scope import get_secret
    return {key: bool(get_secret(key)) for key in ("PHOTON_PROJECT_ID", "PHOTON_PROJECT_SECRET")}


def photon_checks(config, home):
    from hermes_constants import __file__ as host_file
    installed = (Path(host_file).parent / "plugins/platforms/photon/adapter.py").is_file()
    enabled = installed and plugin_enabled(config, "photon-platform", bundled=True)
    photon = mapping(mapping(config.get("platforms")).get("photon"))
    extra = {**mapping(photon.get("extra")), **photon}
    presence = env_presence(home)
    ready = photon.get("enabled") is True and all(
        bool(extra.get(key)) or presence[env] for key, env in
        (("project_id", "PHOTON_PROJECT_ID"), ("project_secret", "PHOTON_PROJECT_SECRET")))
    return [
        Check("photon_plugin", "ok" if enabled else "fail",
              "Bundled Photon is available and enabled." if enabled else "Bundled Photon is missing or disabled.",
              "hermes plugins enable photon-platform"),
        Check("photon_config", "ok" if ready else "fail",
              "Photon is enabled with project credential presence; connectivity is untested." if ready else
              "Photon needs platforms.photon.enabled and both project credential fields; values are never displayed.",
              "hermes photon setup"),
    ]


def visibility_check(config):
    from tools.tool_search import is_deferrable_tool_name
    search = mapping(mapping(config.get("tools")).get("tool_search"))
    deferred = is_deferrable_tool_name("photon_react", defer_tools=frozenset(search.get("defer") or []))
    disabled = search.get("enabled") in (False, "off")
    if disabled:
        return Check("reaction_visibility", "warn", "Disabling tool search can expose plugin schemas outside Photon.",
                     "hermes config set tools.tool_search.enabled auto")
    return Check("reaction_visibility", "warn" if deferred else "ok",
                 "Host defers photon_react, so spontaneous reactions are unlikely." if deferred else
                 "Host classifies photon_react as directly visible; start a new Photon conversation.",
                 'Use tool_describe(name="photon_react"), then tool_call; see SETUP.md (upstream PR: TODO).')


def reasoning_check(config):
    from gateway.config import ChannelOverride
    photon = mapping(mapping(config.get("platforms")).get("photon"))
    overrides = mapping(photon.get("channel_overrides"))
    requested = any("reasoning_effort" in mapping(value) for value in overrides.values())
    supported = "reasoning_effort" in ChannelOverride.__dataclass_fields__
    warn = requested and not supported
    return Check("reasoning", "warn" if warn else "ok",
                 "This host silently ignores configured Photon per-chat reasoning_effort." if warn else
                 "No unsupported Photon per-chat reasoning setting is configured.",
                 "Use /reasoning low in the chat instead; upstream PR: TODO.")


def owner_check(config, home):
    from .followthrough_owner import resolve_owner
    selected = mapping(config.get("photon_followthrough")).get("owner", plugin_settings(config).get("owner", "auto"))
    try:
        owner = resolve_owner(selected, home)
    except ValueError:
        return Check("owner", "fail", "Follow-through owner is invalid or native conflicts with a legacy executable.",
                     "hermes assistant commitment owner; reconcile work and retire the legacy executable before selecting native.")
    return Check("owner", "warn" if owner == "disabled" else "ok",
                 f"Follow-through selection {selected} resolves to {owner}; no work is migrated.",
                 f"hermes assistant commitment owner --select native --expected {owner} (local TTY; reconcile legacy work first)")


def calendar_check(config, home):
    from .calendar.configured import _configured_provider
    from .calendar.provider import CalendarAccessPolicy
    from .coordinator import PhotonConversationScope
    photon = mapping(mapping(config.get("platforms")).get("photon"))
    section = mapping(plugin_settings(config).get("calendar", mapping(photon.get("extra")).get("calendar")))
    fix = "Optional: load the google-workspace skill and complete its OAuth setup in this profile; see SETUP.md."
    if not section.get("enabled"):
        return Check("calendar", "ok", "Optional calendar context is disabled; no account access is needed.", fix)
    grants = section.get("approved_scopes") or []
    valid = False
    for grant in grants:
        try:
            scope = PhotonConversationScope(**grant)
            policy = CalendarAccessPolicy(enabled=True, approval_granted=section.get("approval_granted") is True)
            _, provider = _configured_provider(section, scope, policy)
            valid = policy.allows_read and provider is not None
        except (ValueError, TypeError):
            continue
        if valid:
            break
    if section.get("provider") == "google_workspace":
        valid = valid and (home / "google_token.json").is_file()
    return Check("calendar", "ok" if valid else "warn",
                 "Calendar policy and local prerequisites are present; credentials and access are untested." if valid else
                 "Optional calendar is enabled but approval, exact scope, provider settings or existing OAuth is missing.", fix)


def run_checks():
    home = get_hermes_home()
    try:
        config = read_config(home)
    except (OSError, ValueError, yaml.YAMLError):
        return [asdict(Check("config", "fail", "Profile configuration cannot be read safely; no values are displayed.",
                             "hermes config edit"))]
    plugins = mapping(config.get("plugins"))
    settings = plugin_settings(config)
    isolation = plugins.get("isolation", "in_process") == "in_process"
    active = plugin_enabled(config, PLUGIN_ID) and settings.get("enabled", True) is True
    from hermes_cli.plugin_capabilities import plugin_capability_granted
    granted = plugin_capability_granted(PLUGIN_ID, "gateway.platform_actions", config=config)
    bubble = settings.get("bubble_max_chars", 600)
    bubble_ok = type(bubble) is int and bubble >= 0
    rows = [version_check(),
            Check("isolation", "ok" if isolation else "fail", "Live Photon adapters require in_process isolation.",
                  "hermes config set plugins.isolation in_process (ask your administrator if managed)"),
            *photon_checks(config, home),
            Check("assistant_plugin", "ok" if active else "fail", "Assistant plugin and guidance are enabled." if active else
                  "Assistant plugin or its guidance is disabled/not enabled.",
                  f"hermes plugins enable {PLUGIN_ID}; hermes config set {SETTINGS}.enabled true"),
            Check("capability", "ok" if granted else "fail", "Reaction capability is granted." if granted else
                  "Reactions will refuse without gateway.platform_actions consent.",
                  f"hermes plugins enable {PLUGIN_ID} (interactive consent); hermes plugins capabilities {PLUGIN_ID}"),
            visibility_check(config), reasoning_check(config), calendar_check(config, home), owner_check(config, home),
            Check("bubbles", "ok" if bubble_ok else "fail",
                  f"Bubble soft limit is {bubble}; zero disables splitting." if bubble_ok else "Bubble limit must be a nonnegative integer.",
                  f"hermes config set {SETTINGS}.bubble_max_chars 600")]
    return [asdict(row) for row in rows]


def main(args):
    rows = run_checks()
    ok = not any(row["status"] == "fail" for row in rows)
    if args.json:
        print(json.dumps({"ok": ok, "checks": rows}, ensure_ascii=False))
    else:
        symbols = {"ok": "✓", "warn": "⚠", "fail": "✗"}
        for row in rows:
            print(f"{symbols[row['status']]} {row['id']}: {row['why']} Fix/options: {row['fix']}")
    return 0 if ok else 1


def register_doctor_cli(subs):
    parser = subs.add_parser("doctor", help="Read-only offline setup checks")
    parser.add_argument("--json", action="store_true", help="Machine-readable checks")
    parser.set_defaults(assistant_command="doctor")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    raise SystemExit(main(parser.parse_args()))

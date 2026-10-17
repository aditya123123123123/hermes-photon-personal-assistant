"""Read only the current profile's plugin settings, including CLI subprocesses."""
from collections.abc import Mapping
from hermes_cli.config import load_config_readonly

PLUGIN_ID = "photon-personal-assistant"


def setting(key, default=None):
    config = load_config_readonly()
    entry = config.get("plugins", {}).get("entries", {}).get(PLUGIN_ID, {})
    settings = entry.get("settings", entry.get("config", {}))
    return settings.get(key, default) if isinstance(settings, Mapping) else default

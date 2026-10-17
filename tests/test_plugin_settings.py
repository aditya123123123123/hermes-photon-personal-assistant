import os
from pathlib import Path
import yaml
from assistant.settings import setting
from assistant.followthrough_owner import workflow_owner
from assistant.coordinator import PhotonConversationScope
from assistant.calendar.configured import resolve_calendar_access


def save(config):
    home = Path(os.environ["HERMES_HOME"])
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.yaml").write_text(yaml.safe_dump(config))


def test_plugin_settings_and_legacy_owner_precedence():
    config = {"plugins": {"entries": {"photon-personal-assistant": {"settings": {
        "owner": "disabled", "bubble_max_chars": 0}}}}}
    save(config)
    assert setting("bubble_max_chars", 600) == 0
    assert workflow_owner() == "disabled"
    config["photon_followthrough"] = {"owner": "native"}
    save(config)
    assert workflow_owner() == "native"


def test_calendar_only_exact_trusted_plugin_scope():
    scope = PhotonConversationScope(chat_id="any;-;+15555550123", user_id="test-user")
    calendar = {"enabled": True, "approval_granted": True, "provider": "cached_json",
                "snapshot_path": "calendar.json", "max_age_seconds": 300,
                "approved_scopes": [{"chat_id": scope.chat_id, "user_id": scope.user_id}]}
    save({"plugins": {"entries": {"photon-personal-assistant": {"settings": {"calendar": calendar}}}}})
    policy, provider = resolve_calendar_access(scope)
    assert policy.allows_read and provider is not None
    assert not resolve_calendar_access(PhotonConversationScope(chat_id=scope.chat_id, user_id="other"))[0].allows_read

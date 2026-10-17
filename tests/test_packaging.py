"""Public install metadata and bundled generic guidance contracts."""
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_skill_authoring_and_nonlegacy_name():
    text = (ROOT / "skills/texting-assistant/SKILL.md").read_text()
    data = yaml.safe_load(text.split("---", 2)[1])
    assert data["name"] == "texting-assistant"
    assert len(data["description"]) <= 60 and data["description"].endswith(".")
    sections = ["When to Use", "Prerequisites", "How to Run", "Quick Reference", "Procedure", "Pitfalls", "Verification"]
    positions = [text.index("## " + section) for section in sections]
    assert positions == sorted(positions)
    from assistant.personal_assistant import PHOTON_PERSONAL_ASSISTANT_PROMPT
    assert "photon-personal-assistant:texting-assistant" in PHOTON_PERSONAL_ASSISTANT_PROMPT
    from assistant.followthrough_owner import resolve_owner
    assert resolve_owner("auto", ROOT) == "native"


def test_catalog_draft_matches_manifest():
    manifest = yaml.safe_load((ROOT / "plugin.yaml").read_text())
    draft = yaml.safe_load((ROOT / "catalog/photon-personal-assistant.yaml").read_text())
    for field in ("name", "version", "requires_hermes"):
        assert draft[field] == manifest[field]
    assert draft["category"] == "platform" and draft["tier"] == "community"
    assert draft["sha"].startswith("TODO_")
    for field in ("provides_tools", "provides_hooks", "provides_middleware", "requires_env"):
        assert draft["capabilities"][field] == manifest.get(field, [])
    assert manifest["capabilities"] == ["gateway.platform_actions"]

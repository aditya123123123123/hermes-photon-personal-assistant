"""Directory installs must load outside the source cwd and without its sys.path."""
import json
import os
from pathlib import Path
import subprocess
import sys


def test_real_discovery_from_unrelated_cwd(tmp_path):
    import hermes_constants
    root = Path(__file__).resolve().parents[1]
    upstream = Path(hermes_constants.__file__).resolve().parent
    home = Path(os.environ["HERMES_HOME"])
    (home / "plugins").mkdir(parents=True)
    (home / "plugins" / "photon-personal-assistant").symlink_to(root)
    (home / "config.yaml").write_text("plugins:\n  enabled: [photon-personal-assistant]\n")
    code = """
import sys, json
sys.path.insert(0, sys.argv[1])
from hermes_cli.plugins import get_plugin_manager
pm = get_plugin_manager()
pm.discover_and_load()
from gateway.platform_registry import platform_registry
entry = platform_registry.get('photon')
print(json.dumps({'adapter': entry.adapter_factory.__name__, 'cli': 'assistant' in pm._cli_commands}))
"""
    result = subprocess.run([sys.executable, "-I", "-c", code, str(upstream)], cwd=tmp_path,
                            env=os.environ.copy(), text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.splitlines()[-1])
    assert payload == {"adapter": "PersonalAssistantPhotonAdapter", "cli": True}

    cli_env = {**os.environ, "PYTHONPATH": str(upstream), "PYTHONDONTWRITEBYTECODE": "1"}
    result = subprocess.run([sys.executable, "-m", "hermes_cli.main", "assistant", "doctor", "--json"],
                            cwd=tmp_path, env=cli_env, text=True, capture_output=True, timeout=30)
    assert result.returncode == 1, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert next(row for row in payload["checks"] if row["id"] == "capability")["status"] == "fail"

"""Exercise the unapplied proposal in memory; never edit the upstream checkout."""
import types
from pathlib import Path

from test_integration import installed, surface, names


def candidate_source(original, diff):
    lines = original.splitlines(keepends=True)
    output, cursor = [], 0
    import re
    for line in diff.splitlines(keepends=True):
        if line.startswith(("---", "+++")):
            continue
        if line.startswith("@@"):
            start = int(re.match(r"@@ -(\d+)", line)[1]) - 1
            output.extend(lines[cursor:start])
            cursor = start
        elif line.startswith(" "):
            assert lines[cursor] == line[1:]
            output.append(lines[cursor])
            cursor += 1
        elif line.startswith("-"):
            assert lines[cursor] == line[1:]
            cursor += 1
        elif line.startswith("+"):
            output.append(line[1:])
    return "".join(output + lines[cursor:])


def test_proposed_generic_patch_real_assembly(installed, monkeypatch):
    import tools.tool_search as original
    path = Path(__file__).resolve().parents[1] / "upstream-patches/0001-platform-tools-direct.patch"
    parts = path.read_text().split("--- a/toolsets.py\n")
    source = candidate_source(Path(original.__file__).read_text(), parts[0])
    import toolsets
    toolset_source = candidate_source(Path(toolsets.__file__).read_text(), "--- a/toolsets.py\n" + parts[1])
    toolset_candidate = types.ModuleType("_unapplied_toolsets")
    exec(compile(toolset_source, "<unapplied-toolsets-patch>", "exec"), toolset_candidate.__dict__)
    monkeypatch.setattr(toolsets, "session_disabled_toolsets", toolset_candidate.session_disabled_toolsets)
    monkeypatch.setattr(toolsets, "session_platform_tool_drops", toolset_candidate.session_platform_tool_drops)
    candidate = types.ModuleType("tools._unapplied_candidate")
    candidate.__package__ = "tools"
    import sys
    monkeypatch.setitem(sys.modules, candidate.__name__, candidate)
    exec(compile(source, "<unapplied-platform-surface-patch>", "exec"), candidate.__dict__)
    monkeypatch.setattr(original, "is_deferrable_tool_name", candidate.is_deferrable_tool_name)
    from model_tools import _clear_tool_defs_cache
    _clear_tool_defs_cache()
    assert "photon_react" in names(surface("photon"))
    assert "photon_react" not in names(surface("cli"))
    assert "photon_react" not in names(surface("telegram"))
    assert candidate.is_deferrable_tool_name("photon_react", frozenset({"photon_react"}))
    _clear_tool_defs_cache()

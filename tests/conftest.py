"""Hermetic tests: no network, no production profile, no provider credentials."""
import os
from pathlib import Path
import socket
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    for name in list(os.environ):
        if name.startswith(("HERMES_", "__HERMES", "_HERMES")):
            monkeypatch.delenv(name)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("HERMES_TIMEZONE", "UTC")
    monkeypatch.setenv("PHOTON_PROJECT_ID", "offline-test-project")
    monkeypatch.setenv("PHOTON_PROJECT_SECRET", "offline-test-secret")
    def refuse(*args, **kwargs):
        raise AssertionError("offline test attempted network")
    monkeypatch.setattr(socket.socket, "connect", refuse)

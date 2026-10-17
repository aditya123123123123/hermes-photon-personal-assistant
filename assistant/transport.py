"""Weak, profile-bound references to our own adapters (never other profiles)."""
from weakref import WeakValueDictionary
from hermes_constants import get_hermes_home

_adapters = WeakValueDictionary()


def remember(adapter):
    _adapters[str(get_hermes_home().resolve())] = adapter


def current_adapter():
    return _adapters.get(str(get_hermes_home().resolve()))

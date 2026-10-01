"""The set of installed watchers. Concrete watchers register themselves on import."""

from __future__ import annotations

from app.watchers.base import Watcher

_REGISTRY: dict[str, Watcher] = {}


def register_watcher(watcher: Watcher) -> Watcher:
    """Install a watcher. Registering the same name twice is an error, so two
    watchers can never share saved state."""
    if not watcher.name or not watcher.name.strip():
        raise ValueError("A watcher needs a name")
    if watcher.poll_interval_seconds <= 0:
        raise ValueError(f"Watcher {watcher.name!r}: poll_interval_seconds must be positive")
    if watcher.cooldown_seconds < 0 or watcher.daily_fire_cap < 0:
        raise ValueError(f"Watcher {watcher.name!r}: cooldown_seconds and daily_fire_cap cannot be negative")
    if watcher.name in _REGISTRY:
        raise ValueError(f"A watcher named {watcher.name!r} is already registered")
    _REGISTRY[watcher.name] = watcher
    return watcher


def unregister_watcher(name: str) -> None:
    _REGISTRY.pop(name, None)


def all_watchers() -> list[Watcher]:
    return sorted(_REGISTRY.values(), key=lambda w: w.name)


def get_watcher(name: str) -> Watcher | None:
    return _REGISTRY.get(name)

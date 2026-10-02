"""Saved screens: a small JSON file next to settings.json (runtime/screener_saved.json),
in the same volume as the database, so it survives a Docker rebuild.

Writes are atomic (temp file in the same folder, then os.replace), so a crash
mid-save leaves the previous list intact. A file that cannot be read is treated
as an empty list and logged; the next save replaces it.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from pathlib import Path

from app.config import get_infra_settings
from app.schemas.screener_schemas import MAX_SAVED_SCREENS, SavedScreen, SavedScreenCreate
from app.timeutil import utcnow_naive

logger = logging.getLogger(__name__)

FILE_FORMAT_VERSION = 1
_lock = threading.Lock()


class DuplicateNameError(ValueError):
    """Another saved screen already has this name."""


class TooManyScreensError(ValueError):
    """The saved list is full."""


def saved_file() -> Path:
    """Looked up on every call (not cached) so a test can point it at a tmp path."""
    return get_infra_settings().settings_file.parent / "screener_saved.json"


def _read() -> list[SavedScreen]:
    path = saved_file()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return [SavedScreen.model_validate(item) for item in data.get("screens", [])]
    except (OSError, ValueError, AttributeError, TypeError) as exc:
        logger.warning("Saved screens file %s could not be read (%s); treating it as empty", path, exc)
        return []


def _write(screens: list[SavedScreen]) -> None:
    path = saved_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": FILE_FORMAT_VERSION, "screens": [s.model_dump(mode="json") for s in screens]}
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def list_saved() -> list[SavedScreen]:
    with _lock:
        return sorted(_read(), key=lambda s: s.name.casefold())


def create_saved(spec: SavedScreenCreate) -> SavedScreen:
    with _lock:
        screens = _read()
        if len(screens) >= MAX_SAVED_SCREENS:
            raise TooManyScreensError(f"At most {MAX_SAVED_SCREENS} screens can be saved; delete one first")
        if any(s.name.casefold() == spec.name.strip().casefold() for s in screens):
            raise DuplicateNameError(f"A saved screen named '{spec.name.strip()}' already exists")
        screen = SavedScreen(
            **spec.model_dump(exclude={"name"}),
            name=spec.name.strip(),
            id=uuid.uuid4().hex[:12],
            created_at=utcnow_naive(),
        )
        screens.append(screen)
        _write(screens)
        return screen


def delete_saved(screen_id: str) -> bool:
    """True when something was removed."""
    with _lock:
        screens = _read()
        kept = [s for s in screens if s.id != screen_id]
        if len(kept) == len(screens):
            return False
        _write(kept)
        return True

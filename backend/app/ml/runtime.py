"""Whether the optional ML libraries are installed. Never imports them."""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass

REQUIRED_MODULES = ("lightgbm", "shap", "sklearn")
INSTALL_HINT = "pip install -r requirements-ml.txt"


class MlExtrasMissing(RuntimeError):
    """The ML extras (lightgbm, shap, scikit-learn) are not installed."""


@dataclass(frozen=True)
class ExtrasStatus:
    available: bool
    missing: tuple[str, ...]
    message: str


def extras_status() -> ExtrasStatus:
    missing = []
    for name in REQUIRED_MODULES:
        try:
            found = importlib.util.find_spec(name) is not None
        except (ImportError, ValueError):
            found = False
        if not found:
            missing.append("scikit-learn" if name == "sklearn" else name)
    if missing:
        return ExtrasStatus(
            False,
            tuple(missing),
            f"ML extras not installed (missing: {', '.join(missing)}). Run `{INSTALL_HINT}` in the backend.",
        )
    return ExtrasStatus(True, (), "ML extras installed.")


def require_extras() -> None:
    status = extras_status()
    if not status.available:
        raise MlExtrasMissing(status.message)

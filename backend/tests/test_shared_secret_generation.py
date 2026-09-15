"""AppSettings.api_shared_secret used to default to "" — a stop-gap the
operator had to know to turn on, so the INSECURE state was the default one.
_load_or_create_shared_secret / get_infra_settings flip that: the secure
state (a real, required secret) is now the default, at zero manual setup
cost — see config.py and notes/Decisions.md.

Every test here builds its own InfraSettings pointed at a temp directory
(never the real backend/runtime/) so nothing here can write to, or depend
on, this repo's actual generated secret.
"""

from __future__ import annotations

from pathlib import Path

from app.config import InfraSettings, _load_or_create_shared_secret, get_infra_settings


def _infra(tmp_path: Path, **overrides) -> InfraSettings:
    defaults = dict(settings_path=str(tmp_path / "runtime" / "settings.json"))
    defaults.update(overrides)
    return InfraSettings(**defaults)


def test_a_fresh_install_generates_and_persists_a_real_secret(tmp_path):
    infra = _infra(tmp_path)
    secret_file = infra.generated_secret_file
    assert not secret_file.exists()

    secret = _load_or_create_shared_secret(infra)

    assert secret  # non-empty
    assert len(secret) >= 32  # secrets.token_urlsafe(32) is well over this
    assert secret_file.exists()
    assert secret_file.read_text(encoding="utf-8").strip() == secret


def test_the_same_secret_survives_a_restart(tmp_path):
    """The whole point of persisting to runtime/: a second process (a
    container restart, a redeploy) must get back the SAME secret, or every
    request the already-built frontend sends starts 401ing."""
    infra = _infra(tmp_path)
    first = _load_or_create_shared_secret(infra)
    second = _load_or_create_shared_secret(InfraSettings(settings_path=infra.settings_path))
    assert first == second


def test_two_installs_never_share_a_secret(tmp_path):
    a = _load_or_create_shared_secret(_infra(tmp_path / "install-a"))
    b = _load_or_create_shared_secret(_infra(tmp_path / "install-b"))
    assert a != b


def test_an_empty_secret_file_is_regenerated_not_trusted(tmp_path):
    """A zero-byte file (a previous run crashed mid-write, or someone
    truncated it by hand) must never be treated as "the secret is the empty
    string" — that would make require_shared_secret's `if not expected:
    return` treat the API as unauthenticated again, silently."""
    infra = _infra(tmp_path)
    infra.generated_secret_file.parent.mkdir(parents=True, exist_ok=True)
    infra.generated_secret_file.write_text("", encoding="utf-8")

    secret = _load_or_create_shared_secret(infra)

    assert secret != ""
    assert len(secret) >= 32


def test_get_infra_settings_fills_in_the_secret_when_nothing_is_configured(tmp_path, monkeypatch):
    """The integration point: get_infra_settings (what api/deps.py actually
    calls) must return an InfraSettings whose api_shared_secret is already
    populated — require_shared_secret has no generation logic of its own."""
    monkeypatch.setenv("SETTINGS_PATH", str(tmp_path / "runtime" / "settings.json"))
    monkeypatch.delenv("API_SHARED_SECRET", raising=False)
    # Forced to "false", not deleted: this repo's own backend/.env (gitignored,
    # local-dev convenience — see .env.example) sets
    # ALLOW_UNAUTHENTICATED_API=true, and pydantic-settings falls back to
    # that .env FILE whenever the OS env var is merely absent — delenv only
    # clears the OS-level value, so without this the test would silently
    # read the developer's real local .env instead of exercising "nothing
    # configured" at all.
    monkeypatch.setenv("ALLOW_UNAUTHENTICATED_API", "false")
    get_infra_settings.cache_clear()
    try:
        infra = get_infra_settings()
        assert infra.api_shared_secret != ""
        assert infra.generated_secret_file.exists()
    finally:
        get_infra_settings.cache_clear()


def test_get_infra_settings_respects_the_explicit_opt_out(tmp_path, monkeypatch):
    monkeypatch.setenv("SETTINGS_PATH", str(tmp_path / "runtime" / "settings.json"))
    monkeypatch.delenv("API_SHARED_SECRET", raising=False)
    monkeypatch.setenv("ALLOW_UNAUTHENTICATED_API", "true")
    get_infra_settings.cache_clear()
    try:
        infra = get_infra_settings()
        assert infra.api_shared_secret == ""
        assert not infra.generated_secret_file.exists()  # never even touched
    finally:
        get_infra_settings.cache_clear()


def test_get_infra_settings_never_overrides_an_explicit_secret(tmp_path, monkeypatch):
    monkeypatch.setenv("SETTINGS_PATH", str(tmp_path / "runtime" / "settings.json"))
    monkeypatch.setenv("API_SHARED_SECRET", "operator-chosen-value")
    get_infra_settings.cache_clear()
    try:
        infra = get_infra_settings()
        assert infra.api_shared_secret == "operator-chosen-value"
        assert not infra.generated_secret_file.exists()  # generation never ran
    finally:
        get_infra_settings.cache_clear()

"""POST /api/auth/login, /logout, /status — the endpoints the bundled
frontend's login screen actually calls. See test_auth.py for the
session-token/lockout logic these build on, tested in isolation there."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

# TestClient defaults to a plain http://testserver base_url, and httpx's
# cookie jar correctly refuses to store/resend a Secure-flagged cookie over
# a non-https connection — the exact browser behavior the Secure flag
# exists to produce (see api/routers/auth.py's login: secure=not
# allow_unauthenticated_api). Tests that need the full login -> cookie ->
# authenticated-request round trip use this https-based client instead, to
# match how a real deployment (behind TLS, the expected configuration
# once allow_unauthenticated_api is False) actually behaves.
https_client = TestClient(app, base_url="https://testserver")

VALID = {"username": "test-user", "password": "correct-horse-battery-staple"}


class _FakeInfra:
    def __init__(
        self,
        auth_username: str = "test-user",
        auth_password: str = "correct-horse-battery-staple",
        session_secret: str = "test-session-secret",
        session_lifetime_days: int = 7,
        max_login_attempts: int = 3,
        login_lockout_minutes: int = 15,
        allow_unauthenticated_api: bool = False,
    ):
        self.auth_username = auth_username
        self.auth_password = auth_password
        self.session_secret = session_secret
        self.session_lifetime_days = session_lifetime_days
        self.max_login_attempts = max_login_attempts
        self.login_lockout_minutes = login_lockout_minutes
        self.allow_unauthenticated_api = allow_unauthenticated_api


def _patch_infra(monkeypatch, **overrides) -> _FakeInfra:
    infra = _FakeInfra(**overrides)
    monkeypatch.setattr("app.api.routers.auth.get_infra_settings", lambda: infra)
    return infra


# ----------------------------------------------------------------------- login


def test_correct_username_and_password_logs_in_and_sets_a_cookie(monkeypatch):
    _patch_infra(monkeypatch)
    resp = client.post("/api/auth/login", json=VALID)
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert "strategeia_session" in resp.cookies


def test_wrong_password_is_rejected_without_a_cookie(monkeypatch):
    _patch_infra(monkeypatch)
    resp = client.post("/api/auth/login", json={**VALID, "password": "guess"})
    assert resp.status_code == 401
    assert "strategeia_session" not in resp.cookies


def test_wrong_username_is_rejected_even_with_the_right_password(monkeypatch):
    _patch_infra(monkeypatch)
    resp = client.post("/api/auth/login", json={**VALID, "username": "someone-else"})
    assert resp.status_code == 401
    assert "strategeia_session" not in resp.cookies


def test_wrong_username_and_wrong_password_give_the_identical_error(monkeypatch):
    """Never reveal WHICH field was wrong — that's how a login form leaks
    whether a given username is even valid."""
    _patch_infra(monkeypatch)
    wrong_password = client.post("/api/auth/login", json={**VALID, "password": "guess"})
    wrong_username = client.post("/api/auth/login", json={**VALID, "username": "someone-else"})
    assert wrong_password.json()["detail"] == wrong_username.json()["detail"]


def test_the_session_cookie_actually_authenticates_subsequent_requests(monkeypatch):
    """End to end: log in, then use ONLY the cookie the browser would have
    stored (no X-API-Key at all) against a real protected route."""
    infra = _patch_infra(monkeypatch)
    monkeypatch.setattr("app.api.deps.get_infra_settings", lambda: infra)

    login_resp = https_client.post("/api/auth/login", json=VALID)
    assert login_resp.status_code == 200

    protected_resp = https_client.get("/api/settings")
    assert protected_resp.status_code == 200
    https_client.cookies.clear()


def test_cookie_flags_are_set_correctly(monkeypatch):
    _patch_infra(monkeypatch)
    resp = client.post("/api/auth/login", json=VALID)
    set_cookie = resp.headers.get("set-cookie", "")
    assert "HttpOnly" in set_cookie  # unreadable to page JS — an XSS can't exfiltrate it
    assert "SameSite=lax" in set_cookie or "samesite=lax" in set_cookie.lower()
    assert "Secure" in set_cookie  # allow_unauthenticated_api is False here


def test_cookie_is_not_marked_secure_in_the_local_dev_opt_out(monkeypatch):
    """A Secure cookie is silently refused by the browser over plain HTTP
    — hardcoding Secure=True would break login on http://localhost."""
    _patch_infra(monkeypatch, allow_unauthenticated_api=True)
    resp = client.post("/api/auth/login", json=VALID)
    set_cookie = resp.headers.get("set-cookie", "")
    assert "Secure" not in set_cookie


# --------------------------------------------------------------------- lockout


def test_three_wrong_passwords_lock_out_the_fourth_attempt(monkeypatch):
    _patch_infra(monkeypatch)
    for _ in range(3):
        resp = client.post("/api/auth/login", json={**VALID, "password": "wrong"})
        assert resp.status_code == 401

    locked_resp = client.post("/api/auth/login", json={**VALID, "password": "wrong"})
    assert locked_resp.status_code == 429


def test_lockout_blocks_even_the_correct_password(monkeypatch):
    """The point of a lockout: once triggered, it stops guessing entirely
    for the window, it doesn't just keep rejecting wrong guesses."""
    _patch_infra(monkeypatch)
    for _ in range(3):
        client.post("/api/auth/login", json={**VALID, "password": "wrong"})

    resp = client.post("/api/auth/login", json=VALID)
    assert resp.status_code == 429
    assert "strategeia_session" not in resp.cookies


def test_lockout_is_scoped_per_client_not_global(monkeypatch):
    """A different X-Forwarded-For must not inherit another client's
    lockout — see api/routers/auth.py's _client_key."""
    _patch_infra(monkeypatch)
    for _ in range(3):
        client.post("/api/auth/login", json={**VALID, "password": "wrong"}, headers={"X-Forwarded-For": "10.0.0.1"})
    locked = client.post("/api/auth/login", json={**VALID, "password": "wrong"}, headers={"X-Forwarded-For": "10.0.0.1"})
    assert locked.status_code == 429

    other_client = client.post("/api/auth/login", json=VALID, headers={"X-Forwarded-For": "10.0.0.2"})
    assert other_client.status_code == 200


# ------------------------------------------------------------------------ status


def test_status_reports_false_when_not_logged_in(monkeypatch):
    _patch_infra(monkeypatch)
    client.cookies.clear()
    resp = client.get("/api/auth/status")
    assert resp.status_code == 200
    assert resp.json()["authenticated"] is False


def test_status_reports_true_after_logging_in(monkeypatch):
    _patch_infra(monkeypatch)
    https_client.post("/api/auth/login", json=VALID)
    resp = https_client.get("/api/auth/status")
    assert resp.json()["authenticated"] is True
    https_client.cookies.clear()


def test_status_is_always_true_in_the_local_dev_opt_out(monkeypatch):
    """No session exists at all in this mode (require_auth never checks
    one either) — status must not send the frontend into a login screen
    that guards nothing."""
    _patch_infra(monkeypatch, allow_unauthenticated_api=True)
    client.cookies.clear()
    resp = client.get("/api/auth/status")
    assert resp.json()["authenticated"] is True


# ------------------------------------------------------------------------ logout


def test_logout_clears_the_session(monkeypatch):
    infra = _patch_infra(monkeypatch)
    monkeypatch.setattr("app.api.deps.get_infra_settings", lambda: infra)

    https_client.post("/api/auth/login", json=VALID)
    assert https_client.get("/api/auth/status").json()["authenticated"] is True

    https_client.post("/api/auth/logout")
    assert https_client.get("/api/auth/status").json()["authenticated"] is False
    https_client.cookies.clear()


def test_logout_is_safe_to_call_while_already_logged_out(monkeypatch):
    _patch_infra(monkeypatch)
    client.cookies.clear()
    resp = client.post("/api/auth/logout")
    assert resp.status_code == 200

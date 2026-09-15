"""app/auth.py in isolation: session tokens and the login-attempt lockout,
independent of the FastAPI layer (see test_auth_router.py for the actual
endpoints)."""

from __future__ import annotations

import time

from app.auth import LoginAttemptTracker, create_session_token, verify_session_token


class _FakeInfra:
    def __init__(self, session_secret: str = "test-session-secret", session_lifetime_days: int = 7):
        self.session_secret = session_secret
        self.session_lifetime_days = session_lifetime_days


# ------------------------------------------------------------- session tokens


def test_a_freshly_issued_token_verifies():
    infra = _FakeInfra()
    token = create_session_token(infra)
    assert verify_session_token(token, infra) is True


def test_none_and_empty_tokens_never_verify():
    infra = _FakeInfra()
    assert verify_session_token(None, infra) is False
    assert verify_session_token("", infra) is False


def test_a_malformed_token_never_verifies():
    infra = _FakeInfra()
    for garbage in ("not-a-token-at-all", "12345", "abc.def", "999999999999", "..."):
        assert verify_session_token(garbage, infra) is False


def test_a_token_signed_with_a_different_secret_is_rejected():
    """The core guarantee: possessing a well-formed-looking token isn't
    enough, it has to be signed by THIS server's own session_secret."""
    real_infra = _FakeInfra(session_secret="real-secret")
    forged_infra = _FakeInfra(session_secret="attacker-guessed-secret")
    token = create_session_token(forged_infra)
    assert verify_session_token(token, real_infra) is False


def test_tampering_with_the_expiry_invalidates_the_signature():
    """An attacker extending their own token's lifetime by editing the
    expiry field must break the signature, not silently succeed."""
    infra = _FakeInfra()
    token = create_session_token(infra)
    expiry, _, signature = token.partition(".")
    forged = f"{int(expiry) + 999_999}.{signature}"
    assert verify_session_token(forged, infra) is False


def test_an_expired_token_is_rejected_even_with_a_correct_signature():
    infra = _FakeInfra(session_lifetime_days=0)
    # session_lifetime_days=0 means "expires now" — force it clearly into
    # the past rather than racing the clock.
    import app.auth as auth_module

    expires_at = int(time.time()) - 10
    signature = auth_module._sign(str(expires_at), infra.session_secret)
    expired_token = f"{expires_at}.{signature}"
    assert verify_session_token(expired_token, infra) is False


def test_a_token_for_one_server_does_not_work_against_another_with_a_different_secret():
    """Regenerating session_secret (e.g. a fresh runtime/ on redeploy, or
    explicitly rotating it) must invalidate every previously issued
    session — this is what makes that rotation meaningful at all."""
    infra_before = _FakeInfra(session_secret="old-secret")
    infra_after = _FakeInfra(session_secret="new-secret")
    old_token = create_session_token(infra_before)
    assert verify_session_token(old_token, infra_after) is False


# ------------------------------------------------------- login attempt lockout


def test_no_attempts_means_not_locked_out():
    tracker = LoginAttemptTracker()
    assert tracker.seconds_locked_out("1.2.3.4") == 0.0


def test_fewer_than_max_failures_does_not_lock_out():
    tracker = LoginAttemptTracker()
    tracker.record_failure("1.2.3.4", max_attempts=3, lockout_minutes=15)
    tracker.record_failure("1.2.3.4", max_attempts=3, lockout_minutes=15)
    assert tracker.seconds_locked_out("1.2.3.4") == 0.0


def test_reaching_max_failures_locks_out():
    """"also make maximum logins tries 3" — the exact boundary: the 3rd
    failure is what triggers it, not the 4th."""
    tracker = LoginAttemptTracker()
    tracker.record_failure("1.2.3.4", max_attempts=3, lockout_minutes=15)
    tracker.record_failure("1.2.3.4", max_attempts=3, lockout_minutes=15)
    assert tracker.seconds_locked_out("1.2.3.4") == 0.0
    tracker.record_failure("1.2.3.4", max_attempts=3, lockout_minutes=15)
    locked_for = tracker.seconds_locked_out("1.2.3.4")
    assert 0 < locked_for <= 15 * 60


def test_a_success_clears_a_partial_failure_streak():
    """2 wrong passwords followed by the right one must not carry a
    "1 away from locked" count into the next login attempt."""
    tracker = LoginAttemptTracker()
    tracker.record_failure("1.2.3.4", max_attempts=3, lockout_minutes=15)
    tracker.record_failure("1.2.3.4", max_attempts=3, lockout_minutes=15)
    tracker.record_success("1.2.3.4")
    tracker.record_failure("1.2.3.4", max_attempts=3, lockout_minutes=15)
    assert tracker.seconds_locked_out("1.2.3.4") == 0.0  # only 1 failure since the reset


def test_lockout_is_scoped_per_client_key():
    """The whole reason this isn't one global counter: an attacker
    hammering the login from one IP must not be able to lock the real
    operator, logging in from a different IP, out of their own dashboard."""
    tracker = LoginAttemptTracker()
    for _ in range(3):
        tracker.record_failure("attacker-ip", max_attempts=3, lockout_minutes=15)
    assert tracker.seconds_locked_out("attacker-ip") > 0
    assert tracker.seconds_locked_out("owner-ip") == 0.0


def test_lockout_expires_after_the_window(monkeypatch):
    tracker = LoginAttemptTracker()
    real_monotonic = time.monotonic()
    monkeypatch.setattr(time, "monotonic", lambda: real_monotonic)
    for _ in range(3):
        tracker.record_failure("1.2.3.4", max_attempts=3, lockout_minutes=15)
    assert tracker.seconds_locked_out("1.2.3.4") > 0

    # Jump the clock past the lockout window.
    monkeypatch.setattr(time, "monotonic", lambda: real_monotonic + 15 * 60 + 1)
    assert tracker.seconds_locked_out("1.2.3.4") == 0.0


def test_lockout_expiring_resets_the_failure_count_too():
    """After the window passes, the next attempt starts a fresh count —
    it must not be one failure away from an instant re-lock."""
    tracker = LoginAttemptTracker()
    real_monotonic = time.monotonic()
    for _ in range(3):
        tracker.record_failure("1.2.3.4", max_attempts=3, lockout_minutes=15)
    # Force expiry by directly manipulating the internal clock reference
    # the same way the monkeypatched-time test does, but inline here to
    # also exercise seconds_locked_out's cleanup path explicitly.
    attempts = tracker._clients["1.2.3.4"]
    attempts.locked_until_monotonic = real_monotonic - 1  # already in the past
    assert tracker.seconds_locked_out("1.2.3.4") == 0.0
    assert "1.2.3.4" not in tracker._clients  # cleaned up, not just reporting 0

    tracker.record_failure("1.2.3.4", max_attempts=3, lockout_minutes=15)
    assert tracker.seconds_locked_out("1.2.3.4") == 0.0  # 1 failure, not instantly re-locked

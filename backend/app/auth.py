from __future__ import annotations

import hashlib
import hmac
import time
from dataclasses import dataclass, field

from app.config import InfraSettings

# --------------------------------------------------------------------------
# Session tokens: stateless, HMAC-signed. No server-side session store (no
# new table, no extra moving part) — the token IS the proof, carrying its
# own expiry, verified by recomputing the signature. This means logout
# clears the browser's cookie but cannot revoke an already-issued token
# before its natural expiry if it were somehow copied elsewhere first; for
# a single-operator personal dashboard with a short-ish
# session_lifetime_days, that's an accepted, explicit tradeoff against the
# complexity of a real server-side session table — revisit if that ever
# stops being true.
# --------------------------------------------------------------------------

# Shared with api/deps.py (reading it, to check a request) and
# api/routers/auth.py (setting/clearing it) so the name only lives in one
# place.
SESSION_COOKIE_NAME = "strategeia_session"

_SEPARATOR = "."


def create_session_token(infra: InfraSettings) -> str:
    """`<expiry-unix-ts>.<hex hmac-sha256 of the expiry, keyed by
    infra.session_secret>`. Only the expiry is carried — there is nothing
    else to identify: this app has exactly one user by design (see
    InfraSettings.auth_password's docstring)."""
    expires_at = int(time.time()) + infra.session_lifetime_days * 86400
    signature = _sign(str(expires_at), infra.session_secret)
    return f"{expires_at}{_SEPARATOR}{signature}"


def verify_session_token(token: str | None, infra: InfraSettings) -> bool:
    """False for anything malformed, unsigned, mis-signed, or expired —
    never raises, so callers can treat this as a plain yes/no gate."""
    if not token or _SEPARATOR not in token:
        return False
    expires_at_raw, _, signature = token.partition(_SEPARATOR)
    if not expires_at_raw.isdigit():
        return False
    expected_signature = _sign(expires_at_raw, infra.session_secret)
    # compare_digest, not ==: same timing-leak reasoning as api/deps.py's
    # X-API-Key check — a plain string comparison would let response timing
    # narrow down a correct signature prefix byte by byte.
    if not hmac.compare_digest(signature, expected_signature):
        return False
    return int(expires_at_raw) > int(time.time())


def _sign(payload: str, key: str) -> str:
    return hmac.new(key.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()


# --------------------------------------------------------------------------
# Login-attempt lockout — "also make maximum logins tries 3". In-process,
# not persisted, same pattern (and same tradeoffs — resets on a restart,
# doesn't coordinate across multiple worker processes) as the cooldowns in
# scanner.py/settings.py. Keyed per-client (best-effort by IP; see
# api/routers/auth.py for how the key is derived) rather than one global
# counter specifically so a remote attacker hammering /api/auth/login can
# only ever lock out their OWN key, never the real operator's.
# --------------------------------------------------------------------------


@dataclass
class _ClientAttempts:
    failures: int = 0
    locked_until_monotonic: float | None = None


@dataclass
class LoginAttemptTracker:
    """Not a module-level dict directly (unlike the simpler cooldowns
    elsewhere) so tests can construct a fresh, isolated instance instead of
    monkeypatching shared global state — this one has real read-then-write
    logic worth testing in isolation (lockout triggers at exactly
    max_attempts, resets after the window, a success clears a partial
    streak)."""

    _clients: dict[str, _ClientAttempts] = field(default_factory=dict)

    def seconds_locked_out(self, client_key: str) -> float:
        """0 if not currently locked out, else how many seconds remain."""
        attempts = self._clients.get(client_key)
        if attempts is None or attempts.locked_until_monotonic is None:
            return 0.0
        remaining = attempts.locked_until_monotonic - time.monotonic()
        if remaining <= 0:
            # The lockout window has passed — clear it so this client
            # starts its next real attempt with a fresh count, not one
            # attempt away from an instant re-lock.
            del self._clients[client_key]
            return 0.0
        return remaining

    def record_failure(self, client_key: str, max_attempts: int, lockout_minutes: int) -> None:
        attempts = self._clients.setdefault(client_key, _ClientAttempts())
        attempts.failures += 1
        if attempts.failures >= max_attempts:
            attempts.locked_until_monotonic = time.monotonic() + lockout_minutes * 60

    def record_success(self, client_key: str) -> None:
        self._clients.pop(client_key, None)


# One shared tracker for the process, imported by api/routers/auth.py —
# module-level so its state survives across requests (that's the whole
# point of a lockout) without needing a database table for something this
# disposable.
login_attempts = LoginAttemptTracker()

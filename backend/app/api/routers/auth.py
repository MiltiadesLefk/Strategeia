from __future__ import annotations

import logging
import secrets

from fastapi import APIRouter, HTTPException, Request, Response

from app.auth import SESSION_COOKIE_NAME, create_session_token, login_attempts, verify_session_token
from app.config import get_infra_settings
from app.schemas.auth_schemas import AuthStatusResponse, LoginRequest, LoginResponse

# Deliberately NOT behind Depends(require_auth) — a login endpoint that
# requires being already logged in is a lock with the key on the wrong
# side. /status and /logout are open too: status has to work while
# logged OUT (that's how the frontend knows to show the login screen at
# all), and logout has to be safe to call whether or not the caller
# currently has a valid session.
router = APIRouter(prefix="/api/auth", tags=["auth"])

logger = logging.getLogger(__name__)


def _client_key(request: Request) -> str:
    """Best-effort real client identity for the lockout counter (see
    app.auth.LoginAttemptTracker) — prefers X-Forwarded-For's first hop
    when present (this deployment may sit behind a reverse proxy this repo
    doesn't control), falling back to the direct connection's address.
    Note this is spoofable by anyone who can reach the backend directly
    (bypassing whatever proxy would normally set that header) — acceptable
    here since the actual credential check right below it is what really
    stops an attacker, this only affects how the lockout is bucketed."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@router.post("/login", response_model=LoginResponse)
def login(req: LoginRequest, request: Request, response: Response) -> LoginResponse:
    infra = get_infra_settings()
    client_key = _client_key(request)

    locked_for = login_attempts.seconds_locked_out(client_key)
    if locked_for > 0:
        raise HTTPException(
            status_code=429,
            detail=f"Too many failed attempts — try again in {max(1, round(locked_for / 60))} minute(s).",
        )

    # Both compared unconditionally (not `and`-short-circuited) and the
    # error message never says which one was wrong — a real login system
    # with more than one account would need this to avoid confirming a
    # username exists; kept even with exactly one account so this doesn't
    # need re-auditing if that ever changes.
    username_ok = secrets.compare_digest(req.username, infra.auth_username)
    password_ok = secrets.compare_digest(req.password, infra.auth_password)
    if not (username_ok and password_ok):
        login_attempts.record_failure(client_key, infra.max_login_attempts, infra.login_lockout_minutes)
        logger.warning("Failed login attempt from %s", client_key)
        raise HTTPException(status_code=401, detail="Incorrect username or password")

    login_attempts.record_success(client_key)
    token = create_session_token(infra)
    response.set_cookie(
        SESSION_COOKIE_NAME,
        token,
        max_age=infra.session_lifetime_days * 86400,
        httponly=True,
        samesite="lax",
        # Browsers refuse to send a Secure cookie over plain HTTP, so this
        # has to track the same local-dev opt-out the rest of auth does —
        # a hardcoded True here would silently break login on
        # http://localhost.
        secure=not infra.allow_unauthenticated_api,
        path="/",
    )
    return LoginResponse(ok=True, message="Logged in")


@router.post("/logout", response_model=LoginResponse)
def logout(response: Response) -> LoginResponse:
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return LoginResponse(ok=True, message="Logged out")


@router.get("/status", response_model=AuthStatusResponse)
def status(request: Request) -> AuthStatusResponse:
    infra = get_infra_settings()
    # In the local-dev opt-out, every route is already open (see
    # require_auth) — reporting "authenticated" here too means the
    # frontend's login gate stays out of the way entirely in that mode,
    # instead of demanding a login nothing downstream will ever check.
    if infra.allow_unauthenticated_api:
        return AuthStatusResponse(authenticated=True)
    return AuthStatusResponse(authenticated=verify_session_token(request.cookies.get(SESSION_COOKIE_NAME), infra))

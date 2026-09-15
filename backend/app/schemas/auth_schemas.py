from __future__ import annotations

from pydantic import BaseModel


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    ok: bool
    message: str


class AuthStatusResponse(BaseModel):
    authenticated: bool

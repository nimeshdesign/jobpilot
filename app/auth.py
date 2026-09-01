"""Password gate for a hosted instance.

Locally this does nothing: the server binds to 127.0.0.1 and only you can reach
it. The moment it is deployed, the same database holds your resume, phone
number, salary expectations and every application you have made — so a hosted
instance without a password is not an option, and the app refuses to start as
one rather than quietly exposing you.

A signed, HttpOnly cookie carries the session. No user table, no accounts: one
password, one person.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import time

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

from .config import APP_PASSWORD, SECRET_KEY, SERVERLESS

log = logging.getLogger("jobpilot.auth")

COOKIE = "jobpilot_session"
MAX_AGE = 60 * 60 * 24 * 14          # two weeks

# Paths reachable without a session: the login page itself, its POST target and
# the static assets that page needs to render.
PUBLIC_PATHS = {"/login", "/api/login", "/static/styles.css", "/favicon.ico"}


def _secret() -> str:
    if SECRET_KEY:
        return SECRET_KEY
    # A generated key is fine locally; on a serverless host each instance would
    # generate its own and sessions would break, hence the startup check below.
    return _fallback_secret


_fallback_secret = secrets.token_hex(32)


def required() -> bool:
    """Is the password gate active?"""
    return bool(APP_PASSWORD)


def startup_check() -> None:
    """Refuse to run a hosted instance with no password."""
    if SERVERLESS and not APP_PASSWORD:
        raise RuntimeError(
            "JOBPILOT_PASSWORD is not set. This instance would publish your "
            "resume, contact details and application history to anyone with "
            "the URL. Set JOBPILOT_PASSWORD (and JOBPILOT_SECRET_KEY) in the "
            "deployment's environment variables."
        )
    if SERVERLESS and not SECRET_KEY:
        raise RuntimeError(
            "JOBPILOT_SECRET_KEY is not set. Without a stable key every "
            "serverless instance signs sessions differently and you would be "
            "logged out constantly. Generate one with: "
            "python -c \"import secrets; print(secrets.token_hex(32))\""
        )


def _sign(payload: str) -> str:
    return hmac.new(_secret().encode(), payload.encode(), hashlib.sha256).hexdigest()


def issue(response: Response) -> None:
    """Attach a fresh session cookie."""
    expires = int(time.time()) + MAX_AGE
    payload = str(expires)
    response.set_cookie(
        COOKIE,
        f"{payload}.{_sign(payload)}",
        max_age=MAX_AGE,
        httponly=True,                 # not readable from JavaScript
        samesite="lax",                # blocks cross-site form posts
        secure=SERVERLESS,             # HTTPS-only once hosted
        path="/",
    )


def clear(response: Response) -> None:
    response.delete_cookie(COOKIE, path="/")


def valid(request: Request) -> bool:
    if not required():
        return True
    raw = request.cookies.get(COOKIE, "")
    if "." not in raw:
        return False
    payload, signature = raw.rsplit(".", 1)
    if not hmac.compare_digest(signature, _sign(payload)):
        return False
    try:
        return int(payload) > time.time()
    except ValueError:
        return False


def check_password(candidate: str) -> bool:
    # compare_digest so a wrong guess takes the same time as a right one
    return bool(APP_PASSWORD) and hmac.compare_digest(candidate or "", APP_PASSWORD)


async def middleware(request: Request, call_next):
    """Block everything until a valid session exists."""
    if not required():
        return await call_next(request)

    path = request.url.path
    if path in PUBLIC_PATHS or path.startswith("/static/"):
        return await call_next(request)

    if valid(request):
        return await call_next(request)

    if path.startswith("/api/"):
        return JSONResponse({"detail": "Not signed in"}, status_code=401)
    return RedirectResponse("/login", status_code=302)

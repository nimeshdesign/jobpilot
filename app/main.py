from __future__ import annotations

import logging
import socket
import sys
import webbrowser
from contextlib import asynccontextmanager
from pathlib import Path
from threading import Timer

# Allow `python path\to\app\main.py` as well as `python -m app.main`. Without
# this, launching by file path fails with "No module named 'app'" because the
# project root is not on sys.path.
_ROOT = str(Path(__file__).resolve().parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# Log lines carry status glyphs and job titles in every alphabet job boards use.
# A Windows console (or a redirected stream) defaults to cp1252 and raises
# UnicodeEncodeError mid-log, which would take a worker thread down with it.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # already wrapped, or not a text stream
        pass

import asyncio

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import auth
from .config import HOST, PORT, SERVERLESS, WEB_DIR
from .db import SessionLocal, init_db
from .models import Profile
from .routers.api import router as api_router
from .sources import providers

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s %(name)-20s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("jobpilot")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Refuses to start a hosted instance with no password rather than exposing
    # your resume and application history to anyone with the URL.
    auth.startup_check()
    init_db()
    with SessionLocal() as db:
        profile = db.get(Profile, 1)
        if profile and not profile.enabled_sources:
            profile.enabled_sources = [
                s["key"] for s in providers.list_sources() if s["default_on"]
            ]
            db.commit()
    log.info("JobPilot ready at http://%s:%s", HOST, PORT)
    yield


app = FastAPI(
    title="JobPilot",
    description="Local job-application automation. Your data never leaves this machine.",
    version="1.0.0",
    lifespan=lifespan,
)

# The app binds to localhost only; CORS is permissive so you can open the UI
# from a different local port during development if you want.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost", "http://127.0.0.1", f"http://{HOST}:{PORT}"],
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.middleware("http")(auth.middleware)
app.include_router(api_router)


@app.get("/login", include_in_schema=False)
def login_page() -> FileResponse:
    return FileResponse(WEB_DIR / "login.html")


@app.post("/api/login", include_in_schema=False)
async def do_login(request: Request) -> JSONResponse:
    body = await request.json()
    if not auth.check_password(str(body.get("password", ""))):
        # Deliberately vague, and slow enough that guessing is unattractive.
        await asyncio.sleep(1.0)
        return JSONResponse({"detail": "Wrong password"}, status_code=401)
    response = JSONResponse({"ok": True})
    auth.issue(response)
    return response


@app.post("/api/logout", include_in_schema=False)
def do_logout() -> JSONResponse:
    response = JSONResponse({"ok": True})
    auth.clear(response)
    return response


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")


def open_browser() -> None:
    webbrowser.open(f"http://{HOST}:{PORT}/")


def port_in_use(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.7)
        return probe.connect_ex((host, port)) == 0


def run() -> None:
    import uvicorn

    # Starting a second copy is the most common "it doesn't work": the port is
    # taken, uvicorn dies with a WinError, and the browser shows nothing.
    if port_in_use(HOST, PORT):
        print(
            f"\nJobPilot is already running at http://{HOST}:{PORT}/\n"
            "Open that address in your browser, or close the other window "
            "and start again.\n"
        )
        open_browser()
        return

    Timer(1.5, open_browser).start()
    try:
        uvicorn.run(app, host=HOST, port=PORT, log_level="info")
    except OSError as exc:
        print(
            f"\nCould not start the web server on {HOST}:{PORT} — {exc}\n"
            f"Something else is using that port. Set JOBPILOT_PORT in .env "
            f"to a free port (for example 8790) and try again.\n"
        )
        raise SystemExit(1) from exc


if __name__ == "__main__":
    run()

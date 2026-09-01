"""Runtime configuration. Everything is local; nothing is uploaded anywhere."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

# JOBPILOT_DATA_DIR isolates *everything* a run owns -- database, resumes,
# generated documents, screenshots and browser profile. Isolating only the
# database is not enough: two runs sharing data/generated will overwrite and
# delete each other's documents, because generated filenames are keyed on the
# application id and each run numbers its applications from 1.
DATA_DIR = Path(os.getenv("JOBPILOT_DATA_DIR", "")).expanduser() \
    if os.getenv("JOBPILOT_DATA_DIR") else ROOT / "data"
RESUME_DIR = DATA_DIR / "resumes"
GENERATED_DIR = DATA_DIR / "generated"
SCREENSHOT_DIR = DATA_DIR / "screenshots"
BROWSER_PROFILE_DIR = DATA_DIR / "browser"
WEB_DIR = ROOT / "web"
# Set JOBPILOT_DB to point tests or a second profile at their own database.
# Without this, anything that opens a session — including a test run — writes
# straight into your real profile and job history.
DB_PATH = Path(os.getenv("JOBPILOT_DB", "")).expanduser() if os.getenv("JOBPILOT_DB") \
    else DATA_DIR / "jobpilot.db"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

for _d in (DATA_DIR, RESUME_DIR, GENERATED_DIR, SCREENSHOT_DIR, BROWSER_PROFILE_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# --- AI provider (all optional; the app works fully without one) ----------- #
# auto | ollama | openai | anthropic | none
LLM_PROVIDER = os.getenv("JOBPILOT_LLM_PROVIDER", "auto").strip().lower()

# A model running on this machine. Free and private.
OLLAMA_URL = os.getenv("JOBPILOT_OLLAMA_URL", "http://127.0.0.1:11434").strip().rstrip("/")
OLLAMA_MODEL = os.getenv("JOBPILOT_OLLAMA_MODEL", "llama3.1:8b").strip()

# Any OpenAI-compatible /chat/completions endpoint: Groq, OpenRouter, Together,
# LM Studio, llama.cpp, Google's OpenAI-compat endpoint. Several have free tiers.
OPENAI_BASE_URL = os.getenv("JOBPILOT_OPENAI_BASE_URL", "").strip().rstrip("/")
OPENAI_API_KEY = os.getenv("JOBPILOT_OPENAI_API_KEY", "").strip()
OPENAI_MODEL = os.getenv("JOBPILOT_OPENAI_MODEL", "").strip()

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
MODEL = os.getenv("JOBPILOT_MODEL", "claude-opus-5").strip() or "claude-opus-5"
HOST = os.getenv("JOBPILOT_HOST", "127.0.0.1")
PORT = int(os.getenv("JOBPILOT_PORT", "8765"))

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def llm_enabled() -> bool:
    return bool(ANTHROPIC_API_KEY)

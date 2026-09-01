"""Vercel entrypoint.

Vercel looks for an ASGI app called `app` in this file. Everything else is the
same application that runs locally; only storage and auth change behaviour,
driven by the environment variables set in the Vercel dashboard.
"""
import sys
from pathlib import Path

# The project root is one level up from api/; add it so `app` imports resolve.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.main import app  # noqa: E402,F401

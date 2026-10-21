"""Vercel entry point for the interceptor API (demo profile, bearer-token only).

Vercel looks for a module-level ASGI `app`. The app is built from environment variables at import time (see
api/app.py build_default_app). Migrations are NOT run here: apply them with `alembic upgrade head` before each deploy.

Required environment variables (set in the Vercel dashboard, never in the repository):
  INTERCEPTOR_DATABASE_URL, INTERCEPTOR_PSEUDO_KEY, INTERCEPTOR_JWT_PUBLIC_KEY (the PEM text),
  INTERCEPTOR_AUTH_MODE=bearer, INTERCEPTOR_DEPLOYMENT_PROFILE=vercel-demo.
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("INTERCEPTOR_HOME", str(ROOT))

from interceptor.api.app import build_default_app  # noqa: E402

app = build_default_app()

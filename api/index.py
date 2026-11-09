"""Vercel Serverless Function entry point for ORBITAL Satellite Digital Twin."""

import sys
from pathlib import Path

# Add backend directory to sys.path so app modules are resolvable
project_root = Path(__file__).resolve().parent.parent
backend_dir = project_root / "backend"
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.main import app

# Vercel ASGI handler export
handler = app

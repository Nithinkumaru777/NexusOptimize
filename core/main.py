"""Entry point: python -m core.main  (run from the repo root)."""

import os

import uvicorn

from core.api.app import app

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("NEXUS_PORT", "5000")), log_level="warning")

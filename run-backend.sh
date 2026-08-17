#!/usr/bin/env bash
# AG-UI backend on :8000. Run from this phase's root.
set -euo pipefail
cd "$(dirname "$0")"
uv sync
exec uv run uvicorn backend.main:app --port 8000 --reload

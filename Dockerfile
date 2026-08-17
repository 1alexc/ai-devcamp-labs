# Cloud Run image for the social_poster agent (backend only — the Next.js
# frontend stays local, pointed at this service via ADK_BACKEND_URL).
# Run from the phase root:  docker build -t social-poster .
FROM python:3.12-slim

RUN pip install --no-cache-dir uv==0.8.13

WORKDIR /code

COPY pyproject.toml uv.lock ./
COPY backend ./backend

# --no-dev: skip the eval/dev-only deps eval-gating already exercised in CI.
RUN uv sync --frozen --no-dev

# Cloud Run injects PORT; default matches local `run-backend.sh` for parity.
ENV PORT=8080
EXPOSE 8080

CMD ["sh", "-c", "uv run uvicorn backend.main:app --host 0.0.0.0 --port ${PORT}"]

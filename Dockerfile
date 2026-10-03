# syntax=docker/dockerfile:1
FROM python:3.14-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.21 /uv /uvx /bin/

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

# Install dependencies first (better layer caching). --no-install-project
# because the app runs from source (--app-dir src) and is never imported
# as an installed distribution.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project

COPY src/ ./src/

RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--app-dir", "src"]

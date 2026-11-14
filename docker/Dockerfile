# PitWall application image: Dagster webserver and daemon (and, later, API and jobs).
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    DAGSTER_HOME=/opt/dagster/home

# uv from PyPI (pinned): works anywhere pip does, no extra registry needed.
RUN pip install --no-cache-dir "uv==0.11.32"

WORKDIR /app

# Dependencies first, so code changes do not invalidate this layer.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --extra orchestration --no-install-project

COPY src ./src
RUN uv sync --locked --no-dev --extra orchestration

COPY docker/dagster/dagster.yaml docker/dagster/workspace.yaml ${DAGSTER_HOME}/

RUN useradd --create-home --uid 1000 pitwall \
    && mkdir -p /app/data ${DAGSTER_HOME}/storage \
    && chown -R pitwall:pitwall /app ${DAGSTER_HOME}
USER pitwall

EXPOSE 3000
CMD ["dagster-webserver", "-h", "0.0.0.0", "-p", "3000", "-w", "/opt/dagster/home/workspace.yaml"]

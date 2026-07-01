# Planner-UI BFF (FastAPI) — seeded from the sample extract. Build context = repo root.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app

# The agent-spine package depends on its siblings via uv path sources (../feature-store,
# ../recommendation-engine, ../event-publisher, ../forecasting), so the services/ layout
# must be preserved.
COPY services/feature-store services/feature-store
COPY services/recommendation-engine services/recommendation-engine
COPY services/event-publisher services/event-publisher
COPY services/forecasting services/forecasting
COPY services/agent-spine services/agent-spine

WORKDIR /app/services/agent-spine
# Install the BFF (fastapi) + runtime; uvicorn is added to the synced venv for serving.
RUN uv sync --extra bff --no-dev && uv pip install uvicorn

# Seed from the committed sample extract (overridable).
ENV EXTRACT_DIR=/app/services/recommendation-engine/examples/extract_sample
ENV PLANNER_TENANT=acme

EXPOSE 8000
CMD ["uv", "run", "--no-sync", "uvicorn", "trax_io_spine.bff.asgi:app", \
     "--host", "0.0.0.0", "--port", "8000"]

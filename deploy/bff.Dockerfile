# Planner-UI BFF (FastAPI) — seeded from the sample extract. Build context = repo root.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

# WeasyPrint (bvr pdf extra) native deps — pango/cairo text+render stack.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf-2.0-0 shared-mime-info \
    && rm -rf /var/lib/apt/lists/*

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
# Install the BFF (fastapi) + BVR report rendering (jinja2 + weasyprint — app.py imports
# bvr.render unconditionally, so bff-only sync would ModuleNotFoundError at boot) + runtime.
RUN uv sync --extra bff --extra bvr --extra pdf --no-dev && uv pip install uvicorn

# Seed from the committed sample extract (overridable).
ENV EXTRACT_DIR=/app/services/recommendation-engine/examples/extract_sample
ENV PLANNER_TENANT=acme

EXPOSE 8000
CMD ["uv", "run", "--no-sync", "uvicorn", "trax_io_spine.bff.asgi:app", \
     "--host", "0.0.0.0", "--port", "8000"]

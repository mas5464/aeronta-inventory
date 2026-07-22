# Jobs worker (C2 §5 / C3 ingest) — same image as the BFF, different entrypoint.
# Build context = repo root. Railway selects this file per-service via
# RAILWAY_DOCKERFILE_PATH=deploy/worker.Dockerfile (the deploy/railway-worker.json
# startCommand is NOT read by Railway's default builder — see the C3 deploy notes).
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

# WeasyPrint (bvr pdf extra) native deps — pango/cairo text+render stack. Kept
# identical to bff.Dockerfile so the two images share build-cache layers.
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
# Same extras as the BFF image: the ingest handler imports trax_io_reco (parse/mapper/
# validate) + pg (psycopg). bvr/pdf are pulled in transitively by app.py's unconditional
# bvr.render import, which the worker's module graph also touches via bff.store.
RUN uv sync --extra bff --extra bvr --extra pdf --extra pg --no-dev

# The idle jobs worker: claims queued jobs (FOR UPDATE SKIP LOCKED), dispatches from
# HANDLERS (incl. C3 "ingest"), retries x3, dead-letters unknown kinds. No HTTP port —
# reads WORKER_DATABASE_URL | DATABASE_URL + WORKER_POLL_SECONDS + (for ingest)
# SUPABASE_URL / SUPABASE_SERVICE_KEY.
CMD ["uv", "run", "--no-sync", "python", "-m", "trax_io_spine.pg.worker"]

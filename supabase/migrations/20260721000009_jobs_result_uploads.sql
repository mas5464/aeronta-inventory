-- C3 Task 1: ingest success summary distinct from the error payload.
alter table public.jobs add column result jsonb;

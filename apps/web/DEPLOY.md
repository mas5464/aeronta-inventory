# Deploy Configuration — Trax IO Web

## Build-time Environment Variables

The web frontend requires the following environment variables to be set at build time:

### Required

- **`VITE_SUPABASE_URL`**: The Supabase project URL.
  - Example: `https://sluoxufnqwusmtckklnv.supabase.co`
  - From: Supabase project settings → API → URL

- **`VITE_SUPABASE_ANON_KEY`**: The Supabase anonymous/public key for client-side auth.
  - Example: `eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...`
  - From: Supabase project settings → API → anon key (public, safe to commit or use in build)

- **`VITE_TENANT_SLUGS`**: JSON map of tenant IDs to slugs, parsed by the frontend for multi-tenant UI.
  - Format: `{"<tenant-uuid>":"<slug>", ...}`
  - Example: `{"753b64bd-9885-4639-b116-8f2c5c497232":"aeronta-demo"}`
  - Empty object (no tenants) allowed in local dev: `{}`

### Optional (Deploy-specific)

- **`VITE_BFF_URL`**: The Backend-for-Frontend base URL.
  - **Local dev (default)**: Unset or omitted → uses `http://localhost:8001` (default)
  - **Vercel production**: Pinned to empty string → requests are made relative (same-origin), leveraging the `vercel.json` rewrite from `/v1/:path*` to the real Railway BFF URL. This is pinned via `apps/web/vercel.json`'s `buildCommand` (`"VITE_BFF_URL= npm run build"`), **not** via a Project Settings env var — the Vercel dashboard's Environment Variables UI cannot store an empty string as a value, so do not attempt to set it there.
  - Override example: `https://bff.example.com` (though Vercel deployments use the empty-string route via rewrites)

## Vercel Deployment

### Setup

1. Connect your GitHub repo to Vercel.
2. In Project Settings → Environment Variables, add:
   - `VITE_SUPABASE_URL`: The Supabase URL
   - `VITE_SUPABASE_ANON_KEY`: The anon key
   - `VITE_TENANT_SLUGS`: The JSON tenant map
   - `VITE_BFF_URL`: Do **not** add this one here — it cannot be set to an empty string through the dashboard. It's already pinned empty by `apps/web/vercel.json`'s `buildCommand` (see below), which Vercel runs in place of the default build command.

3. `apps/web/vercel.json` (not a repo-root file) defines a rewrite:
   - `/v1/:path*` → `https://bff-production-6568.up.railway.app/v1/:path*`
   - This is the real, live Railway BFF domain — substituted for the original placeholder in Task 11, ahead of the first deploy. Update it here if the BFF is ever redeployed to a different Railway domain.

### Build & Deploy

```bash
# Build locally (requires env vars)
VITE_SUPABASE_URL=https://... VITE_SUPABASE_ANON_KEY=... VITE_TENANT_SLUGS='{}' VITE_BFF_URL= npm run build

# Or let Vercel build (it reads from Project Settings)
vercel deploy
```

## Local Development

```bash
# Uses defaults: VITE_SUPABASE_URL/VITE_SUPABASE_ANON_KEY omitted (auth disabled),
# VITE_TENANT_SLUGS defaults to {}, VITE_BFF_URL omitted (falls back to localhost:8001)
npm run dev

# With auth enabled against your Supabase project:
VITE_SUPABASE_URL=https://... VITE_SUPABASE_ANON_KEY=... VITE_TENANT_SLUGS='{"<uuid>":"<slug>"}' npm run dev
```

## Testing

```bash
# Unit tests (Vitest)
npm test

# Build check
npm run build

# Lint + type check
npm run lint
```

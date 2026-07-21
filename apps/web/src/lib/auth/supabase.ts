import { createClient, type SupabaseClient } from "@supabase/supabase-js";

const url = import.meta.env.VITE_SUPABASE_URL as string | undefined;
const anon = import.meta.env.VITE_SUPABASE_ANON_KEY as string | undefined;

// Auth-disabled dev mode is the default: with no VITE_SUPABASE_* env set
// (local Docker, most Vitest runs), `supabase` is null and every consumer
// (useAuth, client.ts) must degrade to today's pre-auth behavior exactly.
export const supabase: SupabaseClient | null = url && anon ? createClient(url, anon) : null;
export const authEnabled = supabase !== null;

// Reverse of the BFF's tenant_uuids (slug -> uuid): the access token's
// tenant_id claim is a uuid, but routes need the slug. Baked at build time
// for C2's single-tenant deploy; C4's signup flow replaces this with a
// `/v1/auth/whoami` endpoint (YAGNI now — see task-6 brief).
const slugMapRaw = import.meta.env.VITE_TENANT_SLUGS as string | undefined;
export const tenantSlugByUuid: Record<string, string> = slugMapRaw
  ? (JSON.parse(slugMapRaw) as Record<string, string>)
  : {};

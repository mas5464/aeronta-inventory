import { createClient, type SupabaseClient } from "@supabase/supabase-js";

const url = import.meta.env.VITE_SUPABASE_URL as string | undefined;
const anon = import.meta.env.VITE_SUPABASE_ANON_KEY as string | undefined;

// Auth-disabled dev mode is the default: with no VITE_SUPABASE_* env set
// (local Docker, most Vitest runs), `supabase` is null and every consumer
// (useAuth, client.ts) must degrade to today's pre-auth behavior exactly.
export const supabase: SupabaseClient | null = url && anon ? createClient(url, anon) : null;
export const authEnabled = supabase !== null;

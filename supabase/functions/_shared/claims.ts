// Deployment invariant: this function deploys with verify_jwt=true — the
// Supabase runtime verifies the JWT signature BEFORE invoking; this helper
// only extracts claims from the already-verified token. Never deploy these
// functions with --no-verify-jwt (that would make this a spoofable auth
// bypass, since it performs no signature check itself).
export function claimsFromAuthHeader(
  req: Request,
): Record<string, unknown> | null {
  const auth = req.headers.get("Authorization")?.replace("Bearer ", "");
  if (!auth) return null;
  try {
    return JSON.parse(atob(auth.split(".")[1]));
  } catch {
    return null;
  }
}

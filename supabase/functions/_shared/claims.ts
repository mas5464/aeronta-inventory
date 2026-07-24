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
    return JSON.parse(atob(base64UrlToBase64(auth.split(".")[1])));
  } catch {
    return null;
  }
}

// JWT payloads are base64url-encoded (RFC 7519 §3), not plain base64 —
// `-`/`_` (base64url) stand in for `+`/`/` (base64). `atob` only
// understands the latter, so a claims payload whose base64 form happens
// to contain `+` or `/` would throw/mis-decode without this normalization.
function base64UrlToBase64(b64url: string): string {
  const b64 = b64url.replace(/-/g, "+").replace(/_/g, "/");
  return b64 + "=".repeat((4 - (b64.length % 4)) % 4);
}

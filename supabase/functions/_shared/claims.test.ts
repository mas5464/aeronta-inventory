import { assertEquals } from "jsr:@std/assert@1";
import { claimsFromAuthHeader } from "./claims.ts";

// Base64url-encodes (RFC 4648 §5, no padding) a JSON payload the same way a
// real JWT would — i.e. via `+`→`-`, `/`→`_`, strip `=`. Deliberately picks
// payload content whose standard-base64 encoding contains `-`/`_` chars, the
// scenario that broke the bare-`atob` implementation (base64url chars aren't
// in atob's alphabet, so it threw and the caller saw a 401).
function base64UrlEncode(obj: unknown): string {
  const bytes = new TextEncoder().encode(JSON.stringify(obj));
  let bin = "";
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function fakeJwt(payload: Record<string, unknown>): string {
  const header = base64UrlEncode({ alg: "HS256", typ: "JWT" });
  const body = base64UrlEncode(payload);
  return `${header}.${body}.fakesignature`;
}

function reqWithBearer(token: string): Request {
  return new Request("http://x", {
    headers: { Authorization: `Bearer ${token}` },
  });
}

Deno.test("decodes a payload whose base64 form contains base64url chars (-/_)", () => {
  // "José~?" is chosen (over trial and error against btoa's output) because
  // its UTF-8 bytes base64-encode to a string containing both `-`-worthy and
  // `_`-worthy standard-base64 chars (`+`/`/`), reproducing the real bug:
  // bare `atob` on the RAW base64url string throws on these chars.
  const payload = {
    sub: "11111111-1111-1111-1111-111111111111",
    tenant_id: "22222222-2222-2222-2222-222222222222",
    tenant_role: "planner",
    name: "José~?",
  };
  const token = fakeJwt(payload);
  // Sanity: this token actually exercises the base64url alphabet, otherwise
  // the test would pass vacuously even with the old bare-atob bug.
  const rawPayloadSegment = token.split(".")[1];
  if (!/[-_]/.test(rawPayloadSegment)) {
    throw new Error(
      "test payload does not exercise base64url chars — fix the fixture",
    );
  }

  const claims = claimsFromAuthHeader(reqWithBearer(token));
  assertEquals(claims?.sub, payload.sub);
  assertEquals(claims?.tenant_id, payload.tenant_id);
  assertEquals(claims?.tenant_role, payload.tenant_role);
});

Deno.test("returns null when there's no Authorization header", () => {
  assertEquals(claimsFromAuthHeader(new Request("http://x")), null);
});

Deno.test("returns null on a garbage token instead of throwing", () => {
  assertEquals(claimsFromAuthHeader(reqWithBearer("not.a.jwt")), null);
});

import { test, expect, Page } from "@playwright/test";

const LIVE_URL = "https://aeronta-inventory.vercel.app";
const TEST_EMAIL = "admin@aeronta.test";
const TEST_PASSWORD = "27cpsZda0Ktr15rgWDXN";
const TENANT_SLUG = "aeronta-demo";

test.describe("Live Vercel App - Sign In Flow", () => {
  test("T1: should load the sign-in page", async ({ page }) => {
    await page.goto(LIVE_URL);
    await expect(page.getByRole("heading", { name: /sign in to trax/i })).toBeVisible();
    await expect(page.getByLabel("Email")).toBeVisible();
    await expect(page.getByLabel("Password")).toBeVisible();
    await expect(page.getByRole("button", { name: /sign in/i })).toBeVisible();
  });

  test("T2: should sign in and capture redirect behavior", async ({ page }) => {
    await page.goto(LIVE_URL);

    // Log network requests to capture what happens after sign-in
    const requests: Array<{ url: string; status: number }> = [];
    page.on("response", (response) => {
      requests.push({
        url: response.url(),
        status: response.status(),
      });
    });

    // Fill and submit the sign-in form
    await page.getByLabel("Email").fill(TEST_EMAIL);
    await page.getByLabel("Password").fill(TEST_PASSWORD);

    // Wait for the sign-in request to complete
    const [signInResponse] = await Promise.all([
      page.waitForResponse(
        (resp) =>
          resp.url().includes("/auth/") &&
          (resp.url().includes("token") || resp.url().includes("verify"))
      ),
      page.getByRole("button", { name: /sign in/i }).click(),
    ]);

    console.log(`Sign-in response status: ${signInResponse.status()}`);

    // Wait for the page to settle (either to dashboard or back to login)
    await page.waitForLoadState("networkidle", { timeout: 5000 }).catch(() => {});

    // Capture the current URL and page state
    const currentUrl = page.url();
    const isStillSignIn = await page
      .getByRole("heading", { name: /sign in/i })
      .isVisible()
      .catch(() => false);
    const isDashboard = await page
      .getByRole("heading", { name: /overview|dashboard/i })
      .isVisible()
      .catch(() => false);

    console.log(`Current URL: ${currentUrl}`);
    console.log(`Still on sign-in: ${isStillSignIn}`);
    console.log(`Dashboard visible: ${isDashboard}`);

    // Log the recent network requests
    console.log("Network requests (last 10):");
    requests.slice(-10).forEach((req) => {
      console.log(`  ${req.status} ${new URL(req.url).pathname}`);
    });

    // Flag the issue
    if (isStillSignIn && !isDashboard) {
      console.log("⚠️  ISSUE DETECTED: User bounced back to sign-in after successful auth");
    } else if (isDashboard) {
      console.log("✓ Successfully reached dashboard");
    }
  });

  test("T3: inspect localStorage and session after sign-in attempt", async ({ page }) => {
    await page.goto(LIVE_URL);
    await page.getByLabel("Email").fill(TEST_EMAIL);
    await page.getByLabel("Password").fill(TEST_PASSWORD);
    await page.getByRole("button", { name: /sign in/i }).click();

    // Wait briefly for any post-auth actions
    await page.waitForTimeout(2000);

    // Inspect localStorage
    const storageKeys = await page.evaluate(() => JSON.stringify(Object.keys(localStorage)));
    console.log(`localStorage keys: ${storageKeys}`);

    // Try to get the auth token if it exists (in sessionStorage or cookies)
    const cookies = await page.context().cookies();
    console.log(`Cookies count: ${cookies.length}`);
    cookies.forEach((cookie) => {
      if (cookie.name.toLowerCase().includes("auth") || cookie.name.toLowerCase().includes("token")) {
        console.log(`  Auth-related cookie: ${cookie.name} (domain: ${cookie.domain})`);
      }
    });

    // Inspect what tenant was attempted
    const pathname = new URL(page.url()).pathname;
    const hashSearch = page.url().split("#")[1] || "";
    console.log(`Pathname: ${pathname}, Hash: ${hashSearch}`);
  });

  test("T4: check for VITE_TENANT_SLUGS stale reference in HTML", async ({ page }) => {
    const response = await page.goto(LIVE_URL);
    const bodyText = await page.content();

    // Look for evidence of hardcoded tenant slugs
    const hasSlugs = bodyText.includes("VITE_TENANT_SLUGS") || bodyText.includes("acme");
    console.log(`Body contains VITE_TENANT_SLUGS or 'acme': ${hasSlugs}`);

    // Check the built JS for hardcoded tenant logic
    const scripts = await page.locator("script").all();
    for (let i = 0; i < Math.min(scripts.length, 3); i++) {
      const src = await scripts[i].getAttribute("src");
      if (src) {
        console.log(`Script ${i}: ${src}`);
      }
    }
  });

  test("T5: verify GET /v1/auth/whoami behavior post-sign-in", async ({ page }) => {
    await page.goto(LIVE_URL);

    // Intercept the whoami call to see what tenant it resolves to
    const whoamiResponses: Array<{ status: number; body: string }> = [];
    page.on("response", async (resp) => {
      if (resp.url().includes("/auth/whoami")) {
        const text = await resp.text().catch(() => "(unable to read)");
        whoamiResponses.push({
          status: resp.status(),
          body: text.substring(0, 200),
        });
      }
    });

    await page.getByLabel("Email").fill(TEST_EMAIL);
    await page.getByLabel("Password").fill(TEST_PASSWORD);
    await page.getByRole("button", { name: /sign in/i }).click();

    // Wait for whoami calls
    await page.waitForTimeout(3000);

    console.log("whoami responses:");
    whoamiResponses.forEach((resp, idx) => {
      console.log(`  [${idx}] ${resp.status}: ${resp.body}`);
    });
  });
});

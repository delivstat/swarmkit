import { expect, test } from "@playwright/test";

// Full human→panel OIDC loop: login gate → IdP redirect → PKCE code exchange → the UI sends the
// access token to the panel, which verifies it → fleet data renders. Servers (fake IdP, panel with
// --oidc-issuer, UI) are started by playwright.config.ts.

const PANEL = "http://127.0.0.1:8819";
const OPERATOR_TOKEN = "op-e2e-secret";
const INSTANCE = "sterling-dc";

test.beforeAll(async ({ playwright }) => {
	// Seed an instance via the operator token so there's data to prove the access token works.
	const ctx = await playwright.request.newContext();
	await ctx.post(`${PANEL}/instances`, {
		headers: { Authorization: `Bearer ${OPERATOR_TOKEN}` },
		data: {
			name: INSTANCE,
			endpoint: "(NAT)",
			connection: "poll",
			tier: "run",
		},
	});
	await ctx.dispose();
});

test("operator signs in via OIDC and sees fleet data", async ({ page }) => {
	await page.goto("/dashboard");

	// 1. Unauthenticated → gated behind the login screen.
	await expect(page.getByText("Sign in to manage the fleet")).toBeVisible();

	// 2. Sign in → IdP /authorize → 302 back with ?code&state → oidc-client-ts exchanges the code.
	await page
		.getByRole("button", { name: "Sign in with your identity provider" })
		.click();

	// 3. The access token reaches the panel, is verified, and the seeded instance renders.
	// Scope to the main content — the instance name also appears in the sidebar's global
	// instance selector (an <option>), so an unscoped getByText would be ambiguous.
	await expect(page.getByRole("main").getByText(INSTANCE)).toBeVisible({
		timeout: 30_000,
	});

	// 4. Authenticated shell: sign-out present, and the callback params are stripped from the URL.
	await expect(page.getByRole("button", { name: "Sign out" })).toBeVisible();
	expect(new URL(page.url()).search).toBe("");
});

test("signing out returns to the login gate", async ({ page }) => {
	await page.goto("/dashboard");
	await page
		.getByRole("button", { name: "Sign in with your identity provider" })
		.click();
	await expect(page.getByRole("main").getByText(INSTANCE)).toBeVisible({
		timeout: 30_000,
	});

	await page.getByRole("button", { name: "Sign out" }).click();
	await expect(page.getByText("Sign in to manage the fleet")).toBeVisible();
});

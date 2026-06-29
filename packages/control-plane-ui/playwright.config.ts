import { defineConfig } from "@playwright/test";

// End-to-end OIDC login test. Starts three servers: the fake OIDC IdP, the panel configured with
// --oidc-issuer (pointed at the IdP), and this UI pointed at both. `cwd: "../.."` runs the Python
// servers from the repo root (the uv workspace). Browser: `playwright install chromium`.

const PANEL_DATA = "/tmp/swarmkit-e2e-cp";
const reuse = !process.env.CI;

export default defineConfig({
	testDir: "./e2e",
	timeout: 60_000,
	fullyParallel: false,
	workers: 1,
	reporter: "list",
	use: { baseURL: "http://localhost:3000", trace: "on-first-retry" },
	webServer: [
		{
			command: "uv run python packages/control-plane-ui/e2e/fake-idp.py",
			cwd: "../..",
			url: "http://127.0.0.1:8402/jwks",
			reuseExistingServer: reuse,
			timeout: 60_000,
		},
		{
			command: [
				"uv run swarmkit-control-plane",
				`--data-dir ${PANEL_DATA} --port 8819`,
				"--operator-token op-e2e-secret",
				"--oidc-issuer http://127.0.0.1:8402 --oidc-jwks-url http://127.0.0.1:8402/jwks",
				"--oidc-audience swarmkit-control-plane --cors-origin http://localhost:3000",
			].join(" "),
			cwd: "../..",
			url: "http://127.0.0.1:8819/health",
			reuseExistingServer: reuse,
			timeout: 60_000,
		},
		{
			command: "pnpm dev",
			url: "http://localhost:3000",
			reuseExistingServer: reuse,
			timeout: 120_000,
			env: {
				NEXT_PUBLIC_OIDC_AUTHORITY: "http://127.0.0.1:8402",
				NEXT_PUBLIC_OIDC_CLIENT_ID: "swarmkit-fleet-ui",
				NEXT_PUBLIC_OIDC_AUDIENCE: "swarmkit-control-plane",
				NEXT_PUBLIC_CONTROL_PLANE_API: "http://127.0.0.1:8819",
			},
		},
	],
});

// Screenshot the /author on-ramp page from #1045 PR 11 against a real serve process.
// Covers both halves of the gate: the opt-in explanation (default off) and the mode
// cards (SWARMKIT_AUTHOR_EXPOSE=1).
//
// Preconditions:
//
//   # 1. A throwaway workspace that opts in
//   mkdir -p /tmp/swarmkit-author-demo
//   cat > /tmp/swarmkit-author-demo/workspace.yaml <<'EOF'
//   apiVersion: swarmkit/v1
//   kind: Workspace
//   metadata:
//     id: author-demo
//     name: Author demo
//   authoring:
//     expose: true
//   EOF
//
//   # 2. Serve it (any port > 1024; the dev server reaches it via NEXT_PUBLIC_SWARMKIT_API)
//   swarmkit serve /tmp/swarmkit-author-demo --port 8099 --insecure \
//       --cors-origin http://127.0.0.1:3009
//
//   # 3. Portal dev server
//   NEXT_PUBLIC_SWARMKIT_API=http://127.0.0.1:8099 pnpm --filter @swarmkit/ui dev -p 3009
//
//   # 4. Captures
//   pnpm --filter @swarmkit/ui demo:author [outdir]

import { chromium } from "@playwright/test";

const out = process.argv[2] ?? ".";
const UI = process.env.UI_ORIGIN ?? "http://127.0.0.1:3009";

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 820 } });
page.on("console", (m) => {
	if (m.type() === "error") console.log("console error:", m.text());
});
page.setDefaultTimeout(60000);
page.setDefaultNavigationTimeout(60000);

// 1. The sidebar shows the new entry (always rendered; page itself gates).
await page.goto(`${UI}/dashboard`, { waitUntil: "domcontentloaded" });
await page.waitForSelector("text=Author", { timeout: 60000 });
await page.waitForTimeout(1500);
await page.screenshot({ path: `${out}/sidebar-author.png` });

// 2. The /author page itself with authoring exposed — five mode cards.
await page.goto(`${UI}/author`, { waitUntil: "domcontentloaded" });
await page.waitForSelector("text=Topology", { timeout: 60000 });
await page.waitForTimeout(1500);
await page.screenshot({ path: `${out}/author-modes.png`, fullPage: true });

// 3. Hand-off: click Topology, land on /chat with a fresh conversation.
await page.locator("button", { hasText: "Topology" }).first().click();
await page
	.waitForURL(/\/chat/, { timeout: 60000 })
	.catch(() =>
		console.log("note: chat redirect didn't fire; capturing current page"),
	);
await page.waitForTimeout(3000);
await page.screenshot({
	path: `${out}/chat-after-handoff.png`,
	fullPage: true,
});

await browser.close();
console.log(
	`wrote sidebar-author.png, author-modes.png, chat-after-handoff.png to ${out}`,
);

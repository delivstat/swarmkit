import { chromium } from "@playwright/test";
const out = process.argv[2];
const b = await chromium.launch();
const p = await b.newPage({ viewport: { width: 1440, height: 900 } });
await p.goto("http://127.0.0.1:3011/runs?run=run-42&stage=greeter", {
	waitUntil: "networkidle",
});
await p.waitForSelector("text=Request changes", { timeout: 20000 });
await p.waitForTimeout(2000);
await p.screenshot({ path: `${out}/decision-comment.png` });

// Type a comment and request changes, as a reviewer would.
const row = p.locator("li", { hasText: "security-reviewer" }).first();
await row
	.locator("textarea")
	.fill(
		"The retry loop has no backoff. Add exponential backoff before this ships.",
	);
await p.waitForTimeout(500);
await p.screenshot({ path: `${out}/decision-typed.png` });
await row.getByRole("button", { name: "Request changes" }).click();
await p.waitForTimeout(2500);
await p.screenshot({ path: `${out}/decision-recorded.png` });
console.log(
	"panel:",
	(await p.locator("aside, body").first().innerText())
		.replace(/\s+/g, " ")
		.slice(0, 400),
);
await b.close();

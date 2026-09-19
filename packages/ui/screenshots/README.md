# Portal screenshots

`capture.mjs` renders the queue-stats strip (queue-observability.md) and the A2A trace deep-link
(a2a-federation.md) with Playwright, mocking the serve API with fixtures (no live backend needed).

```bash
# needs a chromium (npx playwright install chromium) and free memory for `next dev`
node packages/ui/screenshots/capture.mjs
# → queue-strip.png, a2a-deeplink.png (gitignored — regenerate as needed)
```

The PNGs are gitignored; they are demo artifacts, regenerated on demand.

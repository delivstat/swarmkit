# swarmkit-webui

The SwarmKit web portal as a **static build**, packaged so `swarmkit serve` can host it at its own
origin. Not a Node app — just the exported HTML/JS/CSS.

Installed via the runtime extra:

```bash
pip install "swarmkit-runtime[ui]"
swarmkit serve ./workspace   # portal + API on one origin
```

The static assets under `swarmkit_webui/_static/` are generated at release from `packages/ui`
(`just build-webui`), not committed. `static_dir()` returns their path, or `None` when not built.

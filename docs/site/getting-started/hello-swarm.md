# Hello Swarm

The smallest workspace that exercises every piece of the mental model.

```bash
# Validate the workspace
swarmkit validate examples/hello-swarm/workspace --tree

# Run it (needs a model provider)
SWARMKIT_PROVIDER=openrouter SWARMKIT_MODEL=meta-llama/llama-3.3-70b-instruct \
  swarmkit run examples/hello-swarm/workspace hello \
  --input "Greet the engineering team"
```

The topology has a root supervisor and a greeter worker. The greeter calls the `hello-world` MCP server's `greet` tool — a FastMCP server that lives alongside the workspace.

See [`examples/hello-swarm/README.md`](https://github.com/delivstat/swarmkit/tree/main/examples/hello-swarm) for the full walkthrough.

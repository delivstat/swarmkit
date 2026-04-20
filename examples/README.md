# examples/

Runnable demos for SwarmKit features. Every feature's PR either adds or links to an example here.

## Rules

- Each example is self-contained: a directory with its own `README.md`, any required YAML artifacts, and a command to run.
- The command to run goes in a top-level `justfile` target `demo-<example-name>` so reviewers can reproduce with one line.
- Examples exercise the real runtime. Do not fake output. If the feature isn't ready to run end-to-end, the demo lives in the PR body (transcript / screenshot) instead of here.
- Retire examples that drift out of sync with the runtime — a broken example is worse than no example.

## Layout

```
examples/
├── README.md
└── <name>/
    ├── README.md
    ├── topology.yaml
    └── ...
```

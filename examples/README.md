# examples/

Runnable demos for SwarmKit features. Every feature's PR either adds or links to an example here.

## Rules

- Each example is self-contained: a directory with its own `README.md`, any required YAML artifacts, and a command to run.
- The command to run goes in a top-level `justfile` target `demo-<example-name>` so reviewers can reproduce with one line.
- Examples exercise the real runtime. Do not fake output. If the feature isn't ready to run end-to-end, the demo lives in the PR body (transcript / screenshot) instead of here.
- Retire examples that drift out of sync with the runtime — a broken example is worse than no example.

## Products built with SwarmKit live elsewhere

Two applications used to sit here, and being under `examples/` is what kept them from being
releasable — a product with no repo of its own cannot pin a runtime version like any other consumer.
They are examples of what SwarmKit is *for*, not examples of how to use a feature, which is what
this directory is.

| | |
| --- | --- |
| **Minder** — self-hosted home-security appliance | `delivstat/minder`, docs at <https://delivstat.github.io/minder-docs/> |
| **Vedanta Advisor** — Hindu scripture knowledge system | `delivstat/vedanta-advisor` |

The docs still cite both as evidence — "measured on the vedanta-advisor workspace", the dual-model
cost comparison. That is deliberate: a framework claim measured against a real product is worth
more than the product's code sitting in-tree, and the citation is the only thing a reader loses.

See [`design/details/extracting-the-products.md`](../design/details/extracting-the-products.md).

## Layout

```
examples/
├── README.md
└── <name>/
    ├── README.md
    ├── topology.yaml
    └── ...
```

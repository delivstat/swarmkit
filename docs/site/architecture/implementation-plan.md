# Implementation plan

See [`design/IMPLEMENTATION-PLAN.md`](https://github.com/delivstat/swarmkit/blob/main/design/IMPLEMENTATION-PLAN.md) for the full milestone-by-milestone roadmap.

Current status (runtime v1.192.0): **Phases 1–4 complete**; **Phase 5** (fleet & self-improvement) largely shipped — eval harness, the fleet control plane + panel UI, the executor/harness-isolation stack, and the topology canvas; **Phase 6** shipped as its **governance** half — funnels, contracts, multi-party approval, defer-and-resume on a human gate, correlated runs and the end-to-end SDLC workspace. Its **sequencing** half (StageGraph + saga controller + the bundled orchestrator) was deliberately removed in 1.189.0: sequencing is the application's, and `examples/pipeline-orchestrator/` is the reference. Remaining before launch: M11 (launch prep) and the M17 self-improvement distribution loop.

# OMS demo repo

A tiny stub Order Management System used as the target repo for the SwarmKit SDLC
harness-build slice (slice 7). The `developer` harness (a `claude-code` executor, run
sandboxed and read-scoped to this repo) implements the approved design into a **candidate
diff** against these files; the code-review gate then decides advance / route-back.

This is a fixture — it is deliberately minimal and is never imported by the runtime.

## API

- `submit_order(order_id, items)` — create an order in the `created` state.

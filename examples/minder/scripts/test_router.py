#!/usr/bin/env python3
"""Integration test for the intent router (Phase 1-3 deterministic pipeline).

Runs the live minder-router topology over router_cases.json and checks the
emitted plan's `kind` against the expected intent. Run INSIDE the minder
container (it needs SwarmKit serve + Ollama):

    docker compose cp scripts/test_router.py minder:/tmp/test_router.py
    docker compose cp scripts/router_cases.json minder:/tmp/router_cases.json
    docker compose exec minder python /tmp/test_router.py /tmp/router_cases.json

Exit code 0 if all cases pass. This is the example's routing acceptance test —
it proves "the LLM does the language" half is solid before the deterministic
execution runs.
"""

import asyncio
import json
import sys
import time

sys.path.insert(0, "/app/webapp")
import minder_ops as m


async def main(cases_path: str) -> int:
    cases = json.loads(open(cases_path).read())
    # Warm the router once so per-case latency is representative.
    await m.route("hello")
    ok = 0
    for c in cases:
        t = time.monotonic()
        plan = await m.route(c["msg"]) or {}
        got = plan.get("kind")
        passed = got == c["kind"]
        ok += passed
        print(
            f"  [{'OK ' if passed else 'XXX'}] {c['msg'][:42]:42} "
            f"exp={c['kind']:12} got={got!s:12} ({time.monotonic() - t:.0f}s)"
        )
    print(f"\nrouter: {ok}/{len(cases)} intents correct")
    return 0 if ok == len(cases) else 1


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/router_cases.json"
    sys.exit(asyncio.run(main(path)))

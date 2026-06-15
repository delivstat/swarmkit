"""Module-integrity guard for minder_ops.

A `global X` statement needs a module-level `X = ...` or the first access throws
NameError at runtime (this happened: a refactor removed `_devices_tool_module`,
breaking weather setup). This test asserts every global declared in minder_ops
has a module-level binding — catching the whole class before deploy.

Run in-container:  docker compose exec -T minder python /app/webapp/test_ops_integrity.py
"""

import inspect
import re

import minder_ops


def test_every_global_has_module_binding():
    src = inspect.getsource(minder_ops)
    names = set(re.findall(r"^\s*global\s+([\w, ]+)", src, re.M))
    flat = {n.strip() for group in names for n in group.split(",") if n.strip()}
    assert flat, "expected at least one global declaration"
    missing = [n for n in sorted(flat) if not hasattr(minder_ops, n)]
    assert not missing, f"globals declared but not bound at module level: {missing}"
    print(f"ok  all {len(flat)} module globals are bound: {sorted(flat)}")


def test_devices_module_loads():
    # _devices_module() uses the _devices_tool_module global — must not NameError
    mod = minder_ops._devices_module()
    assert mod is not None
    # weather_status relays through it (the path that failed in onboarding)
    assert isinstance(minder_ops.weather_status(), dict)
    print("ok  _devices_module() + weather_status() run without NameError")


if __name__ == "__main__":
    test_every_global_has_module_binding()
    test_devices_module_loads()
    print("\nALL OPS-INTEGRITY TESTS PASSED")

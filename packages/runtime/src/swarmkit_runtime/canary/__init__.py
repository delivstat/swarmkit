from swarmkit_runtime.canary._router import CanaryRouter

__all__ = ["CanaryRouter"]


def router_for_workspace(workspace: object) -> CanaryRouter | None:
    """The canary router a workspace's routes describe, or None when it declares none.

    One definition, because canary routing is a property of the *workspace*, not of the front door
    that happens to be running it. `swarmkit serve` built this inline and `swarmkit run` built
    nothing at all, so the same topology name resolved to the canary over HTTP and to the base
    version on the CLI — the two interfaces genuinely executed different things, silently.
    """
    from swarmkit_runtime.server._config import _parse_canary_routes  # noqa: PLC0415

    routes = _parse_canary_routes(workspace)
    if not routes:
        return None
    topologies = getattr(workspace, "topologies", {}) or {}
    available: dict[str, set[str]] = {name.split("@")[0]: set() for name in topologies}
    for name, topo in topologies.items():
        try:
            available[name.split("@")[0]].add(topo.raw.metadata.version)
        except AttributeError:  # a double or a topology that never loaded its raw form
            continue
    return CanaryRouter(routes, available)

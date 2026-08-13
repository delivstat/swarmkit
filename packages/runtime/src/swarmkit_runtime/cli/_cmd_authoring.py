"""CLI commands — validate, gaps, init, the review queue, conversational authoring, edit."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

if TYPE_CHECKING:
    pass

import typer

from swarmkit_runtime._workspace_runtime import (
    MissingMCPServerError,
    WorkspaceRuntime,
    resolve_authoring_provider,
)
from swarmkit_runtime.authoring import run_authoring_session
from swarmkit_runtime.authoring._prompts import AuthoringMode
from swarmkit_runtime.errors import ResolutionErrors
from swarmkit_runtime.gaps import SkillGapLog
from swarmkit_runtime.resolver import resolve_workspace
from swarmkit_runtime.review import FileReviewQueue
from swarmkit_runtime.review._multiparty import membership_error

from ._app import app, author_app, review_app
from ._common import (
    _EXIT_RESOLUTION_ERROR,
    _EXIT_USAGE,
    _emit_errors,
    _emit_success,
    _print_banner,
    _stderr,
    _suppress_noisy_logs,
)
from ._render import should_colour

# ---- validate -----------------------------------------------------------


@app.command()
def validate(
    path: Annotated[
        Path,
        typer.Argument(
            help="Workspace root (directory containing workspace.yaml).",
            show_default=False,
        ),
    ] = Path("."),
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit JSONL instead of human-formatted output."),
    ] = False,
    tree: Annotated[
        bool,
        typer.Option("--tree", help="On success, print the fully-expanded resolved agent tree."),
    ] = False,
    quiet: Annotated[
        bool,
        typer.Option("--quiet", "-q", help="Suppress the success summary; errors still print."),
    ] = False,
    require: Annotated[
        bool,
        typer.Option(
            "--require",
            help=(
                "Exit non-zero when anything declared is reached by no code path. For CI. Off by "
                "default: turning green pipelines red on upgrade is not this check's job."
            ),
        ),
    ] = False,
    color: Annotated[
        bool | None,
        typer.Option(
            "--color/--no-color",
            help=(
                "Override TTY auto-detection for coloured output. "
                "NO_COLOR env var always suppresses."
            ),
        ),
    ] = None,
) -> None:
    """Validate a SwarmKit workspace and print a resolved tree or errors."""
    use_colour = should_colour(sys.stdout.isatty(), color)
    workspace_root = path.resolve()

    try:
        workspace = resolve_workspace(workspace_root)
    except ResolutionErrors as exc:
        _emit_errors(
            list(exc.errors), json_mode=json_output, workspace_root=workspace_root, color=use_colour
        )
        raise typer.Exit(_EXIT_RESOLUTION_ERROR) from exc
    except FileNotFoundError as exc:
        _stderr(f"error: {exc}")
        raise typer.Exit(_EXIT_USAGE) from exc

    # Reachability is reported ALWAYS, because the complaint in every bug of this family is that
    # `validate` accepted the binding and displayed it while nothing loaded it
    # (design/details/declared-but-unreachable.md).
    report = _reachability_report(workspace_root)

    if not quiet:
        _emit_success(workspace, json_mode=json_output, tree=tree, color=use_colour)
        if report is not None:
            _emit_reachability(report, json_mode=json_output)

    # Any unreachable declaration fails under `--require`, not only the REQUIRED ones: funnel
    # layers carry no `required` flag, so gating on that alone would pass a workspace whose every
    # validate layer is inert. REQUIRED stays as emphasis in the report, not as the gate.
    if require and report is not None and not report.ok:
        raise typer.Exit(_EXIT_RESOLUTION_ERROR)


def _reachability_report(workspace_root: Path) -> Any:
    """Compile every topology with a wiring ledger and diff against what is declared.

    Returns None when a runtime cannot be built at all (unconfigured MCP servers, missing
    credentials). `validate`'s job is to report what it can; a workspace that cannot be wired is a
    different failure, and losing the resolution result to it would be a worse outcome than a
    missing section.
    """
    from swarmkit_runtime._workspace_runtime import WorkspaceRuntime  # noqa: PLC0415

    try:
        return WorkspaceRuntime.from_workspace_path(workspace_root).reachability()
    except Exception:
        return None


def _reachable_line(decl: Any) -> str:
    """One wired declaration, shaped like an unreachable one so the two read as a single list."""
    required = "  REQUIRED" if getattr(decl, "required", False) else ""
    detail = f" — {decl.detail}" if getattr(decl, "detail", "") else ""
    return f"{decl.kind} {decl.key!r} declared on {decl.declared_on}{detail}: wired{required}"


def _emit_reachability(report: Any, *, json_mode: bool) -> None:
    if json_mode:
        typer.echo(json.dumps({"event": "validate.reachability", **report.to_dict()}))
        return
    # Named, not counted. "all 12 declared bindings are wired" is a summary OF a check, not the
    # check: with per-kind funnels a reader wants to know WHICH twelve, and an aggregate hides the
    # case where the twelve are not the twelve they meant to declare.
    if report.ok:
        typer.echo(f"\nreachability: {len(report.reachable)} declared, all wired")
        for decl in report.reachable:
            typer.echo(f"  {_reachable_line(decl)}")
        return
    typer.echo(
        f"\nreachability: {len(report.reachable) + len(report.unreachable)} declared, "
        f"{len(report.unreachable)} reached by nothing"
    )
    for decl in report.reachable:
        typer.echo(f"  {_reachable_line(decl)}")
    for item in report.unreachable:
        typer.echo(f"  {item.line()}")
    if report.blocking:
        typer.echo(
            f"\n  {len(report.blocking)} of these is REQUIRED — the workspace believes a check "
            f"is enforcing something that has never run."
        )


# ---- review + gaps -------------------------------------------------------


@review_app.command("gate")
def review_gate(
    gate_id: Annotated[str, typer.Argument(help="Gate id, e.g. wms-design:designer.")],
    workspace_path: Annotated[
        Path, typer.Argument(help="Workspace root.", show_default=False)
    ] = Path("."),
    author: Annotated[
        str,
        typer.Option(
            "--author",
            help="Artifact's author — needed when the policy excludes the author from approving.",
        ),
    ] = "",
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    """Whether a gate is resolved, with its approval policy applied.

    Under `review`, not `gates`: `swarmkit gates` is pipeline gate COVERAGE — a static analysis of
    which edges are verified — and this is a live question about one queue.
    """
    from swarmkit_runtime.gate_state import (  # noqa: PLC0415
        PolicyUnresolvedError,
        UnknownGateError,
        compute_gate_state,
    )
    from swarmkit_runtime.resolver import resolve_workspace  # noqa: PLC0415
    from swarmkit_runtime.review import FileReviewQueue  # noqa: PLC0415

    root = workspace_path.resolve()
    workspace = resolve_workspace(root)
    try:
        state = compute_gate_state(
            FileReviewQueue(root),
            workspace.role_registry,
            workspace,
            gate_id,
            author=author or None,
        )
    except UnknownGateError:
        _stderr(f"error: no gate {gate_id!r} in this workspace's review queue.")
        raise typer.Exit(1) from None
    except PolicyUnresolvedError as exc:
        _stderr(f"error: {exc}")
        raise typer.Exit(1) from None

    if json_output:
        typer.echo(json.dumps(state.to_dict()))
        return

    typer.echo(f"{state.status}  {state.gate_id}")
    typer.echo(f"  funnel {state.funnel_id or '-'} on {state.topology_id}/{state.agent_id}")
    if state.artifact_ref:
        typer.echo(f"  about  {state.artifact_ref} (round {state.round})")
    for res in state.resolutions:
        mark = "  (stale — decided on an earlier artifact)" if res.stale else ""
        who = f" by {res.resolved_by}" if res.resolved_by else ""
        typer.echo(f"  {res.status:<18} {res.role} ({res.scope}){who}{mark}")
    if state.outstanding:
        typer.echo(f"  waiting on: {', '.join(state.outstanding)}")
    if state.exclude_author_unapplied:
        # Never silently: this policy discounts the author's own approval, and without an identity
        # that rule was not applied — so `approved` here may not be the real verdict.
        typer.echo(
            "  note: this policy excludes the author, and no --author was given, so that rule "
            "was NOT applied."
        )


_KINDS = {
    "harness-approval": "permission",
    "harness-input": "input",
    "multi-party-approval": "role_task",
}


@review_app.command("list")
def review_list(
    workspace_path: Annotated[
        Path, typer.Argument(help="Workspace root.", show_default=False)
    ] = Path("."),
    kind: Annotated[
        str,
        typer.Option("--kind", help="Filter by kind: permission | input | role_task | other."),
    ] = "",
    gate: Annotated[str, typer.Option("--gate", help="Filter role-tasks to one gate id.")] = "",
) -> None:
    """List pending review items."""
    queue = FileReviewQueue(workspace_path.resolve())
    pending = queue.list_pending()
    if kind:
        pending = [i for i in pending if _KINDS.get(i.skill_id, "other") == kind]
    if gate:
        pending = [i for i in pending if i.output.get("gate_id") == gate]
    if not pending:
        typer.echo("No pending reviews.")
        return
    for item in pending:
        # A role-task's role is the decision being asked for; the skill id is the same for all of
        # them, so showing it alone would make sibling tasks indistinguishable.
        detail = (
            f"role={item.output.get('role', '')} scope={item.output.get('scope', '')}"
            if item.skill_id == "multi-party-approval"
            else item.reason
        )
        typer.echo(f"  {item.id[:8]}  {item.agent_id:<16} {item.skill_id:<24} {detail}")


@review_app.command("show")
def review_show(
    item_id: str,
    workspace_path: Annotated[
        Path, typer.Argument(help="Workspace root.", show_default=False)
    ] = Path("."),
) -> None:
    """Show full details of a review item."""
    queue = FileReviewQueue(workspace_path.resolve())
    for item in queue.list_all():
        if item.id.startswith(item_id):
            typer.echo(f"ID:       {item.id}")
            typer.echo(f"Agent:    {item.agent_id}")
            typer.echo(f"Skill:    {item.skill_id}")
            typer.echo(f"Status:   {item.status}")
            typer.echo(f"Reason:   {item.reason}")
            # Friendly rendering for harness gates (§6.2 permission / §6.3 input).
            if item.skill_id == "harness-approval":
                typer.echo(f"Capability: {item.output.get('capability', '')}")
                typer.echo("Resolve with: swarmkit review approve|reject <id>")
            elif item.skill_id == "harness-input":
                typer.echo(f"Question:   {item.output.get('question', '')}")
                options = item.output.get("options") or []
                for i, opt in enumerate(options):
                    typer.echo(f"  [{i}] {opt}")
                if item.output.get("free_text_allowed", True):
                    typer.echo("  (free text also accepted)")
                typer.echo('Resolve with: swarmkit review answer <id> "<your answer>"')
            elif item.skill_id == "multi-party-approval":
                typer.echo(f"Gate:       {item.output.get('gate_id', '')}")
                typer.echo(f"Role:       {item.output.get('role', '')}")
                typer.echo(f"Scope:      {item.output.get('scope', '')}")
                if item.resolved_by or item.answer:
                    typer.echo(f"Resolved by: {item.resolved_by or item.answer}")
                if item.artifact_ref:
                    typer.echo(f"About:      {item.artifact_ref} (round {item.round})")
                if item.comment:
                    typer.echo(f"Comment:    {item.comment}")
                typer.echo("Resolve with: swarmkit review resolve <id> --as <identity> --approve")
            else:
                typer.echo(f"Output:   {json.dumps(item.output, indent=2)}")
                typer.echo(f"Verdict:  {json.dumps(item.verdict, indent=2)}")
            return
    _stderr(f"Review item '{item_id}' not found.")
    raise typer.Exit(1)


@review_app.command("approve")
def review_approve(
    item_id: str,
    workspace_path: Annotated[
        Path, typer.Argument(help="Workspace root.", show_default=False)
    ] = Path("."),
    comment: Annotated[
        str,
        typer.Option(
            "--comment", "-m", help="Why. Relayed to the agent and recorded on the audit."
        ),
    ] = "",
) -> None:
    """Approve a pending review item."""
    queue = FileReviewQueue(workspace_path.resolve())
    for item in queue.list_all():
        if item.id.startswith(item_id):
            queue.resolve(item.id, "approved", comment)
            note = f' — "{comment}"' if comment else ""
            typer.echo(f"✓ Approved {item.id[:8]}{note}")
            return
    _stderr(f"Review item '{item_id}' not found.")
    raise typer.Exit(1)


@review_app.command("reject")
def review_reject(
    item_id: str,
    workspace_path: Annotated[
        Path, typer.Argument(help="Workspace root.", show_default=False)
    ] = Path("."),
    comment: Annotated[
        str,
        typer.Option(
            "--comment", "-m", help="Why. Relayed to the agent and recorded on the audit."
        ),
    ] = "",
) -> None:
    """Reject a pending review item."""
    queue = FileReviewQueue(workspace_path.resolve())
    for item in queue.list_all():
        if item.id.startswith(item_id):
            queue.resolve(item.id, "rejected")
            typer.echo(f"✗ Rejected {item.id[:8]}")
            return
    _stderr(f"Review item '{item_id}' not found.")
    raise typer.Exit(1)


@review_app.command("resolve")
def review_resolve(
    item_id: str,
    identity: Annotated[
        str,
        typer.Option(
            "--as",
            help="Resolver identity — must be a member of the role in the workspace registry.",
        ),
    ],
    approve: Annotated[
        bool, typer.Option("--approve/--reject", help="Approve or reject this role-task.")
    ] = True,
    changes_requested: Annotated[
        bool,
        typer.Option(
            "--changes-requested",
            help="Send it back for revision. Unlike --reject this does NOT end the run: the stage "
            "re-runs with your comment.",
        ),
    ] = False,
    comment: Annotated[
        str,
        typer.Option(
            "--comment", "-m", help="Why. Relayed to the agent and recorded on the audit."
        ),
    ] = "",
    workspace_path: Annotated[
        Path, typer.Argument(help="Workspace root.", show_default=False)
    ] = Path("."),
) -> None:
    """Resolve a multi-party approval role-task as *identity*.

    Unlike `approve`/`reject`, the resolver identity is recorded and checked — the approval engine
    counts a resolution only from a registry member of that role. Local resolution is filesystem
    trust: the CLI has no serve credential, so `--as` is asserted, not authenticated. Anyone who can
    run this can already edit the queue on disk. Over HTTP the identity comes from the session
    instead (design/details/pipeline-gate-approval-ui.md).
    """
    root = workspace_path.resolve()
    queue = FileReviewQueue(root)
    matches = [i for i in queue.list_all() if i.id.startswith(item_id)]
    if not matches:
        _stderr(f"Review item '{item_id}' not found.")
        raise typer.Exit(1)
    item = matches[0]
    if item.skill_id != "multi-party-approval":
        _stderr(
            f"Review item '{item.id}' is not a multi-party approval role-task "
            f"(skill_id={item.skill_id!r}). Use `swarmkit review approve|reject|answer`."
        )
        raise typer.Exit(_EXIT_USAGE)

    role = str(item.output.get("role", ""))
    scope = str(item.output.get("scope", ""))
    try:
        workspace = resolve_workspace(root)
    except ResolutionErrors as exc:
        _stderr(f"Workspace did not resolve ({len(exc.errors)} error(s)); cannot check roles.")
        raise typer.Exit(_EXIT_RESOLUTION_ERROR) from exc
    error = membership_error(workspace.role_registry, role=role, scope=scope, identity=identity)
    if error is not None:
        _stderr(f"{identity} may not resolve {item.id}: {error}")
        raise typer.Exit(_EXIT_USAGE)

    if changes_requested:
        status, verb, mark = "changes-requested", "Requested changes on", "↻"
    elif approve:
        status, verb, mark = "approved", "Approved", "✓"
    else:
        status, verb, mark = "rejected", "Rejected", "✗"
    if changes_requested and not comment:
        _stderr("note: --changes-requested without --comment leaves the agent nothing to act on.")
    queue.record_resolution(item.id, status, identity, comment=comment)  # type: ignore[arg-type]
    typer.echo(f"{mark} {verb} {item.id} as {identity} (role={role}, scope={scope})")
    if comment:
        typer.echo(f'  comment: "{comment}"')


@review_app.command("answer")
def review_answer(
    item_id: str,
    answer: Annotated[str, typer.Argument(help="The answer text (or an option shown by `show`).")],
    workspace_path: Annotated[
        Path, typer.Argument(help="Workspace root.", show_default=False)
    ] = Path("."),
) -> None:
    """Answer a harness input request (§6.3) with text. Inspect it first with `review show <id>`.

    An answer that is a bare integer selects that option index from the request; otherwise the text
    is used verbatim (when the request allows free text)."""
    queue = FileReviewQueue(workspace_path.resolve())
    for item in queue.list_all():
        if not item.id.startswith(item_id):
            continue
        resolved = answer
        options = item.output.get("options") or []
        if answer.isdigit() and 0 <= int(answer) < len(options):
            resolved = str(options[int(answer)])
        queue.answer_input(item.id, resolved)
        typer.echo(f"✓ Answered {item.id[:8]}: {resolved!r}")
        return
    _stderr(f"Review item '{item_id}' not found.")
    raise typer.Exit(1)


@app.command()
def gaps(
    workspace_path: Annotated[
        Path, typer.Argument(help="Workspace root.", show_default=False)
    ] = Path("."),
    inert: Annotated[
        bool,
        typer.Option(
            "--inert",
            help=(
                "Instead of skill gaps: decision-skill bindings that were wired and have never "
                "once fired, against a denominator of applicable runs."
            ),
        ),
    ] = False,
) -> None:
    """List recorded skill gaps."""
    if inert:
        _echo_inert(workspace_path.resolve())
        return
    log = SkillGapLog(workspace_path.resolve())
    gap_list = log.list_gaps()
    if not gap_list:
        typer.echo("No skill gaps recorded.")
        return
    for gap in gap_list:
        typer.echo(
            f"  {gap.skill_id:<24} {gap.pattern:<40} ({gap.occurrences}x) → {gap.suggested_action}"
        )


def _echo_inert(workspace_root: Path) -> None:
    """`swarmkit gaps --inert` — wired is not fired (design/details/declared-but-unreachable.md).

    A `required: true` binding at 0/N is the loudest line this system can print, and it is the line
    bug 23 would have produced months before anyone noticed.
    """
    import asyncio  # noqa: PLC0415

    from swarmkit_runtime._workspace_runtime import WorkspaceRuntime  # noqa: PLC0415

    runtime = WorkspaceRuntime.from_workspace_path(workspace_root)
    rows = asyncio.run(runtime.inert_bindings())
    if not rows:
        typer.echo(
            "No inert bindings: every declared decision skill has fired at least once, or has "
            "had no applicable runs yet."
        )
        return
    typer.echo("Wired, and never fired:\n")
    for row in rows:
        typer.echo(f"  {row.line()}")


# ---- authoring -----------------------------------------------------------


@app.command()
def init(
    path: Annotated[
        Path, typer.Argument(help="Directory to create the workspace in.", show_default=False)
    ] = Path("."),
) -> None:
    """Create a new SwarmKit workspace through conversation."""
    _print_banner()
    _suppress_noisy_logs()
    _scaffold_env_file(path.resolve())
    provider, model = resolve_authoring_provider()
    run_authoring_session(
        mode="init", model_provider=provider, model_name=model, workspace_path=path.resolve()
    )


#: Written by `swarmkit init` when absent. Deterministic scaffolding, not authored by the model:
#: a workspace that has no place to put a connection string ends up with one hardcoded in
#: workspace.yaml, and a secret with no `secrets:` entry ends up on the System page.
_ENV_FILE_TEMPLATE = """# Workspace parameters — the values that differ between machines.
#
# Referenced from any artifact as ${dotted.path}, and ${ENV_VAR} here is resolved from the
# environment, so a secret can live in the environment while its NAME lives in version control.
#
# Per-environment overrides: workspace.env.<name>.yaml, selected with SWARMKIT_ENV=<name>.
#
# `secrets:` lists the paths whose values must never be displayed. `swarmkit system` and the
# web UI's System page show every other property resolved; these show only "set". Declare them —
# the fallback is a guess at the name, and it does not catch things like `db.dsn`.

secrets: []
#   - db.dsn
#   - openai.api_key

# db:
#   dsn: ${SWARMKIT_STORE_URL}
#   pool: 5
"""


def _scaffold_env_file(workspace_path: Path) -> None:
    """Create a starter workspace.env.yaml if the workspace has none. Never overwrites."""
    target = workspace_path / "workspace.env.yaml"
    if target.exists():
        return
    workspace_path.mkdir(parents=True, exist_ok=True)
    target.write_text(_ENV_FILE_TEMPLATE, encoding="utf-8")
    typer.echo(f"created {target} — put machine-specific values there, not in workspace.yaml")


def _run_authoring(
    mode: AuthoringMode,
    workspace_path: Path,
    thorough: bool,
    input_text: str = "",
) -> None:
    """Route authoring to single-agent (quick) or swarm (thorough)."""
    _print_banner()
    _suppress_noisy_logs()
    if thorough:
        try:
            runtime = WorkspaceRuntime.from_workspace_path(workspace_path)
            prompt = f"Create a new {mode}. {input_text}".strip()
            result = asyncio.run(runtime.run("skill-authoring", prompt))
            if result.output:
                typer.echo(result.output)
        except KeyError:
            _stderr(
                "error: --thorough requires the skill-authoring topology in the workspace. "
                "Add it from reference/topologies/skill-authoring.yaml."
            )
            raise typer.Exit(_EXIT_USAGE) from None
        except Exception as exc:
            _stderr(f"error: authoring failed: {exc}")
            raise typer.Exit(_EXIT_RESOLUTION_ERROR) from exc
    else:
        provider, model = resolve_authoring_provider()
        run_authoring_session(
            mode=mode,
            model_provider=provider,
            model_name=model,
            workspace_path=workspace_path.resolve(),
        )


@author_app.command("topology")
def author_topology(
    workspace_path: Annotated[
        Path, typer.Argument(help="Workspace directory.", show_default=False)
    ] = Path("."),
    thorough: Annotated[
        bool,
        typer.Option(
            "--thorough", help="Use the multi-agent authoring swarm instead of single agent."
        ),
    ] = False,
) -> None:
    """Author a new topology through conversation."""
    _run_authoring("topology", workspace_path, thorough)


@author_app.command("skill")
def author_skill(
    workspace_path: Annotated[
        Path, typer.Argument(help="Workspace directory.", show_default=False)
    ] = Path("."),
    thorough: Annotated[
        bool,
        typer.Option(
            "--thorough", help="Use the multi-agent authoring swarm instead of single agent."
        ),
    ] = False,
) -> None:
    """Author a new skill through conversation."""
    _run_authoring("skill", workspace_path, thorough)


@author_app.command("archetype")
def author_archetype(
    workspace_path: Annotated[
        Path, typer.Argument(help="Workspace directory.", show_default=False)
    ] = Path("."),
    thorough: Annotated[
        bool,
        typer.Option(
            "--thorough", help="Use the multi-agent authoring swarm instead of single agent."
        ),
    ] = False,
) -> None:
    """Author a new archetype through conversation."""
    _run_authoring("archetype", workspace_path, thorough)


@author_app.command("mcp-server")
def author_mcp_server(
    workspace_path: Annotated[
        Path, typer.Argument(help="Workspace directory.", show_default=False)
    ] = Path("."),
    thorough: Annotated[
        bool,
        typer.Option(
            "--thorough", help="Use the multi-agent authoring swarm instead of single agent."
        ),
    ] = False,
) -> None:
    """Author a new MCP server through conversation."""
    _run_authoring("mcp-server", workspace_path, thorough)


# ---- edit (M7 — Skill Authoring Swarm in edit mode) ----------------------


@app.command()
def edit(
    workspace_path: Annotated[
        Path,
        typer.Argument(help="Workspace to edit.", show_default=False),
    ] = Path("."),
    input_text: Annotated[
        str | None,
        typer.Option(
            "--input",
            "-i",
            help="Describe the change (or omit for interactive conversation).",
        ),
    ] = None,
    color: Annotated[bool | None, typer.Option("--color/--no-color")] = None,
) -> None:
    """Edit an existing workspace through conversation (M7 Skill Authoring Swarm).

    Reads the current workspace state, understands the requested change,
    drafts modifications, validates, and writes. The user never edits
    YAML directly.
    """
    use_colour = should_colour(sys.stdout.isatty(), color)

    try:
        runtime = WorkspaceRuntime.from_workspace_path(workspace_path)
    except ResolutionErrors as exc:
        _emit_errors(
            list(exc.errors),
            json_mode=False,
            workspace_root=workspace_path.resolve(),
            color=use_colour,
        )
        raise typer.Exit(_EXIT_RESOLUTION_ERROR) from exc
    except MissingMCPServerError as exc:
        for skill_id, server_id in exc.missing:
            _stderr(
                f"error: skill '{skill_id}' targets MCP server '{server_id}' "
                f"but the workspace declares no such server."
            )
        raise typer.Exit(_EXIT_RESOLUTION_ERROR) from exc

    user_input = input_text or ""
    if not user_input and not sys.stdin.isatty():
        user_input = sys.stdin.read().strip()
    if not user_input:
        user_input = "What would you like to change in this workspace?"

    try:
        result = asyncio.run(runtime.run("skill-authoring", user_input))
    except KeyError:
        _stderr(
            "error: the skill-authoring topology is not available in this workspace. "
            "Add it from reference/topologies/skill-authoring.yaml, or use "
            "`swarmkit author` for single-agent authoring."
        )
        raise typer.Exit(_EXIT_USAGE) from None
    except Exception as exc:
        _stderr(f"error: edit failed: {exc}")
        raise typer.Exit(_EXIT_RESOLUTION_ERROR) from exc

    if result.output:
        typer.echo(result.output)

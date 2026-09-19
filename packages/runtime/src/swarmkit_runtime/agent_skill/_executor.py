"""Executing an ``agent`` skill: the tool the model calls, and what comes back.

The tool takes ``{input, context?}``. Its result is the other agent's answer as text — or, when
that agent asked a question and the policy is ``agent``, a JSON object ``{"status":
"input_required", "task_id": …, "question": …}`` that the calling agent answers by calling the
same tool again with ``{task_id, answer}``. A human gate on the far side is reported, never
answered: the caller is an agent, and approval is not its to give (§8.7).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from swarmkit_runtime._run_scope import current_run_id
from swarmkit_runtime.executors import ExecInputRequested
from swarmkit_runtime.governance import AuditEvent, GovernanceProvider
from swarmkit_runtime.langgraph_compiler._helpers import _progress
from swarmkit_runtime.prerequisites import Requires
from swarmkit_runtime.skills import ResolvedSkill

from ._context import MAX_DEPTH, AgentSkillContext, ChildRunOutcome, current_agent_context
from ._governed import check_agent_permission
from ._remote import A2AClient, AgentCard, RemoteAgentError, RemoteTask
from ._spec import AgentSkillSpec, parse_agent_spec

#: How a refused call is marked — the same mark the other skill types use, so `is_refusal` and the
#: audit reader treat all four alike.
DENIED_MARK = "] DENIED: "

#: Per-process card cache keyed by URL. Cards change on a deploy, not per call; a stale entry costs
#: one failed call, which refetches.
_CARD_CACHE: dict[str, AgentCard] = {}

#: Questions the calling agent has already answered on a task, per run — the `max_agent_answers`
#: budget. Keyed by (run, task) so one task's clarifications never spend another's allowance.
_ANSWERS: dict[tuple[str, str], int] = {}


def _scopes(skill: ResolvedSkill) -> frozenset[str]:
    iam = getattr(skill.raw, "iam", None)
    if iam and isinstance(iam, dict):
        return frozenset(iam.get("required_scopes", []))
    if iam is not None:
        return frozenset(getattr(iam, "required_scopes", None) or [])
    return frozenset()


def _arguments(input_text: str) -> dict[str, Any]:
    """The tool's arguments: a JSON object, or a bare string as `input`."""
    text = input_text.strip()
    if text.startswith("{"):
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            return parsed
    return {"input": text}


def _input_required(task_id: str, question: str, *, target: str, gate: bool = False) -> str:
    """The tool result that hands a question back to the calling agent."""
    body: dict[str, Any] = {
        "status": "input_required",
        "task_id": task_id,
        "agent": target,
        "question": question,
    }
    if gate:
        body["kind"] = "human_gate"
        body["hint"] = (
            "This is a human approval on the other agent's side. You cannot answer it; a person "
            "resolves it through that agent's review queue. Do not call again with an answer."
        )
    else:
        body["hint"] = "Answer by calling this tool again with {task_id, answer}."
    return json.dumps(body)


async def _audit(
    governance: GovernanceProvider | None,
    event_type: str,
    agent_id: str,
    payload: dict[str, object],
) -> None:
    if governance is None:
        return
    await governance.record_event(
        AuditEvent(
            event_type=event_type,
            agent_id=agent_id,
            timestamp=datetime.now(tz=UTC),
            payload=payload,
        )
    )


async def execute_agent_skill(
    skill: ResolvedSkill,
    *,
    input_text: str,
    governance: GovernanceProvider | None = None,
    agent_id: str = "",
    requires: Requires | None = None,
    context: AgentSkillContext | None = None,
) -> str:
    """Run one call of an ``agent`` skill and return the tool result text."""
    try:
        spec = parse_agent_spec(skill.raw.implementation)
    except ValueError as exc:
        return f"[skill:{skill.id}] {exc}"

    allowed, reason = await check_agent_permission(
        spec,
        governance,
        agent_id=agent_id,
        skill_id=skill.id,
        scopes=_scopes(skill),
        requires=requires,
    )
    if not allowed:
        return f"[skill:{skill.id}]{DENIED_MARK}{reason}"

    ctx = context if context is not None else current_agent_context()
    if ctx is None:
        return (
            f"[skill:{skill.id}] agent skills can only be called from inside a workspace run "
            "(no run context is active)"
        )
    if ctx.depth >= MAX_DEPTH:
        return (
            f"[skill:{skill.id}] refused: this run is already {ctx.depth} agent calls deep "
            f"(limit {MAX_DEPTH}); a topology calling a topology that calls it back is a cycle"
        )

    args = _arguments(input_text)
    if spec.is_local:
        return await _call_local(skill, spec, ctx, args, agent_id=agent_id, governance=governance)
    return await _call_remote(skill, spec, ctx, args, agent_id=agent_id, governance=governance)


# ---- local: a child run ----------------------------------------------------------------------


async def _call_local(  # noqa: PLR0911 — one return per outcome the caller must tell apart
    skill: ResolvedSkill,
    spec: AgentSkillSpec,
    ctx: AgentSkillContext,
    args: dict[str, Any],
    *,
    agent_id: str,
    governance: GovernanceProvider | None,
) -> str:
    topology = spec.topology or ""
    if ctx.run_child is None:
        return f"[skill:{skill.id}] this runtime cannot start a child run here"
    if args.get("task_id") and args.get("answer"):
        # A local child parks only on a human gate; there is nothing an answer could go to.
        return _input_required(str(args["task_id"]), "", target=topology, gate=True)
    user_input = str(args.get("input") or "")
    if args.get("context"):
        user_input = f"{user_input}\n\nContext:\n{json.dumps(args['context'])}"
    if not user_input.strip():
        return f"[skill:{skill.id}] `input` is required"
    _progress(f"  [{agent_id}] → topology {topology} (child run)")
    outcome: ChildRunOutcome = await ctx.run_child(topology, user_input)
    if outcome.gate_id:
        _progress(f"  [{agent_id}] ← {topology} parked on gate {outcome.gate_id}")
        if spec.on_unanswerable == "abort":
            return (
                f"[skill:{skill.id}] the child run {outcome.run_id} parked on human gate "
                f"{outcome.gate_id}; on_unanswerable is `abort`"
            )
        return _input_required(
            outcome.run_id,
            f"child run {outcome.run_id} awaits approval at gate {outcome.gate_id}",
            target=topology,
            gate=True,
        )
    if outcome.error:
        _progress(f"  [{agent_id}] ← {topology} failed")
        return f"[skill:{skill.id}] child run {outcome.run_id} failed: {outcome.error}"
    _progress(f"  [{agent_id}] ← {topology} completed")
    return outcome.output or "(no output)"


# ---- remote: an A2A task ----------------------------------------------------------------------


async def _card(spec: AgentSkillSpec, client: A2AClient) -> AgentCard:
    url = spec.card_url or ""
    card = _CARD_CACHE.get(url)
    if card is None:
        card = await client.fetch_card(url)
        _CARD_CACHE[url] = card
    if spec.skill_id and spec.skill_id not in card.skills:
        _CARD_CACHE.pop(url, None)
        raise RemoteAgentError(
            f"the card at {url} has no skill '{spec.skill_id}' (it lists: {', '.join(card.skills)})"
        )
    return card


async def _bearer(spec: AgentSkillSpec, ctx: AgentSkillContext) -> str | None:
    if not spec.credentials_ref:
        return None
    if ctx.credential_service is None:
        raise RemoteAgentError(
            f"`credentials_ref: {spec.credentials_ref}` but no credential service is available"
        )
    return str(await ctx.credential_service.resolve(spec.credentials_ref))


async def _call_remote(  # noqa: PLR0911, PLR0912 — one branch per policy and task state
    skill: ResolvedSkill,
    spec: AgentSkillSpec,
    ctx: AgentSkillContext,
    args: dict[str, Any],
    *,
    agent_id: str,
    governance: GovernanceProvider | None,
) -> str:
    client = A2AClient(transport=ctx.transport)
    try:
        card = await _card(spec, client)
        client = A2AClient(bearer=await _bearer(spec, ctx), transport=ctx.transport)
    except RemoteAgentError as exc:
        return f"[skill:{skill.id}] {exc}"
    skill_id = spec.skill_id or (card.skills[0] if card.skills else None)
    run_id = current_run_id() or ""
    task_id = str(args.get("task_id") or "")
    answer = str(args.get("answer") or "")

    def _state(task: RemoteTask) -> None:
        _progress(f"  [{agent_id}] ← {card.name}: {task.state}")

    try:
        if task_id and answer:
            key = (run_id, task_id)
            if spec.on_unanswerable != "agent" or _ANSWERS.get(key, 0) >= spec.max_agent_answers:
                return (
                    f"[skill:{skill.id}] this task's questions are not yours to answer "
                    f"(policy {spec.on_unanswerable}, {_ANSWERS.get(key, 0)} answered); "
                    "it has been referred to a person"
                )
            _ANSWERS[key] = _ANSWERS.get(key, 0) + 1
            await _audit(
                governance,
                "executor.input_response",
                agent_id,
                {"task_id": task_id, "answer": answer, "responder": f"agent:{agent_id}"},
            )
            _progress(f"  [{agent_id}] → {card.name}: answer on task {task_id}")
            task = await client.send(
                card.url, answer, skill_id=skill_id, context_id=None, task_id=task_id
            )
        else:
            user_input = str(args.get("input") or "")
            if not user_input.strip():
                return f"[skill:{skill.id}] `input` is required"
            _progress(f"  [{agent_id}] → {card.name} ({card.url})")
            # Forward our remaining budget so a SwarmKit callee caps the child run
            # (a2a-federation.md). None outside a run or on an unbounded run — then nothing is sent.
            from swarmkit_runtime.governance._limits import current_tracker  # noqa: PLC0415

            tracker = current_tracker()
            budget = tracker.remaining_budget() if tracker is not None else {}
            task = await client.send(
                card.url,
                user_input,
                skill_id=skill_id,
                context_id=run_id or None,
                data=args.get("context") if isinstance(args.get("context"), dict) else None,
                budget=budget or None,
            )
        task = await client.wait(card.url, task, timeout_s=spec.timeout_s, on_state=_state)

        # A relayed question is answered by a person and re-sent here; loop until the task ends.
        while task.state == "input-required":
            question = task.message or "(the remote agent asked for input without saying what)"
            gate = task.raw.get("metadata", {}).get("swarmkit", {}).get("gate_url")
            if gate:
                # A SwarmKit run on the far side parked on ITS human gate. Nobody on this side
                # answers that — not the agent, not our review queue.
                await _audit(
                    governance,
                    "executor.input_requested",
                    agent_id,
                    {"task_id": task.id, "question": question, "kind": "remote_gate"},
                )
                if spec.on_unanswerable == "abort":
                    await client.cancel(card.url, task.id)
                    return f"[skill:{skill.id}] {card.name} parked on a human gate: {gate}"
                return _input_required(task.id, question, target=card.name, gate=True)
            if spec.on_unanswerable == "abort":
                await client.cancel(card.url, task.id)
                return f"[skill:{skill.id}] {card.name} asked: {question} (on_unanswerable: abort)"
            key = (run_id, task.id)
            if spec.on_unanswerable == "agent" and _ANSWERS.get(key, 0) < spec.max_agent_answers:
                await _audit(
                    governance,
                    "executor.input_requested",
                    agent_id,
                    {"task_id": task.id, "question": question, "to": f"agent:{agent_id}"},
                )
                return _input_required(task.id, question, target=card.name)
            # relay — or the agent's allowance is spent — a person answers, bounded wait.
            human = await _relay_question(ctx, question, agent_id=agent_id, governance=governance)
            if human is None:
                await client.cancel(card.url, task.id)
                return (
                    f"[skill:{skill.id}] {card.name} asked: {question} — no answer from a person "
                    "in time; the remote task was cancelled"
                )
            task = await client.send(
                card.url, human, skill_id=skill_id, context_id=None, task_id=task.id
            )
            task = await client.wait(card.url, task, timeout_s=spec.timeout_s, on_state=_state)
    except TimeoutError as exc:
        return f"[skill:{skill.id}] {exc}"
    except RemoteAgentError as exc:
        return f"[skill:{skill.id}] {exc}"

    await _record_remote_usage(task, card, skill_id, agent_id=agent_id, governance=governance)
    if task.state == "completed":
        return task.artifact or task.message or "(no output)"
    return f"[skill:{skill.id}] {card.name} task {task.id} ended {task.state}: {task.message}"


async def _record_remote_usage(
    task: RemoteTask,
    card: AgentCard,
    skill_id: str | None,
    *,
    agent_id: str,
    governance: GovernanceProvider | None,
) -> None:
    """Stitch a SwarmKit callee's record into ours (a2a-federation.md).

    A SwarmKit remote returns its run id, token/cost usage and an observability pointer in the
    task's `metadata.swarmkit`. Record it as an `a2a.remote_usage` audit event — attributed to the
    remote, marked as its report — so the caller's own audit links the two runs and carries the
    remote's cost. A non-SwarmKit remote sends none of this and nothing is recorded.
    """
    sk = task.raw.get("metadata", {}).get("swarmkit", {}) if isinstance(task.raw, dict) else {}
    remote_run = sk.get("run_id")
    usage = sk.get("usage") if isinstance(sk.get("usage"), dict) else {}
    if not remote_run and not usage:
        return
    await _audit(
        governance,
        "a2a.remote_usage",
        agent_id,
        {
            "skill_id": skill_id,
            "card": card.name,
            "endpoint": card.url,
            "remote_run_id": remote_run,
            "input_tokens": int(usage.get("input_tokens", 0) or 0),
            "output_tokens": int(usage.get("output_tokens", 0) or 0),
            "cost_usd": float(usage.get("cost_usd", 0.0) or 0.0),
            "observability": sk.get("observability") or {},
            "source": "reported",
        },
    )


async def _relay_question(
    ctx: AgentSkillContext,
    question: str,
    *,
    agent_id: str,
    governance: GovernanceProvider | None,
) -> str | None:
    """A person answers, through the same inbox a harness question uses (`resolve_input`)."""
    if ctx.review_queue is None or governance is None:
        return None
    from swarmkit_runtime.langgraph_compiler._relay import resolve_input  # noqa: PLC0415

    return await resolve_input(
        ExecInputRequested(question=question, options=(), free_text_allowed=True),
        agent_id=agent_id,
        topology_id=ctx.topology_id,
        governance=governance,
        review_queue=ctx.review_queue,
        max_wait_seconds=ctx.relay_wait_s,
    )

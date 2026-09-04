---
title: Channel skills — letting a swarm talk to a human where they are
description: Agent-initiated messaging and inbound replies over Telegram/Discord/Slack, as skills over a first-party MCP server. Includes the finding that the existing notification providers are wired to nothing.
status: draft
---

# Channel skills

## The finding this starts from

SwarmKit already ships channel transports. `packages/runtime/src/swarmkit_runtime/notifications/`
is 548 lines covering Telegram (Bot API), Discord (webhook + embeds), Slack (incoming webhook),
a generic webhook, and a terminal provider. There are 31 unit tests. The module docstring says it
fires on `hitl_requested`, `run_ended_error` and `skill_gap_surfaced`.

None of it runs.

```
constructors called outside the package:      0
notify() call sites outside the package:      0
"notifications" key in the workspace schema:  0
referenced from `swarmkit serve`:             0
```

`build_provider` is never called, `NotificationRegistry` is never constructed, and there is no way
to express `notifications:` in a workspace, because the key does not exist in the schema. The
providers are complete, tested, documented — and unreachable. This is the
[declared-but-unreachable](declared-but-unreachable.md) family, in its purest form: the unit tests
prove the provider *works* and nothing proves it *runs*, which is exactly how it stayed green.

Worth noting that `WorkspaceRuntime.reachability()` could not have caught this. It compares
*declared* configuration against what compilation wires, and notifications are not declarable — so
there was no declaration to find unreached. A capability that cannot be configured at all is
invisible to a check that starts from configuration.

## Goal

Make a swarm able to reach a human on the channel they already use, and — the part that does not
exist in any form today — **hear the answer back**.

## Non-goals

- **Not a chat framework.** No sessions, no conversation memory, no channel-native command parsing.
  A swarm sends and reads; it does not host a bot experience.
- **Not a replacement for the portal.** Approvals stay where identity is established. See below.
- **No account-impersonating transports.** Telegram's MTProto servers act as *you* and risk the
  account; the Bot API is the sanctioned path and the only one used here.

## The three directions

| direction | today | this note |
| --- | --- | --- |
| runtime event → channel (outbound, runtime-triggered) | code exists, wired to nothing | wire it (PR 2) |
| agent decides → channel (outbound, agent-triggered) | nothing | **skills** (PR 1) |
| channel → agent (inbound, a human replies) | nothing | **skills** (PR 1, Telegram only) |

The second and third are what turn a notification into a conversation. Today the channel is a
doorbell: it can tell you a gate is waiting, and there is no path back.

## Shape: an MCP server, because skills are the only capability primitive

The capability reaches agents as skills (invariant #2), so it needs a skill backing. `mcp_tool` is
the right one, served by a first-party stdio MCP server — the same pattern as
`swarmkit_runtime.knowledge` and `swarmkit_runtime.gate_validator`, both already launched this way
from `reference/workspace.yaml`:

```yaml
mcp_servers:
  - id: channels
    transport: stdio
    command: ["uv", "run", "python", "-m", "swarmkit_runtime.channels"]
    permission: cautious
    effects:
      channel_send: write
      channel_ask: write
      channel_replies: read
```

**No new transport code.** The server calls the existing notification providers for sending. Writing
a second Telegram client next to the one already in the tree would be the actual mistake here — the
providers are not the problem, their reachability is.

Rejected alternatives:

- **A community Telegram/Discord MCP server from the catalogue.** We would be adopting a third
  party's HTTP call to replace one we already ship and test, and the popular Telegram servers split
  between Bot API and MTProto — the latter logs in as a real person and can get the account banned.
  Not a dependency worth taking for `POST /sendMessage`.
- **A command pack.** There is no `telegram` binary; the pack would wrap `curl`, which puts a bot
  token on an argv line.
- **An executor.** Sending a message answers and returns. It holds no session and produces no diff.

## Tools

| tool | effect | what it does |
| --- | --- | --- |
| `channel_send` | write | post a message to a configured channel |
| `channel_ask` | write | post a question and wait for a reply, up to a bounded timeout |
| `channel_replies` | read | messages received since a cursor |

`channel_ask` is the interesting one and the reason the inbound half exists: an agent that can ask
a question and block on the answer is a different thing from one that can only announce.

**Bounds are never infinite.** `timeout_s` is capped (default 300, max 3600) and a timeout is a
normal return value — `{"answered": false}` — not an exception. An agent that cannot reach anyone
should be able to carry on and say so, the same way an unanswered gate does.

## Inbound: Telegram first, and why only Telegram

Telegram's Bot API offers `getUpdates` — long-polling, outbound-only HTTPS, no inbound port, no
public URL. That works behind NAT on a self-hosted box, which is where SwarmKit runs.

Discord and Slack have no equivalent. Discord needs a gateway WebSocket held open; Slack needs
Socket Mode or a public request URL. Both mean a persistent connection with its own lifecycle inside
a runtime that is otherwise request-shaped. They are deferred, and `channel_replies` on those
channels returns an explicit `unsupported` rather than an empty list — an empty list would read as
"nobody answered", which is a different and much worse claim.

`getUpdates` carries one sharp edge: it is a **single-consumer** API. Two pollers on one bot token
steal each other's updates, and the loser silently sees nothing. So the server holds the offset in
the workspace store and refuses to start a second poller for a token already being polled, with an
error that says which process holds it.

## The decision that constrains everything: a chat reply cannot resolve a gate

The obvious next feature is approving from your phone. It is not in this note, deliberately.

Invariant #6 reserves `approvals:resolve` for human identity, enforced structurally. A Telegram
`chat_id` is not an identity assertion — it says a message arrived from a chat, not that a
particular person authenticated. Anyone with access to that chat, on any device, logged in as
anyone, would be exercising a scope no agent may hold.

So `channel_ask` returns **information** to the agent, and the agent remains subject to every gate
it was already subject to. A human who wants to approve still does it where identity is
established: the portal or the CLI. The notification tells them a gate is waiting; the reply is not
the approval.

Making chat approval safe needs an identity binding — a signed link that carries a session, or an
enrolment step tying a chat id to a workspace principal. That is a separate design, and it should
not arrive by accident on the back of a messaging skill.

## Config

A `channels:` block in the workspace, resolved through the credentials store like every other
secret:

```yaml
channels:
  ops-telegram:
    provider: telegram
    credentials_ref: telegram-bot-token
    config: { chat_id: "-1001234567890" }
  eng-discord:
    provider: discord
    credentials_ref: discord-webhook
```

Named channels rather than one global destination, so a topology can route an infrastructure
question to ops and a release question to engineering without a second workspace.

This is a schema change, so it follows `docs/notes/schema-change-discipline.md`: canonical schema,
bundled `_schemas/` sync, fixtures, both codegens.

## PR 2 — wire the orphan

Separate PR, because it is a separate defect. `notifications:` gains a schema key, `swarmkit serve`
constructs the registry from it, and the three events the docstring already claims actually fire.
It has waited long enough that shipping the skills first is the right order: the skills are the
requested feature, and the orphan is a bug found on the way.

## Test plan

- **Unit.** Each tool against a mocked provider: send, ask-answered, ask-timed-out, replies since a
  cursor, `unsupported` on a channel with no inbound.
- **The regression that matters.** A test asserting the workspace `channels:` block reaches a
  constructed provider — the check whose absence is the whole first section of this note. It must
  fail if the wiring is removed.
- **Single-consumer.** Two pollers on one token: the second refuses, naming the holder.
- **Governance.** Under `permission: readonly`, `channel_send` and `channel_ask` are denied and
  `channel_replies` is allowed, through the real gate.
- **Bounds.** `timeout_s` above the cap is clamped, not honoured.

## Demo plan

`just demo-channels` — a topology whose worker asks a question on Telegram and blocks until a human
answers, with the transcript in the PR body. Falls back to the terminal provider when no token is
configured, so the demo runs for someone with no bot.

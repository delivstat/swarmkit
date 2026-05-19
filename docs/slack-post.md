---
title: LangChain Slack post
description: Short, conversational, focused on LangGraph usage
channel: "#show-your-work" or "#general"
---

# Slack Post

Hey all — I built an open-source tool that sits on top of LangGraph
and lets you define multi-agent topologies in YAML instead of Python.

The short version: instead of writing `StateGraph`, `add_node`,
`add_conditional_edges` etc. for every agent setup, you write this:

```yaml
agents:
  root:
    role: root
    model: { provider: openrouter, name: meta-llama/llama-3.3-70b-instruct }
    children:
      - id: researcher
        role: worker
        archetype: domain-researcher
      - id: analyst
        role: worker
        archetype: code-analyst
```

The runtime compiles it into a `StateGraph` with nodes, edges, and
routing. Each agent gets its own model, tools, and system prompt
from the archetype YAML. Delegation happens through synthetic
`delegate_to_<child>` tool calls that the runtime intercepts.

I've been running it on a real enterprise project with 9 MCP tool
servers, 21 skills, and 5 topologies. Some things I learned that
might be useful for anyone building with LangGraph:

**1. Multi-turn tool loops matter a lot.** After an agent calls tools,
you need a follow-up LLM call where it sees the results and
synthesises. Without this, agents return raw ChromaDB scores and
grep output as "answers." We do up to 8 rounds per turn.

**2. Per-agent model selection saves real money.** The router uses
llama-3.3 ($0.10/M), workers use deepseek ($0.32/M). 507 requests,
$0.33/day. The router doesn't need to be smart — just fast and cheap.

**3. Tool names override system prompts.** If a tool name matches
what the user asked, the model calls it regardless of what you wrote
in the prompt. Remove the tool, don't try to prompt around it.

**4. Models say "let me check" and then stop.** Detection for
planning-language-without-action ("let me examine...") + a nudge
back eliminated a whole class of lazy non-answers.

It's called SwarmKit: https://github.com/delivstat/swarmkit

pip/uv installable:
```
uv tool install swarmkit-runtime
swarmkit init my-swarm/
```

Happy to answer questions about the LangGraph compilation or any
of the patterns above. The design doc in the repo goes deep on the
architecture.

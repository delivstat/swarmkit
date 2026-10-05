# swarmkit author bench — minimum-signal results

## Headline

- cases: **10**
- generated (exit 0 + >= min files): **9**
- validates: **10**
- passed (both): **9**

## By mode

| mode | cases | passed |
|---|---:|---:|
| archetype | 2 | 2 |
| init | 1 | 0 |
| mcp-server | 2 | 2 |
| skill | 2 | 2 |
| topology | 3 | 3 |

## Per-prompt

| mode | id | pass | exit | files | wall s | validate |
|---|---|---|---:|---:|---:|---|
| archetype | curator-worker | ✅ | 0 | 1 | 47.2 | yes |
| archetype | decision-role | ✅ | 0 | 2 | 64.8 | yes |
| init | cold-start | ❌ | 2 | 0 | 2.8 | yes |
| mcp-server | http-remote | ✅ | 0 | 0 | 36.1 | yes |
| mcp-server | stdio-filesystem | ✅ | 0 | 5 | 62.8 | yes |
| skill | llm-prompt-skill | ✅ | 0 | 1 | 36.3 | yes |
| skill | mcp-tool-wrap | ✅ | 0 | 1 | 59.0 | yes |
| topology | cold-brief | ✅ | 0 | 4 | 118.5 | yes |
| topology | triage-with-funnel | ✅ | 0 | 5 | 88.7 | yes |
| topology | uses-existing-skill | ✅ | 0 | 2 | 41.9 | yes |

## Failure digest

### init/cold-start
- reason: exit 2, no files created
- transcript tail:

      Usage: swarmkit author [OPTIONS] COMMAND [ARGS]...
      Try 'swarmkit author --help' for help.
      ╭─ Error ──────────────────────────────────────────────────────────────────────╮
      │ No such command 'init'.                                                      │
      ╰──────────────────────────────────────────────────────────────────────────────╯


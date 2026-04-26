# Skills catalogue

20 reference skills ship with SwarmKit.

## Capability skills

| Skill | MCP server | Tool |
|---|---|---|
| github-repo-read | github | get_file_contents |
| github-pr-read | github | get_pull_request |
| github-issue-read | github | get_issue |
| query-swarmkit-docs | swarmkit-knowledge | search_docs |
| list-reference-skills | swarmkit-knowledge | list_reference_skills |
| get-schema | swarmkit-knowledge | get_schema |
| validate-workspace | swarmkit-knowledge | validate_workspace |
| read-workspace-file | swarmkit-knowledge | read_workspace_file |
| write-workspace-file | swarmkit-knowledge | write_workspace_file |
| run-tests | swarmkit-knowledge | run_pytest |
| search-codebase | (template) | — |
| summarize-review | (llm_prompt) | — |

## Decision skills

| Skill | Outputs |
|---|---|
| code-quality-review | verdict, confidence, reasoning, issues |
| security-scan | verdict, confidence, reasoning, findings |
| test-coverage-review | verdict, confidence, reasoning, gaps |
| qa-verdict | verdict, confidence, reasoning |
| deploy-risk-review | verdict, confidence, reasoning, risks |
| lint-check | verdict, confidence, reasoning, violations |

## Coordination skills

| Skill | Description |
|---|---|
| peer-handoff | A2A context packaging for leader-to-leader handoff |

## Persistence skills

| Skill | Description |
|---|---|
| audit-log-write | Structured event to governance audit log |

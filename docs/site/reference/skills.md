# Skills catalogue

20 reference skills ship with Swael.

## Capability skills

| Skill | MCP server | Tool |
|---|---|---|
| github-repo-read | github | get_file_contents |
| github-pr-read | github | get_pull_request |
| github-issue-read | github | get_issue |
| query-swael-docs | swael-knowledge | search_docs |
| list-reference-skills | swael-knowledge | list_reference_skills |
| get-schema | swael-knowledge | get_schema |
| validate-workspace | swael-knowledge | validate_workspace |
| read-workspace-file | swael-knowledge | read_workspace_file |
| write-workspace-file | swael-knowledge | write_workspace_file |
| run-tests | swael-knowledge | run_pytest |
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

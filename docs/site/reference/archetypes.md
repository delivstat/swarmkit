# Archetypes catalogue

16 reference archetypes ship with Swael.

## Leaders

| Archetype | Role | Used in |
|---|---|---|
| supervisor-leader | root | Code Review Swarm |
| engineering-leader | leader | Code Review Swarm |
| qa-leader | leader | Code Review Swarm |
| ops-leader | leader | Code Review Swarm |
| authoring-supervisor | root | Skill Authoring Swarm |
| conversation-leader | leader | Skill Authoring Swarm |

## Code review workers

| Archetype | Skills |
|---|---|
| github-reader | github-repo-read, github-pr-read |
| code-analyst | code-quality-review, query-swael-docs |
| security-reviewer | security-scan, query-swael-docs |
| test-analyst | test-coverage-review |
| llm-judge | qa-verdict (or deploy-risk-review via override) |

## Authoring workers

| Archetype | Skills |
|---|---|
| knowledge-searcher | query-swael-docs, list-reference-skills, validate-workspace |
| schema-drafter | get-schema, query-swael-docs, read-workspace-file |
| artifact-validator | validate-workspace, query-swael-docs |
| test-writer | get-schema, write-workspace-file, run-tests |
| artifact-publisher | write-workspace-file |

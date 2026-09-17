# Capstone — every tutorial level in one run

The workspace and demo the guided tutorials end on (`docs/site/tutorials/`, levels 1–22). It is the
[showcase](../showcase) plus everything that came after it: an attachment beside the input, a command
pack and an `agent` skill on the workspace, `pack:workspace`, this instance as an A2A agent, the
operator's reads, and a cooperative stop.

```bash
just demo-capstone
```

No API keys and no network — the mock provider answers deterministically. Every claim is made through
the public HTTP API.

| step | level | what it proves |
|---|---|---|
| 1 | 21 | `/capabilities`, `/system`, `/storage` answer from the resolved config |
| 2 | 19, 20 | a command-pack command and every topology are skills nobody wrote |
| 3 | 18 | `/workspace/reachability`: nothing declared is unwired |
| 4 | 19 | a run with an attachment; a bad path is a 422 on the request |
| 5 | 18 | the run parks on the human gate; `funnel.gate_opened`, `GET /gates/{id}` |
| 6 | 20 | the same run over A2A is `input-required` and a follow-up is refused |
| 7 | 18 | a person resolves it; the run resumes on its own |
| 8 | 21 | `run.attachments` by digest; runs grouped by `correlation_id` |
| 9 | 20 | another agent's `message/send` becomes a job with `source: a2a` |
| 10 | 18 | `POST /jobs/{id}/stop` — the next agent boundary, never a kill |

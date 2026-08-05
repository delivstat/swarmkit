# The audit log shows what an event was for

**Status:** implemented (runtime 1.153.0, UI 0.32.0)

## Goal

Make `/audit` return the event the store already holds, and make the audit page show it.

## Non-goals

- No new fields, no schema change, no migration. Every field already exists and is already written.
- No write affordance. The media pillar stays read-only (design §8.3) — this is a read surface and
  the page adds a disclosure, nothing else.
- No redaction change. `redact_json_pointers` already governs what reaches `inputs`/`outputs` at
  write time; surfacing does not widen it.

## The gap

`AuditEvent` was expanded in M6 to carry structured observability: policy decision and reason, skill
category, inputs, outputs, verdict, reasoning, confidence, model provider and name, tokens, cost,
duration, error, parent event. The store persists all of it — 25 columns — and reads all of it back.

`_audit_event_to_dict` serialized nine keys: the event's header. So:

| | count |
| --- | --- |
| columns the store persists | 25 |
| keys `GET /audit` returned | 9 |

The audit page was therefore not hiding detail — it was never sent any. A reader could see *that*
`skill.executed` happened and never what the skill was asked or what it answered, which is most of
why anyone opens the log. Every governance decision rendered as a blank, so "allowed" and "never
evaluated" looked identical.

## Design

**Serialize everything, explicitly.** `_AUDIT_DETAIL_FIELDS` lists the non-header fields in reading
order; the serializer walks it and converts UUIDs to strings (a `parent_event_id` would otherwise
500 the whole request, so one linked event would break the entire log). A test states the property
against the table definition — anything the store has a column for must reach the client — so a
future column that is persisted but never surfaced fails there rather than going unnoticed.

**Absent is null, never missing.** A dropped key makes the client guess, and the guess it would make
about a missing `policy_decision` — that the call was allowed — is the wrong one in a governance
record. The UI renders null as `-`, distinct from an `allow` badge.

**The row summarises; the disclosure explains.** `summarize()` picks the one line that distinguishes
a row from its neighbours, in order: a decision skill's verdict and confidence, a policy denial, an
error, the first input argument, the skill id. Expanding shows Policy, Inputs, Outputs, Reasoning,
Error and Payload — omitting any section that holds nothing, so a header-only event offers no
disclosure that opens onto emptiness.

## Surface

- `GET /audit` — 25 keys instead of 9. Additive; no key renamed, so existing readers are unaffected.
- `/audit` — new **Skill**, **Detail** and **Policy** columns; a row expands to the full event.

## Test plan

`test_audit_api_returns_detail.py` — each group of fields round-trips, absent is null not missing,
a UUID serializes, the header is unchanged, and the every-persisted-column property.
`lib/audit.test.ts` — summary precedence, section order and omission, cost/duration formatting
(including that `0.0004` does not round to `$0.00` and that unrecorded ≠ zero), truncation.

## Demo

`packages/runtime/demos/audit_detail.py` — prints one event as the API used to serialize it and as
it does now, with the column counts.

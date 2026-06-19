# Scenario Studio — NL Authoring of Count Rules (Design Note)

**Scope:** `examples/minder` — author a count scenario from plain language
("alert if more than 3 cars in the driveway") instead of the manual count builder.
Builds on the count condition ([[scenario-studio-phase1-count]]) + zones. **Status:**
implemented.

## Goal

The count condition shipped with a manual builder (object + operator + value). This
adds the conversational path: type/say "alert me if there are more than 3 cars on
Porch-1" (dashboard "Describe a scenario" box, or chat) and Minder persists the count
rule. Matches the authoring-first model — the user describes, code does the doing.

## Why deterministic parsing (not the router)

The 3B router doesn't reliably emit count fields (op/value/object) — it often mis-tags
count phrasing as a query or a plain presence scenario (the same wobble the Phase-1
`_correct_plan` guards). But the phrasing is **regular** ("more than N <object>"), so
a small deterministic parser is more reliable than the model here. (LLM does language;
code does the doing.)

## How it works

`author_scenario` (the convergence point for the dashboard box *and* chat) checks
`_parse_count_scenario(text)` first; `create_scenario` also checks it before the
router's dispatch-kind gate, so a count scenario is authored even when the router
mis-classifies it.

- **`_parse_count_scenario`** — operator phrase → op (`more than`/`over`→`>`,
  `at least`→`>=`, `fewer than`/`under`→`<`, `no more than`/`at most`/`up to`→`<=`,
  `exactly`→`==`); object (car/person/dog/cat + synonyms); number (digits or words
  one–ten). Returns `None` if any piece is missing (falls through to normal authoring).
- **`_persist_count_rule`** — builds the count rule, resolving the camera from the
  router's extraction, falling back to **`_match_camera_in_text`** (a camera named in
  the text — the 3B frequently drops it), and attaching a **zone** if one of the
  camera's zone names appears in the text. Dedups against existing count rules.

The rule it writes is exactly the Phase-1 count shape (`condition_type:"count"`, …),
enforced by the same deterministic matcher — this is purely an authoring path.

## Test plan

`webapp/test_nl_count.py` (standalone): operator/object/number parsing (digit + word),
non-count rejection, `_persist_count_rule` round-trip + dedup, zone attachment, the
text camera-match, and camera scoping when the router misses it.

## Demo (verified live)

Posted "alert me if there are more than 3 cars on Porch-1" to `/api/ops/scenario` →
a count rule was created: `condition_type:count, count_object:car, count_op:">",
count_value:3, cameras:["Porch-1"]` (camera recovered from the text).

## Follow-ups

NL authoring of `cross` ("alert when a car enters the driveway") and of explicit
zones; richer number/operator phrasings.

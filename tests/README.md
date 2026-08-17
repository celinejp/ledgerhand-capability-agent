Test suite (pytest), covering only pure-logic functions that have no browser or LLM API
dependency:

- `test_artifact_schema.py` — the artifact JSON round trip (`asdict()` → `json.dumps()` →
  `compiler.load_artifact()`), including a `Locator` with a fallback, a checkpoint, and an
  extraction, plus `InputParam`'s `values`/`min` fields (both set and unset).
- `test_guardrails.py` — `check_allowlist()` (allowed case, disallowed domain/route/action),
  `check_risk_policy()` (`"safe"` always proceeds, each `irreversible_handling` value maps to
  the right decision, an unrecognized value fails safe), and `redact()` (flat/nested/list-of-dict
  redaction, case-insensitive matching, unrelated keys left alone).

Run with `pytest tests/` from the repo root (with the venv active).

**Not covered here, on purpose:** `discovery_loop.py`, `replay_engine.py`, `escalation.py`,
`operator_console/app.py`, and `mock_app/app.py`. All of these need a live browser session
and/or a live Anthropic API call to mean anything — a unit test with everything mocked out would
verify the mocks, not the system. They're verified instead via real execution against the real
mock app, with the transcripts, screenshots, and results kept as evidence under `evidence/` and
narrated in `REPORT.md` — see the Escalation & handoff and Safety sections in particular for
runs that exercise these modules end to end for real.

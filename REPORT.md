# Report

_Design decisions, trade-offs, and evaluation notes go here._

## Architecture

Chose Claude (Anthropic API, model `claude-sonnet-4-6`) for the discovery loop's tool-calling
support and reliable structured-output behavior. The LLM provider is fully decoupled from the
rest of the system (`SurfaceAdapter`, artifact schema, replay engine) so it could be swapped
without touching anything downstream.

## Artifact schema

`InputParam` gained optional `values` and `min` fields during the human review pass, to express
enum constraints (`account_type`) and numeric minimums (`opening_deposit`) that weren't
anticipated in the original schema draft. Existing fields and previously-compiled artifacts are
unaffected — this is exactly the kind of controlled evolution the schema's `version` field
exists to support.

## Determinism & error handling

**Accessibility API migration:** Playwright's older `page.accessibility.snapshot()` API is
removed in the installed version (1.62+). `SurfaceAdapter` uses `Locator.aria_snapshot()`
instead — the current supported replacement, and browser-computed rather than heuristic — with
a parser flattening its tree into the flat `{role, name, value}` list the discovery loop
consumes. One concrete finding: `<input type="number">` maps to ARIA role `spinbutton`, not
`textbox` — relevant when writing locators against numeric fields like the opening-deposit
input.

**Compiler doesn't auto-infer judgment calls:** The compiler intentionally does not auto-infer
inputs, outputs, checkpoints, or business-outcome branches from a raw discovery transcript —
this requires human judgment the compiler can't safely automate, so it outputs a "draft"
artifact with an explicit review checklist. This caught two real issues from the first real
discovery run: (a) a duplicate `type_text` retry into the Member ID field that a naive
auto-compiler would have silently kept as two steps, and (b) an account-type value
(`"Checking"`) that only worked at discovery time because Playwright's `select_option()` falls
back to label-matching — replay must not depend on that fallback, so the review pass
parameterized it into `{{account_type}}` rather than leaving a brittle literal.

## Heterogeneity & multi-tenant

_TBD._

## Escalation & handoff

_TBD._

## Safety

The discovery loop enforces a same-host check on the resulting URL after every action, not just
explicit `navigate` calls, since a click could in principle follow a link off-host. This is a
minimal safety net specific to the discovery phase; the full allowlist/risk-policy model
(domain/route/action-level enforcement, irreversible-action handling) lives in `guardrails.py`,
described in the rest of this section.

The initial `guardrails_config.json` allowlist route `/members/*` excluded the bare `/members`
path, which a real discovery run legitimately tried to reach while searching for account-level
operations. This was a genuine allowlist gap, not a demo workaround — fixed by broadening the
route to `['/members', '/members/*']`. This is a small concrete example of the kind of allowlist
drift real usage surfaces, which a production system would want to catch via monitoring
rejected-but-plausible navigation attempts rather than only during manual testing.

## Cuts

Manual locator verification during the artifact review pass reused the same live mock-app
instance as the original discovery run, mutating its in-memory state (creating extra test
accounts on members 1001 and 1004). In a production system, artifact review/testing would run
against an isolated staging instance or a reset-per-verification harness. Noted here as a
deliberate cut given the mock app's simplicity — the mock app is restarted before any replay
demo runs to guarantee clean state.

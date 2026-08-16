# Report

_Design decisions, trade-offs, and evaluation notes go here._

## Architecture

Single-process, synchronous architecture throughout — no queues, no separate services beyond
the mock app and operator console (each a small standalone Flask process). That's appropriate
at this scale and is a deliberate choice, not an oversight: the brief warns against building
premature scaling infrastructure, and a message queue or worker pool would be exactly that here.

The pipeline: a natural-language goal drives the **discovery loop** (LLM-driven, `discovery_loop.py`,
observe/decide/act against a live browser) → a **compiler** turns the resulting transcript into a
draft **artifact JSON** → a **human review** pass promotes it to `review_status: "approved"` → the
**replay engine** (`replay_engine.py`, fully deterministic, no LLM involved at all) executes
approved artifacts against the live app → **guardrails and escalation** wrap both the discovery
loop and the replay engine, not just one of them.

The target app is a self-built mock core-banking Flask app (`mock_app/app.py`) rather than a
public site, chosen for two specific reasons: it gives reliable, reproducible failure injection
(not-found member, validation error, artificial slow load, session timeout via query-param
flags) that a public site can't guarantee on demand, and its markup is deliberately
legacy-flavored — table-based layout, no `id`/`class`/`data-testid` attributes — to actually
exercise the "no clean DOM" problem the brief cares about, rather than automating against a
modern app with convenient test hooks that wouldn't stress the locator strategy at all.

Technology choices: Playwright for browser automation (mature accessibility-tree support via
`aria_snapshot()`); the Anthropic API called directly rather than through a heavier agent
framework, since the tool-calling loop here is simple enough that a framework would add
indirection without adding capability; Flask for both the mock app and the operator console, to
keep the whole stack minimal and dependency-light.

Chose Claude (Anthropic API, model `claude-sonnet-4-6`) for the discovery loop's tool-calling
support and reliable structured-output behavior. The LLM provider is fully decoupled from the
rest of the system (`SurfaceAdapter`, artifact schema, replay engine) so it could be swapped
without touching anything downstream.

## Artifact schema

The schema's core shape (`artifact_schema.py`): an ordered list of `ArtifactStep`s, each with an
`action`, a `locator`, an optional `value`, an optional `checkpoint` that confirms the step
actually landed, a `risk_class` (`"safe"` | `"irreversible"`), and optional `extractions` for
steps that read data out of the page. Wrapping the steps: typed `inputs`/`outputs` at the
artifact level (not just embedded per-step), an `app_target` naming the vendor app and entry
URL, `recorded_from` provenance (which discovery run produced this artifact, when, which model),
a `review_status` (`"draft"` | `"approved"`), and `safety` metadata.

The artifact is deliberately its own typed representation, decoupled from the raw discovery
transcript rather than being the transcript with some fields renamed. The transcript is
exploratory by nature — it can contain retries, dead ends, and locator choices that only worked
by coincidence at discovery time — while the artifact is meant to be the reviewed, reusable
capability that survives that process. `version` exists on the artifact specifically so the
schema itself can evolve (as it already has once, see below) without invalidating
already-compiled, already-reviewed artifacts. And the schema is designed to be read by two very
different consumers — a human reviewer deciding whether to approve it, and a calling agent
deciding whether and how to invoke it — which is why it's a typed contract (explicit input
types, enum `values`, numeric `min`s, named `outputs`) rather than just a step list a human
could follow but a caller would have to guess the interface of; that dual-readability is an
explicit requirement in the brief, not an incidental nicety.

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

This section is design-only, per the brief's "design, not necessarily build" scope for this
part — none of it is implemented, and it deliberately isn't: building multi-tenant/drift
infrastructure now would be premature scaling work ahead of a second real surface or tenant
ever existing.

**Surface abstraction.** `SurfaceAdapter`'s `observe`/`click`/`type_text`/`select_option`/
`navigate`/`read_text`/`wait_for` interface is the seam, and it's already proven to generalize
within this one project: the discovery loop and replay engine never touch Playwright directly —
only `SurfaceAdapter` does (`replay_engine.py` used to call `adapter.page.inner_text("body")`,
`adapter.page.url`, and `adapter.page.screenshot()` inline; that was refactored specifically so
replay stays fully decoupled from Playwright specifics, see `get_page_text()`/`current_url()`/
`screenshot()` on `SurfaceAdapter`). Locators are resolved role/label-first, with CSS as a
fallback of last resort (`LocatorStrategy = Literal["role", "label", "text", "css"]` in
`surface_adapter.py`) — that ordering is exactly the strategy that would carry over to:
- **A legacy web app with worse markup:** same interface, same role/label-first locator
  strategy, just messier concrete selectors discovered per-app during that app's own discovery
  runs.
- **A desktop app:** same interface, but backed by OS accessibility APIs (UIAutomation on
  Windows, AT-SPI on Linux) instead of Playwright. `Locator.strategy="role"` maps directly onto
  OS accessibility roles, which is precisely why role-based locators were chosen over raw
  CSS/DOM-position locators in the first place — CSS has no desktop equivalent, accessibility
  roles do.

**What would actually change for a new surface:** only `SurfaceAdapter`'s internals — a new
adapter class implementing the same method signatures. The artifact schema, compiler, replay
engine, guardrails, and escalation mechanism would all work unmodified, since none of them know
anything about Playwright specifically. That's a deliberate design constraint enforced during
review, not an accident (see the `replay_engine.py` refactor referenced above).

**Multi-tenant reuse (proposed, not built):** structure artifacts as a "base" artifact capturing
the vendor-app-level flow (e.g. this repo's `cap_open_subaccount_v1` artifact represents the
underlying vendor product's flow) plus small per-tenant "override" documents layered on top at
replay time — different `entry_url`, different button/label text if a tenant white-labels their
instance — rather than duplicating the whole artifact per tenant. `strategy="role"` with
name-based matching is what makes this remotely viable: accessible role stays stable across
tenant branding even when visible text changes, unlike CSS selectors, which would break per
tenant.

**Drift detection (proposed, not built):** a scheduled canary replay of each artifact against
its target on a regular interval, independent of real agent traffic, using checkpoint failures
as the drift signal. This is a natural, already-existing hook rather than new instrumentation —
`replay_engine.py` already reports `checkpoint_not_met` as a `failure_type` distinct from
`locator_not_found`, `action_failed`, `session_expired`, and `guardrail_violation`, so a
drift-detection job can watch for exactly that failure type without touching replay logic.

## Escalation & handoff

**The mechanism.** Automation and a human operator share the same live browser session — the
same `SurfaceAdapter`/Playwright page — coordinated by a file-based control flag
(`evidence/<run_id>/control.json`: `{"controller": "automation" | "human"}`), not a fresh
session handed off separately. Automation pauses by polling in
`wait_for_control_return()` rather than blocking the browser itself, so whatever the human does
happens on the exact page state automation left off at, not a re-navigated copy.

**Detection.** `"stuck"` is a first-class action in the discovery LLM's tool vocabulary
(`TOOLS` in `discovery_loop.py`), sitting alongside `click`/`type_text`/`navigate`/etc., not an
exception-based afterthought bolted on around the loop. The system prompt explicitly tells the
model to call it "if you cannot safely proceed — e.g. a locator has failed repeatedly, the page
shows an unexpected state, or the next step requires a judgment call only a human should make."

**Real demonstrated behavior**, not hypotheticals:
- `evidence/discovery_run_20260816T001137` — the model loaded member 1003's page and called
  `stuck` for real, reasoning that a compliance sign-off requires human judgment.
  `escalation.raise_intervention()` fired for real (screenshot taken, `intervention.json`
  written); a real human operator took control and handed it back via actual curl calls against
  the operator console's live HTTP routes (`GET /take-control`, `POST /take-control`,
  `GET /hand-back`) — not a simulated call. The run resumed from its current turn (turn 6, not a
  restart from turn 1), the model called `stuck` a second time on the same underlying ambiguity
  once it saw nothing on the page itself confirmed sign-off, and — since no second hand-back
  came — `wait_for_control_return()` genuinely timed out after 5 minutes, and `run_discovery()`
  raised `DiscoveryStuck` with the reason extended to note the escalation timeout. This proves
  the timeout path works, not just the happy path.
- `evidence/discovery_run_20260816T003623` + `evidence/20260816T003623` — the model tried to
  open a second savings sub-account for member 1002 (who already has one, per the mock app's
  seeded data), hit the real duplicate-account validation error, and reasonably called `stuck`
  rather than silently failing or guessing. Full cycle: escalation → take-control → a logged
  human action (`human_actions.jsonl`) → hand-back → resume → `done`, completing successfully
  after human input, with `intervention.json` ending in `status: "resolved"`.

**A real bug found and fixed during this.** The initial integration called
`raise_intervention()` then `wait_for_control_return()` without ever calling
`set_control(run_id, "human")` in between. Since `get_control()` defaults to `"automation"` when
no `control.json` exists yet, the "pause" was a no-op: an early real run burned through all
`max_steps` in seconds via repeated instant escalate-and-immediately-resume cycles, because
`wait_for_control_return()` saw the default `"automation"` state and returned `True` before any
human had a chance to act. Fixed by explicitly calling `set_control(run_id, "human")`
immediately after raising the intervention. This bug was invisible in code review — the
integration matched its own spec exactly — and only surfaced under actual execution against the
real system, which is the concrete reason this had to be demonstrated for real rather than just
described.

**Explicit limits**, honestly, per the brief's scope note rather than hidden as a weakness: the
operator console is a bare Flask page, not a real co-browsing UI — a polling screenshot refresh,
no live video. It supports one active intervention per run, not a real multi-run queue.
Handoff is polling-based (`wait_for_control_return` polls `control.json` on an interval), not
push-based. None of that is load-bearing, though — the control-transfer mechanism itself (the
shared file-based flag, the shared live browser session, resume-from-current-turn rather than
restart, the human action log kept in the same evidence trail as automated steps) is the real
design and is what was actually verified end-to-end above. The UI polish around it is what was
intentionally left minimal.

## Safety

The actual guardrail model (`guardrails.py`) has three parts. **Allowlist enforcement**:
domain, route, and action-level checks, all read from `guardrails_config.json` and checked via
`check_allowlist()` before every single `navigate` — both the discovery loop's and the replay
engine's, and both the initial entry-URL navigate and every in-run one, not just the first.
**Risk policy on irreversible steps**: `check_risk_policy()` maps a step's `risk_class` plus
`risk_policy.irreversible_handling` in config to one of `"proceed"` / `"require_confirmation"` /
`"block"`. This project's config uses `require_confirmation`, and that path is demonstrated for
real, not just unit-tested — replay pauses on a real blocking terminal `input()` prompt before
executing an irreversible step, confirmed by piping `y` into a live replay run and observing it
proceed only after that. **Redaction**: `redact()` recursively walks a log record and replaces
the value of any key matching `redaction.never_log_fields` (case-insensitive), verified against
both nested dicts and lists of dicts, applied to every record before it's written to `log.jsonl`.

The limits are worth stating plainly rather than glossing over: `require_confirmation` is
currently a blocking CLI prompt (`request_confirmation()` in `guardrails.py`), an explicit
stand-in for what would be a real approval-queue or API call in production — its own docstring
says as much. The allowlist is static config, hand-edited, not learned or monitored for
near-miss patterns (the `/members/*` gap documented below is exactly the kind of drift that
static config won't catch on its own). There's no rate limiting and no anomaly detection —
guardrails here answer "is this one action allowed right now," not "does this run's overall
behavior look suspicious."

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

No automated test suite — manual and scripted verification (real discovery runs, real replay
scenarios, real curl-driven escalation cycles, all shown in this report's other sections) was
used throughout instead. The operator console is
functional but visually minimal — a real product version would need actual live co-browsing
(e.g. a shared remote-debugging view or streamed video) rather than a polling screenshot
refresh. None of the stretch goals in the brief's Section 8 were attempted, prioritizing full
depth on all six core requirements over partial coverage plus one polished extra. Multi-tenant
reuse and drift detection are designed (see Heterogeneity & multi-tenant above) but not built,
per the brief's explicit instruction not to prematurely build scaling infrastructure.

With more time, the confidence/approval stretch goal — scoring artifacts by replay reliability
and gating unattended replay on that score — would be the natural next step: `review_status`
already exists as a field on every artifact, but right now a human sets it once and nothing
ever revisits it; it isn't yet driven by any real signal from how the artifact actually performs
on repeated replay.

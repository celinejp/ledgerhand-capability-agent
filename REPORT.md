# Report

_Design decisions, trade-offs, and evaluation notes go here._

## Architecture

Single-process, synchronous architecture throughout — no queues, no separate services beyond
the mock app, operator console, and control panel (each a small standalone Flask process).
Deliberate, not an oversight: the brief warns against premature scaling infrastructure, and a
message queue or worker pool would be exactly that here.

The pipeline: a natural-language goal drives the **discovery loop** (LLM-driven,
`discovery_loop.py`, observe/decide/act against a live browser) → a **compiler** turns the
resulting transcript into a draft **artifact JSON** → a **human review** pass promotes it to
`review_status: "approved"` → the **replay engine** (`replay_engine.py`, fully deterministic, no
LLM involved at all) executes approved artifacts against the live app → **guardrails and
escalation** wrap both the discovery loop and the replay engine, not just one of them.

The end-to-end thread (goal → discovery → artifact → replay → escalation) is demonstrated across
multiple real runs rather than one artificial monolithic session, since escalation is inherently
a rare-path event — see `evidence/discovery_run_20260815T190600` for goal → artifact and
`evidence/discovery_run_20260816T003623` for a full escalate → resume → complete cycle.

The target app is a self-built mock core-banking Flask app (`mock_app/app.py`) rather than a
public site. Two reasons: reliable, reproducible failure injection (not-found member, validation
error, slow load, session timeout via query-param flags) a public site can't guarantee on
demand, and deliberately legacy-flavored markup (table layout, no `id`/`class`/`data-testid`) to
actually exercise the "no clean DOM" problem rather than a modern app with convenient test hooks.

Technology choices: Playwright for browser automation (mature accessibility-tree support via
`aria_snapshot()`); Claude (Anthropic API, model `claude-sonnet-4-6`) for the discovery loop's
tool-calling and reliable structured output, called directly rather than through a heavier agent
framework since the loop here is simple enough that a framework would add indirection without
capability, and fully decoupled from the rest of the system (`SurfaceAdapter`, artifact schema,
replay engine) so it could be swapped without touching anything downstream; Flask for the mock
app, operator console, and control panel, to keep the stack minimal and dependency-light.

Each discovery turn: `observe()` captures the accessibility-tree state, goes to Claude with a
fixed tool vocabulary (`click`, `type_text`, `select_option`, `navigate`, `read_text`,
`wait_for`, plus `done`/`stuck`), and `tool_choice` forces exactly one tool call per turn
(`{"type": "any", "disable_parallel_tool_use": True}`) — never freeform text. Each real
`ActionResult` feeds back as the next `tool_result`, so the model reasons from ground truth —
which is also why the transcript is directly compilable into an artifact, since every turn is
already one discrete, typed action with a real outcome attached.

## Artifact schema

The schema's core shape (`artifact_schema.py`): an ordered list of `ArtifactStep`s, each with an
`action`, a `locator`, an optional `value`, an optional `checkpoint` that confirms the step
actually landed, a `risk_class` (`"safe"` | `"irreversible"`), and optional `extractions` for
steps that read data out of the page. Wrapping the steps: typed `inputs`/`outputs` at the
artifact level (not just embedded per-step), an `app_target` naming the vendor app and entry
URL, `recorded_from` provenance (which discovery run produced this artifact, when, which model),
a `review_status` (`"draft"` | `"approved"`), and `safety` metadata.

The artifact is deliberately decoupled from the raw discovery transcript — the transcript is
exploratory (retries, dead ends, locator choices that only worked by coincidence), while the
artifact is the reviewed, reusable capability that survives that process. `version` exists so
the schema itself can evolve (as it already has once, see below) without invalidating
already-reviewed artifacts. It's also a typed contract — explicit input types, enum `values`,
numeric `min`s, named `outputs` — rather than a step list, so it's readable by both a human
reviewer deciding whether to approve it and a calling agent deciding how to invoke it, which the
brief requires explicitly.

`InputParam` gained optional `values` and `min` fields during the human review pass, to express
enum constraints (`account_type`) and numeric minimums (`opening_deposit`) that weren't
anticipated in the original schema draft. Existing fields and previously-compiled artifacts are
unaffected — this is exactly the kind of controlled evolution the schema's `version` field
exists to support.

## Determinism & error handling

**Accessibility API migration:** Playwright's older `page.accessibility.snapshot()` API is
removed in the installed version (1.62+); `SurfaceAdapter` uses `Locator.aria_snapshot()`
instead — browser-computed rather than heuristic — with a parser flattening its tree into the
flat `{role, name, value}` list the discovery loop consumes. One concrete finding: `<input
type="number">` maps to ARIA role `spinbutton`, not `textbox`.

**Compiler doesn't auto-infer judgment calls:** The compiler intentionally does not auto-infer
inputs, outputs, checkpoints, or business-outcome branches from a raw transcript — that requires
human judgment it can't safely automate, so it outputs a "draft" artifact with a review
checklist. This caught two real issues on the first discovery run: a duplicate `type_text` retry
into the Member ID field a naive compiler would have kept as two steps, and an account-type
value (`"Checking"`) that only worked at discovery time because Playwright's `select_option()`
falls back to label-matching — the review pass parameterized it into `{{account_type}}` instead
of leaving a brittle literal.

**No automatic dialog/interstitial recovery:** Recoverable conditions (dismissing a known
interstitial, waiting out a transient load) are handled via explicit `wait_for` steps baked into
the artifact during review, not automatic detection-and-recovery at replay time. A genuinely
unexpected dialog surfaces as a hard failure rather than being auto-dismissed — blindly
dismissing an unknown dialog on regulated financial data without human judgment is itself a
safety risk, not just a robustness gap.

## Heterogeneity & multi-tenant

This section is design-only, per the brief's "design, not necessarily build" scope for this
part — building multi-tenant/drift infrastructure now would be premature scaling work ahead of a
second real surface or tenant ever existing.

**Surface abstraction.** `SurfaceAdapter`'s `observe`/`click`/`type_text`/`select_option`/
`navigate`/`read_text`/`wait_for` interface is the seam — the discovery loop and replay engine
never touch Playwright directly, only `SurfaceAdapter` does (enforced via a refactor removing
direct `adapter.page.*` access from `replay_engine.py`; see `get_page_text()`/`current_url()`/
`screenshot()`). Locators resolve role/label-first, CSS as a last-resort fallback
(`LocatorStrategy = Literal["role", "label", "text", "css"]`) — the same ordering that would
carry over to:
- **A legacy web app with worse markup:** same interface and strategy, just messier concrete
  selectors discovered per-app.
- **A desktop app:** same interface, backed by OS accessibility APIs (UIAutomation on Windows,
  AT-SPI on Linux) instead of Playwright — `Locator.strategy="role"` maps directly onto OS
  accessibility roles, which is why role-based locators were chosen over CSS/DOM-position
  locators in the first place.

**What would actually change:** only `SurfaceAdapter`'s internals — a new adapter class with the
same method signatures. Artifact schema, compiler, replay engine, guardrails, and escalation all
work unmodified, since none of them know anything about Playwright — a deliberate constraint
enforced during review, not an accident.

**Multi-tenant reuse (proposed, not built):** a "base" artifact captures the vendor-app-level
flow (e.g. `open_member_subaccount_v1` here); small per-tenant "override" documents (different
`entry_url`, different button/label text for white-labeled instances) layer on top at replay
time instead of duplicating the whole artifact per tenant. `strategy="role"` is what makes this
viable — accessible role stays stable across tenant branding even when visible text changes,
unlike CSS selectors.

**Drift detection (proposed, not built):** a scheduled canary replay of each artifact on a
regular interval, independent of real agent traffic, using `checkpoint_not_met` — already a
distinct `failure_type` in `replay_engine.py` — as the drift signal, a hook that needs no new
instrumentation.

## Escalation & handoff

**The mechanism.** Automation and a human operator share the same live browser session — the
same `SurfaceAdapter`/Playwright page — coordinated by a file-based flag
(`evidence/<run_id>/control.json`: `{"controller": "automation" | "human"}`), not a fresh
session handed off separately. Automation pauses by polling `wait_for_control_return()` rather
than blocking the browser, so whatever the human does happens on the exact page state automation
left off at.

Concretely: the discovery script runs with a visible browser window, so escalation fires onto a
session already on screen; the operator visits `/take-control` (flips the control flag, shows
the screenshot and stop reason) and interacts directly with that same window — not a rendered
copy — logging what they did in the console's action-log for the evidence trail. Clicking "Hand
Back" flips control back and resumes the paused loop. The console is a control/logging surface,
not a remote-control surface, per the brief's scope note allowing a bare/mock operator UI — the
control-transfer state machine (shared session, file-based flag, resume-from-current-state) is
what's real.

**Detection.** `"stuck"` is a first-class action in the discovery LLM's tool vocabulary (`TOOLS`
in `discovery_loop.py`), sitting alongside `click`/`type_text`/`navigate`/etc., not an
exception-based afterthought bolted on around the loop. The system prompt explicitly tells the
model to call it "if you cannot safely proceed — e.g. a locator has failed repeatedly, the page
shows an unexpected state, or the next step requires a judgment call only a human should make."

**Real demonstrated behavior**, not hypotheticals:
- `evidence/discovery_run_20260816T001137` — real `stuck` (compliance sign-off needed) → real
  escalation via `raise_intervention()` → real curl-driven take-control/hand-back against the
  operator console's live routes → resumed from turn 6 (not restarted). The model called `stuck`
  again on the same ambiguity, and since no second hand-back came, `wait_for_control_return()`
  genuinely timed out after 5 minutes and `run_discovery()` raised `DiscoveryStuck` — proving the
  timeout path works, not just the happy path.
- `evidence/discovery_run_20260816T003623` + `evidence/20260816T003623` — a duplicate-account
  validation error (member 1002 already has savings) triggered a real `stuck` rather than a
  silent failure or guess. Full cycle completed: escalation → take-control → logged action →
  hand-back → resume → `done`, `intervention.json` ending `status: "resolved"`.

**A real bug found and fixed during this.** The initial integration called `raise_intervention()`
then `wait_for_control_return()` without calling `set_control(run_id, "human")` — since
`get_control()` defaults to `"automation"` with no `control.json`, the "pause" was a no-op, and
an early run burned through all `max_steps` in seconds via repeated instant escalate-and-resume
cycles. Fixed by calling `set_control(run_id, "human")` right after raising the intervention.
Invisible in code review (the integration matched its spec exactly), only surfacing under real
execution — the reason this had to be demonstrated for real, not described.

**Explicit limits**, honestly, per the brief's scope note rather than hidden as a weakness: the
operator console is a bare Flask page, not real co-browsing — a polling screenshot refresh, no
live video. One active intervention per run, not a real multi-run queue. Handoff is
polling-based (`wait_for_control_return` polls `control.json`), not push-based — the dashboard
itself poll-refreshes every 5 seconds, sorting pending interventions first and showing a live
pending count in the tab title, but a production version would need a real notification channel
(email, Slack, PagerDuty) firing on `raise_intervention()`, which wasn't built since it's
infrastructure, not core mechanism. None of that is load-bearing — the control-transfer
mechanism itself (shared file-based flag, shared live browser session, resume-from-current-turn,
the human action log in the same evidence trail as automated steps) is the real design; the UI
polish around it was intentionally left minimal.

## Safety

The guardrail model (`guardrails.py`) has three parts. **Allowlist enforcement**: domain, route,
and action-level checks from `guardrails_config.json`, via `check_allowlist()` before every
`navigate` — discovery and replay, entry URL and every in-run one. **Risk policy on irreversible
steps**: `check_risk_policy()` maps `risk_class` plus config's `risk_policy.irreversible_handling`
to `"proceed"`/`"require_confirmation"`/`"block"`. This project uses `require_confirmation`,
demonstrated for real — replay pauses on a real blocking terminal `input()` prompt, confirmed by
piping `y` into a live run and observing it proceed only after. **Redaction**: `redact()`
recursively replaces any key matching `redaction.never_log_fields` (case-insensitive) in nested
dicts and lists of dicts, applied to every record before `log.jsonl`.

The limits, stated plainly: `require_confirmation` is a blocking CLI prompt
(`request_confirmation()`), an explicit stand-in for a real approval-queue/API call in
production. The allowlist is static config, hand-edited, not learned or monitored for near-miss
patterns (the `/members/*` gap below is exactly that kind of drift). No rate limiting, no
anomaly detection — guardrails here answer "is this one action allowed right now," not "does
this run's overall behavior look suspicious."

The control panel (`src/control_panel/app.py`) is where that CLI-prompt limit actually bites — a
blocking `input()` has no usable stdin inside a web request and would hang the whole server.
Rather than change `guardrails.py`, `replay_artifact()` gained an optional
`confirmation_callback` parameter (default `request_confirmation`, so every existing caller is
unaffected); the control panel passes a callback that just returns `True`, since it already
gates on an explicit checkbox in the web form before calling `replay_artifact()` at all.

The discovery loop also enforces a same-host check on the resulting URL after every action (not
just `navigate` calls, since a click could follow a link off-host) — a minimal safety net
specific to discovery; the full allowlist/risk-policy model lives in `guardrails.py`, described
above.

The initial allowlist route `/members/*` excluded the bare `/members` path, which a real
discovery run legitimately tried while searching for account-level operations — a genuine gap,
not a demo workaround, fixed by broadening the route to `['/members', '/members/*']`. A small
concrete example of allowlist drift real usage surfaces, which production would want to catch
via monitoring rejected-but-plausible navigation rather than only manual testing.

## Cuts

Manual locator verification during the artifact review pass reused the same live mock-app
instance as the original discovery run, mutating its in-memory state (extra test accounts on
members 1001 and 1004). Production would run this against an isolated staging instance or a
reset-per-verification harness — a deliberate cut given the mock app's simplicity; it's
restarted before any replay demo to guarantee clean state.

No automated test suite — manual and scripted verification (real discovery runs, replay
scenarios, curl-driven escalation cycles, all shown elsewhere in this report) was used instead.
The operator console was restyled for presentability but still lacks real live co-browsing — a
polling screenshot refresh, not live video, is the real product gap, not the visual polish. Of
the brief's Section 8 stretch goals, only confidence/approval was attempted (below); the rest
were skipped to prioritize full depth on the core requirements plus that one extra. Multi-tenant
reuse and drift detection are designed (see Heterogeneity & multi-tenant) but not built, per the
brief's instruction not to prematurely build scaling infrastructure.

**Confidence/approval, built:** `confidence.py` records every real replay's outcome to
`artifacts/<artifact_id>.replay_history.jsonl` and scores an artifact from that history
(`total_runs`, `success_count`, `success_rate` — `None`, not `0.0`, when there's no history yet,
since "unknown" and "known to always fail" aren't the same thing). `replay_artifact()` gained a
`require_approval` gate (default `True`): an artifact whose `review_status` isn't `"approved"`
is refused with `failure_type="not_approved"` before the browser is touched. Demonstrated for
real against `open_member_subaccount_v1` (previously `"draft"`): the gate correctly blocked,
several real replays with `require_approval=False` built up a real 75% success rate, below the
80%-over-3-runs approval-suggestion threshold `scripts/check_confidence.py` uses, and after
manually approving via `compiler.approve_artifact()` — never called automatically —
the same call that was blocked earlier succeeded. Verify this directly:
`python3 scripts/check_confidence.py open_member_subaccount_v1` prints the real computed numbers
from `artifacts/open_member_subaccount_v1.replay_history.jsonl`.

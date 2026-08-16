# LedgerHand Capability Agent

An LLM-driven computer-use agent that discovers how to accomplish a goal in a live web UI once,
compiles the successful run into a typed, deterministic "capability artifact," and replays that
artifact afterward with no LLM in the loop. Full design write-up: see [REPORT.md](REPORT.md).

## Setup

Requires **Python 3.11+** (developed and tested on 3.13.6).

```bash
git clone <this-repo-url>
cd ledgerhand-capability-agent
python3 -m venv venv
source venv/bin/activate
```

Install both dependency sets — the agent's own (`requirements.txt`) and the mock app's
(`mock_app/requirements.txt`):

```bash
pip install -r requirements.txt
pip install -r mock_app/requirements.txt
```

Install Playwright's browser binary:

```bash
playwright install chromium
```

Create your `.env` from the template and set a real key:

```bash
cp .env.example .env
# then edit .env and set ANTHROPIC_API_KEY=sk-ant-...
```

`guardrails_config.json` must exist at the repo root. It's already committed, so no action is
needed — but note that every discovery and replay run loads it at startup and refuses to
proceed if it's missing (`load_guardrails_config()` raises `FileNotFoundError` rather than
running with no allowlist/risk policy at all).

## Running the system

Two terminals, both from the repo root with the venv activated.

**Terminal 1 — mock core-banking app (required):**

```bash
python3 mock_app/app.py
```

Serves at `http://127.0.0.1:8420`.

**Terminal 2 — operator console (optional, only for the escalation/handoff demo):**

```bash
python3 src/operator_console/app.py
```

Serves at `http://127.0.0.1:8421`.

## Demo path: discover, then replay

**1. Run a real discovery session** against the mock app:

```bash
python3 scripts/run_discovery_demo.py
```

This is a real entry point, not a stub — it loads `.env`, launches a visible (non-headless)
Chromium window, and runs `run_discovery()` against `http://127.0.0.1:8420/members/search` with
a fixed goal ("Look up member 1003 and open a new checking sub-account with a $75 opening
deposit, reaching the confirmation screen."). The browser is visible on purpose: automation and
a human operator are meant to share the same live session (see [REPORT.md](REPORT.md)'s
Escalation & handoff section), so nothing about this loop assumes a hidden browser. To try a
different goal, edit the `GOAL` and `START_URL` constants at the top of the script, or write a
short script of your own that calls `run_discovery(goal, start_url, adapter)` directly — there's
no CLI flag for it yet.

Each run writes its transcript and screenshots to `evidence/discovery_run_<timestamp>/`.

**2. The resulting artifact** already lands, reviewed and parameterized, at
[`artifacts/cap_open_subaccount_v1.json`](artifacts/cap_open_subaccount_v1.json) — this is the
real output of that discovery goal after a human review pass turned concrete values into
`{{member_id}}`/`{{account_type}}`/`{{opening_deposit}}` placeholders, marked the confirmation
step's checkpoint, and flagged the final submit as `risk_class: "irreversible"`. There's no
separate compile script committed yet, so turning a fresh discovery transcript into a new
artifact currently means calling `compiler.compile_artifact()` directly — the existing artifact
is the ready-to-replay example.

**3. Replay that artifact** with real parameters. There's no committed replay script either, so
this is a short one-liner against the real, already-existing helpers:

```bash
python3 -c "
import sys; sys.path.insert(0, 'src')
from playwright.sync_api import sync_playwright
from compiler import load_artifact
from replay_engine import replay_artifact
from surface_adapter import SurfaceAdapter

artifact = load_artifact('artifacts/cap_open_subaccount_v1.json')
params = {'member_id': '1001', 'account_type': 'checking', 'opening_deposit': 50}

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page()
    adapter = SurfaceAdapter(page)
    result = replay_artifact(artifact, params, adapter)
    browser.close()

print(result)
"
```

Step 6 of this artifact is marked irreversible, so this will pause on a real terminal prompt
(`Proceed? [y/N]:`) before submitting — type `y` to let it continue.

**What the result looks like.** `replay_artifact()` returns a `ReplayResult` with a `status` of:
- `"success"` — goal achieved; `outputs` holds the extracted values (e.g.
  `{'account_number': '4324837542', 'confirmation_id': 'CONF-C07016CY'}`).
- `"business_outcome"` — a known, named non-error outcome (e.g. `member_not_found`), not a bug —
  `outcome` names it.
- `"failure"` — something went wrong; `failure_type` is one of `invalid_params`,
  `locator_not_found`, `action_failed`, `session_expired`, `checkpoint_not_met`, or
  `guardrail_violation`, with `expected`/`observed` describing the mismatch and (usually)
  `screenshot_path` pointing at evidence of the failure.

Every run's transcript, screenshots, and result land under `evidence/replay_run_<timestamp>/`.

## Running without live services

There's no true offline mode — discovery needs a real Anthropic API call and both discovery and
replay need a real browser against a real running app, so don't expect a mock/stub mode that
isn't there. The one meaningful reduction available: **replay never touches the LLM at all**.
Once an artifact exists, `replay_artifact()` runs purely deterministically — no API key needed,
no network call beyond the browser talking to the target app. Only the discovery step (finding
a new capability in the first place) needs `ANTHROPIC_API_KEY`.

## Evidence

See [`evidence/`](evidence/) for real discovery, replay, and escalation-handoff runs — for
example `evidence/discovery_run_20260815T234507` (a real discovery run correctly blocked by the
guardrails allowlist before it could leave the approved surface) and
`evidence/discovery_run_20260816T003623` + `evidence/20260816T003623` (a real "stuck" escalation,
handed to a human operator via the operator console, resumed, and completed). See
[REPORT.md](REPORT.md) for the full design write-up.

## Reproducing the escalation handoff manually

With the operator console running (Terminal 2 above) and a discovery run currently paused on a
real `stuck` call, visit `http://127.0.0.1:8421/` to see pending interventions, or go straight to
`http://127.0.0.1:8421/take-control?run_id=<run_id>` to take control, log an action, and hand
back via `http://127.0.0.1:8421/hand-back?run_id=<run_id>` — the same three routes (plus the
dashboard) the automated demo above drives with curl.

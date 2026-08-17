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

Serves at `http://127.0.0.1:8420/members/search` (the app has no root route, so the bare port
URL 404s).

**Terminal 2 — operator console (optional, only for the escalation/handoff demo):**

```bash
python3 src/operator_console/app.py
```

Serves at `http://127.0.0.1:8421`.

## Demo path: discover, then replay

**1. Run a real discovery session:**

```bash
python3 scripts/run_discovery_demo.py
```

Real Claude API calls, a real visible browser, a fixed goal against
`http://127.0.0.1:8420/members/search` (edit `GOAL`/`START_URL` in the script to change it).
Writes to `evidence/discovery_run_<timestamp>/` (see REPORT.md for the design rationale).

**2. The resulting artifact** is already reviewed and parameterized at
[`artifacts/open_member_subaccount_v1.json`](artifacts/open_member_subaccount_v1.json).

**3. Replay it** with real parameters — no committed replay script yet, so this is a one-liner
against the real helpers:

```bash
python3 -c "
import sys; sys.path.insert(0, 'src')
from playwright.sync_api import sync_playwright
from compiler import load_artifact
from replay_engine import replay_artifact
from surface_adapter import SurfaceAdapter

artifact = load_artifact('artifacts/open_member_subaccount_v1.json')
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

Step 6 is irreversible, so this pauses on a real `Proceed? [y/N]:` prompt — type `y`. Writes to
`evidence/replay_run_<timestamp>/`.

**Result status:** `success` (goal achieved, real data in `outputs`) · `business_outcome` (a
valid non-error answer, e.g. member not found) · `failure` — `failure_type` is one of
`invalid_params`, `locator_not_found`, `action_failed`, `session_expired`, `checkpoint_not_met`,
`guardrail_violation`, or `not_approved` (see REPORT.md's Safety section for the approval gate).

### Verify the confidence score

```bash
python3 scripts/check_confidence.py open_member_subaccount_v1
```

Prints `total_runs`, `success_count`, `success_rate`, and `last_run_status` computed live from
the real replay history file, so the numbers cited in REPORT.md's Cuts section can be checked
directly rather than taken on faith.

## Control panel (presentation-friendly UI)

```bash
python3 src/control_panel/app.py
```

Serves at `http://127.0.0.1:8422` with two forms — run a discovery goal, or replay an artifact,
with input fields rendered dynamically from that artifact's real `inputs` list. It's a thin
wrapper around the same `run_discovery()`/`replay_artifact()` functions above, not a new
execution path — the scripts and one-liners still work identically and independently of it. See
REPORT.md's Safety section for how it handles the irreversible-step confirmation without hanging
a web request.

## Trying it out

Beyond the basic demo above, here's how to see the system's different behaviors — a clean
success, an expected non-error outcome, a real failure, and the human escalation path.

### Seed data

`mock_app/app.py` starts every process with the same four members, one of whom already has an
account:

| Member ID | Name | Starting balance | Pre-existing account |
|---|---|---|---|
| `1001` | Alice Whitfield | $4,523.10 | none |
| `1002` | Marcus Boyd | $128.50 | savings |
| `1003` | Priya Anand | $9,876.00 | none |
| `1004` | Diego Castillo | $0.00 | none |

The mock app's data lives in memory only, so it resets to this table every time
`mock_app/app.py` restarts. If a member already has the account type you're trying to open
(either from this seed data or from a prior run), you'll see a `business_outcome` result instead
of `success` — that's expected, not a bug. Use a different member ID or account type, or restart
`mock_app/app.py` to reset.

All in the control panel's Replay Artifact form (`http://127.0.0.1:8422/`,
`open_member_subaccount_v1.json`) unless noted:

- **A successful run:** `member_id` `1001`, `account_type` `checking`, `opening_deposit` `60`
  (see Seed data above for other members). Result page shows a real account number and
  confirmation ID.
- **An expected non-error outcome:** `member_id` `9999` (doesn't exist) → `business_outcome:
  member_not_found`, not a crash.
- **A structural failure:** `account_type` set to something invalid like `business` →
  `failure: invalid_params`, caught before a browser opens.
- **The confirmation gate:** valid, success-shaped params still land on a confirmation page
  first (opening an account is irreversible) — check the box and submit again to run it; see
  REPORT.md's Safety section for why this genuinely blocks execution.
- **Escalation and handoff:** in the Run Discovery form, use a goal that hits a real ambiguity
  (e.g. a member who already has that account type). Once it escalates, take control from the
  operator console (`http://127.0.0.1:8421/`), interact with the live (visible, non-headless)
  browser window directly, log a note, and hand back. See REPORT.md's Escalation & handoff
  section for exactly what each step does.

## Running without live services

There's no true offline mode — discovery needs a real Anthropic API call and both discovery and
replay need a real browser against a real running app, so don't expect a mock/stub mode that
isn't there. The one meaningful reduction available: **replay never touches the LLM at all**.
Once an artifact exists, `replay_artifact()` runs purely deterministically — no API key needed,
no network call beyond the browser talking to the target app. Only the discovery step (finding
a new capability in the first place) needs `ANTHROPIC_API_KEY`.

## Evidence

See [`evidence/README.md`](evidence/README.md) for an index of real discovery, replay, and
escalation-handoff runs. See [REPORT.md](REPORT.md) for the full design write-up.

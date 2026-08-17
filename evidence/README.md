# Evidence

Real runs of the system, organized by what each one demonstrates. Every folder here is a real
run's transcript/log/screenshots — nothing staged. Pruned to one clean example per distinct
outcome; redundant repeat runs were removed.

## Discovery runs

| Folder | What it shows |
|---|---|
| `discovery_run_20260815T190600` | **Original discovery run.** 8 turns, reached `done`. Compiled and reviewed into `artifacts/open_member_subaccount_v1.json` (see below). |
| `discovery_run_20260815T234507` | Goal: delete member 1003's account (no such feature exists). 10 turns; correctly blocked by the guardrails allowlist navigating to `/` before ever leaving the approved surface. |
| `discovery_run_20260816T001137` | Goal engineered to require human sign-off. Real `stuck` → escalated → resumed → `stuck` again → genuine 5-minute timeout → `DiscoveryStuck`. Paired with `20260816T001137` below. |
| `discovery_run_20260816T003623` | Goal: open a duplicate savings account for member 1002. Real `stuck` on the resulting validation error → escalated → resumed → `done`. Paired with `20260816T003623` below. |
| `discovery_run_20260816T171828` | Same original goal, targeting member 1004; 8 turns, `done`. Kept as a second clean success on a different member. |
| `discovery_run_20260816T174144` | Goal: open a savings account for member 1002. Real `stuck` after a repeated validation error → escalated → resumed → `done` at turn 17. Paired with `20260816T174144` below. |

## Replay runs

| Folder | Scenario | Result |
|---|---|---|
| `replay_run_20260815T194212` | member 1001, checking | success |
| `replay_run_20260815T194214` | member 9999 (doesn't exist) | business_outcome: `member_not_found` |
| `replay_run_20260815T194215` | invalid input params | **failure: `invalid_params`** |
| `replay_run_20260815T231127` | member 1003, checking | success |
| `replay_run_20260815T231136` | member 9999 (doesn't exist) | business_outcome: `member_not_found` |
| `replay_run_20260815T231143` | tampered locator on step 2 | **failure: `locator_not_found`** |
| `replay_run_20260815T231207` | allowlist route temporarily stripped | **failure: `guardrail_violation`** |
| `replay_run_20260816T173144` | member 1002, savings (already has one) | business_outcome: `validation_error` |

**Error/exceptional state example:** `evidence/replay_run_20260815T231143` — `failure_type: locator_not_found`, demonstrates a step's locator genuinely failing to resolve (a tampered "Submit" button target), returning a proper `failure` result with `expected`/`observed`/`screenshot_path` filled in rather than crashing. Two other real failure types are also on file: `replay_run_20260815T194215` (`invalid_params`, rejected before the browser even opens) and `replay_run_20260815T231207` (`guardrail_violation`, blocked by the allowlist). `replay_run_20260816T173144` covers the other non-error exceptional case — a duplicate-account business outcome, distinct from the not-found case in `replay_run_20260815T194214`.

## Escalation & handoff

| Discovery run + escalation state | What happened | Outcome |
|---|---|---|
| `discovery_run_20260816T001137` + `20260816T001137` | Compliance sign-off goal; real `stuck` → escalated → resumed → `stuck` again on the same ambiguity | **Timed out** — no second hand-back came, so `wait_for_control_return()` genuinely timed out after 5 minutes and raised `DiscoveryStuck`. Intervention left `pending`, control left `human`. |
| `discovery_run_20260816T003623` + `20260816T003623` | Duplicate savings-account goal; real `stuck` → escalated → hand-back (with a logged action) → resumed | **Resumed & completed** — `done`; intervention `resolved`. |
| `discovery_run_20260816T174144` + `20260816T174144` | Repeated validation-error struggle on member 1002's savings sub-account; real `stuck` → escalated → hand-back → resumed | **Resumed & completed** — `done`; intervention `resolved`. |

## The saved artifact

[`artifacts/open_member_subaccount_v1.json`](../artifacts/open_member_subaccount_v1.json) is the
reviewed, parameterized output of `discovery_run_20260815T190600` above — the artifact every
replay run in this folder actually executes.

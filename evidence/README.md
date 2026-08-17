# Evidence

Real runs of the system, organized by what each one demonstrates. Every folder here is a real
run's transcript/log/screenshots — nothing staged. Pruned to one clean example per distinct
outcome; redundant repeat runs were removed.

## Discovery runs

- [`discovery_run_20260816T232211`](discovery_run_20260816T232211): default goal (member 1003,
  checking, $75) → reached `done` in 7 turns. This is the original clean success run and what
  [`artifacts/open_member_subaccount_v1.json`](../artifacts/open_member_subaccount_v1.json) was
  compiled from.
- [`discovery_run_20260816T232659`](discovery_run_20260816T232659): goal "delete member 1003's
  account" (no such feature exists) → real attempt correctly blocked by the guardrails allowlist
  before leaving the approved surface.
- [`discovery_run_20260816T232729`](discovery_run_20260816T232729): goal "open a new savings
  sub-account for member 1002" (who already has one) → real `stuck` at turn 12 (duplicate-account
  validation error) → real escalation → real human takeover via the operator console → hand-back
  → resumed and reached `done` at turn 13. Paired with
  [`evidence/20260816T232729`](20260816T232729) below. This is the full, complete
  escalation-to-completion cycle.

## Escalation & handoff

- [`evidence/20260816T232729`](20260816T232729): `intervention.json` status `"resolved"`, real
  logged human action ("Reviewed: duplicate account, do not retry, report outcome") in
  `human_actions.jsonl`, paired with `discovery_run_20260816T232729` above which reached `done`
  after resume.

## Replay runs

- [`replay_run_20260816T232300`](replay_run_20260816T232300): `success` (clean happy path, real
  extracted `account_number`/`confirmation_id`)
- [`replay_run_20260816T232324`](replay_run_20260816T232324): `business_outcome`,
  `member_not_found`
- [`replay_run_20260816T232344`](replay_run_20260816T232344): `business_outcome`,
  `validation_error` (duplicate account)
- [`replay_run_20260816T232438`](replay_run_20260816T232438): `failure`, `invalid_params` — the
  hard-failure/exceptional-state example Section 6 explicitly asks to include, demonstrating
  rejection before the browser is ever touched
- [`replay_run_20260816T234015`](replay_run_20260816T234015): `success` (second clean example,
  run after the control-panel/confidence-history fix)

## The saved artifact

[`artifacts/open_member_subaccount_v1.json`](../artifacts/open_member_subaccount_v1.json) — the
reviewed, approved capability every replay run above actually executes. See
[`artifacts/README.md`](../artifacts/README.md) for details on the artifact and its confidence
history.

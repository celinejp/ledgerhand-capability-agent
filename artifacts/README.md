# Artifacts

`open_member_subaccount_v1.json` is the reviewed, parameterized capability this project produces
from `discovery_run_20260816T232211` (see [`evidence/README.md`](../evidence/README.md)). Its
`review_status` is currently `"approved"` — new artifacts always start as `"draft"`, and
`replay_artifact()` refuses to run them unattended until a human explicitly approves them via
`compiler.approve_artifact()`, based on real replay history. See REPORT.md's Safety section for
the full design rationale.

`open_member_subaccount_v1.replay_history.jsonl` is a running log of every real replay's outcome
(status, failure_type, timestamp), written automatically by `confidence.replay_and_record()` —
the function the control panel's replay route calls. Check the artifact's live computed
reliability with:

```bash
python3 scripts/check_confidence.py open_member_subaccount_v1
```

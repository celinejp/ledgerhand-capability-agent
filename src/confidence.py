"""Replay reliability tracking: records each real replay's outcome and scores an artifact's confidence from that real history — no synthetic or estimated data."""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict

from artifact_schema import CapabilityArtifact
from guardrails import request_confirmation
from replay_engine import ReplayResult, replay_artifact
from surface_adapter import SurfaceAdapter

ARTIFACTS_DIR = Path("artifacts")


def _history_path(artifact_id: str) -> Path:
    return ARTIFACTS_DIR / f"{artifact_id}.replay_history.jsonl"


def replay_and_record(
    artifact: CapabilityArtifact,
    params: Dict[str, Any],
    adapter: SurfaceAdapter,
    require_approval: bool = True,
    confirmation_callback: Callable[[Any, Dict[str, Any]], bool] = request_confirmation,
) -> ReplayResult:
    """Drop-in wrapper around replay_artifact() that also appends the real outcome to the
    artifact's replay history (artifacts/<artifact_id>.replay_history.jsonl).

    Same arguments and return value as replay_artifact() — any caller of replay_artifact() can
    switch to this instead to get confidence-tracking for free. require_approval still defaults
    to True: this wrapper doesn't bypass the approval gate itself, it just records whatever
    replay_artifact() actually did (including a blocked not_approved run, so approval-gate
    failures show up in the history too).
    """
    result = replay_artifact(
        artifact,
        params,
        adapter,
        require_approval=require_approval,
        confirmation_callback=confirmation_callback,
    )

    history_path = _history_path(artifact.artifact_id)
    history_path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status": result.status,
        "failure_type": result.failure_type,
    }
    with history_path.open("a") as f:
        f.write(json.dumps(record) + "\n")

    return result


def compute_confidence(artifact_id: str) -> Dict[str, Any]:
    history_path = _history_path(artifact_id)
    if not history_path.exists():
        records = []
    else:
        records = [json.loads(line) for line in history_path.read_text().splitlines() if line.strip()]

    total_runs = len(records)
    success_count = sum(1 for r in records if r["status"] == "success")

    return {
        "total_runs": total_runs,
        "success_count": success_count,
        "success_rate": (success_count / total_runs) if total_runs > 0 else None,
        "last_run_status": records[-1]["status"] if records else None,
    }

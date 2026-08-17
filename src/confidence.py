"""Replay reliability tracking: records each real replay's outcome and scores an artifact's confidence from that real history — no synthetic or estimated data."""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from playwright.sync_api import sync_playwright

from compiler import load_artifact
from replay_engine import ReplayResult, replay_artifact
from surface_adapter import SurfaceAdapter

ARTIFACTS_DIR = Path("artifacts")


def _history_path(artifact_id: str) -> Path:
    return ARTIFACTS_DIR / f"{artifact_id}.replay_history.jsonl"


def replay_and_record(artifact_path: str, params: dict) -> ReplayResult:
    """Replay an artifact against a fresh browser session and append the outcome to its replay history.

    Always calls replay_artifact() with require_approval=False: this function exists precisely
    to build up confidence history for artifacts that aren't approved yet, so it's a deliberate,
    supervised bypass of the approval gate, not an unattended production entry point.
    """
    artifact = load_artifact(artifact_path)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        adapter = SurfaceAdapter(page)
        result = replay_artifact(artifact, params, adapter, require_approval=False)
        browser.close()

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

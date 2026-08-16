"""Intervention queue and control-transfer flag for pausing automation and handing the live browser session to a human operator."""

import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from surface_adapter import SurfaceAdapter

CONTROLLERS = ("automation", "human")


def _run_dir(run_id: str) -> Path:
    run_dir = Path("evidence") / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def raise_intervention(
    run_id: str,
    capability_or_goal: str,
    current_step_id: Optional[int],
    reason: str,
    adapter: SurfaceAdapter,
) -> str:
    run_dir = _run_dir(run_id)
    intervention_id = f"intervention_{uuid.uuid4().hex[:8]}"

    screenshot_path = run_dir / f"{intervention_id}.png"
    adapter.screenshot(screenshot_path)

    record = {
        "intervention_id": intervention_id,
        "run_id": run_id,
        "capability_or_goal": capability_or_goal,
        "current_step_id": current_step_id,
        "reason": reason,
        "status": "pending",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "screenshot_path": str(screenshot_path),
    }
    (run_dir / "intervention.json").write_text(json.dumps(record, indent=2))
    return intervention_id


def get_control(run_id: str) -> str:
    control_path = Path("evidence") / run_id / "control.json"
    if not control_path.exists():
        return "automation"
    return json.loads(control_path.read_text()).get("controller", "automation")


def set_control(run_id: str, controller: str) -> None:
    if controller not in CONTROLLERS:
        raise ValueError(f"controller must be one of {CONTROLLERS}, got {controller!r}")
    run_dir = _run_dir(run_id)
    (run_dir / "control.json").write_text(json.dumps({"controller": controller}))


def wait_for_control_return(run_id: str, poll_interval_s: float = 1.0, timeout_s: float = 300.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if get_control(run_id) == "automation":
            return True
        time.sleep(poll_interval_s)
    return False


def record_human_action(run_id: str, action_description: str) -> None:
    run_dir = _run_dir(run_id)
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "action_description": action_description,
    }
    with (run_dir / "human_actions.jsonl").open("a") as f:
        f.write(json.dumps(record) + "\n")


def resolve_intervention(run_id: str, resolution_note: str) -> None:
    intervention_path = Path("evidence") / run_id / "intervention.json"
    if not intervention_path.exists():
        raise FileNotFoundError(f"No intervention record found for run {run_id!r} at {intervention_path!r}")

    record = json.loads(intervention_path.read_text())
    record["status"] = "resolved"
    record["resolution_note"] = resolution_note
    record["resolved_at"] = datetime.now(timezone.utc).isoformat()
    intervention_path.write_text(json.dumps(record, indent=2))

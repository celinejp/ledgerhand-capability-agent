"""Turns a successful discovery transcript into a typed, versioned, serializable capability artifact."""

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from artifact_schema import ArtifactStep, CapabilityArtifact, InputParam, OutputParam
from discovery_loop import DiscoveryStep
from surface_adapter import Locator

ARTIFACTS_DIR = Path("artifacts")


def _extract_value(action: str, params: Dict[str, Any]) -> Optional[str]:
    if action == "type_text":
        return params.get("text")
    if action == "select_option":
        return params.get("value")
    if action == "navigate":
        return params.get("url")
    return None


def compile_artifact(
    steps: List[DiscoveryStep],
    goal: str,
    app_target: Dict[str, Any],
    discovery_run_id: str,
    model: str,
    artifact_id: str,
) -> CapabilityArtifact:
    artifact_steps = [
        ArtifactStep(
            step_id=step.step_number,
            action=step.action,
            locator=step.locator,
            value=_extract_value(step.action, step.params),
        )
        for step in steps
    ]

    artifact = CapabilityArtifact(
        artifact_id=artifact_id,
        version=1,
        app_target=app_target,
        goal_description=goal,
        inputs=[],
        outputs=[],
        steps=artifact_steps,
        recorded_from={
            "discovery_run_id": discovery_run_id,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "model": model,
        },
        review_status="draft",
        safety={
            "allowlist_scope": urlparse(app_target["entry_url"]).hostname,
            "irreversible_step_ids": [],
        },
    )

    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = ARTIFACTS_DIR / f"{artifact_id}.json"
    with out_path.open("w") as f:
        json.dump(asdict(artifact), f, indent=2)

    print(
        f"Artifact compiled with {len(artifact_steps)} steps. Human review required to: "
        "(1) parameterize concrete values into {{inputs}}, "
        "(2) mark the confirmation step's checkpoint, "
        "(3) identify any irreversible step, "
        "(4) add business-outcome branches if applicable."
    )

    return artifact


def _locator_from_dict(data: Optional[Dict[str, Any]]) -> Optional[Locator]:
    if data is None:
        return None
    return Locator(
        strategy=data["strategy"],
        value=data["value"],
        role=data.get("role"),
        fallback=_locator_from_dict(data.get("fallback")),
    )


def _artifact_step_from_dict(data: Dict[str, Any]) -> ArtifactStep:
    return ArtifactStep(
        step_id=data["step_id"],
        action=data["action"],
        locator=_locator_from_dict(data.get("locator")),
        value=data.get("value"),
        checkpoint=data.get("checkpoint"),
        risk_class=data.get("risk_class", "safe"),
        extractions=data.get("extractions"),
        business_outcome_branches=data.get("business_outcome_branches"),
    )


def load_artifact(path: str) -> CapabilityArtifact:
    with open(path) as f:
        data = json.load(f)

    return CapabilityArtifact(
        artifact_id=data["artifact_id"],
        version=data["version"],
        app_target=data["app_target"],
        goal_description=data["goal_description"],
        inputs=[InputParam(**d) for d in data["inputs"]],
        outputs=[OutputParam(**d) for d in data["outputs"]],
        steps=[_artifact_step_from_dict(d) for d in data["steps"]],
        recorded_from=data["recorded_from"],
        review_status=data.get("review_status", "draft"),
        safety=data["safety"],
    )

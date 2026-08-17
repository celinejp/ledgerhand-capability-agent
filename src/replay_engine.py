"""Deterministic executor for capability artifacts (no LLM in the loop); returns success, known business outcome, or hard failure."""

import json
import re
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from artifact_schema import ArtifactStep, CapabilityArtifact
from guardrails import check_allowlist, check_risk_policy, load_guardrails_config, redact, request_confirmation
from surface_adapter import ActionResult, Locator, LocatorNotFoundError, SurfaceAdapter, WaitCondition

DEFAULT_CHECKPOINT_TIMEOUT_MS = 5000

# Body-text substrings that indicate a known, non-error business outcome rather
# than a technical failure (e.g. a validly rejected lookup), paired with the
# outcome name reported back to the caller.
KNOWN_BUSINESS_OUTCOME_SIGNATURES = [
    ("No member found", "member_not_found"),
    ("Validation Error", "validation_error"),
]

KNOWN_RECOVERABLE_STEPS: set = set()

_PARAM_PLACEHOLDER_RE = re.compile(r"\{\{(\w+)\}\}")


@dataclass
class ReplayResult:
    """Discriminated by `status`; only the fields for that status are populated."""

    status: str
    # status: "success" | "failure" | "business_outcome"

    outputs: Optional[Dict[str, Any]] = None

    outcome: Optional[str] = None
    message: Optional[str] = None

    failure_type: Optional[str] = None
    expected: Optional[str] = None
    observed: Optional[str] = None
    screenshot_path: Optional[str] = None

    step_id: Optional[int] = None


# ---------------------------------------------------------------------------
# Locator / condition / param parsing (artifact JSON -> surface_adapter types)
# ---------------------------------------------------------------------------


def _locator_from_dict(data: Optional[Dict[str, Any]]) -> Optional[Locator]:
    if data is None:
        return None
    return Locator(
        strategy=data["strategy"],
        value=data["value"],
        role=data.get("role"),
        fallback=_locator_from_dict(data.get("fallback")),
    )


def _checkpoint_to_wait_condition(checkpoint: Optional[Dict[str, Any]]) -> Optional[WaitCondition]:
    if checkpoint is None:
        return None
    return WaitCondition(
        kind=checkpoint["type"],
        locator=_locator_from_dict(checkpoint.get("locator")),
        text=checkpoint.get("text"),
        pattern=checkpoint.get("pattern"),
    )


def _substitute_params(value: Optional[str], params: Dict[str, Any]) -> Optional[str]:
    if value is None:
        return None
    return _PARAM_PLACEHOLDER_RE.sub(lambda m: str(params.get(m.group(1), m.group(0))), value)


def _validate_params(artifact: CapabilityArtifact, params: Dict[str, Any]) -> Optional[ReplayResult]:
    for input_param in artifact.inputs:
        if input_param.name not in params:
            if input_param.required:
                return ReplayResult(
                    status="failure",
                    failure_type="invalid_params",
                    step_id=0,
                    expected=f"required input {input_param.name!r} to be provided",
                    observed="missing",
                )
            continue

        value = params[input_param.name]

        if input_param.values is not None and value not in input_param.values:
            return ReplayResult(
                status="failure",
                failure_type="invalid_params",
                step_id=0,
                expected=f"{input_param.name!r} to be one of {input_param.values}",
                observed=repr(value),
            )

        if input_param.min is not None:
            try:
                numeric_value = float(value)
            except (TypeError, ValueError):
                return ReplayResult(
                    status="failure",
                    failure_type="invalid_params",
                    step_id=0,
                    expected=f"{input_param.name!r} to be numeric (>= {input_param.min})",
                    observed=repr(value),
                )
            if numeric_value < input_param.min:
                return ReplayResult(
                    status="failure",
                    failure_type="invalid_params",
                    step_id=0,
                    expected=f"{input_param.name!r} >= {input_param.min}",
                    observed=repr(value),
                )

    return None


# ---------------------------------------------------------------------------
# Business-outcome detection
# ---------------------------------------------------------------------------


def _detect_business_outcome(adapter: SurfaceAdapter) -> Optional[str]:
    body_text = adapter.get_page_text()
    for signature, outcome in KNOWN_BUSINESS_OUTCOME_SIGNATURES:
        if signature in body_text:
            return outcome
    return None


# ---------------------------------------------------------------------------
# Step execution
# ---------------------------------------------------------------------------


def _execute_step_action(adapter: SurfaceAdapter, step: ArtifactStep, value: Optional[str]) -> ActionResult:
    if step.action == "click":
        return adapter.click(step.locator)

    if step.action == "type_text":
        return adapter.type_text(step.locator, value or "")

    if step.action == "select_option":
        return adapter.select_option(step.locator, value or "")

    if step.action == "navigate":
        return adapter.navigate(value or "")

    if step.action == "read_text":
        try:
            text = adapter.read_text(step.locator)
            return ActionResult(success=True, detail=text)
        except LocatorNotFoundError as exc:
            return ActionResult(success=False, error_type="locator_not_found", detail=str(exc))

    if step.action == "wait_for":
        condition = _checkpoint_to_wait_condition(step.checkpoint)
        if condition is None:
            return ActionResult(success=False, error_type="invalid_step", detail="wait_for step has no condition to wait on")
        return adapter.wait_for(condition)

    return ActionResult(success=False, error_type="unknown_action", detail=f"Unrecognized action: {step.action!r}")


def _save_failure_screenshot(adapter: SurfaceAdapter, run_dir: Path, step_id: int) -> str:
    path = run_dir / f"failure_step_{step_id}.png"
    adapter.screenshot(str(path))
    return str(path)


def _run_extractions(adapter: SurfaceAdapter, step: ArtifactStep, outputs: Dict[str, Any], run_dir: Path) -> Optional[ReplayResult]:
    if not step.extractions:
        return None

    for extraction in step.extractions:
        locator = _locator_from_dict(extraction["locator"])
        try:
            outputs[extraction["output_name"]] = adapter.read_text(locator)
        except LocatorNotFoundError as exc:
            return ReplayResult(
                status="failure",
                failure_type="locator_not_found",
                step_id=step.step_id,
                expected=f"element for extraction {extraction['output_name']!r} ({locator.strategy}:{locator.value})",
                observed=str(exc),
                screenshot_path=_save_failure_screenshot(adapter, run_dir, step.step_id),
            )

    return None


# ---------------------------------------------------------------------------
# Evidence logging
# ---------------------------------------------------------------------------


def _append_log(path: Path, record: Dict[str, Any], config: dict) -> None:
    with path.open("a") as f:
        f.write(json.dumps(redact(record, config), default=str) + "\n")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def replay_artifact(
    artifact: CapabilityArtifact,
    params: Dict[str, Any],
    adapter: SurfaceAdapter,
    require_approval: bool = True,
    confirmation_callback: Callable[[Any, Dict[str, Any]], bool] = request_confirmation,
) -> ReplayResult:
    """Deterministically execute a CapabilityArtifact against a live SurfaceAdapter session. No LLM involved.

    require_approval (default True) gates unattended replay on the artifact's review_status: if
    True and artifact.review_status != "approved", this returns immediately with
    failure_type="not_approved" and never touches the browser at all — an unattended/production
    caller can't accidentally run a draft artifact.

    confirmation_callback (default guardrails.request_confirmation) is called for irreversible
    steps under a "require_confirmation" risk policy. It defaults to the real terminal-blocking
    confirmation prompt, so every existing caller behaves exactly as before. Pass a different
    callable only when a real blocking input() would be wrong for the calling context — e.g. a
    web request handler, which has no usable stdin to answer it and would otherwise hang the
    whole server process. See src/control_panel/app.py for that case: it gates on an explicit
    "confirmed" checkbox in the web form *before* ever calling replay_artifact(), then passes a
    callback that simply returns True, since human confirmation already happened in the browser.

    Pass require_approval=False to deliberately bypass that gate for manual testing/review of an
    unapproved artifact — e.g. running a brand-new draft artifact's first few confidence-building
    replays (see confidence.replay_and_record()) before a human approves it. This is an
    intentional escape hatch for a human explicitly choosing to run a draft artifact, not a hole
    in the gate: nothing in this codebase passes require_approval=False on an unattended caller's
    behalf.
    """
    config = load_guardrails_config()

    run_id = time.strftime("%Y%m%dT%H%M%S")
    run_dir = Path("evidence") / f"replay_run_{run_id}"
    run_dir.mkdir(parents=True, exist_ok=True)
    adapter.evidence_dir = run_dir
    log_path = run_dir / "log.jsonl"

    def _finish(result: ReplayResult) -> ReplayResult:
        (run_dir / "result.json").write_text(json.dumps(asdict(result), indent=2))
        return result

    invalid = _validate_params(artifact, params)
    if invalid is not None:
        return _finish(invalid)

    if require_approval and artifact.review_status != "approved":
        return _finish(
            ReplayResult(
                status="failure",
                failure_type="not_approved",
                step_id=0,
                expected="artifact review_status to be 'approved' for unattended replay",
                observed=f"artifact review_status is {artifact.review_status!r}; unattended replay requires 'approved'",
            )
        )

    entry_url = artifact.app_target["entry_url"]
    allowed, reason = check_allowlist(entry_url, "navigate", config)
    if not allowed:
        return _finish(
            ReplayResult(
                status="failure",
                failure_type="guardrail_violation",
                step_id=0,
                expected="entry URL to be permitted by the guardrails allowlist",
                observed=reason,
            )
        )

    nav_result = adapter.navigate(entry_url)
    if not nav_result.success:
        return _finish(
            ReplayResult(
                status="failure",
                failure_type="action_failed",
                step_id=0,
                expected=f"navigate to {entry_url!r} to succeed",
                observed=nav_result.detail or "navigation failed",
                screenshot_path=_save_failure_screenshot(adapter, run_dir, 0),
            )
        )

    outputs: Dict[str, Any] = {}

    for step in artifact.steps:
        value = _substitute_params(step.value, params)

        decision, policy_reason = check_risk_policy(step, config)
        if decision == "block":
            return _finish(
                ReplayResult(
                    status="failure",
                    failure_type="guardrail_violation",
                    step_id=step.step_id,
                    expected="step to be permitted by risk policy",
                    observed=policy_reason,
                )
            )
        if decision == "require_confirmation":
            confirmed = confirmation_callback(step, {"raw_value": step.value, "resolved_value": value})
            if not confirmed:
                return _finish(
                    ReplayResult(
                        status="failure",
                        failure_type="guardrail_violation",
                        step_id=step.step_id,
                        expected="step to be permitted by risk policy",
                        observed="user declined confirmation",
                    )
                )

        if step.action == "navigate":
            allowed, reason = check_allowlist(value or "", "navigate", config)
            if not allowed:
                return _finish(
                    ReplayResult(
                        status="failure",
                        failure_type="guardrail_violation",
                        step_id=step.step_id,
                        expected="navigate target to be permitted by the guardrails allowlist",
                        observed=reason,
                    )
                )

        result = _execute_step_action(adapter, step, value)

        _append_log(
            log_path,
            {
                "step_id": step.step_id,
                "action": step.action,
                "params_used": {"raw_value": step.value, "resolved_value": value},
                "result": asdict(result),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
            config,
        )

        if not result.success and step.step_id not in KNOWN_RECOVERABLE_STEPS:
            failure_type = "locator_not_found" if result.error_type == "locator_not_found" else "action_failed"
            expected = (
                f"{step.action} on {step.locator.strategy}:{step.locator.value}"
                if step.locator
                else f"{step.action} to succeed"
            )
            return _finish(
                ReplayResult(
                    status="failure",
                    failure_type=failure_type,
                    step_id=step.step_id,
                    expected=expected,
                    observed=result.detail or result.error_type or "action failed",
                    screenshot_path=_save_failure_screenshot(adapter, run_dir, step.step_id),
                )
            )

        current_url = adapter.current_url()
        if "/login" in current_url:
            return _finish(
                ReplayResult(
                    status="failure",
                    failure_type="session_expired",
                    step_id=step.step_id,
                    expected="session to remain active",
                    observed=f"redirected to {current_url!r}",
                    screenshot_path=_save_failure_screenshot(adapter, run_dir, step.step_id),
                )
            )

        outcome = _detect_business_outcome(adapter)
        if outcome is not None:
            return _finish(
                ReplayResult(
                    status="business_outcome",
                    outcome=outcome,
                    step_id=step.step_id,
                    message=f"Detected known business outcome {outcome!r} after step {step.step_id} ({step.action}).",
                )
            )

        if step.checkpoint is not None:
            condition = _checkpoint_to_wait_condition(step.checkpoint)
            checkpoint_result = adapter.wait_for(condition, timeout_ms=DEFAULT_CHECKPOINT_TIMEOUT_MS)
            if not checkpoint_result.success:
                return _finish(
                    ReplayResult(
                        status="failure",
                        failure_type="checkpoint_not_met",
                        step_id=step.step_id,
                        expected=f"checkpoint {step.checkpoint}",
                        observed=checkpoint_result.detail or "checkpoint not satisfied",
                        screenshot_path=_save_failure_screenshot(adapter, run_dir, step.step_id),
                    )
                )

        extraction_failure = _run_extractions(adapter, step, outputs, run_dir)
        if extraction_failure is not None:
            return _finish(extraction_failure)

    return _finish(ReplayResult(status="success", outputs=outputs))

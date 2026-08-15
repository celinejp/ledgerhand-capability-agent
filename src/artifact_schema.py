"""Typed, versioned, serializable capability artifact: ordered steps, locator strategy, typed inputs/outputs, and checkpoints.

ArtifactStep.locator reuses surface_adapter.Locator directly (same strategy/value/role/fallback
shape used during discovery), and checkpoint/wait entries follow the same {kind, locator, text,
pattern} shape as surface_adapter.WaitCondition. replay_engine.py can therefore resolve and wait
on artifact steps with the exact same SurfaceAdapter methods discovery_loop.py already used,
rather than re-deriving element-targeting or wait logic from a separate representation.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from surface_adapter import Locator


@dataclass
class InputParam:
    name: str
    type: str
    required: bool
    description: str
    values: Optional[List[str]] = None  # allowed values, for type == "enum"
    min: Optional[float] = None  # minimum allowed value, for numeric types


@dataclass
class OutputParam:
    name: str
    type: str
    source: str  # "extracted"
    description: str


@dataclass
class ArtifactStep:
    step_id: int
    action: str  # click / type_text / select_option / navigate / read_text / wait_for
    locator: Optional[Locator]
    value: Optional[str] = None  # for type_text/select_option/navigate; may be a {{param_name}} placeholder
    checkpoint: Optional[Dict[str, Any]] = None  # {type, locator/text/pattern} — confirms the step landed
    risk_class: str = "safe"  # "safe" | "irreversible"
    extractions: Optional[List[Dict[str, Any]]] = None  # [{output_name, locator}] for read_text steps
    business_outcome_branches: Optional[List[Dict[str, Any]]] = None
    # each entry: {match: {type, value/locator}, outcome_name, terminal: bool}


@dataclass
class CapabilityArtifact:
    artifact_id: str
    version: int
    app_target: Dict[str, Any]  # {vendor_app, entry_url}
    goal_description: str
    inputs: List[InputParam]
    outputs: List[OutputParam]
    steps: List[ArtifactStep]
    recorded_from: Dict[str, Any]  # {discovery_run_id, recorded_at, model}
    review_status: str = "draft"  # "draft" | "approved"
    safety: Dict[str, Any] = field(default_factory=dict)  # {allowlist_scope, irreversible_step_ids}

import json
from dataclasses import asdict

from artifact_schema import ArtifactStep, CapabilityArtifact, InputParam, OutputParam
from compiler import load_artifact
from surface_adapter import Locator


def _build_sample_artifact() -> CapabilityArtifact:
    return CapabilityArtifact(
        artifact_id="cap_test_roundtrip_v1",
        version=1,
        app_target={"vendor_app": "test-bank", "entry_url": "http://127.0.0.1:8420/members/search"},
        goal_description="Look up a member and open a sub-account.",
        inputs=[
            InputParam(name="member_id", type="string", required=True, description="Member ID to search for"),
            InputParam(
                name="account_type",
                type="enum",
                required=True,
                description="Account type to open",
                values=["checking", "savings"],
            ),
            InputParam(
                name="opening_deposit",
                type="integer",
                required=True,
                description="Opening deposit amount",
                min=0.0,
            ),
        ],
        outputs=[
            OutputParam(name="account_number", type="string", source="extracted", description="New account number"),
        ],
        steps=[
            ArtifactStep(
                step_id=1,
                action="click",
                locator=Locator(
                    strategy="role",
                    value="Search",
                    role="button",
                    fallback=Locator(strategy="css", value="button.search-btn", role=None, fallback=None),
                ),
                value=None,
            ),
            ArtifactStep(
                step_id=2,
                action="click",
                locator=Locator(strategy="role", value="Submit", role="button", fallback=None),
                value=None,
                checkpoint={
                    "type": "element_visible",
                    "locator": {"strategy": "role", "value": "Account Opened", "role": "heading", "fallback": None},
                },
                risk_class="irreversible",
                extractions=[
                    {
                        "output_name": "account_number",
                        "locator": {"strategy": "css", "value": "td.account-number", "role": None, "fallback": None},
                    }
                ],
            ),
        ],
        recorded_from={
            "discovery_run_id": "discovery_run_test",
            "recorded_at": "2026-01-01T00:00:00+00:00",
            "model": "test-model",
        },
        review_status="draft",
        safety={"allowlist_scope": "test-bank:members/*", "irreversible_step_ids": [2]},
    )


def _write_artifact(tmp_path, artifact: CapabilityArtifact):
    out_path = tmp_path / f"{artifact.artifact_id}.json"
    out_path.write_text(json.dumps(asdict(artifact), indent=2))
    return out_path


def test_artifact_round_trips_through_json(tmp_path):
    """A CapabilityArtifact with a fallback locator, a checkpoint, and an extraction survives asdict() -> json.dumps() -> load_artifact() unchanged."""
    original = _build_sample_artifact()
    out_path = _write_artifact(tmp_path, original)

    loaded = load_artifact(str(out_path))

    assert loaded == original


def test_input_param_values_and_min_round_trip(tmp_path):
    """InputParam.values (enum) and InputParam.min (numeric) survive the same round trip, both set and unset."""
    original = _build_sample_artifact()
    out_path = _write_artifact(tmp_path, original)

    loaded = load_artifact(str(out_path))

    account_type_input = next(i for i in loaded.inputs if i.name == "account_type")
    assert account_type_input.values == ["checking", "savings"]
    assert account_type_input.min is None

    deposit_input = next(i for i in loaded.inputs if i.name == "opening_deposit")
    assert deposit_input.min == 0.0
    assert deposit_input.values is None

    member_id_input = next(i for i in loaded.inputs if i.name == "member_id")
    assert member_id_input.values is None
    assert member_id_input.min is None

"""CLI: prints an artifact's real replay-confidence summary, computed from artifacts/<artifact_id>.replay_history.jsonl."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from compiler import load_artifact  # noqa: E402
from confidence import compute_confidence  # noqa: E402

ARTIFACTS_DIR = Path("artifacts")


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python3 scripts/check_confidence.py <artifact_id>")
        return 1

    artifact_id = sys.argv[1]
    confidence = compute_confidence(artifact_id)

    print(f"Confidence report for {artifact_id!r}:")
    print(f"  total_runs:      {confidence['total_runs']}")
    print(f"  success_count:   {confidence['success_count']}")
    if confidence["success_rate"] is None:
        print("  success_rate:    unknown (no recorded runs yet)")
    else:
        print(f"  success_rate:    {confidence['success_rate']:.0%}")
    print(f"  last_run_status: {confidence['last_run_status']}")

    artifact_path = ARTIFACTS_DIR / f"{artifact_id}.json"
    if not artifact_path.exists():
        return 0

    artifact = load_artifact(str(artifact_path))
    print(f"  review_status:   {artifact.review_status!r}")

    if (
        confidence["success_rate"] is not None
        and confidence["success_rate"] >= 0.8
        and confidence["total_runs"] >= 3
        and artifact.review_status == "draft"
    ):
        print("\nConsider approving this artifact.")

    return 0


if __name__ == "__main__":
    sys.exit(main())

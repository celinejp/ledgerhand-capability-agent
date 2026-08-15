"""Demo entry point: loads environment variables, then runs the discovery loop against the mock app."""

import pickle
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from playwright.sync_api import sync_playwright

from discovery_loop import DiscoveryDeadEnd, DiscoveryStuck, SafetyViolation, run_discovery
from surface_adapter import SurfaceAdapter

GOAL = (
    "Look up member 1003 and open a new checking sub-account with a $75 "
    "opening deposit, reaching the confirmation screen."
)
START_URL = "http://127.0.0.1:8420/members/search"


def main() -> int:
    exit_code = 0
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)
        try:
            page = browser.new_page()
            adapter = SurfaceAdapter(page)
            steps = run_discovery(GOAL, START_URL, adapter)
        except (DiscoveryStuck, DiscoveryDeadEnd, SafetyViolation) as exc:
            print(f"{type(exc).__name__}: {exc}")
            exit_code = 1
        else:
            print(f"Discovery succeeded in {len(steps)} steps.")
            out_path = adapter.evidence_dir / "steps.pkl"
            with out_path.open("wb") as f:
                pickle.dump(steps, f)
            print(f"Saved steps to {out_path}")
        finally:
            browser.close()
    return exit_code


if __name__ == "__main__":
    sys.exit(main())

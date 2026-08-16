"""Safety/policy layer shared by replay_engine.py and discovery_loop.py: allowlist enforcement, irreversible-step risk policy, and log redaction, driven by guardrails_config.json."""

import fnmatch
import json
from pathlib import Path
from typing import Any, Dict, Tuple
from urllib.parse import urlparse


def load_guardrails_config(path: str = "guardrails_config.json") -> dict:
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(
            f"Guardrails config not found at {config_path!r}. Refusing to proceed without an explicit "
            "allowlist/risk policy — create guardrails_config.json (see repo root) or pass an explicit path."
        )
    with config_path.open() as f:
        return json.load(f)


def check_allowlist(url: str, action: str, config: dict) -> Tuple[bool, str]:
    allowlist = config.get("allowlist", {})
    domains = allowlist.get("domains", [])
    routes = allowlist.get("routes", [])
    actions_allowed = allowlist.get("actions_allowed", [])

    parsed = urlparse(url)
    host = parsed.hostname
    if host is None or host.lower() not in {d.lower() for d in domains}:
        return False, f"host {host!r} is not in allowlist domains {domains}"

    if parsed.path and not any(fnmatch.fnmatchcase(parsed.path, route) for route in routes):
        return False, f"path {parsed.path!r} does not match any allowlist route {routes}"

    if action not in actions_allowed:
        return False, f"action {action!r} is not in allowlist actions_allowed {actions_allowed}"

    return True, ""


def check_risk_policy(step, config: dict) -> Tuple[str, str]:
    risk_class = getattr(step, "risk_class", "safe")
    if risk_class == "safe":
        return "proceed", ""

    handling = config.get("risk_policy", {}).get("irreversible_handling", "require_confirmation")

    if handling == "block":
        return "block", f"risk_class={risk_class!r} is blocked by risk_policy.irreversible_handling={handling!r}"
    if handling == "require_confirmation":
        return (
            "require_confirmation",
            f"risk_class={risk_class!r} requires confirmation per risk_policy.irreversible_handling={handling!r}",
        )
    if handling in ("proceed", "allow"):
        return "proceed", ""

    # Unrecognized policy value: fail safe rather than silently letting an irreversible step through.
    return "block", f"unrecognized risk_policy.irreversible_handling={handling!r}; blocking risk_class={risk_class!r} step by default"


def request_confirmation(step, context: dict) -> bool:
    """Blocking terminal yes/no gate for a single irreversible step.

    This is a CLI stand-in for what would be a real approval-queue or API
    call in production (e.g. a Slack approval, a ticket, a signed callback).
    escalation.py's intervention-request mechanism is the more general
    version of this pattern, for cases that need full human takeover of the
    session rather than a single yes/no gate on one step.
    """
    locator = getattr(step, "locator", None)
    locator_desc = f"{locator.strategy}:{locator.value}" if locator else "(no locator)"

    print("\n[guardrails] Confirmation required for irreversible step:")
    print(f"  step_id: {getattr(step, 'step_id', '?')}")
    print(f"  action:  {getattr(step, 'action', '?')}")
    print(f"  locator: {locator_desc}")
    if context:
        print(f"  context: {context}")

    response = input("Proceed? [y/N]: ").strip().lower()
    return response == "y"


def redact(record: dict, config: dict) -> dict:
    never_log_fields = {name.lower() for name in config.get("redaction", {}).get("never_log_fields", [])}

    def _redact_dict(d: Dict[str, Any]) -> Dict[str, Any]:
        result = {}
        for key, value in d.items():
            if isinstance(key, str) and key.lower() in never_log_fields:
                result[key] = "***REDACTED***"
            elif isinstance(value, dict):
                result[key] = _redact_dict(value)
            elif isinstance(value, list):
                result[key] = [_redact_dict(item) if isinstance(item, dict) else item for item in value]
            else:
                result[key] = value
        return result

    return _redact_dict(record)

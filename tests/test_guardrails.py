from types import SimpleNamespace

from guardrails import check_allowlist, check_risk_policy, redact

CONFIG = {
    "allowlist": {
        "domains": ["127.0.0.1", "localhost"],
        "routes": ["/members", "/members/*"],
        "actions_allowed": ["click", "type_text", "select_option", "navigate", "read_text", "wait_for"],
    },
    "risk_policy": {"irreversible_handling": "require_confirmation"},
    "redaction": {"never_log_fields": ["password", "ssn", "account_pin", "credentials"]},
}


# ---------------------------------------------------------------------------
# check_allowlist
# ---------------------------------------------------------------------------


def test_check_allowlist_allowed_url_and_action_passes():
    allowed, reason = check_allowlist("http://127.0.0.1:8420/members/search", "navigate", CONFIG)
    assert allowed is True
    assert reason == ""


def test_check_allowlist_disallowed_domain_fails():
    allowed, reason = check_allowlist("http://evil.example.com/members/search", "navigate", CONFIG)
    assert allowed is False
    assert "evil.example.com" in reason


def test_check_allowlist_disallowed_route_fails():
    allowed, reason = check_allowlist("http://127.0.0.1:8420/admin", "navigate", CONFIG)
    assert allowed is False
    assert "/admin" in reason


def test_check_allowlist_disallowed_action_fails():
    allowed, reason = check_allowlist("http://127.0.0.1:8420/members/search", "delete_everything", CONFIG)
    assert allowed is False
    assert "delete_everything" in reason


# ---------------------------------------------------------------------------
# check_risk_policy
# ---------------------------------------------------------------------------


def test_check_risk_policy_safe_always_proceeds_regardless_of_handling():
    step = SimpleNamespace(risk_class="safe")
    config = {"risk_policy": {"irreversible_handling": "block"}}  # even a strict policy shouldn't matter for "safe"

    decision, reason = check_risk_policy(step, config)

    assert decision == "proceed"
    assert reason == ""


def test_check_risk_policy_irreversible_block():
    step = SimpleNamespace(risk_class="irreversible")
    config = {"risk_policy": {"irreversible_handling": "block"}}

    decision, _ = check_risk_policy(step, config)

    assert decision == "block"


def test_check_risk_policy_irreversible_require_confirmation():
    step = SimpleNamespace(risk_class="irreversible")
    config = {"risk_policy": {"irreversible_handling": "require_confirmation"}}

    decision, _ = check_risk_policy(step, config)

    assert decision == "require_confirmation"


def test_check_risk_policy_irreversible_proceed():
    step = SimpleNamespace(risk_class="irreversible")
    config = {"risk_policy": {"irreversible_handling": "proceed"}}

    decision, _ = check_risk_policy(step, config)

    assert decision == "proceed"


def test_check_risk_policy_unrecognized_handling_fails_safe():
    step = SimpleNamespace(risk_class="irreversible")
    config = {"risk_policy": {"irreversible_handling": "some_typo_value"}}

    decision, reason = check_risk_policy(step, config)

    assert decision == "block"
    assert "some_typo_value" in reason


# ---------------------------------------------------------------------------
# redact
# ---------------------------------------------------------------------------


def test_redact_flat_sensitive_key():
    record = {"username": "alice", "password": "hunter2"}

    result = redact(record, CONFIG)

    assert result["password"] == "***REDACTED***"
    assert result["username"] == "alice"


def test_redact_nested_dict():
    record = {"step": 1, "params": {"member_id": "1003", "account_pin": "4321"}}

    result = redact(record, CONFIG)

    assert result["params"]["account_pin"] == "***REDACTED***"
    assert result["params"]["member_id"] == "1003"


def test_redact_list_of_dicts():
    record = {"entries": [{"credentials": "secret-token"}, {"note": "fine"}]}

    result = redact(record, CONFIG)

    assert result["entries"][0]["credentials"] == "***REDACTED***"
    assert result["entries"][1]["note"] == "fine"


def test_redact_is_case_insensitive():
    record = {"PASSWORD": "hunter2", "Ssn": "123-45-6789"}

    result = redact(record, CONFIG)

    assert result["PASSWORD"] == "***REDACTED***"
    assert result["Ssn"] == "***REDACTED***"


def test_redact_leaves_unrelated_keys_untouched():
    record = {"action": "click", "step_id": 6, "result": {"success": True}}

    result = redact(record, CONFIG)

    assert result == record

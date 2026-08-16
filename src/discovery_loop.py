"""LLM-driven observe-decide-act loop that discovers how to accomplish a natural-language goal against the live target app."""

import json
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import anthropic

import escalation
from guardrails import check_allowlist, load_guardrails_config
from surface_adapter import ActionResult, Locator, LocatorNotFoundError, SurfaceAdapter, WaitCondition

DEFAULT_MODEL = "claude-sonnet-4-6"
DEFAULT_MAX_STEPS = 20
CONSECUTIVE_FAILURE_LIMIT = 3

# ---------------------------------------------------------------------------
# Result / error types
# ---------------------------------------------------------------------------


@dataclass
class DiscoveryStep:
    step_number: int
    action: str  # tool name: click / type_text / select_option / navigate / read_text / wait_for
    params: Dict[str, Any]  # raw tool input, minus "reasoning"
    locator: Optional[Locator]
    result: ActionResult
    reasoning: str
    output: Optional[Any] = None  # populated for read_text


class DiscoveryStuck(Exception):
    """Raised when the LLM calls the 'stuck' control action; carries its stated reason and last observed state."""

    def __init__(self, reason: str, state: dict):
        self.reason = reason
        self.state = state
        super().__init__(reason)


class DiscoveryDeadEnd(Exception):
    """Raised when the loop cannot make progress: max_steps exceeded or the same target failed repeatedly."""

    def __init__(self, message: str, history: List[DiscoveryStep]):
        self.history = history
        super().__init__(message)


class SafetyViolation(Exception):
    """Raised when the LLM attempts to navigate off the starting app's host."""

    def __init__(self, message: str, url: str):
        self.url = url
        super().__init__(message)


# ---------------------------------------------------------------------------
# Tool vocabulary (Anthropic tool-calling schema)
# ---------------------------------------------------------------------------

LOCATOR_FALLBACK_SCHEMA = {
    "type": "object",
    "description": "A single fallback locator, tried once if the primary fails. Does not itself carry a further fallback.",
    "properties": {
        "strategy": {"type": "string", "enum": ["role", "label", "text", "css"]},
        "value": {"type": "string", "description": "Accessible name (role/label), visible text, or a CSS selector."},
        "role": {"type": "string", "description": "ARIA role, e.g. 'button', 'textbox', 'link'. Required when strategy is 'role'."},
    },
    "required": ["strategy", "value"],
}

LOCATOR_SCHEMA = {
    "type": "object",
    "properties": {
        "strategy": {"type": "string", "enum": ["role", "label", "text", "css"]},
        "value": {"type": "string", "description": "Accessible name (role/label), visible text, or a CSS selector."},
        "role": {"type": "string", "description": "ARIA role, e.g. 'button', 'textbox', 'link'. Required when strategy is 'role'."},
        "fallback": LOCATOR_FALLBACK_SCHEMA,
    },
    "required": ["strategy", "value"],
}

REASONING_PROPERTY = {
    "type": "string",
    "description": "One or two sentences on why this action moves toward the goal.",
}

TOOLS = [
    {
        "name": "click",
        "description": "Click an element identified by a locator (button, link, etc.).",
        "input_schema": {
            "type": "object",
            "properties": {"reasoning": REASONING_PROPERTY, "locator": LOCATOR_SCHEMA},
            "required": ["reasoning", "locator"],
        },
    },
    {
        "name": "type_text",
        "description": "Type text into an input or textarea identified by a locator, replacing any existing value.",
        "input_schema": {
            "type": "object",
            "properties": {
                "reasoning": REASONING_PROPERTY,
                "locator": LOCATOR_SCHEMA,
                "text": {"type": "string"},
            },
            "required": ["reasoning", "locator", "text"],
        },
    },
    {
        "name": "select_option",
        "description": "Select an option by its value in a dropdown identified by a locator.",
        "input_schema": {
            "type": "object",
            "properties": {
                "reasoning": REASONING_PROPERTY,
                "locator": LOCATOR_SCHEMA,
                "value": {"type": "string", "description": "The option's value attribute, not its visible label."},
            },
            "required": ["reasoning", "locator", "value"],
        },
    },
    {
        "name": "navigate",
        "description": "Navigate the browser directly to a URL. Only use this when no visible link or button can reach the destination.",
        "input_schema": {
            "type": "object",
            "properties": {"reasoning": REASONING_PROPERTY, "url": {"type": "string"}},
            "required": ["reasoning", "url"],
        },
    },
    {
        "name": "read_text",
        "description": "Read the current text or value of an element identified by a locator, without acting on it.",
        "input_schema": {
            "type": "object",
            "properties": {"reasoning": REASONING_PROPERTY, "locator": LOCATOR_SCHEMA},
            "required": ["reasoning", "locator"],
        },
    },
    {
        "name": "wait_for",
        "description": "Wait for a condition before proceeding: network activity to settle, an element to become visible, specific text to appear, or the URL to change.",
        "input_schema": {
            "type": "object",
            "properties": {
                "reasoning": REASONING_PROPERTY,
                "condition": {
                    "type": "object",
                    "properties": {
                        "kind": {
                            "type": "string",
                            "enum": ["network_idle", "element_visible", "text_present", "url_matches"],
                        },
                        "locator": {**LOCATOR_SCHEMA, "description": "Required when kind is 'element_visible'."},
                        "text": {"type": "string", "description": "Required when kind is 'text_present'."},
                        "pattern": {"type": "string", "description": "Substring to match against the URL. Required when kind is 'url_matches'."},
                    },
                    "required": ["kind"],
                },
                "timeout_ms": {"type": "integer", "description": "Defaults to 5000 if omitted."},
            },
            "required": ["reasoning", "condition"],
        },
    },
    {
        "name": "done",
        "description": "Declare that the goal has been fully achieved. Ends the discovery run successfully.",
        "input_schema": {
            "type": "object",
            "properties": {
                "reasoning": REASONING_PROPERTY,
                "summary": {"type": "string", "description": "Brief statement of the final, confirmed outcome."},
            },
            "required": ["reasoning"],
        },
    },
    {
        "name": "stuck",
        "description": "Declare that the goal cannot be safely achieved right now (e.g. a locator keeps failing, the page is in an unexpected state, or a decision needs human judgment). Stops the discovery run for escalation.",
        "input_schema": {
            "type": "object",
            "properties": {"reasoning": REASONING_PROPERTY},
            "required": ["reasoning"],
        },
    },
]

SYSTEM_PROMPT = """You are a browser automation agent driving a real web app toward a stated goal.

Each turn you receive the current page state as a simplified accessibility tree \
(role, accessible name, and value for every interactive element) plus a screenshot saved for evidence. \
Choose exactly one action per turn from the available tools.

Locator guidance: prefer strategy="role" with the element's ARIA role and accessible name; use \
strategy="text" for links/buttons best identified by visible text; use strategy="css" only as a \
last resort. You may set a single "fallback" locator to try if the primary one fails.

Only use "navigate" when no visible link or button can reach the destination — prefer clicking \
in-page controls. Call "done" once the goal is fully and verifiably achieved. Call "stuck" if you \
cannot safely proceed — e.g. a locator has failed repeatedly, the page shows an unexpected state, \
or the next step requires a judgment call only a human should make."""


# ---------------------------------------------------------------------------
# Locator / condition parsing (LLM tool input -> surface_adapter types)
# ---------------------------------------------------------------------------


def _parse_locator(data: Dict[str, Any]) -> Locator:
    fallback_data = data.get("fallback")
    fallback = (
        Locator(strategy=fallback_data["strategy"], value=fallback_data["value"], role=fallback_data.get("role"))
        if fallback_data
        else None
    )
    return Locator(strategy=data["strategy"], value=data["value"], role=data.get("role"), fallback=fallback)


def _parse_wait_condition(data: Dict[str, Any]) -> WaitCondition:
    locator = _parse_locator(data["locator"]) if "locator" in data else None
    return WaitCondition(kind=data["kind"], locator=locator, text=data.get("text"), pattern=data.get("pattern"))


# ---------------------------------------------------------------------------
# Action execution
# ---------------------------------------------------------------------------


def _execute_action(adapter: SurfaceAdapter, name: str, tool_input: Dict[str, Any]):
    """Run one tool call against the adapter. Returns (ActionResult, locator_or_None, output_or_None)."""
    if name == "click":
        locator = _parse_locator(tool_input["locator"])
        return adapter.click(locator), locator, None

    if name == "type_text":
        locator = _parse_locator(tool_input["locator"])
        return adapter.type_text(locator, tool_input["text"]), locator, None

    if name == "select_option":
        locator = _parse_locator(tool_input["locator"])
        return adapter.select_option(locator, tool_input["value"]), locator, None

    if name == "navigate":
        return adapter.navigate(tool_input["url"]), None, None

    if name == "read_text":
        locator = _parse_locator(tool_input["locator"])
        try:
            text = adapter.read_text(locator)
            return ActionResult(success=True), locator, text
        except LocatorNotFoundError as exc:
            return ActionResult(success=False, error_type="locator_not_found", detail=str(exc)), locator, None

    if name == "wait_for":
        condition = _parse_wait_condition(tool_input["condition"])
        timeout_ms = tool_input.get("timeout_ms", 5000)
        return adapter.wait_for(condition, timeout_ms=timeout_ms), condition.locator, None

    raise ValueError(f"Unknown action: {name!r}")


def _failure_key(name: str, locator: Optional[Locator], params: Dict[str, Any]):
    if locator is not None:
        return (name, locator.strategy, locator.value)
    return (name, tuple(sorted(params.items())))


def _format_tool_result(name: str, result: ActionResult, output: Optional[Any]) -> str:
    if name == "read_text" and result.success:
        return output if output else ""
    if result.success:
        return "OK"
    return f"FAILED ({result.error_type}): {result.detail}"


def _format_observation(observation: Dict[str, Any]) -> str:
    return "Current page state:\n" + json.dumps(observation, indent=2)


# ---------------------------------------------------------------------------
# Evidence logging
# ---------------------------------------------------------------------------


def _append_transcript(path: Path, record: Dict[str, Any]) -> None:
    with path.open("a") as f:
        f.write(json.dumps(record, default=str) + "\n")


def _base_record(turn: int, observation: Dict[str, Any], tool_name: str, tool_input: Dict[str, Any], reasoning: str) -> Dict[str, Any]:
    return {
        "turn": turn,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "observation": observation,
        "llm_tool_call": {"name": tool_name, "input": tool_input},
        "reasoning": reasoning,
    }


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------


def run_discovery(
    goal: str,
    start_url: str,
    adapter: SurfaceAdapter,
    *,
    model: str = DEFAULT_MODEL,
    max_steps: int = DEFAULT_MAX_STEPS,
    client: Optional[anthropic.Anthropic] = None,
) -> List[DiscoveryStep]:
    """Run the observe-decide-act loop until the goal is achieved, the model gets stuck, or a limit is hit.

    Returns the ordered list of successfully executed DiscoveryStep records on success.
    Raises DiscoveryStuck, DiscoveryDeadEnd, or SafetyViolation otherwise.
    """
    client = client or anthropic.Anthropic()
    config = load_guardrails_config()

    run_id = time.strftime("%Y%m%dT%H%M%S")
    run_dir = Path("evidence") / f"discovery_run_{run_id}"
    run_dir.mkdir(parents=True, exist_ok=True)
    adapter.evidence_dir = run_dir  # keep screenshots in this run's folder, not a shared one
    (run_dir / "goal.txt").write_text(goal)
    transcript_path = run_dir / "transcript.jsonl"

    allowed, reason = check_allowlist(start_url, "navigate", config)
    if not allowed:
        raise SafetyViolation(f"Refusing to navigate to {start_url!r}: {reason}", start_url)

    nav_result = adapter.navigate(start_url)
    if not nav_result.success:
        raise DiscoveryDeadEnd(f"Failed to load starting URL {start_url!r}: {nav_result.detail}", [])

    history: List[DiscoveryStep] = []
    messages: List[Dict[str, Any]] = []
    pending_tool_result = None  # (tool_use_id, content) queued for the next user turn
    failure_key = None
    failure_count = 0

    for turn in range(1, max_steps + 1):
        observation = adapter.observe()

        user_content = []
        if pending_tool_result is not None:
            tool_use_id, content = pending_tool_result
            user_content.append({"type": "tool_result", "tool_use_id": tool_use_id, "content": content})

        observation_text = _format_observation(observation)
        if turn == 1:
            observation_text = f"Goal: {goal}\n\n{observation_text}"
        user_content.append({"type": "text", "text": observation_text})

        messages.append({"role": "user", "content": user_content})

        response = client.messages.create(
            model=model,
            max_tokens=2048,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            tool_choice={"type": "any", "disable_parallel_tool_use": True},
            messages=messages,
        )
        messages.append({"role": "assistant", "content": response.content})

        tool_use = next((block for block in response.content if block.type == "tool_use"), None)
        if tool_use is None:
            raise DiscoveryDeadEnd(
                f"Turn {turn}: model response contained no tool call (stop_reason={response.stop_reason}).",
                history,
            )

        name = tool_use.name
        tool_input = tool_use.input
        reasoning = tool_input.get("reasoning", "")
        record = _base_record(turn, observation, name, tool_input, reasoning)

        if name == "done":
            record["action"] = "done"
            record["summary"] = tool_input.get("summary")
            _append_transcript(transcript_path, record)
            return history

        if name == "stuck":
            record["action"] = "stuck"
            _append_transcript(transcript_path, record)

            escalation.raise_intervention(
                run_id=run_id,
                capability_or_goal=goal,
                current_step_id=(history[-1].step_number if history else None),
                reason=reasoning or "Model reported being stuck.",
                adapter=adapter,
            )
            escalation.set_control(run_id, "human")
            print(
                f"Escalated to human operator — visit "
                f"http://127.0.0.1:8421/take-control?run_id={run_id} to take over. "
                f"Waiting for control to be handed back..."
            )
            control_returned = escalation.wait_for_control_return(run_id)

            if control_returned:
                resumed_observation = adapter.observe()
                _append_transcript(
                    transcript_path,
                    {
                        "turn": turn,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "action": "human_intervention_resolved",
                        "note": "Human intervention occurred; control was returned to automation.",
                        "observation": resumed_observation,
                    },
                )
                pending_tool_result = (
                    tool_use.id,
                    "Escalated to a human operator due to being stuck. The human took manual action and "
                    "handed control back to automation.",
                )
                continue

            raise DiscoveryStuck(
                f"{reasoning or 'Model reported being stuck.'} Escalation timed out after waiting for human handoff.",
                observation,
            )

        if name == "navigate":
            allowed, reason = check_allowlist(tool_input["url"], "navigate", config)
            if not allowed:
                record["action"] = "navigate"
                record["blocked"] = True
                record["safety_violation"] = reason
                _append_transcript(transcript_path, record)
                raise SafetyViolation(
                    f"Refusing to navigate to {tool_input['url']!r}: {reason}",
                    tool_input["url"],
                )

        result, locator, output = _execute_action(adapter, name, tool_input)

        post_action_url = adapter.observe()["url"]
        allowed, reason = check_allowlist(post_action_url, name, config)
        if not allowed:
            record["action"] = name
            record["blocked"] = True
            record["safety_violation"] = reason
            _append_transcript(transcript_path, record)
            raise SafetyViolation(
                f"Action {name!r} navigated off-allowlist to {post_action_url!r}: {reason}",
                post_action_url,
            )

        params = {k: v for k, v in tool_input.items() if k != "reasoning"}
        step = DiscoveryStep(
            step_number=turn, action=name, params=params, locator=locator, result=result, reasoning=reasoning, output=output
        )
        history.append(step)

        record["action"] = name
        record["action_result"] = asdict(result)
        _append_transcript(transcript_path, record)

        key = _failure_key(name, locator, params)
        if not result.success:
            failure_count = failure_count + 1 if key == failure_key else 1
            failure_key = key
            if failure_count >= CONSECUTIVE_FAILURE_LIMIT:
                raise DiscoveryDeadEnd(
                    f"{CONSECUTIVE_FAILURE_LIMIT} consecutive failures on the same target: {key}", history
                )
        else:
            failure_key = None
            failure_count = 0

        pending_tool_result = (tool_use.id, _format_tool_result(name, result, output))

    raise DiscoveryDeadEnd(f"Exceeded max_steps ({max_steps}) without reaching 'done'.", history)

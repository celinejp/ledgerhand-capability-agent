"""Observe/click/type/read/wait_for wrapper over Playwright, providing a stable interface between the agent and the live browser UI.

This is the seam a desktop or legacy-web adapter would replace to reuse the
same discovery loop and replay engine against a different kind of surface:
as long as a replacement implements the same Locator/ActionResult contract
and observe()/click()/type_text()/select_option()/navigate()/read_text()/
wait_for() methods, nothing above this layer needs to change.
"""

import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional, Union

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Locator as PWLocator
from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

# ---------------------------------------------------------------------------
# Shared data contract (also used, unmodified, by discovery and replay)
# ---------------------------------------------------------------------------

LocatorStrategy = Literal["role", "label", "text", "css"]
WaitKind = Literal["network_idle", "element_visible", "text_present", "url_matches"]


@dataclass
class Locator:
    strategy: LocatorStrategy
    value: str  # role's accessible name, label text, text content, or CSS selector
    role: Optional[str] = None  # required when strategy == "role", e.g. "button", "textbox"
    fallback: Optional["Locator"] = None  # tried once if the primary locator fails


@dataclass
class ActionResult:
    success: bool
    error_type: Optional[str] = None  # e.g. "locator_not_found", "timeout", "unknown"
    detail: Optional[str] = None  # human-readable: what was expected vs. observed


@dataclass
class WaitCondition:
    kind: WaitKind
    locator: Optional[Locator] = None  # required for "element_visible"
    text: Optional[str] = None  # required for "text_present"
    pattern: Optional[str] = None  # required for "url_matches"; substring match


class LocatorNotFoundError(Exception):
    """Raised when a Locator's primary strategy and its single fallback both fail to resolve."""


# ---------------------------------------------------------------------------
# Accessibility-tree extraction
#
# Playwright's legacy `page.accessibility` API has been removed; the
# supported replacement is Locator.aria_snapshot(), which returns a
# browser-computed (not hand-approximated) YAML-like tree of role/name
# pairs, correctly resolving things like implicit label-wrapped inputs.
# We flatten it down to the interactive elements observe() cares about.
# ---------------------------------------------------------------------------

INTERACTIVE_ROLES = {
    "button",
    "link",
    "textbox",
    "searchbox",
    "spinbutton",
    "combobox",
    "listbox",
    "checkbox",
    "radio",
    "switch",
    "menuitem",
}

_SNAPSHOT_LINE_RE = re.compile(r'^-\s+(?P<role>[A-Za-z]+) "(?P<name>[^"]*)"(?P<tail>.*)$')
_SNAPSHOT_VALUE_RE = re.compile(r': "([^"]*)"')


def _parse_accessibility_snapshot(snapshot_text: str) -> list[dict]:
    """Flatten Playwright's aria_snapshot() YAML-like text into role/name/value dicts."""
    elements: list[dict] = []
    stack: list[tuple[int, Optional[dict]]] = []  # (depth, element or None)

    for raw_line in snapshot_text.splitlines():
        stripped = raw_line.lstrip(" ")
        depth = (len(raw_line) - len(stripped)) // 2
        while stack and stack[-1][0] >= depth:
            stack.pop()
        parent = stack[-1][1] if stack else None

        match = _SNAPSHOT_LINE_RE.match(stripped)
        if not match:
            stack.append((depth, None))
            continue

        role, name, tail = match.group("role"), match.group("name"), match.group("tail")

        if role == "option":
            if "[selected]" in tail and parent is not None and parent["role"] in ("combobox", "listbox"):
                parent["value"] = name
            stack.append((depth, None))
            continue

        if role not in INTERACTIVE_ROLES:
            stack.append((depth, None))
            continue

        value_match = _SNAPSHOT_VALUE_RE.search(tail)
        if value_match:
            value = value_match.group(1)
        elif "[checked]" in tail:
            value = "true"
        else:
            value = ""

        element = {"role": role, "name": name, "value": value}
        elements.append(element)
        stack.append((depth, element))

    return elements


# ---------------------------------------------------------------------------
# SurfaceAdapter
# ---------------------------------------------------------------------------

DEFAULT_TIMEOUT_MS = 5000
DEFAULT_SETTLE_TIMEOUT_MS = 2000


class SurfaceAdapter:
    def __init__(
        self,
        page: Page,
        evidence_dir: Union[str, Path] = "evidence",
        default_timeout_ms: int = DEFAULT_TIMEOUT_MS,
    ):
        self.page = page
        self.evidence_dir = Path(evidence_dir)
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        self.default_timeout_ms = default_timeout_ms
        self._screenshot_count = 0

    # -- observation ----------------------------------------------------

    def observe(self) -> dict:
        """Return URL, title, and the interactive-element accessibility tree; save a screenshot as a side effect."""
        self._save_screenshot()
        snapshot_text = self.page.locator("body").aria_snapshot()
        return {
            "url": self.page.url,
            "title": self.page.title(),
            "elements": _parse_accessibility_snapshot(snapshot_text),
        }

    def _save_screenshot(self) -> Path:
        self._screenshot_count += 1
        filename = f"{time.strftime('%Y%m%dT%H%M%S')}_{self._screenshot_count:04d}.png"
        path = self.evidence_dir / filename
        self.page.screenshot(path=str(path))
        return path

    # -- locator resolution ----------------------------------------------

    def _build_pw_locator(self, locator: Locator) -> PWLocator:
        if locator.strategy == "role":
            return self.page.get_by_role(locator.role, name=locator.value)
        if locator.strategy == "label":
            return self.page.get_by_label(locator.value)
        if locator.strategy == "text":
            return self.page.get_by_text(locator.value)
        if locator.strategy == "css":
            return self.page.locator(locator.value)
        raise ValueError(f"Unknown locator strategy: {locator.strategy!r}")

    def _try_resolve(self, locator: Locator, timeout_ms: int) -> Optional[PWLocator]:
        pw_locator = self._build_pw_locator(locator).first
        try:
            pw_locator.wait_for(state="visible", timeout=timeout_ms)
            return pw_locator
        except PlaywrightTimeoutError:
            return None

    def locate_element(self, locator: Locator, timeout_ms: Optional[int] = None) -> PWLocator:
        """Resolve a Locator to a single visible Playwright element.

        Tries the primary strategy, then the locator's single `fallback`
        (never the fallback's own fallback) if the primary fails to
        resolve. Raises LocatorNotFoundError naming every strategy tried.
        """
        timeout_ms = self.default_timeout_ms if timeout_ms is None else timeout_ms
        tried = [f"{locator.strategy}:{locator.value}"]

        resolved = self._try_resolve(locator, timeout_ms)
        if resolved is not None:
            return resolved

        if locator.fallback is not None:
            tried.append(f"{locator.fallback.strategy}:{locator.fallback.value}")
            resolved = self._try_resolve(locator.fallback, timeout_ms)
            if resolved is not None:
                return resolved

        raise LocatorNotFoundError(f"Could not resolve locator after trying: {', '.join(tried)}")

    # -- actions ----------------------------------------------------------

    def _settle(self, timeout_ms: int = DEFAULT_SETTLE_TIMEOUT_MS) -> None:
        """Best-effort wait for network idle after an action; a lingering connection shouldn't fail the action."""
        try:
            self.page.wait_for_load_state("networkidle", timeout=timeout_ms)
        except PlaywrightTimeoutError:
            pass

    def _run_action(self, fn) -> ActionResult:
        try:
            fn()
            return ActionResult(success=True)
        except LocatorNotFoundError as exc:
            return ActionResult(success=False, error_type="locator_not_found", detail=str(exc))
        except PlaywrightTimeoutError as exc:
            return ActionResult(success=False, error_type="timeout", detail=str(exc))
        except Exception as exc:  # noqa: BLE001 - classify any remaining Playwright/browser error for replay
            return ActionResult(success=False, error_type="unknown", detail=str(exc))

    def click(self, locator: Locator) -> ActionResult:
        def _do() -> None:
            self.locate_element(locator).click()
            self._settle()

        return self._run_action(_do)

    def type_text(self, locator: Locator, text: str) -> ActionResult:
        def _do() -> None:
            self.locate_element(locator).fill(text)
            self._settle()

        return self._run_action(_do)

    def select_option(self, locator: Locator, value: str) -> ActionResult:
        def _do() -> None:
            self.locate_element(locator).select_option(value)
            self._settle()

        return self._run_action(_do)

    def navigate(self, url: str) -> ActionResult:
        def _do() -> None:
            self.page.goto(url)
            self._settle()

        return self._run_action(_do)

    def read_text(self, locator: Locator) -> str:
        """Return an element's value (inputs/selects) or text content. Raises LocatorNotFoundError if unresolved."""
        target = self.locate_element(locator)
        try:
            return target.input_value()
        except PlaywrightError:
            return target.inner_text()

    def wait_for(self, condition: WaitCondition, timeout_ms: int = DEFAULT_TIMEOUT_MS) -> ActionResult:
        def _do() -> None:
            if condition.kind == "network_idle":
                self.page.wait_for_load_state("networkidle", timeout=timeout_ms)
            elif condition.kind == "element_visible":
                if condition.locator is None:
                    raise ValueError("element_visible wait condition requires a locator")
                self.locate_element(condition.locator, timeout_ms=timeout_ms)
            elif condition.kind == "text_present":
                if condition.text is None:
                    raise ValueError("text_present wait condition requires text")
                self.page.get_by_text(condition.text).first.wait_for(state="visible", timeout=timeout_ms)
            elif condition.kind == "url_matches":
                if condition.pattern is None:
                    raise ValueError("url_matches wait condition requires a pattern")
                pattern = condition.pattern
                self.page.wait_for_url(lambda url: pattern in url, timeout=timeout_ms)
            else:
                raise ValueError(f"Unknown wait condition kind: {condition.kind!r}")

        return self._run_action(_do)

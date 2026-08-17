"""Minimal Flask control panel: web forms wrapped around the real run_discovery()/replay_artifact() functions — not a new execution path."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from flask import Flask, render_template_string, request  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

from compiler import ARTIFACTS_DIR, load_artifact  # noqa: E402
from discovery_loop import (  # noqa: E402
    DEFAULT_MAX_STEPS,
    DEFAULT_MODEL,
    DiscoveryDeadEnd,
    DiscoveryStuck,
    SafetyViolation,
    run_discovery,
)
from replay_engine import replay_artifact  # noqa: E402
from surface_adapter import SurfaceAdapter  # noqa: E402

app = Flask(__name__)

EVIDENCE_ROOT = Path("evidence")

DEFAULT_GOAL = (
    "Look up member 1003 and open a new checking sub-account with a $75 "
    "opening deposit, reaching the confirmation screen."
)
DEFAULT_START_URL = "http://127.0.0.1:8420/members/search"


def _web_confirmation_already_granted(step, context):
    """Stand-in for guardrails.request_confirmation() on the web path.

    Always returns True. Safe only because /replay already required an explicit "confirmed"
    checkbox in the web form, and refused to call replay_artifact() at all otherwise — see
    replay_artifact()'s docstring in replay_engine.py for why this exists (a real terminal
    input() would hang the whole server process, not just one request).
    """
    return True


# ---------------------------------------------------------------------------
# Styling (matches operator_console/app.py)
# ---------------------------------------------------------------------------

STYLE = """
<style>
  :root {
    --accent: #0d9488;
    --accent-dark: #0f766e;
    --bg: #f1f5f9;
    --card-bg: #ffffff;
    --text: #1e293b;
    --text-muted: #64748b;
    --border: #e2e8f0;
    --warn-bg: #fef3c7;
    --warn-text: #92400e;
    --ok-bg: #e2e8f0;
    --ok-text: #475569;
    --bad-bg: #fee2e2;
    --bad-text: #991b1b;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    background: var(--bg);
    color: var(--text);
  }
  .topbar {
    background: var(--accent-dark);
    color: #fff;
    padding: 16px 24px;
    display: flex;
    align-items: baseline;
    gap: 12px;
  }
  .topbar h1 { font-size: 18px; margin: 0; font-weight: 600; letter-spacing: 0.2px; }
  .topbar .subtitle { font-size: 13px; color: rgba(255, 255, 255, 0.75); }
  .container { max-width: 900px; margin: 32px auto; padding: 0 24px 48px; }
  .card {
    background: var(--card-bg);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 24px;
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.04);
    margin-bottom: 20px;
  }
  .section-title {
    font-size: 13px;
    text-transform: uppercase;
    letter-spacing: 0.4px;
    color: var(--text-muted);
    margin: 0 0 16px;
    font-weight: 600;
  }
  label { display: block; font-size: 13px; font-weight: 500; margin: 14px 0 6px; }
  label:first-child { margin-top: 0; }
  input[type="text"], input[type="number"], select, textarea {
    width: 100%;
    padding: 8px 12px;
    border: 1px solid var(--border);
    border-radius: 6px;
    font-size: 14px;
    font-family: inherit;
    background: #fff;
  }
  textarea { resize: vertical; line-height: 1.4; }
  input[type="text"]:focus, input[type="number"]:focus, select:focus, textarea:focus {
    outline: 2px solid var(--accent);
    outline-offset: -1px;
  }
  .hint { font-size: 12px; color: var(--text-muted); margin-top: 4px; }
  .checkbox-row { display: flex; align-items: center; gap: 8px; margin: 16px 0; }
  .checkbox-row input { width: auto; }
  .checkbox-row label { margin: 0; font-weight: 500; }
  .btn {
    display: inline-block;
    background: var(--accent);
    color: #fff;
    padding: 9px 18px;
    border-radius: 6px;
    border: none;
    font-size: 14px;
    font-weight: 500;
    text-decoration: none;
    cursor: pointer;
    transition: background 0.15s ease;
    margin-top: 16px;
  }
  .btn:hover { background: var(--accent-dark); }
  .back-link { color: var(--text-muted); text-decoration: none; font-size: 14px; }
  .back-link:hover { color: var(--text); text-decoration: underline; }
  .field-table { width: 100%; border-collapse: collapse; }
  .field-table td { padding: 8px 4px; border-bottom: 1px solid var(--border); font-size: 14px; vertical-align: top; }
  .field-table td:first-child { color: var(--text-muted); width: 160px; font-weight: 500; }
  .badge {
    display: inline-block;
    padding: 3px 10px;
    border-radius: 999px;
    font-size: 12px;
    font-weight: 600;
  }
  .badge-ok { background: var(--ok-bg); color: var(--ok-text); }
  .badge-good { background: #dcfce7; color: #166534; }
  .badge-warn { background: var(--warn-bg); color: var(--warn-text); }
  .badge-bad { background: var(--bad-bg); color: var(--bad-text); }
  .running-note {
    background: var(--warn-bg);
    color: var(--warn-text);
    border-radius: 6px;
    padding: 10px 14px;
    font-size: 13px;
    margin-top: 16px;
  }
  .btn:disabled { opacity: 0.65; cursor: not-allowed; }
  .spinner {
    display: inline-block;
    width: 11px;
    height: 11px;
    margin-right: 6px;
    border: 2px solid rgba(255, 255, 255, 0.45);
    border-top-color: #fff;
    border-radius: 50%;
    animation: spin 0.7s linear infinite;
    vertical-align: middle;
  }
  @keyframes spin { to { transform: rotate(360deg); } }
</style>
"""

FAVICON = (
    "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E"
    "%3Ccircle cx='50' cy='50' r='45' fill='%230d9488'/%3E"
    "%3Cpath d='M40 30 L72 50 L40 70 Z' fill='white'/%3E%3C/svg%3E"
)

OUTER_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
<title>{{ title }}</title>
<link rel="icon" type="image/svg+xml" href=\"""" + FAVICON + """\">
""" + STYLE + """
</head>
<body>
<div class="topbar">
<h1>Control Panel</h1>
{% set subtitle = title|replace("Control Panel", "")|trim(" —") %}
{% if subtitle %}<span class="subtitle">{{ subtitle }}</span>{% endif %}
</div>
<div class="container">
{{ content|safe }}
</div>
<script>
document.querySelectorAll("form[data-loading]").forEach(function (form) {
    form.addEventListener("submit", function () {
        var btn = form.querySelector('button[type="submit"]');
        if (!btn) return;
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner"></span>' + form.getAttribute("data-loading");
        var titleOverride = form.getAttribute("data-loading-title");
        if (titleOverride) document.title = titleOverride;
    });
});
</script>
</body>
</html>
"""

HOME_TEMPLATE = """
<div class="card">
<p class="section-title">Run Discovery</p>
<form method="post" action="/run-discovery" data-loading="Running..." data-loading-title="Control Panel — Running Discovery...">
<label>Goal</label>
<textarea name="goal" rows="2" placeholder="{{ default_goal }}"></textarea>
<div class="hint">Leave blank to use the example goal shown as a placeholder.</div>
<label>Start URL</label>
<input type="text" name="start_url" value="{{ default_start_url }}">
<div class="running-note">Running discovery calls the real Claude API and drives a real
browser turn by turn &mdash; this can take a minute or more, and the page will stay
unresponsive until it finishes. That's expected, not a hang.</div>
<button type="submit" class="btn">Run</button>
</form>
</div>

<div class="card">
<p class="section-title">Replay Artifact</p>
<form method="post" action="/replay" data-loading="Replaying..." data-loading-title="Control Panel — Replaying...">
<label>Artifact</label>
<select name="artifact_filename" id="artifact-select" onchange="showArtifactFields(this.value)">
{% for a in artifacts %}
<option value="{{ a.filename }}">{{ a.filename }}</option>
{% endfor %}
</select>

{% for a in artifacts %}
<div class="artifact-fields" data-artifact="{{ a.filename }}" style="{{ '' if loop.first else 'display:none' }}">
{% for inp in a.inputs %}
<label>{{ inp.name }}{% if inp.required %} *{% endif %}</label>
{% if inp['values'] %}
<select name="param_{{ inp.name }}">
{% for v in inp['values'] %}
<option value="{{ v }}">{{ v }}</option>
{% endfor %}
</select>
{% elif inp.min is not none %}
<input type="number" name="param_{{ inp.name }}" min="{{ inp.min }}">
{% else %}
<input type="text" name="param_{{ inp.name }}">
{% endif %}
<div class="hint">{{ inp.description }}</div>
{% endfor %}
{% if not a.inputs %}
<div class="hint">This artifact has no declared inputs.</div>
{% endif %}
</div>
{% endfor %}

<button type="submit" class="btn">Replay</button>
</form>
</div>

<script>
function showArtifactFields(filename) {
    document.querySelectorAll(".artifact-fields").forEach(function (el) {
        el.style.display = (el.getAttribute("data-artifact") === filename) ? "" : "none";
    });
}
</script>
"""

DISCOVERY_RESULT_TEMPLATE = """
<div class="card">
<p class="section-title">Discovery Result</p>
<table class="field-table">
<tr><td>Goal</td><td>{{ goal }}</td></tr>
<tr><td>Start URL</td><td>{{ start_url }}</td></tr>
<tr><td>Outcome</td><td>
{% if outcome == "done" %}
<span class="badge badge-good">&#10003; Done</span>
{% elif outcome == "stuck" %}
<span class="badge badge-warn">&#9888; Stuck</span>
{% else %}
<span class="badge badge-bad">&#10007; {{ outcome }}</span>
{% endif %}
</td></tr>
<tr><td>Steps</td><td>{{ step_count }}</td></tr>
<tr><td>Detail</td><td>{{ detail }}</td></tr>
{% if run_dir %}
<tr><td>Evidence</td><td>{{ run_dir }}</td></tr>
{% endif %}
</table>
</div>
<a href="/" class="back-link">&larr; Back to control panel</a>
"""

CONFIRM_TEMPLATE = """
<div class="card">
<p class="section-title">Confirmation Required</p>
<p>This artifact includes an irreversible step:</p>
<table class="field-table">
{% for s in irreversible_steps %}
<tr><td>Step {{ s.step_id }}</td><td>{{ s.action }}{% if s.locator %} on {{ s.locator.strategy }}:{{ s.locator.value }}{% endif %}</td></tr>
{% endfor %}
</table>
<form method="post" action="/replay" data-loading="Replaying..." data-loading-title="Control Panel — Replaying...">
{% for key, value in hidden_fields %}
<input type="hidden" name="{{ key }}" value="{{ value }}">
{% endfor %}
<div class="checkbox-row">
<input type="checkbox" id="confirmed" name="confirmed" value="yes">
<label for="confirmed">I confirm this irreversible action</label>
</div>
<button type="submit" class="btn">Replay</button>
</form>
</div>
<a href="/" class="back-link">&larr; Back to control panel</a>
"""

REPLAY_RESULT_TEMPLATE = """
<div class="card">
<p class="section-title">Replay Result</p>
<table class="field-table">
<tr><td>Artifact</td><td>{{ artifact_filename }}</td></tr>
<tr><td>Params</td><td>{{ params }}</td></tr>
<tr><td>Status</td><td>
{% if result.status == "success" %}
<span class="badge badge-good">&#10003; Success</span>
{% elif result.status == "business_outcome" %}
<span class="badge badge-warn">&#9888; Business Outcome</span>
{% else %}
<span class="badge badge-bad">&#10007; Failure</span>
{% endif %}
</td></tr>
{% if result.outputs %}
<tr><td>Outputs</td><td>{{ result.outputs }}</td></tr>
{% endif %}
{% if result.outcome %}
<tr><td>Outcome</td><td>{{ result.outcome }}</td></tr>
<tr><td>Message</td><td>{{ result.message }}</td></tr>
{% endif %}
{% if result.failure_type %}
<tr><td>Failure Type</td><td>{{ result.failure_type }}</td></tr>
<tr><td>Expected</td><td>{{ result.expected }}</td></tr>
<tr><td>Observed</td><td>{{ result.observed }}</td></tr>
{% endif %}
{% if run_dir %}
<tr><td>Evidence</td><td>{{ run_dir }}</td></tr>
{% endif %}
</table>
</div>
<a href="/" class="back-link">&larr; Back to control panel</a>
"""


def render_page(title, template, **context):
    inner = render_template_string(template, **context)
    return render_template_string(OUTER_TEMPLATE, title=title, content=inner)


def _latest_dir(pattern: str):
    dirs = sorted(EVIDENCE_ROOT.glob(pattern), key=lambda p: p.stat().st_mtime)
    return str(dirs[-1]) if dirs else None


def _artifact_infos():
    infos = []
    for path in sorted(ARTIFACTS_DIR.glob("*.json")):
        artifact = load_artifact(str(path))
        infos.append(
            {
                "filename": path.name,
                "artifact_id": artifact.artifact_id,
                "inputs": [
                    {
                        "name": i.name,
                        "required": i.required,
                        "description": i.description,
                        "values": i.values,
                        "min": i.min,
                    }
                    for i in artifact.inputs
                ],
            }
        )
    return infos


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.route("/", methods=["GET"])
def home():
    return render_page(
        "Control Panel — Dashboard",
        HOME_TEMPLATE,
        artifacts=_artifact_infos(),
        default_goal=DEFAULT_GOAL,
        default_start_url=DEFAULT_START_URL,
    )


@app.route("/run-discovery", methods=["POST"])
def run_discovery_route():
    goal = request.form.get("goal", "").strip() or DEFAULT_GOAL
    start_url = request.form.get("start_url", "").strip() or DEFAULT_START_URL

    outcome = "done"
    step_count = 0
    detail = ""

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        adapter = SurfaceAdapter(page)
        try:
            steps = run_discovery(goal, start_url, adapter, model=DEFAULT_MODEL, max_steps=DEFAULT_MAX_STEPS)
            outcome = "done"
            step_count = len(steps)
            detail = "Goal achieved."
        except DiscoveryStuck as exc:
            outcome = "stuck"
            detail = exc.reason
        except DiscoveryDeadEnd as exc:
            outcome = "dead_end"
            step_count = len(exc.history)
            detail = str(exc)
        except SafetyViolation as exc:
            outcome = "safety_violation"
            detail = str(exc)
        finally:
            browser.close()

    return render_page(
        "Control Panel — Discovery Result",
        DISCOVERY_RESULT_TEMPLATE,
        goal=goal,
        start_url=start_url,
        outcome=outcome,
        step_count=step_count,
        detail=detail,
        run_dir=_latest_dir("discovery_run_*"),
    )


@app.route("/replay", methods=["POST"])
def replay_route():
    artifact_filename = request.form.get("artifact_filename", "")
    artifact_path = ARTIFACTS_DIR / artifact_filename
    artifact = load_artifact(str(artifact_path))

    irreversible_steps = [s for s in artifact.steps if s.risk_class == "irreversible"]
    confirmed = request.form.get("confirmed") == "yes"

    if irreversible_steps and not confirmed:
        hidden_fields = [("artifact_filename", artifact_filename)]
        for input_param in artifact.inputs:
            field_name = f"param_{input_param.name}"
            hidden_fields.append((field_name, request.form.get(field_name, "")))
        return render_page(
            "Control Panel — Confirm",
            CONFIRM_TEMPLATE,
            irreversible_steps=irreversible_steps,
            hidden_fields=hidden_fields,
        )

    params = {}
    for input_param in artifact.inputs:
        value = request.form.get(f"param_{input_param.name}", "").strip()
        if value:
            params[input_param.name] = value

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        adapter = SurfaceAdapter(page)
        result = replay_artifact(
            artifact,
            params,
            adapter,
            require_approval=True,
            confirmation_callback=_web_confirmation_already_granted,
        )
        browser.close()

    return render_page(
        "Control Panel — Replay Result",
        REPLAY_RESULT_TEMPLATE,
        artifact_filename=artifact_filename,
        params=params,
        result=result,
        run_dir=_latest_dir("replay_run_*"),
    )


if __name__ == "__main__":
    app.run(debug=True, port=8422)

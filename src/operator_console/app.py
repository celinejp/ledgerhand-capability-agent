"""Minimal Flask app giving a human operator a control surface to take over and hand back the live browser session."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flask import Flask, abort, render_template_string, request, send_file  # noqa: E402

import escalation  # noqa: E402

app = Flask(__name__)

EVIDENCE_ROOT = Path("evidence")

OUTER_TEMPLATE = """<!DOCTYPE html>
<html>
<head><title>{{ title }}</title></head>
<body>
<table border="1" cellpadding="8" cellspacing="0" width="100%">
<tr><td><h1>{{ title }}</h1></td></tr>
<tr><td>{{ content|safe }}</td></tr>
</table>
</body>
</html>
"""

LIST_TEMPLATE = """
<table border="1" cellpadding="4" cellspacing="0">
<tr><td><b>Run ID</b></td><td><b>Status</b></td><td><b>Reason</b></td><td></td></tr>
{% for i in interventions %}
<tr>
<td>{{ i.run_id }}</td>
<td>{{ i.status }}</td>
<td>{{ i.reason }}</td>
<td><a href="/?run_id={{ i.run_id }}">View</a></td>
</tr>
{% else %}
<tr><td colspan="4">No interventions found under evidence/.</td></tr>
{% endfor %}
</table>
"""

DETAIL_TEMPLATE = """
<table border="0" cellpadding="4" cellspacing="0">
<tr><td>Run ID</td><td>{{ intervention.run_id }}</td></tr>
<tr><td>Capability / Goal</td><td>{{ intervention.capability_or_goal }}</td></tr>
<tr><td>Current Step</td><td>{{ intervention.current_step_id }}</td></tr>
<tr><td>Reason</td><td>{{ intervention.reason }}</td></tr>
<tr><td>Status</td><td>{{ intervention.status }}</td></tr>
<tr><td>Created At</td><td>{{ intervention.created_at }}</td></tr>
</table>
<p><img src="/screenshot?run_id={{ intervention.run_id }}" width="800" alt="run screenshot"></p>
<p><a href="/take-control?run_id={{ intervention.run_id }}">Take Control</a></p>
<p><a href="/">Back to dashboard</a></p>
"""

TAKE_CONTROL_TEMPLATE = """
<p>Controller is now: <b>human</b></p>
<p><img id="live-screenshot" src="/screenshot?run_id={{ run_id }}" width="800" alt="live screenshot"></p>
<script>
setInterval(function () {
    document.getElementById("live-screenshot").src = "/screenshot?run_id={{ run_id }}&t=" + Date.now();
}, 2000);
</script>
<form method="post" action="/take-control?run_id={{ run_id }}">
<table border="0" cellpadding="4" cellspacing="0">
<tr><td><label>Action taken <input type="text" name="action_taken"></label></td></tr>
<tr><td><button type="submit">Log Action</button></td></tr>
</table>
</form>
<h3>Actions logged this session</h3>
<table border="1" cellpadding="4" cellspacing="0">
<tr><td><b>Timestamp</b></td><td><b>Action</b></td></tr>
{% for a in actions %}
<tr><td>{{ a.timestamp }}</td><td>{{ a.action_description }}</td></tr>
{% else %}
<tr><td colspan="2">No actions logged yet.</td></tr>
{% endfor %}
</table>
<p><a href="/hand-back?run_id={{ run_id }}">Hand Control Back to Automation</a></p>
"""

HAND_BACK_TEMPLATE = """
<p>Control returned to automation for run <b>{{ run_id }}</b>.</p>
<p>Intervention resolved: "Handed back by operator".</p>
<p><a href="/">Back to dashboard</a></p>
"""


def render_page(title, template, **context):
    inner = render_template_string(template, **context)
    return render_template_string(OUTER_TEMPLATE, title=title, content=inner)


def _load_intervention(run_id):
    path = EVIDENCE_ROOT / run_id / "intervention.json"
    if not path.exists():
        return None
    return json.loads(path.read_text())


def _load_human_actions(run_id):
    path = EVIDENCE_ROOT / run_id / "human_actions.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


@app.route("/", methods=["GET"])
def dashboard():
    run_id = request.args.get("run_id")
    if run_id:
        intervention = _load_intervention(run_id)
        if intervention is None:
            abort(404, f"No intervention.json found for run_id {run_id!r}")
        return render_page(f"Intervention: {run_id}", DETAIL_TEMPLATE, intervention=intervention)

    interventions = [json.loads(path.read_text()) for path in sorted(EVIDENCE_ROOT.glob("*/intervention.json"))]
    return render_page("Operator Console — Interventions", LIST_TEMPLATE, interventions=interventions)


@app.route("/screenshot", methods=["GET"])
def screenshot():
    run_id = request.args.get("run_id", "")
    intervention = _load_intervention(run_id)
    if intervention is None or not intervention.get("screenshot_path"):
        abort(404)
    path = Path(intervention["screenshot_path"])
    if not path.exists():
        abort(404)
    return send_file(str(path.resolve()), mimetype="image/png")


@app.route("/take-control", methods=["GET", "POST"])
def take_control():
    run_id = request.args.get("run_id", "")
    if not run_id:
        abort(400, "run_id query param is required")

    if request.method == "GET":
        escalation.set_control(run_id, "human")
    else:
        action_taken = request.form.get("action_taken", "").strip()
        if action_taken:
            escalation.record_human_action(run_id, action_taken)

    actions = _load_human_actions(run_id)
    return render_page(f"Take Control: {run_id}", TAKE_CONTROL_TEMPLATE, run_id=run_id, actions=actions)


@app.route("/hand-back", methods=["GET"])
def hand_back():
    run_id = request.args.get("run_id", "")
    if not run_id:
        abort(400, "run_id query param is required")

    escalation.set_control(run_id, "automation")
    escalation.resolve_intervention(run_id, "Handed back by operator")
    return render_page(f"Handed Back: {run_id}", HAND_BACK_TEMPLATE, run_id=run_id)


if __name__ == "__main__":
    app.run(debug=True, port=8421)

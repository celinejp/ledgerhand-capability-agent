"""Minimal Flask app giving a human operator a control surface to take over and hand back the live browser session."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flask import Flask, abort, render_template_string, request, send_file  # noqa: E402

import escalation  # noqa: E402

app = Flask(__name__)

EVIDENCE_ROOT = Path("evidence")

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
  .container { max-width: 960px; margin: 32px auto; padding: 0 24px 48px; }
  .card {
    background: var(--card-bg);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 24px;
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.04);
    margin-bottom: 20px;
  }
  .btn {
    display: inline-block;
    background: var(--accent);
    color: #fff;
    padding: 8px 16px;
    border-radius: 6px;
    border: none;
    font-size: 14px;
    font-weight: 500;
    text-decoration: none;
    cursor: pointer;
    transition: background 0.15s ease;
  }
  .btn:hover { background: var(--accent-dark); }
  .btn-secondary { background: transparent; color: var(--accent-dark); border: 1px solid var(--border); }
  .btn-secondary:hover { background: var(--bg); }
  .badge {
    display: inline-block;
    padding: 3px 10px;
    border-radius: 999px;
    font-size: 12px;
    font-weight: 600;
    letter-spacing: 0.3px;
  }
  .badge-pending { background: var(--warn-bg); color: var(--warn-text); }
  .badge-resolved { background: var(--ok-bg); color: var(--ok-text); }
  table.queue { width: 100%; border-collapse: collapse; }
  table.queue th {
    text-align: left;
    font-size: 12px;
    text-transform: uppercase;
    letter-spacing: 0.4px;
    color: var(--text-muted);
    padding: 8px 12px;
    border-bottom: 1px solid var(--border);
  }
  table.queue td { padding: 14px 12px; border-bottom: 1px solid var(--border); vertical-align: middle; font-size: 14px; }
  table.queue tr:last-child td { border-bottom: none; }
  table.queue tr:hover { background: #f8fafc; }
  .run-link {
    color: var(--accent-dark);
    font-weight: 600;
    text-decoration: none;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 13px;
  }
  .run-link:hover { text-decoration: underline; }
  .timestamp { color: var(--text-muted); font-size: 13px; }
  .empty-state { text-align: center; color: var(--text-muted); padding: 32px; }
  .field-table { width: 100%; border-collapse: collapse; }
  .field-table td { padding: 8px 4px; border-bottom: 1px solid var(--border); font-size: 14px; vertical-align: top; }
  .field-table td:first-child { color: var(--text-muted); width: 160px; font-weight: 500; }
  .screenshot-frame { text-align: center; margin: 20px 0; }
  .screenshot-frame img {
    max-width: 900px;
    width: 100%;
    border: 1px solid var(--border);
    border-radius: 8px;
    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.08);
  }
  .section-title {
    font-size: 13px;
    text-transform: uppercase;
    letter-spacing: 0.4px;
    color: var(--text-muted);
    margin: 0 0 16px;
    font-weight: 600;
  }
  .list-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 16px; }
  .list-header .section-title { margin: 0; }
  form.action-form { display: flex; gap: 8px; }
  form.action-form input[type="text"] {
    flex: 1;
    padding: 8px 12px;
    border: 1px solid var(--border);
    border-radius: 6px;
    font-size: 14px;
    font-family: inherit;
  }
  form.action-form input[type="text"]:focus { outline: 2px solid var(--accent); outline-offset: -1px; }
  .activity-feed { list-style: none; margin: 0 0 16px; padding: 0; }
  .activity-feed li { padding: 10px 0; border-bottom: 1px solid var(--border); font-size: 14px; }
  .activity-feed li:last-child { border-bottom: none; }
  .activity-feed .ts { color: var(--text-muted); font-size: 12px; display: block; margin-bottom: 2px; }
  .back-link { color: var(--text-muted); text-decoration: none; font-size: 14px; }
  .back-link:hover { color: var(--text); text-decoration: underline; }
  .actions-row { display: flex; gap: 10px; margin-top: 16px; align-items: center; }
</style>
"""

OUTER_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
<title>{{ title }}</title>
{{ extra_head|default("")|safe }}
""" + STYLE + """
</head>
<body>
<div class="topbar">
<h1>Operator Console</h1>
<span class="subtitle">{{ title|replace("Operator Console ", "") }}</span>
</div>
<div class="container">
{{ content|safe }}
</div>
</body>
</html>
"""

LIST_TEMPLATE = """
<div class="card">
<div class="list-header">
<p class="section-title">Intervention Queue</p>
<a href="/" class="btn btn-secondary">&#8635; Refresh</a>
</div>
<table class="queue">
<tr><th>Run</th><th>Goal</th><th>Status</th><th>Created</th><th></th></tr>
{% for i in interventions %}
<tr>
<td><a class="run-link" href="/take-control?run_id={{ i.run_id }}">{{ i.run_id }}</a></td>
<td>{{ i.capability_or_goal|truncate(70) }}</td>
<td>
{% if i.status == "pending" %}
<span class="badge badge-pending">Pending</span>
{% else %}
<span class="badge badge-resolved">Resolved</span>
{% endif %}
</td>
<td class="timestamp">{{ i.created_at }}</td>
<td><a href="/take-control?run_id={{ i.run_id }}" class="btn">Take Control</a></td>
</tr>
{% else %}
<tr><td colspan="5" class="empty-state">No interventions found under evidence/.</td></tr>
{% endfor %}
</table>
</div>
"""

DETAIL_TEMPLATE = """
<div class="card">
<p class="section-title">Intervention Detail</p>
<table class="field-table">
<tr><td>Run ID</td><td>{{ intervention.run_id }}</td></tr>
<tr><td>Capability / Goal</td><td>{{ intervention.capability_or_goal }}</td></tr>
<tr><td>Current Step</td><td>{{ intervention.current_step_id }}</td></tr>
<tr><td>Reason</td><td>{{ intervention.reason }}</td></tr>
<tr><td>Status</td><td>
{% if intervention.status == "pending" %}
<span class="badge badge-pending">Pending</span>
{% else %}
<span class="badge badge-resolved">Resolved</span>
{% endif %}
</td></tr>
<tr><td>Created At</td><td>{{ intervention.created_at }}</td></tr>
</table>
<div class="screenshot-frame">
<img src="/screenshot?run_id={{ intervention.run_id }}" alt="run screenshot">
</div>
<div class="actions-row">
<a href="/take-control?run_id={{ intervention.run_id }}" class="btn">Take Control</a>
<a href="/" class="back-link">&larr; Back to dashboard</a>
</div>
</div>
"""

TAKE_CONTROL_TEMPLATE = """
<div class="card">
<p class="section-title">Live Session &mdash; Controller: Human</p>
<div class="screenshot-frame">
<img id="live-screenshot" src="/screenshot?run_id={{ run_id }}" alt="live screenshot">
</div>
<script>
setInterval(function () {
    document.getElementById("live-screenshot").src = "/screenshot?run_id={{ run_id }}&t=" + Date.now();
}, 2000);
</script>
</div>
<div class="card">
<p class="section-title">Log an Action</p>
<form class="action-form" method="post" action="/take-control?run_id={{ run_id }}">
<input type="text" name="action_taken" placeholder="What did you just do on the live page?">
<button type="submit" class="btn">Log Action</button>
</form>
</div>
<div class="card">
<p class="section-title">Activity Feed</p>
<ul class="activity-feed">
{% for a in actions %}
<li><span class="ts">{{ a.timestamp }}</span>{{ a.action_description }}</li>
{% else %}
<li class="empty-state" style="padding: 12px 0;">No actions logged yet.</li>
{% endfor %}
</ul>
<div class="actions-row">
<a href="/hand-back?run_id={{ run_id }}" class="btn">Hand Control Back to Automation</a>
</div>
</div>
"""

HAND_BACK_TEMPLATE = """
<div class="card">
<p class="section-title">Handoff Complete</p>
<p>Control returned to automation for run <strong>{{ run_id }}</strong>.</p>
<p>Intervention resolved: &ldquo;Handed back by operator&rdquo;.</p>
<a href="/" class="back-link">&larr; Back to dashboard</a>
</div>
"""


def render_page(title, template, extra_head="", **context):
    inner = render_template_string(template, **context)
    return render_template_string(OUTER_TEMPLATE, title=title, content=inner, extra_head=extra_head)


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


DASHBOARD_REFRESH_META = '<meta http-equiv="refresh" content="5">'


@app.route("/", methods=["GET"])
def dashboard():
    run_id = request.args.get("run_id")
    if run_id:
        intervention = _load_intervention(run_id)
        if intervention is None:
            abort(404, f"No intervention.json found for run_id {run_id!r}")
        return render_page(f"Intervention: {run_id}", DETAIL_TEMPLATE, intervention=intervention)

    interventions = [json.loads(path.read_text()) for path in EVIDENCE_ROOT.glob("*/intervention.json")]
    interventions.sort(key=lambda i: i.get("created_at", ""), reverse=True)
    pending_count = sum(1 for i in interventions if i.get("status") == "pending")

    return render_page(
        f"Operator Console ({pending_count} pending)",
        LIST_TEMPLATE,
        interventions=interventions,
        extra_head=DASHBOARD_REFRESH_META,
    )


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

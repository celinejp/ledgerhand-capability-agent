"""Self-contained mock core-banking web app (server-rendered HTML, table-based layout) used as the automation target."""

import random
import string
import time

from flask import Flask, redirect, render_template_string, request, url_for

app = Flask(__name__)

# ---------------------------------------------------------------------------
# In-memory "database"
# ---------------------------------------------------------------------------

MEMBERS = {
    "1001": {"id": "1001", "name": "Alice Whitfield", "savings_balance": 4523.10},
    "1002": {"id": "1002", "name": "Marcus Boyd", "savings_balance": 128.50},
    "1003": {"id": "1003", "name": "Priya Anand", "savings_balance": 9876.00},
    "1004": {"id": "1004", "name": "Diego Castillo", "savings_balance": 0.00},
}

# (member_id, account_type) pairs that already exist, pre-seeded so the
# "duplicate account" validation path is reachable without any setup.
ACCOUNTS = {("1002", "savings")}

ACCOUNT_TYPES = ("checking", "savings")

# ---------------------------------------------------------------------------
# Templates (inline, table-based layout, no id/class/data-testid attributes;
# every input is wrapped in a <label> with visible text so it has an
# accessible name, and every button/link has clear visible action text)
# ---------------------------------------------------------------------------

OUTER_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
<title>{{ title }}</title>
<style>
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    background: #f1f5f9;
    color: #1e293b;
    margin: 0;
    padding: 40px 24px;
  }
  h1 {
    font-size: 20px;
    font-weight: 600;
    margin: 0;
    color: #0f766e;
  }
  table {
    width: 100%;
    border-collapse: collapse;
    border: none;
  }
  body > table {
    max-width: 640px;
    margin: 0 auto;
    background: #ffffff;
    border: 1px solid #e2e8f0;
    border-radius: 10px;
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.06);
    border-collapse: separate;
    overflow: hidden;
  }
  td, th {
    padding: 10px 14px;
    border: none;
  }
  button {
    background: #0d9488;
    color: #ffffff;
    border: none;
    padding: 8px 18px;
    border-radius: 6px;
    font-size: 14px;
    font-family: inherit;
    cursor: pointer;
  }
  button:hover {
    background: #0f766e;
  }
  input, select {
    padding: 6px 10px;
    border: 1px solid #cbd5e1;
    border-radius: 6px;
    font-size: 14px;
    font-family: inherit;
  }
  a {
    color: #0d9488;
  }
</style>
</head>
<body>
<table border="1" cellpadding="8" cellspacing="0" width="100%">
<tr><td><h1>{{ title }}</h1></td></tr>
<tr><td>{{ content|safe }}</td></tr>
</table>
</body>
</html>
"""

SEARCH_FORM_TEMPLATE = """
<table border="1" cellpadding="4" cellspacing="0">
<tr><td>
<form method="post" action="/members/search">
<table border="0" cellpadding="4" cellspacing="0">
<tr><td><label>Member ID <input type="text" name="member_id"></label></td></tr>
<tr><td><button type="submit">Search</button></td></tr>
</table>
</form>
</td></tr>
</table>
"""

NOT_FOUND_TEMPLATE = """
<table border="1" cellpadding="4" cellspacing="0">
<tr><td>No member found for ID "{{ member_id }}".</td></tr>
<tr><td><a href="/members/search">Search Again</a></td></tr>
</table>
"""

MEMBER_DETAIL_TEMPLATE = """
<table border="1" cellpadding="4" cellspacing="0">
<tr><td>Member ID</td><td>{{ member.id }}</td></tr>
<tr><td>Name</td><td>{{ member.name }}</td></tr>
<tr><td>Savings Balance</td><td>${{ "%.2f"|format(member.savings_balance) }}</td></tr>
<tr><td colspan="2"><a href="/members/{{ member.id }}/new-account">Open Sub-Account</a></td></tr>
</table>
"""

NEW_ACCOUNT_FORM_TEMPLATE = """
<table border="1" cellpadding="4" cellspacing="0">
<tr><td>Member ID</td><td>{{ member.id }}</td></tr>
<tr><td>Name</td><td>{{ member.name }}</td></tr>
</table>
<form method="post" action="/members/{{ member.id }}/new-account">
<table border="0" cellpadding="4" cellspacing="0">
<tr><td><label>Account Type
<select name="account_type">
<option value="checking">Checking</option>
<option value="savings">Savings</option>
</select>
</label></td></tr>
<tr><td><label>Opening Deposit <input type="number" name="deposit" step="0.01"></label></td></tr>
<tr><td><button type="submit">Submit</button></td></tr>
</table>
</form>
"""

VALIDATION_ERROR_TEMPLATE = """
<table border="1" cellpadding="4" cellspacing="0">
<tr><td>Validation Error</td></tr>
{% for e in errors %}
<tr><td>{{ e }}</td></tr>
{% endfor %}
</table>
<table border="0" cellpadding="4" cellspacing="0">
<tr><td>Member ID</td><td>{{ member.id }}</td></tr>
<tr><td>Requested Account Type</td><td>{{ account_type }}</td></tr>
<tr><td>Requested Deposit</td><td>{{ deposit }}</td></tr>
<tr><td colspan="2"><a href="/members/{{ member.id }}/new-account">Try Again</a></td></tr>
</table>
"""

CONFIRMATION_TEMPLATE = """
<table border="1" cellpadding="4" cellspacing="0">
<tr><td>Account Number</td><td>{{ account_number }}</td></tr>
<tr><td>Confirmation ID</td><td>{{ confirmation_id }}</td></tr>
<tr><td>Member</td><td>{{ member.name }} ({{ member.id }})</td></tr>
<tr><td>Account Type</td><td>{{ account_type }}</td></tr>
<tr><td>Opening Deposit</td><td>${{ "%.2f"|format(deposit) }}</td></tr>
<tr><td colspan="2"><a href="/members/{{ member.id }}">Back to Member</a></td></tr>
</table>
"""

LOGIN_TEMPLATE = """
<table border="1" cellpadding="4" cellspacing="0">
<tr><td>Your session has expired. Please log in again to continue.</td></tr>
<tr><td><a href="/members/search">Return to Search</a></td></tr>
</table>
"""

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def render_page(title, content):
    return render_template_string(OUTER_TEMPLATE, title=title, content=content)


def render_not_found(member_id):
    inner = render_template_string(NOT_FOUND_TEMPLATE, member_id=member_id)
    return render_page("No Member Found", inner)


def render_member_detail(member):
    inner = render_template_string(MEMBER_DETAIL_TEMPLATE, member=member)
    return render_page(f"Member {member['id']}", inner)


def maybe_slow():
    if request.args.get("slow") == "1":
        time.sleep(3)


def generate_account_number():
    return "".join(random.choices(string.digits, k=10))


def generate_confirmation_id():
    return "CONF-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=8))


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.route("/members/search", methods=["GET"])
def members_search_form():
    maybe_slow()
    inner = render_template_string(SEARCH_FORM_TEMPLATE)
    return render_page("Member Search", inner)


@app.route("/members/search", methods=["POST"])
def members_search_submit():
    maybe_slow()
    member_id = request.form.get("member_id", "").strip()
    member = MEMBERS.get(member_id)
    if member is None:
        return render_not_found(member_id)
    return render_member_detail(member)


@app.route("/members/<member_id>", methods=["GET"])
def member_detail(member_id):
    maybe_slow()
    member = MEMBERS.get(member_id)
    if member is None:
        return render_not_found(member_id)
    return render_member_detail(member)


@app.route("/members/<member_id>/new-account", methods=["GET"])
def new_account_form(member_id):
    maybe_slow()
    member = MEMBERS.get(member_id)
    if member is None:
        return render_not_found(member_id)
    inner = render_template_string(NEW_ACCOUNT_FORM_TEMPLATE, member=member)
    return render_page("Open Sub-Account", inner)


@app.route("/members/<member_id>/new-account", methods=["POST"])
def new_account_submit(member_id):
    maybe_slow()

    if request.args.get("session_expire") == "1":
        return redirect(url_for("login"))

    member = MEMBERS.get(member_id)
    if member is None:
        return render_not_found(member_id)

    account_type = request.form.get("account_type", "")
    deposit_raw = request.form.get("deposit", "")

    errors = []
    deposit_value = None
    try:
        deposit_value = float(deposit_raw)
    except ValueError:
        errors.append("Opening deposit must be a number.")

    if deposit_value is not None and deposit_value < 0:
        errors.append("Opening deposit must be zero or greater.")

    if account_type not in ACCOUNT_TYPES:
        errors.append("Account type must be checking or savings.")

    if not errors and (member_id, account_type) in ACCOUNTS:
        errors.append(f"Member already has a {account_type} account.")

    if errors:
        inner = render_template_string(
            VALIDATION_ERROR_TEMPLATE,
            member=member,
            errors=errors,
            account_type=account_type,
            deposit=deposit_raw,
        )
        return render_page("Validation Error", inner)

    ACCOUNTS.add((member_id, account_type))
    account_number = generate_account_number()
    confirmation_id = generate_confirmation_id()
    inner = render_template_string(
        CONFIRMATION_TEMPLATE,
        member=member,
        account_type=account_type,
        deposit=deposit_value,
        account_number=account_number,
        confirmation_id=confirmation_id,
    )
    return render_page("Account Opened", inner)


@app.route("/login", methods=["GET"])
def login():
    maybe_slow()
    inner = render_template_string(LOGIN_TEMPLATE)
    return render_page("Session Expired", inner)


if __name__ == "__main__":
    app.run(debug=True, port=8420)

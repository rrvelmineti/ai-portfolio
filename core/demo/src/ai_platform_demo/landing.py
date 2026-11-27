"""The public demo's landing page (ADR 0008).

It lists the **guest** logins only: viewer, analyst and reviewer, each with the password
from its env var. The demo admin is never shown: this module doesn't read
``DEMO_ADMIN_PASSWORD`` at all, and the hosted app isn't given it (CI uses it only to
seed). A guest whose password isn't configured is listed as "not available".

The page is self-contained (no external scripts or fonts) and works without JavaScript
for reading; the small "try it" form needs JavaScript and calls ``/auth/login`` and
``/demo/ask``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from html import escape

from ai_platform_common.config import get_env

from ai_platform_demo.usage import UsageCaps


@dataclass(frozen=True)
class Guest:
    username: str
    role: str
    password_env: str
    purpose: str


# Never add the admin here: it stays private to the owner.
GUESTS: tuple[Guest, ...] = (
    Guest("demo-viewer", "viewer", "DEMO_VIEWER_PASSWORD",
          "Ask the AI (guardrails on) and request a refund that needs approval."),
    Guest("demo-analyst", "analyst", "DEMO_ANALYST_PASSWORD",
          "Everything a viewer can do, plus approve other people's requests."),
    Guest("demo-reviewer", "analyst", "DEMO_REVIEWER_PASSWORD",
          "A second approver, so you can request with one guest and approve with another."),
)  # fmt: skip

TENANT = "demo"

# Inline script and styles only; nothing loads from other origins.
CONTENT_SECURITY_POLICY = (
    "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
    "connect-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
)


def guest_logins(environ: Mapping[str, str] | None = None) -> list[tuple[Guest, str | None]]:
    """Each guest with its password, or ``None`` when that env var isn't set."""
    return [(g, get_env(g.password_env, environ)) for g in GUESTS]


def render_landing(
    caps: UsageCaps,
    environ: Mapping[str, str] | None = None,
    usage: Mapping[str, int] | None = None,
) -> str:
    """The landing page HTML. Every value is escaped."""
    rows = []
    for guest, password in guest_logins(environ):
        shown = (f"<code>{escape(password)}</code>" if password
                 else "<em>not available</em>")  # fmt: skip
        rows.append(
            f"<tr><td><code>{escape(TENANT)}/{escape(guest.username)}</code></td>"
            f"<td>{escape(guest.role)}</td><td>{shown}</td><td>{escape(guest.purpose)}</td></tr>"
        )
    options = "".join(
        f'<option value="{escape(g.username)}">{escape(g.username)}</option>'
        for g, pw in guest_logins(environ) if pw
    )  # fmt: skip
    used = ""
    if usage is not None:
        today, month = usage.get("today", 0), usage.get("this_month", 0)
        used = (f"<p>Used so far: {today} of {caps.global_daily} today, "
                f"{month} of {caps.global_monthly} this month.</p>")  # fmt: skip
    return _PAGE.format(
        rows="".join(rows),
        options=options,
        guest_daily=caps.per_guest_daily,
        global_daily=caps.global_daily,
        global_monthly=caps.global_monthly,
        used=used,
        tenant=escape(TENANT),
    )


_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>ai-portfolio demo</title>
<style>
:root {{ color-scheme: light dark; --fg: #1d232a; --bg: #fbfbfa; --muted: #5b6570;
  --line: #d9dde1; --accent: #1f6feb; }}
@media (prefers-color-scheme: dark) {{ :root {{ --fg: #e6e8ea; --bg: #15181b;
  --muted: #9aa4ae; --line: #2c3238; --accent: #6ea8fe; }} }}
body {{ margin: 0; background: var(--bg); color: var(--fg);
  font: 16px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }}
main {{ max-width: 860px; margin: 0 auto; padding: 32px 16px 64px; }}
h1 {{ font-size: 1.6rem; margin: 0 0 4px; }}
h2 {{ font-size: 1.15rem; margin-top: 2rem; }}
p.lead, .muted {{ color: var(--muted); }}
table {{ border-collapse: collapse; width: 100%; font-size: .95rem; }}
th, td {{ text-align: left; padding: 8px; border-bottom: 1px solid var(--line);
  vertical-align: top; }}
.scroll {{ overflow-x: auto; }}
code {{ font-size: .9em; overflow-wrap: anywhere; }}
form {{ display: grid; gap: 8px; }}
select, textarea, button {{ font: inherit; padding: 8px; border-radius: 6px;
  border: 1px solid var(--line); background: transparent; color: inherit; }}
button {{ background: var(--accent); color: #fff; border: 0; cursor: pointer;
  justify-self: start; padding: 8px 16px; }}
button[disabled] {{ opacity: .6; cursor: wait; }}
#out {{ white-space: pre-wrap; border-left: 3px solid var(--line); padding-left: 12px; }}
</style>
</head>
<body>
<main>
<h1>ai-portfolio demo</h1>
<p class="lead">A shared AI platform: login with roles and tenants, an append-only audit
log, PII masking and prompt-injection guardrails on every AI call, and approvals for
risky actions. All data here is synthetic.</p>

<h2>Guest logins</h2>
<div class="scroll"><table>
<thead><tr><th>Username</th><th>Role</th><th>Password</th><th>Use it to</th></tr></thead>
<tbody>{rows}</tbody>
</table></div>
<p class="muted">Guests can't create users, change roles or see any configuration.
Demo data is reset every night, so nothing you do here is kept.</p>

<h2>Try it</h2>
<form id="ask">
<label>Guest <select id="user">{options}</select></label>
<label for="q">Question</label>
<textarea id="q" rows="3" maxlength="2000" required>Summarise: member Ana Ruiz \
(ana.ruiz@example.com) asks why her claim was delayed.</textarea>
<button id="go" type="submit">Ask</button>
</form>
<p id="out" aria-live="polite"></p>

<h2>Limits</h2>
<p>To keep the demo free, AI calls are capped: {guest_daily} per guest per day,
{global_daily} per day for everyone, and {global_monthly} per month. When a cap is reached
you'll see "Demo limit reached" and the time it resets.</p>
{used}
<p class="muted">The demo sleeps when nobody uses it. The first visit can take a few
seconds to wake it, and the first AI call a few more while the guardrail models load.</p>

<h2>API</h2>
<p>Interactive API docs: <a href="/docs">/docs</a> (click "Authorize" and log in as
<code>{tenant}/demo-viewer</code>). Health: <a href="/readyz">/readyz</a>.</p>
</main>
<script>
(function () {{
  var form = document.getElementById("ask"), out = document.getElementById("out");
  var go = document.getElementById("go");
  var passwords = {{}};
  document.querySelectorAll("tbody tr").forEach(function (tr) {{
    var c = tr.querySelectorAll("td code");
    if (c.length === 2) passwords[c[0].textContent.split("/")[1]] = c[1].textContent;
  }});
  form.addEventListener("submit", async function (e) {{
    e.preventDefault();
    var user = document.getElementById("user").value;
    go.disabled = true; out.textContent = "Working (the first call can take a few seconds)...";
    try {{
      var body = new URLSearchParams({{username: "{tenant}/" + user,
                                       password: passwords[user] || ""}});
      var login = await fetch("/auth/login", {{method: "POST", body: body}});
      if (!login.ok) throw new Error((await login.json()).detail || "Login failed");
      var token = (await login.json()).access_token;
      var r = await fetch("/demo/ask", {{method: "POST",
        headers: {{"Content-Type": "application/json", "Authorization": "Bearer " + token}},
        body: JSON.stringify({{question: document.getElementById("q").value}})}});
      var data = await r.json();
      out.textContent = r.ok ? data.answer + "\\n\\n(" + data.provider + ", masked PII: "
        + JSON.stringify(data.pii_masked) + ")" : (data.detail || "Request failed");
    }} catch (err) {{ out.textContent = String(err.message || err); }}
    finally {{ go.disabled = false; }}
  }});
}})();
</script>
</body>
</html>
"""

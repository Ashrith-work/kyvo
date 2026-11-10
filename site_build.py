"""Part I: the published report site. Builds out/site/ from the database, the policy files and out/eval.
Plain HTML and CSS, no framework, responsive, accessible, no em-dashes. Colours and fonts match the
Kyvo landing page (docs/index.html).
"""
import os, json, html, statistics, datetime as dt
import database as db
import workflow as wf
import config

CSS = """
:root{--bg:#F7F6F2;--surface:#FFFFFF;--ink:#16181D;--muted:#5A606B;--line:#E2E0D8;--accent:#1F4FD8;
--accent-ink:#173FAF;--ok:#1B7A50;--ok-bg:#E7F3EC;--hold:#9A5B00;--hold-bg:#FBF1E1;--no:#B42318;--no-bg:#FCEBEA;
--sans:"IBM Plex Sans",system-ui,-apple-system,"Segoe UI",Roboto,Arial,sans-serif;
--mono:"IBM Plex Mono",ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;--radius:10px;--maxw:1100px}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:400 16px/1.6 var(--sans);overflow-wrap:break-word}
a{color:var(--accent);text-underline-offset:3px}a:hover{color:var(--accent-ink)}
header.top{position:sticky;top:0;background:rgba(247,246,242,.95);border-bottom:1px solid var(--line);padding:12px 0}
.wrap{max-width:var(--maxw);margin:0 auto;padding:0 clamp(16px,4vw,32px)}
.brand{display:inline-flex;align-items:center;gap:10px;font-weight:700;text-decoration:none;color:var(--ink)}
.mark{width:26px;height:26px;border-radius:7px;background:var(--ink);color:#fff;display:grid;place-items:center;font-size:15px}
nav a{margin-left:14px;color:var(--muted);text-decoration:none;font-size:15px}nav a:hover{color:var(--ink)}
h1{font-size:clamp(24px,3.5vw,34px);margin:28px 0 6px;letter-spacing:-.01em}
h2{font-size:20px;margin:34px 0 10px}h3{font-size:16px;margin:22px 0 6px}
.sub{color:var(--muted);margin:0 0 10px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:14px;margin:18px 0}
.card{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);padding:16px}
.card .n{font-size:28px;font-weight:700;letter-spacing:-.01em}
.card .d{color:var(--muted);font-size:13.5px;margin-top:4px}
table{width:100%;border-collapse:collapse;background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);overflow:hidden;font-size:14.5px;margin:10px 0}
th,td{text-align:left;padding:9px 12px;border-bottom:1px solid var(--line);vertical-align:top}
th{background:#FBFAF7;font-weight:600}tr:last-child td{border-bottom:none}
.pill{display:inline-block;padding:2px 9px;border-radius:999px;font-size:12.5px;font-weight:600}
.APPROVED{background:var(--ok-bg);color:var(--ok)}.PENDING{background:var(--hold-bg);color:var(--hold)}
.REJECTED{background:var(--no-bg);color:var(--no)}
.mono,code{font-family:var(--mono);font-size:.92em}
.muted{color:var(--muted)}.small{font-size:13px}
footer{color:var(--muted);font-size:13px;margin:40px 0 24px}
@media(max-width:620px){nav a{margin-left:8px}}
"""

NAV = ('<a href="index.html">Overview</a><a href="verify.html">Verify</a><a href="policy.html">Policy</a>'
       '<a href="flags.html">Flags</a><a href="eval.html">Eval</a>')


def _page(title, body):
    return (f"<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            f"<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            f"<title>{html.escape(title)}</title><link rel=\"stylesheet\" href=\"style.css\"></head><body>"
            f"<header class=\"top\"><div class=\"wrap\" style=\"display:flex;align-items:center;justify-content:space-between\">"
            f"<a class=\"brand\" href=\"index.html\"><span class=\"mark\">K</span> Kyvo</a><nav>{NAV}</nav></div></header>"
            f"<main class=\"wrap\">{body}<footer>Generated from the Kyvo database. Policy {html.escape(config.POLICY_VERSION)}. "
            f"Format and consistency checks only; official registries are not queried.</footer></main></body></html>")


def e(x):
    return html.escape(str(x if x is not None else ""))


def _median_hours(pairs):
    vals = []
    for a, b in pairs:
        if a and b:
            vals.append((dt.datetime.fromisoformat(b) - dt.datetime.fromisoformat(a)).total_seconds() / 3600)
    return round(statistics.median(vals), 1) if vals else None


def _metrics(conn):
    subs = db.rows(conn, "SELECT id, created_at FROM submissions")
    total = len(subs)
    counts = db.overview_counts(conn)
    auto_approved = db.one(conn, f"SELECT COUNT(*) AS n FROM {db.LATEST_DECISION} x WHERE x.status='APPROVED' AND x.decided_by='system'")["n"]
    first_decision = []
    for s in subs:
        d = db.one(conn, "SELECT MIN(created_at) AS t FROM decisions WHERE submission_id=?", (s["id"],))
        first_decision.append((s["created_at"], d["t"] if d else None))
    stale = db.one(conn, "SELECT COUNT(*) AS n FROM submissions WHERE stale=1")["n"]
    hold = db.one(conn, "SELECT COUNT(DISTINCT vendor_id) AS n FROM payments WHERE status='ON_HOLD'")["n"]
    wv = db.one(conn, f"SELECT COUNT(*) AS n FROM {db.LATEST_DECISION} x WHERE x.waiting_on='VENDOR'")["n"]
    wr = db.one(conn, f"SELECT COUNT(*) AS n FROM {db.LATEST_DECISION} x WHERE x.waiting_on='REVIEWER'")["n"]
    asks_by_type = db.rows(conn, "SELECT rule_id, COUNT(*) AS n FROM follow_up_requests WHERE status='OPEN' GROUP BY rule_id ORDER BY n DESC")
    return {"total": total, "approved": counts["APPROVED"], "pending": counts["PENDING"], "rejected": counts["REJECTED"],
            "auto_approved": auto_approved, "auto_share": round(auto_approved / total, 2) if total else 0,
            "median_first_hours": _median_hours(first_decision), "waiting_vendor": wv, "waiting_reviewer": wr,
            "stale": stale, "on_hold": hold, "asks_by_type": asks_by_type}


def _index(conn):
    m = _metrics(conn)
    cards = [
        ("Submissions", m["total"], "Every vendor submission received."),
        ("Approved with no human step", f"{int(m['auto_share'] * 100)}%", "Share approved by the rules alone, no reviewer."),
        ("Median time to first decision", f"{m['median_first_hours']} h" if m["median_first_hours"] is not None else "n/a", "From arrival to the first decision."),
        ("Waiting on vendor", m["waiting_vendor"], "The vendor has an open request to answer."),
        ("Waiting on reviewer", m["waiting_reviewer"], "A person must make a judgement call."),
        ("Stale", m["stale"], "No reply past the stale window; recommended for closure."),
        ("Payments on hold", m["on_hold"], "A bank-details change is awaiting reviewer confirmation."),
    ]
    body = ["<h1>Vendor onboarding</h1><p class=\"sub\">Live figures from the Kyvo database.</p>", "<div class=\"grid\">"]
    for name, n, d in cards:
        body.append(f"<div class=\"card\"><div class=\"n\">{e(n)}</div><div class=\"d\"><strong>{e(name)}</strong><br>{e(d)}</div></div>")
    body.append("</div>")
    if m["asks_by_type"]:
        body.append("<h2>Open asks by type</h2><table><tr><th>Check</th><th>Open requests</th></tr>")
        for a in m["asks_by_type"]:
            body.append(f"<tr><td class=\"mono\">{e(a['rule_id'])}</td><td>{e(a['n'])}</td></tr>")
        body.append("</table>")
    body.append("<h2>Submissions</h2><table><tr><th>Ref</th><th>Vendor</th><th>Status</th><th>Waiting</th><th>Risk</th><th>Policy</th></tr>")
    for s in db.list_submissions(conn):
        sid = s["id"]
        d = db.latest_decision(conn, sid) or {}
        body.append(f"<tr><td><a href=\"sub/{e(db.ref(sid))}.html\">{e(db.ref(sid))}</a></td><td>{e(s['legal_name'])}</td>"
                    f"<td><span class=\"pill {e(d.get('status'))}\">{e(d.get('status'))}</span></td>"
                    f"<td>{e(d.get('waiting_on'))}</td><td>{e(d.get('risk_level'))}</td><td class=\"mono small\">{e(d.get('policy_version'))}</td></tr>")
    body.append("</table>")
    return _page("Kyvo report", "".join(body))


def _submission(conn, sid):
    rec = wf.decision_json(conn, sid)
    ref = db.ref(sid)
    run = db.latest_decision(conn, sid)["run_no"]
    body = [f"<h1>{e(ref)}</h1>",
            f"<p class=\"sub\"><span class=\"pill {e(rec['status'])}\">{e(rec['status'])}</span> "
            f"waiting on {e(rec['waiting_on'])}, risk {e(rec['risk_level'])}, "
            f"payments {'on hold' if rec['payment_hold'] else 'not held'}. Policy <span class=\"mono\">{e(rec['policy_version'])}</span>.</p>"]
    body.append("<h2>Identity assurance</h2><table><tr><th>Identity</th><th>Level reached</th><th>Not verified</th></tr>")
    for ident, a in rec["assurance"].items():
        body.append(f"<tr><td>{e(ident.replace('_', ' '))}</td><td>{e(a['level'])}</td><td class=\"small\">{e('; '.join(a['not_verified']))}</td></tr>")
    body.append("</table>")
    if rec["requests_to_vendor"]:
        body.append("<h2>Open requests to the vendor</h2><table><tr><th>About</th><th>Ask</th><th>Status</th></tr>")
        for q in rec["requests_to_vendor"]:
            body.append(f"<tr><td>{e(q['about'])}</td><td>{e(q['ask'])}</td><td>{e(q['status'])}</td></tr>")
        body.append("</table>")
    body.append("<h2>Checks</h2><table><tr><th>Check</th><th>Status</th><th>Assurance</th><th>Evidence</th><th>Source</th></tr>")
    for r in db.rows(conn, "SELECT * FROM validation_results WHERE submission_id=? AND run_no=? ORDER BY id", (sid, run)):
        body.append(f"<tr><td>{e(r['label'])}</td><td>{e(r['status'])}</td><td>{e(r['assurance'])}</td>"
                    f"<td class=\"small\">{e(r['evidence'])}</td><td class=\"small mono\">{e(r['source'])}</td></tr>")
    body.append("</table>")
    body.append("<h2>Timeline</h2><table><tr><th>When</th><th>Event</th><th>Actor</th></tr>")
    for ev in db.rows(conn, "SELECT created_at, event, actor FROM audit_events WHERE submission_id=? ORDER BY id", (sid,)):
        body.append(f"<tr><td class=\"small mono\">{e(ev['created_at'])}</td><td>{e(ev['event'])}</td><td>{e(ev['actor'])}</td></tr>")
    body.append("</table>")
    body.append("<h2>Emails</h2><table><tr><th>Type</th><th>To</th><th>Subject</th><th>Sent</th></tr>")
    for d in db.rows(conn, "SELECT draft_type, to_addr, subject, sent FROM communication_drafts WHERE submission_id=? ORDER BY id", (sid,)):
        body.append(f"<tr><td>{e(d['draft_type'])}</td><td class=\"small\">{e(d['to_addr'])}</td><td class=\"small\">{e(d['subject'])}</td><td>{'yes' if d['sent'] else 'no'}</td></tr>")
    body.append("</table>")
    return _page(f"Kyvo {ref}", "".join(body))


def _policy_page():
    doc = config.POLICY
    body = ["<h1>Decision policy</h1>", f"<p class=\"sub\">Version <span class=\"mono\">{e(config.POLICY_VERSION)}</span>. "
            "Every threshold with why it is set there, who owns it, and what moves if it changes.</p>"]
    for section in ("thresholds", "risk_weights"):
        body.append(f"<h2>{e(section.replace('_', ' '))}</h2><table><tr><th>Key</th><th>Value</th><th>Why</th><th>Owner</th><th>If raised</th><th>If lowered</th></tr>")
        for k, v in doc.get(section, {}).items():
            body.append(f"<tr><td class=\"mono\">{e(k)}</td><td>{e(v.get('value'))} {e(v.get('unit', ''))}</td>"
                        f"<td class=\"small\">{e(v.get('why'))}</td><td>{e(v.get('owner'))}</td>"
                        f"<td class=\"small\">{e(v.get('if_raised'))}</td><td class=\"small\">{e(v.get('if_lowered'))}</td></tr>")
        body.append("</table>")
    return _page("Kyvo policy", "".join(body))


def _flags_page():
    body = ["<h1>Flags</h1><p class=\"sub\">Every flag the engine can raise, its routing, the message the vendor "
            "receives and the reviewer step. One source for the vendor message and the reviewer instruction.</p>",
            "<table><tr><th>Code</th><th>Severity</th><th>Blocks</th><th>Waiting</th><th>Assurance</th><th>Vendor ask</th><th>Reviewer step</th></tr>"]
    for code, f in config.FLAGS.items():
        body.append(f"<tr><td class=\"mono\">{e(code)}</td><td>{e(f.get('severity'))}</td><td>{'yes' if f.get('blocking') else 'no'}</td>"
                    f"<td>{e(f.get('waiting_on'))}</td><td>{e(f.get('assurance'))}</td>"
                    f"<td class=\"small\">{e(f.get('vendor_ask'))}</td><td class=\"small\">{e(f.get('reviewer_step'))}</td></tr>")
    body.append("</table>")
    return _page("Kyvo flags", "".join(body))


def _eval_page(out_root):
    path = os.path.join(out_root, "eval", "eval.json")
    if not os.path.exists(path):
        return _page("Kyvo eval", "<h1>Evaluation</h1><p class=\"sub\">Run <span class=\"mono\">cli.py eval</span> to produce this report.</p>")
    r = json.load(open(path, encoding="utf-8"))
    s = r["summary"]
    body = ["<h1>Evaluation</h1>", f"<p class=\"sub\">Tesseract OCR available: {'yes' if r['tesseract_available'] else 'no'}.</p>",
            "<div class=\"grid\">"]
    for name, n, d in [
        ("Status accuracy", s["status_accuracy"], "Expected vs actual status per scenario."),
        ("Unsafe errors", s["unsafe_errors"], "Approved when it should not be. Must be zero."),
        ("Friction errors", s["friction_errors"], "Held when it should have passed."),
        ("Sweep unsafe errors", s["sweep_unsafe_errors"], "Wrong approvals under degraded images. Must be zero."),
        ("Extraction, PDF text", s["pdf_text_accuracy"], "Exact match after the rules' normalisation."),
        ("Extraction, OCR", s["ocr_accuracy"], "Exact match on photos and scans."),
    ]:
        body.append(f"<div class=\"card\"><div class=\"n\">{e(n)}</div><div class=\"d\"><strong>{e(name)}</strong><br>{e(d)}</div></div>")
    body.append("</div><h2>Decision accuracy by scenario</h2><table><tr><th>Scenario</th><th>Expected</th><th>Actual</th><th>Unsafe</th><th>Friction</th></tr>")
    for p in r["decision"]["per_scenario"]:
        body.append(f"<tr><td class=\"mono small\">{e(p['scenario'])}</td><td>{e(p['expected'])}</td><td>{e(p['actual'])}</td>"
                    f"<td>{'yes' if p['unsafe'] else ''}</td><td>{'yes' if p['friction'] else ''}</td></tr>")
    body.append("</table>")
    sw = r["degradation_sweep"]
    body.append(f"<h2>Degradation sweep</h2><p class=\"sub\">{e(sw['approved'])} approved, {e(sw['pending'])} pending across "
                f"{e(sw['levels'])} blur and resolution levels; {e(sw['unsafe_errors'])} wrong approvals.</p>")
    return _page("Kyvo eval", "".join(body))


def build(conn, out_root):
    site = os.path.join(out_root, "site")
    os.makedirs(os.path.join(site, "sub"), exist_ok=True)
    with open(os.path.join(site, "style.css"), "w", encoding="utf-8") as fh:
        fh.write(CSS)
    with open(os.path.join(site, "index.html"), "w", encoding="utf-8") as fh:
        fh.write(_index(conn))
    with open(os.path.join(site, "policy.html"), "w", encoding="utf-8") as fh:
        fh.write(_policy_page())
    with open(os.path.join(site, "flags.html"), "w", encoding="utf-8") as fh:
        fh.write(_flags_page())
    with open(os.path.join(site, "eval.html"), "w", encoding="utf-8") as fh:
        fh.write(_eval_page(out_root))
    for s in db.rows(conn, "SELECT id FROM submissions ORDER BY id"):
        with open(os.path.join(site, "sub", f"{db.ref(s['id'])}.html"), "w", encoding="utf-8") as fh:
            fh.write(_submission(conn, s["id"]))
    return os.path.join(site, "index.html")

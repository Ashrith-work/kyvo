"""Part C output contract. Every run writes out/<ref>/ with the decision, a human report, every
outgoing email as an .eml file, and an append-only audit log. Also a daily digest and a ledger.
Plain sentences, sentence case, no em-dashes.
"""
import os, json, csv, datetime as dt
import database as db
import workflow as wf
from config import DOC_LABELS

SENDER = "Kyvo Vendor Onboarding <onboarding@kyvo.example>"
LEDGER_COLS = ["ref", "vendor_id", "status", "waiting_on", "risk_level", "payment_hold",
               "run_no", "policy_version", "decided_at", "open_asks"]


def eml(draft: dict, sender: str = SENDER) -> str:
    """Render one outgoing message as an RFC 822 .eml string. Never sent by this function."""
    date = draft.get("created_at") or db.now()
    headers = [
        f"From: {sender}",
        f"To: {draft.get('to_addr', '') or ''}",
        f"Subject: {draft.get('subject', '') or ''}",
        f"Date: {date}",
        "MIME-Version: 1.0",
        'Content-Type: text/plain; charset="utf-8"',
        f"X-Kyvo-Draft-Type: {draft.get('draft_type', '')}",
        "X-Kyvo-Sent: no",
    ]
    return "\r\n".join(headers) + "\r\n\r\n" + (draft.get("body", "") or "") + "\r\n"


def report_md(conn, sid: int) -> str:
    rec = wf.decision_json(conn, sid)
    ref = db.ref(sid)
    L = [f"# {ref} vendor onboarding decision", ""]
    L += [f"- Status: {rec['status']}",
          f"- Waiting on: {rec['waiting_on']}",
          f"- Risk level: {rec['risk_level']}",
          f"- Payments on hold: {'yes' if rec['payment_hold'] else 'no'}",
          f"- Run: {rec['run_no']}",
          f"- Policy: {rec['policy_version']}",
          f"- Decided at: {rec['decided_at']}", ""]
    L += ["## Identity assurance", "",
          "Highest level reached per identity, and what this build does not verify.", ""]
    for ident, a in rec["assurance"].items():
        nv = "; ".join(a["not_verified"])
        L.append(f"- {ident.replace('_', ' ')}: {a['level']}. Not verified: {nv}.")
    if not rec["assurance"]:
        L.append("- No identity checks applied.")
    L += ["", "## Reasons", ""]
    if rec["reasons"]:
        for r in rec["reasons"]:
            L.append(f"- [{r['severity']}] {r['label']} ({r['assurance']}): {r['evidence']}")
    else:
        L.append("- All applicable checks passed.")
    L += ["", "## Open requests to the vendor", ""]
    if rec["requests_to_vendor"]:
        for q in rec["requests_to_vendor"]:
            L.append(f"- {q['ask']} (about: {q['about']}, status: {q['status']})")
    else:
        L.append("- None.")
    L += ["", "## Timeline", ""]
    for e in db.rows(conn, "SELECT created_at, event, actor FROM audit_events WHERE submission_id=? ORDER BY id", (sid,)):
        L.append(f"- {e['created_at']} {e['event']} ({e['actor']})")
    L += ["", "## Emails drafted", ""]
    for d in db.rows(conn, "SELECT draft_type, to_addr, subject FROM communication_drafts WHERE submission_id=? ORDER BY id", (sid,)):
        L.append(f"- {d['draft_type']} to {d['to_addr']}: {d['subject']}")
    L += ["", f"Next action: {rec['next_action']}", ""]
    return "\n".join(L)


def write_outputs(conn, sid: int, out_root: str) -> str:
    """Write out/<ref>/ : decision.json, report.md, emails/NN_type.eml, audit.jsonl. Returns the folder."""
    ref = db.ref(sid)
    folder = os.path.join(out_root, ref)
    emails = os.path.join(folder, "emails")
    os.makedirs(emails, exist_ok=True)
    rec = wf.decision_json(conn, sid)
    with open(os.path.join(folder, "decision.json"), "w", encoding="utf-8") as fh:
        json.dump(rec, fh, indent=2)
    with open(os.path.join(folder, "report.md"), "w", encoding="utf-8") as fh:
        fh.write(report_md(conn, sid))
    for i, d in enumerate(db.rows(conn, "SELECT * FROM communication_drafts WHERE submission_id=? ORDER BY id", (sid,)), 1):
        name = f"{i:02d}_{d['draft_type'] or 'message'}.eml"
        with open(os.path.join(emails, name), "w", encoding="utf-8") as fh:
            fh.write(eml(d))
    with open(os.path.join(folder, "audit.jsonl"), "w", encoding="utf-8") as fh:
        for e in db.rows(conn, "SELECT created_at, event, actor, details_json FROM audit_events WHERE submission_id=? ORDER BY id", (sid,)):
            fh.write(json.dumps({"ts": e["created_at"], "event": e["event"], "actor": e["actor"],
                                 "details": json.loads(e["details_json"] or "{}")}) + "\n")
    return folder


def _ledger_row(conn, sid) -> dict:
    rec = wf.decision_json(conn, sid)
    return {"ref": db.ref(sid), "vendor_id": rec["vendor_id"], "status": rec["status"],
            "waiting_on": rec["waiting_on"], "risk_level": rec["risk_level"], "payment_hold": rec["payment_hold"],
            "run_no": rec["run_no"], "policy_version": rec["policy_version"], "decided_at": rec["decided_at"],
            "open_asks": len(rec["requests_to_vendor"])}


def write_ledger(conn, out_root: str) -> str:
    """Rewrite out/ledger.csv: one row per submission, latest run."""
    os.makedirs(out_root, exist_ok=True)
    path = os.path.join(out_root, "ledger.csv")
    sids = [r["id"] for r in db.rows(conn, "SELECT id FROM submissions ORDER BY id")]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=LEDGER_COLS)
        w.writeheader()
        for sid in sids:
            w.writerow(_ledger_row(conn, sid))
    return path


def write_digest(conn, out_root: str, date=None) -> str:
    """out/digest-<date>.md: the day's headline numbers, each with a one-line definition."""
    date = date or dt.date.today().isoformat()
    os.makedirs(out_root, exist_ok=True)
    c = db.overview_counts(conn)
    wv = db.one(conn, f"SELECT COUNT(*) AS n FROM {db.LATEST_DECISION} x WHERE x.waiting_on='VENDOR'")["n"]
    wr = db.one(conn, f"SELECT COUNT(*) AS n FROM {db.LATEST_DECISION} x WHERE x.waiting_on='REVIEWER'")["n"]
    stale = db.one(conn, "SELECT COUNT(*) AS n FROM submissions WHERE stale=1")["n"]
    hold = db.one(conn, "SELECT COUNT(DISTINCT vendor_id) AS n FROM payments WHERE status='ON_HOLD'")["n"]
    lines = [
        f"# Kyvo onboarding digest {date}", "",
        f"- Submissions: {c['total']}. Every vendor submission received.",
        f"- Approved: {c['APPROVED']}. Passed all blocking checks and the two-source rule.",
        f"- Pending: {c['PENDING']}. Held for a vendor reply or a reviewer.",
        f"- Rejected: {c['REJECTED']}. A written policy disqualification or a reviewer rejection.",
        f"- Waiting on vendor: {wv}. The vendor has an open request to answer.",
        f"- Waiting on reviewer: {wr}. A person must make a judgement call.",
        f"- Stale: {stale}. No vendor reply past the stale window; recommended for closure.",
        f"- Payments on hold: {hold}. A bank-details change is awaiting reviewer confirmation.",
        "",
    ]
    path = os.path.join(out_root, f"digest-{date}.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    return path

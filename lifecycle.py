"""Part E lifecycle driver: reminders on the policy days, stale escalation, and delivery of the drafts.

tick() is time-driven off the injected clock (or an explicit `now`). It drafts reminders and marks stale
submissions through workflow.reminders_due, then, when an outbox is given, delivers reminder drafts and
sends a reviewer escalation (with the submission report) for stale submissions. Closing a stale submission
still needs a REVIEWER_DECISION with a reason.
"""
import database as db
import workflow as wf
import outputs
import mailer
import config


def tick(conn, now=None, outbox_dir=None, out_root=None):
    rows = wf.reminders_due(conn, now)
    for r in rows:
        sid = r["submission_id"]
        if outbox_dir:
            if r["stale"]:
                report = outputs.report_md(conn, sid)
                draft = {"draft_type": "stale_escalation", "to_addr": ", ".join(config.REVIEWER_ALLOWLIST),
                         "subject": f"[{db.ref(sid)}] Stale vendor registration, reviewer decision needed",
                         "body": ("Recommend: close as rejected (incomplete). A reviewer must confirm and comment.\n\n"
                                  + report)}
                mailer.deliver(conn, sid, draft, outbox_dir)
            else:
                which = 2 if "Second" in r["action"] else 1
                dr = db.one(conn, "SELECT id FROM communication_drafts WHERE submission_id=? AND draft_type=? "
                                  "ORDER BY id DESC LIMIT 1", (sid, f"reminder_{which}"))
                if dr:
                    mailer.send_existing(conn, outbox_dir, draft_id=dr["id"])
        if out_root:
            outputs.write_outputs(conn, sid, out_root)
    return rows

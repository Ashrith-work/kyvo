"""Part C: follow-up ledger, vendor_response full re-run, reminders and STALE."""
import os
import datetime as dt
import database as db


def flags(r): return {f["code"] for f in r["flags"]}
def opens(conn, sid): return db.rows(conn, "SELECT * FROM follow_up_requests WHERE submission_id=? AND status='OPEN' ORDER BY id", (sid,))


def test_follow_up_opens_for_blocking_requests(env):
    r = env.run("2_wrong_document")
    sid = r["submission_id"]
    o = opens(env.conn, sid)
    assert any(x["rule_id"] == "DOC_GST_CERT" for x in o)
    assert all(x["status"] == "OPEN" and x["opened_run"] == 1 for x in o)


def test_vendor_response_resolves_request_and_approves(env):
    from sample_data import generate as g
    r = env.run("2_wrong_document")
    sid = r["submission_id"]
    assert r["status"] == "PENDING"
    form = env.sc["2_wrong_document"]["form"]
    co = {"legal": form["legal_name"], "trade": form.get("trading_name", ""), "gstin": form["gstin"],
          "pan": form["pan"], "address": form["address"]}
    path = os.path.join(env.dir, "aster_gst_fixed.pdf")
    g.make_pdf(path, g.gst_doc(co))
    with open(path, "rb") as fh:
        data = fh.read()
    out = env.wf.vendor_response(env.conn, sid, files={"GST_CERT": ("aster_gst_fixed.pdf", data)}, upload_dir=env.up)
    assert out["run_no"] == 2 and out["status"] == "APPROVED"
    row = db.one(env.conn, "SELECT * FROM follow_up_requests WHERE submission_id=? AND rule_id='DOC_GST_CERT'", (sid,))
    assert row["status"] == "RESOLVED" and row["resolved_run"] == 2
    # the re-extracted document field carries the vendor_resubmitted provenance
    assert db.rows(env.conn, "SELECT * FROM extracted_fields WHERE submission_id=? AND provenance='vendor_resubmitted'", (sid,))
    ev = {e["event"] for e in db.rows(env.conn, "SELECT event FROM audit_events WHERE submission_id=?", (sid,))}
    assert "VENDOR_RESPONDED" in ev
    # history preserved: two decision rows
    decs = [d["status"] for d in db.rows(env.conn, "SELECT status FROM decisions WHERE submission_id=? ORDER BY id", (sid,))]
    assert decs == ["PENDING", "APPROVED"]


def test_email_lists_only_open_items(env):
    r = env.run("1_happy_path", pan="", ifsc="")
    sid = r["submission_id"]
    assert r["status"] == "PENDING" and len(opens(env.conn, sid)) >= 2
    assert "PAN" in r["draft"]["body"] and "IFSC" in r["draft"]["body"]
    out = env.wf.vendor_response(env.conn, sid, field_changes={"pan": "AABCM4821K"}, upload_dir=env.up)
    assert out["run_no"] == 2
    dr = db.one(env.conn, "SELECT * FROM communication_drafts WHERE submission_id=? ORDER BY id DESC LIMIT 1", (sid,))
    assert "IFSC" in dr["body"] and "Please provide your PAN" not in dr["body"]


def test_reminders_day3_day7_and_stale(env):
    r = env.run("2_wrong_document")
    sid = r["submission_id"]
    base = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
    env.conn.execute("UPDATE follow_up_requests SET created_at=? WHERE submission_id=?", (base.isoformat(), sid))
    env.conn.commit()

    out = env.wf.reminders_due(env.conn, (base + dt.timedelta(days=4)).isoformat())
    assert any(x["submission_id"] == sid and "First reminder" in x["action"] for x in out)
    assert db.one(env.conn, "SELECT id FROM communication_drafts WHERE submission_id=? AND draft_type='reminder_1'", (sid,))

    out = env.wf.reminders_due(env.conn, (base + dt.timedelta(days=8)).isoformat())
    assert any(x["submission_id"] == sid and "Second reminder" in x["action"] for x in out)
    assert db.one(env.conn, "SELECT id FROM communication_drafts WHERE submission_id=? AND draft_type='reminder_2'", (sid,))

    out = env.wf.reminders_due(env.conn, (base + dt.timedelta(days=15)).isoformat())
    row = next(x for x in out if x["submission_id"] == sid)
    assert row["stale"] is True
    assert db.one(env.conn, "SELECT stale FROM submissions WHERE id=?", (sid,))["stale"] == 1


def test_reminders_are_not_drafted_twice(env):
    r = env.run("2_wrong_document")
    sid = r["submission_id"]
    base = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
    env.conn.execute("UPDATE follow_up_requests SET created_at=? WHERE submission_id=?", (base.isoformat(), sid))
    env.conn.commit()
    env.wf.reminders_due(env.conn, (base + dt.timedelta(days=4)).isoformat())
    env.wf.reminders_due(env.conn, (base + dt.timedelta(days=5)).isoformat())
    n = env.conn.execute("SELECT COUNT(*) FROM communication_drafts WHERE submission_id=? AND draft_type='reminder_1'", (sid,)).fetchone()[0]
    assert n == 1

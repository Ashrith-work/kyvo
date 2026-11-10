"""Part E: follow-up supersede, reminders and stale on an injectable clock, and delivery of the
approval confirmation. The same policy days (3, 7, 14) are used, now sourced from the policy file."""
import os, json
import database as db
import workflow as wf
import clock
import lifecycle
import inbox
from sample_data import generate as gen


def test_followup_is_superseded_when_the_ask_changes(env):
    r = env.run("2_wrong_document")
    sid = r["submission_id"]
    opens = db.rows(env.conn, "SELECT * FROM follow_up_requests WHERE submission_id=? AND status='OPEN'", (sid,))
    assert opens
    row = opens[0]
    changed = [{"rule_id": row["rule_id"], "label": row["about"], "status": "FAIL", "blocking": 1,
                "action": "A reworded different ask", "flag_code": "MISSING_DOCUMENT"}]
    wf._sync_follow_ups(env.conn, sid, 2, changed)
    assert db.one(env.conn, "SELECT status FROM follow_up_requests WHERE id=?", (row["id"],))["status"] == "SUPERSEDED"
    fresh = db.one(env.conn, "SELECT * FROM follow_up_requests WHERE submission_id=? AND rule_id=? AND status='OPEN'",
                   (sid, row["rule_id"]))
    assert fresh and fresh["ask"] == "A reworded different ask"


def test_tick_reminders_and_stale_on_fixed_clock(env, tmp_path):
    c = clock.at("2026-01-01T00:00:00+00:00")
    sid = env.run("2_wrong_document")["submission_id"]
    outbox = str(tmp_path / "outbox")
    c.set("2026-01-05T00:00:00+00:00")                       # day 4 -> first reminder
    rows = lifecycle.tick(env.conn, outbox_dir=outbox)
    assert any(x["submission_id"] == sid and "First reminder" in x["action"] for x in rows)
    c.set("2026-01-09T00:00:00+00:00")                       # day 8 -> second reminder
    rows = lifecycle.tick(env.conn, outbox_dir=outbox)
    assert any(x["submission_id"] == sid and "Second reminder" in x["action"] for x in rows)
    c.set("2026-01-16T00:00:00+00:00")                       # day 15 -> stale
    rows = lifecycle.tick(env.conn, outbox_dir=outbox)
    assert any(x["submission_id"] == sid and x["stale"] for x in rows)
    assert db.one(env.conn, "SELECT stale FROM submissions WHERE id=?", (sid,))["stale"] == 1
    blob = "\n".join(open(os.path.join(outbox, f), encoding="utf-8").read()
                     for f in os.listdir(outbox) if f.endswith(".eml"))
    assert "close as rejected" in blob and "reviewer@kyvo.example" in blob


def test_approval_confirmation_is_delivered(env, tmp_path):
    d = {k: str(tmp_path / k) for k in ("inbox", "out", "uploads", "outbox")}
    os.makedirs(d["inbox"], exist_ok=True)
    folder = os.path.join(d["inbox"], "001_new")
    os.makedirs(folder)
    docmap = {}
    for t, (fn, data) in gen.scenario_files(env.dir, env.sc["1_happy_path"]).items():
        open(os.path.join(folder, fn), "wb").write(data)
        docmap[t] = fn
    json.dump({"type": "NEW_SUBMISSION", "submission": env.sc["1_happy_path"]["form"], "documents": docmap},
              open(os.path.join(folder, "item.json"), "w"))
    res = inbox.process_inbox(env.conn, d["inbox"], d["out"], d["uploads"], d["outbox"])[0]
    assert res["status"] == "APPROVED"
    blob = "\n".join(open(os.path.join(d["outbox"], f), encoding="utf-8").read()
                     for f in os.listdir(d["outbox"]) if f.endswith(".eml"))
    assert "approved" in blob.lower() and "X-Kyvo-Sent: no" in blob

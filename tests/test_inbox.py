"""Part D: inbound processing, the four item types, sender/allowlist checks, idempotency,
the outbox never sending, and a bad item never blocking the queue."""
import os, json
import database as db
import inbox
from sample_data import generate as gen


def _dirs(tmp_path):
    d = {k: str(tmp_path / k) for k in ("inbox", "out", "uploads", "outbox")}
    os.makedirs(d["inbox"], exist_ok=True)
    return d


def _new_submission_item(inbox_root, name, scenario, sample_dir):
    folder = os.path.join(inbox_root, name)
    os.makedirs(folder, exist_ok=True)
    docmap = {}
    for t, (fn, data) in gen.scenario_files(sample_dir, scenario).items():
        with open(os.path.join(folder, fn), "wb") as fh:
            fh.write(data)
        docmap[t] = fn
    item = {"type": "NEW_SUBMISSION", "submission": scenario["form"], "documents": docmap}
    json.dump(item, open(os.path.join(folder, "item.json"), "w"))
    return folder


def _json_item(inbox_root, name, obj):
    folder = os.path.join(inbox_root, name)
    os.makedirs(folder, exist_ok=True)
    json.dump(obj, open(os.path.join(folder, "item.json"), "w"))
    return folder


def test_new_submission_issues_vendor_id_and_acks(env, tmp_path):
    d = _dirs(tmp_path)
    _new_submission_item(d["inbox"], "001_new", env.sc["1_happy_path"], env.dir)
    res = inbox.process_inbox(env.conn, d["inbox"], d["out"], d["uploads"], d["outbox"])
    assert len(res) == 1 and res[0]["vendor_id"].startswith("V")
    # acknowledgement written to the outbox, never sent
    emls = [f for f in os.listdir(d["outbox"]) if f.endswith(".eml")]
    assert emls
    body = open(os.path.join(d["outbox"], emls[0]), encoding="utf-8").read()
    assert "Vendor ID is V" in body and "X-Kyvo-Sent: no" in body
    # moved to processed/, out/<ref>/ written
    assert os.path.isdir(os.path.join(str(tmp_path), "processed"))
    assert os.path.exists(os.path.join(d["out"], res[0]["ref"], "decision.json"))


def test_idempotent_new_submission(env, tmp_path):
    d = _dirs(tmp_path)
    _new_submission_item(d["inbox"], "001_new", env.sc["1_happy_path"], env.dir)
    inbox.process_inbox(env.conn, d["inbox"], d["out"], d["uploads"], d["outbox"])
    _new_submission_item(d["inbox"], "001_new_again", env.sc["1_happy_path"], env.dir)
    res = inbox.process_inbox(env.conn, d["inbox"], d["out"], d["uploads"], d["outbox"])
    assert res[0]["status"] == "duplicate_skipped"
    assert db.one(env.conn, "SELECT COUNT(*) AS n FROM submissions")["n"] == 1


def test_vendor_reply_requires_registered_sender(env, tmp_path):
    d = _dirs(tmp_path)
    _new_submission_item(d["inbox"], "001_new", env.sc["3_bank_mismatch"], env.dir)
    r0 = inbox.process_inbox(env.conn, d["inbox"], d["out"], d["uploads"], d["outbox"])[0]
    ref, reg = r0["ref"], env.sc["3_bank_mismatch"]["form"]["email"]
    # reply from a stranger: ignored, no change
    _json_item(d["inbox"], "002_reply_bad", {"type": "VENDOR_REPLY", "ref": ref,
               "from": "stranger@evil.example", "fields": {"bank_holder": "BrightPath Facility Services Private Limited"}})
    r1 = inbox.process_inbox(env.conn, d["inbox"], d["out"], d["uploads"], d["outbox"])[0]
    assert r1.get("ignored") == "unregistered_sender"
    # reply from the registered address is applied
    _json_item(d["inbox"], "003_reply_ok", {"type": "VENDOR_REPLY", "ref": ref, "from": reg,
               "fields": {"bank_holder": "BrightPath Facility Services Private Limited"}})
    r2 = inbox.process_inbox(env.conn, d["inbox"], d["out"], d["uploads"], d["outbox"])[0]
    assert r2["run_no"] == 2


def test_reviewer_decision_allowlist_and_reason(env, tmp_path):
    d = _dirs(tmp_path)
    _new_submission_item(d["inbox"], "001_new", env.sc["3_bank_mismatch"], env.dir)
    ref = inbox.process_inbox(env.conn, d["inbox"], d["out"], d["uploads"], d["outbox"])[0]["ref"]
    sid = inbox.sid_from_ref(ref)
    # non-reviewer command is ignored
    _json_item(d["inbox"], "002_rev_bad", {"type": "REVIEWER_DECISION", "ref": ref,
               "from": "mallory@evil.example", "command": "APPROVE"})
    assert inbox.process_inbox(env.conn, d["inbox"], d["out"], d["uploads"], d["outbox"])[0]["ignored"] == "not_allowlisted"
    assert db.latest_decision(env.conn, sid)["status"] == "PENDING"
    # REJECT without a reason is refused (item fails, submission unchanged)
    _json_item(d["inbox"], "003_rev_noreason", {"type": "REVIEWER_DECISION", "ref": ref,
               "from": "reviewer@kyvo.example", "command": "REJECT"})
    r = inbox.process_inbox(env.conn, d["inbox"], d["out"], d["uploads"], d["outbox"])[0]
    assert r["status"] == "failed"
    assert db.latest_decision(env.conn, sid)["status"] == "PENDING"
    # a proper REJECT with a reason is honoured
    _json_item(d["inbox"], "004_rev_reject", {"type": "REVIEWER_DECISION", "ref": ref,
               "from": "reviewer@kyvo.example", "command": "REJECT: third-party bank account not resolved"})
    inbox.process_inbox(env.conn, d["inbox"], d["out"], d["uploads"], d["outbox"])
    assert db.latest_decision(env.conn, sid)["status"] == "REJECTED"


def test_bad_item_never_blocks_the_queue(env, tmp_path):
    d = _dirs(tmp_path)
    _json_item(d["inbox"], "001_garbage", {"type": "NONSENSE"})
    _new_submission_item(d["inbox"], "002_good", env.sc["1_happy_path"], env.dir)
    res = inbox.process_inbox(env.conn, d["inbox"], d["out"], d["uploads"], d["outbox"])
    statuses = {r["item"]: r.get("status", r.get("vendor_id")) for r in res}
    assert statuses["001_garbage"] == "failed"
    assert any(r.get("vendor_id") for r in res)       # the good item still processed
    assert os.path.isdir(os.path.join(str(tmp_path), "failed"))

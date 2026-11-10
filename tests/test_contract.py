"""Part A: submission/decision contract, CLI, TRC-for-International, new fields, and .docx/.doc handling."""
import json, os
import database as db
import workflow as wf
import extraction as ex
import cli
from config import required_docs


def flags(r): return {f["code"] for f in r["flags"]}
def rule(r, rid): return next((x for x in r["results"] if x["rule_id"] == rid), None)


def test_trc_required_for_international():
    assert "TRC" in required_docs({"region": "International"})
    assert "TRC" not in required_docs({"region": "Domestic", "domestic_type": "PAN with GST"})


def test_international_missing_trc_is_pending(env):
    from sample_data import generate as g
    s = env.sc["7_international_us"]
    files = g.scenario_files(env.dir, s); files.pop("TRC")
    r = env.wf.run_submission(env.conn, {**s["form"], "trc_number": ""}, files, env.up)
    assert r["status"] == "PENDING"
    assert {"MISSING_DOCUMENT", "MISSING_FIELD"} & flags(r)


def test_decision_json_matches_contract(env):
    r = env.run("2_wrong_document")
    rec = wf.decision_json(env.conn, r["submission_id"])
    for k in ("submission_id", "vendor_id", "status", "waiting_on", "risk_level", "policy_version",
              "run_no", "decided_at", "reasons", "requests_to_vendor", "next_action"):
        assert k in rec, k
    assert rec["status"] == "PENDING" and rec["waiting_on"] == "VENDOR"
    import config
    assert rec["policy_version"] == config.POLICY_VERSION
    assert rec["policy_version"].startswith("KYVO-POLICY-") and "+" in rec["policy_version"]
    assert rec["requests_to_vendor"] and all({"id", "about", "ask", "status"} <= set(x) for x in rec["requests_to_vendor"])
    assert all({"rule_id", "label", "status", "severity", "evidence", "source", "provenance"} <= set(x) for x in rec["reasons"])


def test_waiting_on_reviewer_for_duplicate(env):
    env.run("1_happy_path")
    r = env.run("5_duplicate_pan")
    assert wf.decision_json(env.conn, r["submission_id"])["waiting_on"] == "REVIEWER"


def test_waiting_on_none_when_approved(env):
    r = env.run("1_happy_path")
    rec = wf.decision_json(env.conn, r["submission_id"])
    assert rec["status"] == "APPROVED" and rec["waiting_on"] == "NONE"


def test_cli_exit_codes(tmp_path, env):
    s = env.sc["1_happy_path"]
    obj = dict(s["form"])
    obj["documents"] = {t: os.path.join(env.dir, name) for t, name in s["files"].items()}
    p = tmp_path / "sub.json"; p.write_text(json.dumps(obj), encoding="utf-8")
    assert cli.verify(str(p), str(tmp_path / "cli.db")) == 0        # APPROVED -> 0

    s2 = env.sc["2_wrong_document"]
    obj2 = dict(s2["form"])
    obj2["documents"] = {t: os.path.join(env.dir, name) for t, name in s2["files"].items()}
    p2 = tmp_path / "sub2.json"; p2.write_text(json.dumps(obj2), encoding="utf-8")
    assert cli.verify(str(p2), str(tmp_path / "cli2.db")) == 1      # PENDING -> 1


def test_state_and_pincode_required_domestic(env):
    r = env.run("1_happy_path", state="", city="", pincode="")
    assert r["status"] == "PENDING" and "MISSING_FIELD" in flags(r)


def test_pincode_format_flagged(env):
    r = env.run("1_happy_path", pincode="12A45")
    assert rule(r, "FMT_PINCODE")["status"] == "FAIL"


def test_docx_is_read_as_text(tmp_path):
    import docx
    p = tmp_path / "pan.docx"
    d = docx.Document()
    for line in ["INCOME TAX DEPARTMENT", "Permanent Account Number Card",
                 "PAN: AABCM4821K", "Name: Meridian Precision Components Private Limited"]:
        d.add_paragraph(line)
    d.save(str(p))
    rd = ex.read_document(str(p))
    assert rd["state"] == "TEXT" and "AABCM4821K" in "\n".join(rd["pages"])


def test_doc_is_held_for_review(tmp_path):
    p = tmp_path / "legacy.doc"; p.write_bytes(b"\xd0\xcf\x11\xe0 legacy word binary")
    rd = ex.read_document(str(p))
    assert rd["state"] == "UNREADABLE_DOC"

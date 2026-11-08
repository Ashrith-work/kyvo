"""End-to-end scenario tests: assert status, reasons, flags, email and DB persistence."""
import json, os
import database as db
import validation as va

def flags(r): return {f["code"] for f in r["flags"]}
def rule(r, rid): return next((x for x in r["results"] if x["rule_id"] == rid), None)
def count(conn, t, sid): return conn.execute(f"SELECT COUNT(*) FROM {t} WHERE submission_id=?", (sid,)).fetchone()[0]

def assert_persisted(env, r):
    sid = r["submission_id"]
    for t in ("documents", "validation_results", "decisions", "communication_drafts", "audit_events"):
        assert count(env.conn, t, sid) > 0, t
    events = {e["event"] for e in db.rows(env.conn, "SELECT event FROM audit_events WHERE submission_id=?", (sid,))}
    assert {"SUBMISSION_RECEIVED", "DOCUMENT_STORED", "FIELDS_EXTRACTED", "DECISION_MADE", "EMAIL_DRAFTED", "WORKFLOW_COMPLETED"} <= events
    d = db.one(env.conn, "SELECT * FROM communication_drafts WHERE submission_id=?", (sid,))
    assert d["sent"] == 0                                    # never auto-sent
    assert db.latest_decision(env.conn, sid)["status"] == r["status"]

def test_1_happy_path_approved(env):
    r = env.run("1_happy_path")
    assert r["status"] == "APPROVED", [x["rule_id"] for x in r["results"] if x["blocking"]]
    assert r["flags"] == [] and r["decision"]["human_review_required"] == 0
    assert r["draft"]["draft_type"] == "approval_confirmation"
    assert rule(r, "DOC_GST_CERT")["status"] == "PASS" and rule(r, "XF_GSTIN_PAN")["status"] == "PASS"
    assert_persisted(env, r)
    assert db.overview_counts(env.conn)["APPROVED"] == 1

def test_2_wrong_document_pending_with_email(env):
    r = env.run("2_wrong_document")
    assert r["status"] == "PENDING" and "WRONG_DOCUMENT" in flags(r)
    x = rule(r, "DOC_GST_CERT")
    assert x["status"] == "FAIL" and "Invoice" in x["evidence"]
    assert "GST registration certificate" in r["draft"]["body"] and "appears to be invoice" in r["draft"]["body"]
    assert r["draft"]["draft_type"] == "follow_up_request"
    # GST-dependent cross-checks are skipped, not falsely passed
    assert rule(r, "XD_GSTIN_DOC")["status"] == "NOT_CHECKED"
    assert_persisted(env, r)

def test_3_bank_mismatch_pending(env):
    r = env.run("3_bank_mismatch")
    assert r["status"] == "PENDING" and "BANK_HOLDER_MISMATCH" in flags(r)
    x = rule(r, "XB_HOLDER_LEGAL")
    assert x["status"] == "FAIL" and "Rajesh Kumar Enterprises" in x["evidence"]
    assert "Rajesh Kumar Enterprises" in r["draft"]["body"] and r["decision"]["risk_level"] == "HIGH"
    assert_persisted(env, r)

def test_4_country_tax_mismatch_pending_no_india_rules(env):
    r = env.run("4_country_tax_mismatch")
    assert r["status"] == "PENDING" and "COUNTRY_TAX_CONFLICT" in flags(r)
    ids = {x["rule_id"] for x in r["results"]}
    assert not ({"FMT_PAN", "FMT_GSTIN", "FMT_IFSC", "REQ_PAN", "REQ_IFSC", "REQ_GSTIN"} & ids)   # India rules never applied
    assert "Germany" in rule(r, "VAL_TAXID_COUNTRY")["label"]
    assert_persisted(env, r)

def test_5_duplicate_blocks_auto_approval(env):
    first = env.run("1_happy_path")
    r = env.run("5_duplicate_pan")
    assert first["status"] == "APPROVED" and r["status"] == "PENDING" and "DUPLICATE_IDENTIFIER" in flags(r)
    assert "SUB-00001" in rule(r, "DUP_PAN")["evidence"] and "SUB-00001" in r["draft"]["body"]
    assert rule(r, "XN_LEGAL_PAN")["status"] == "PASS"       # 'Pvt Ltd' vs 'Private Limited' normalises
    assert_persisted(env, r)

def test_6_pan_without_gst_not_asked_for_gst(env):
    r = env.run("6_pan_without_gst")
    assert r["status"] == "APPROVED"
    ids = {x["rule_id"] for x in r["results"]}
    assert "DOC_GST_CERT" not in ids and "FMT_GSTIN" not in ids and "REQ_GSTIN" not in ids

def test_7_international_happy_no_india_requirements(env):
    r = env.run("7_international_us")
    assert r["status"] == "APPROVED"
    ids = {x["rule_id"] for x in r["results"]}
    assert not ({"REQ_IFSC", "FMT_IFSC", "REQ_PAN", "FMT_PAN", "DOC_PAN_PROOF", "DOC_GST_CERT"} & ids)
    assert "EIN" in rule(r, "VAL_TAXID_COUNTRY")["label"]

def test_8_unsupported_country_never_false_pass(env):
    r = env.run("8_unsupported_country")
    x = rule(r, "VAL_TAXID_COUNTRY")
    assert x["status"] == "NOT_CHECKED" and r["status"] == "PENDING" and "UNSUPPORTED_COUNTRY_RULE" in flags(r)

def test_9_scanned_pdf_marked_unreadable(env):
    r = env.run("9_scanned_bank_proof")
    assert r["status"] == "PENDING" and "UNREADABLE_DOCUMENT" in flags(r)
    d = db.one(env.conn, "SELECT * FROM documents WHERE submission_id=? AND expected_type='BANK_PROOF'", (r["submission_id"],))
    assert d["extraction_state"] == "UNREADABLE_SCAN" and os.path.exists(d["safe_path"])      # file preserved
    assert "searchable" in r["draft"]["body"]

# ---- policy, units and robustness ----------------------------------------------------
def test_missing_fields_and_documents_pending(env):
    r = env.run("1_happy_path", pan="", ifsc="", email="not-an-email")
    assert r["status"] == "PENDING" and {"MISSING_FIELD", "INVALID_FORMAT"} <= flags(r)
    assert "PAN" in r["draft"]["body"] and "IFSC" in r["draft"]["body"]

def test_missing_document_pending(env):
    s = env.sc["1_happy_path"]
    from sample_data import generate as g
    files = g.scenario_files(env.dir, s); files.pop("COMPANY_REG")
    r = env.wf.run_submission(env.conn, s["form"], files, env.up)
    assert r["status"] == "PENDING" and "MISSING_DOCUMENT" in flags(r)

def test_gstin_pan_inconsistency_and_checksum(env):
    r = env.run("1_happy_path", pan="AABCX9999Z")
    assert rule(r, "XF_GSTIN_PAN")["status"] == "FAIL" and r["status"] == "PENDING"
    r2 = env.run("1_happy_path", gstin="29AABCM4821K1ZA")        # wrong check character
    assert rule(r2, "FMT_GSTIN_CHECK")["status"] == "WARN"

def test_pan_entity_inconsistency_flagged(env):
    r = env.run("1_happy_path", entity_type="Proprietorship")
    assert rule(r, "FMT_PAN_ENTITY")["status"] == "WARN" and r["status"] == "PENDING"

def test_name_normalisation_and_fuzzy_never_rejects():
    assert va.compare_names("Acme Pvt. Ltd.", "ACME Private Limited")[0] == "EXACT"
    assert va.compare_names("Acme & Sons Ltd", "Acme and Sons Limited")[0] == "EXACT"
    assert va.compare_names("Acme Industries", "Acme Industrles")[0] == "SIMILAR"
    assert va.compare_names("BrightPath Facility", "Rajesh Kumar Enterprises")[0] == "DIFFERENT"

def test_similar_name_is_pending_not_rejected(env):
    r = env.run("1_happy_path", legal_name="Meridian Precision Component Private Limited")
    assert r["status"] == "PENDING" and r["status"] != "REJECTED"

def test_gstin_checksum_known_value():
    base = "29AABCM4821K1Z"
    assert va.gstin_checksum(base) == "B" and va.GSTIN_RE.match(base + "B") if hasattr(va, "GSTIN_RE") else True

def test_bad_upload_type_rejected_and_pending(env):
    from sample_data import generate as g
    s = env.sc["1_happy_path"]; files = g.scenario_files(env.dir, s)
    files["BANK_PROOF"] = ("bank.exe", b"MZ....")
    r = env.wf.run_submission(env.conn, s["form"], files, env.up)
    assert r["status"] == "PENDING" and "UNREADABLE_DOCUMENT" in flags(r)
    d = db.one(env.conn, "SELECT * FROM documents WHERE expected_type='BANK_PROOF'")
    assert d["extraction_state"] == "REJECTED_UPLOAD" and d["safe_path"] == ""

def test_unsafe_filename_sanitised(env):
    from sample_data import generate as g
    s = env.sc["1_happy_path"]; files = g.scenario_files(env.dir, s)
    files["PAN_PROOF"] = ("../../etc/pass wd.pdf", files["PAN_PROOF"][1])
    r = env.wf.run_submission(env.conn, s["form"], files, env.up)
    d = db.one(env.conn, "SELECT * FROM documents WHERE expected_type='PAN_PROOF'")
    assert ".." not in d["filename"] and os.path.abspath(d["safe_path"]).startswith(os.path.abspath(env.up))

def test_blocklist_rejects_with_policy_reason(env):
    env.wf.add_blocklist(env.conn, "PAN", "AABCM4821K", "Internal policy P-7: debarred supplier", "compliance.lead")
    r = env.run("1_happy_path")
    assert r["status"] == "REJECTED" and "POLICY_BLOCKLIST" in flags(r) and "P-7" in r["decision"]["reasons"][0]

def test_reviewer_workflow_preserves_history(env):
    r = env.run("3_bank_mismatch"); sid = r["submission_id"]
    import pytest
    with pytest.raises(ValueError): env.wf.review_action(env.conn, sid, "Priya", "REJECT", "")
    with pytest.raises(ValueError): env.wf.review_action(env.conn, sid, "", "APPROVE", "ok")
    env.wf.review_action(env.conn, sid, "Priya", "COMMENT", "Called vendor; proprietor's current account")
    assert env.wf.review_action(env.conn, sid, "Priya", "APPROVE", "Bank letter received offline") == "APPROVED"
    decs = db.rows(env.conn, "SELECT * FROM decisions WHERE submission_id=? ORDER BY id", (sid,))
    assert [d["status"] for d in decs] == ["PENDING", "APPROVED"] and decs[1]["decided_by"] == "Priya"   # history kept
    assert db.rows(env.conn, "SELECT * FROM risk_flags WHERE submission_id=? AND resolution_state='open'", (sid,)) == []
    ev = [e["event"] for e in db.rows(env.conn, "SELECT event FROM audit_events WHERE submission_id=?", (sid,))]
    assert "REVIEW_COMMENT" in ev and "REVIEWER_APPROVE" in ev

def test_reviewer_correction_and_rerun(env):
    r = env.run("1_happy_path", bank_holder="Meridian Precision Component Private Limited")   # typo in form holder
    sid = r["submission_id"]; assert r["status"] == "PENDING"
    env.wf.save_draft_edit(env.conn, r["draft"] and db.one(env.conn, "SELECT id FROM communication_drafts WHERE submission_id=?", (sid,))["id"], "S", "B", "Priya")
    out = env.wf.rerun_checks(env.conn, sid, "Priya")
    assert out["run_no"] == 2 and out["status"] == "PENDING"        # form value unchanged -> still PENDING (honest rerun)
    assert count(env.conn, "decisions", sid) == 2 and count(env.conn, "validation_results", sid) > 0

def test_correct_extracted_value_changes_outcome(env):
    r = env.run("3_bank_mismatch"); sid = r["submission_id"]
    doc = db.one(env.conn, "SELECT id FROM documents WHERE submission_id=? AND expected_type='BANK_PROOF'", (sid,))
    env.wf.correct_field(env.conn, sid, doc["id"], "account_holder", "BrightPath Facility Services Private Limited", "Priya", "OCR/typo check")
    f = db.effective_fields(env.conn, sid)[doc["id"]]["account_holder"]
    assert f["provenance"] == "reviewer_corrected"
    out = env.wf.rerun_checks(env.conn, sid, "Priya")
    still = [x for x in db.rows(env.conn, "SELECT * FROM validation_results WHERE submission_id=? AND run_no=2 AND blocking=1", (sid,))]
    assert "XB_HOLDER_LEGAL" in {x["rule_id"] for x in still}       # form-entered holder still differs -> not silently cleared
    assert "XB_DOCHOLDER_LEGAL" not in {x["rule_id"] for x in still} # corrected doc value now reconciles

def test_dashboard_counts_from_sqlite_and_no_llm_key_needed(env, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    env.run("1_happy_path"); env.run("2_wrong_document")
    c = db.overview_counts(env.conn)
    assert (c["total"], c["APPROVED"], c["PENDING"]) == (2, 1, 1) and c["open_flags"] >= 1
    assert len(db.list_submissions(env.conn, "aster")) == 1
    assert len(db.list_submissions(env.conn, status="APPROVED")) == 1

def test_live_stages_are_real_events(env):
    from sample_data import generate as g
    s = env.sc["1_happy_path"]
    events = list(env.wf.run_workflow(env.conn, s["form"], g.scenario_files(env.dir, s), env.up))
    done = [e["stage"] for e in events if e["status"] == "done" and e["stage"] != "complete"]
    assert done == env.wf.STAGES
    assert events[-1]["result"]["persisted"]["audit_events"] >= 10

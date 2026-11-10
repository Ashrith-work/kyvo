"""Edge cases E1-E4. Each has a bundled scenario, an examples/ JSON and tests here."""
import os
import database as db


def flags(r): return {f["code"] for f in r["flags"]}
def rule(r, rid): return next((x for x in r["results"] if x["rule_id"] == rid), None)


# ---- E1: sole proprietor paid into the owner's personal account ----------------------
def test_e1_proprietor_into_own_account_is_approved(env):
    r = env.run("E1_proprietor")
    assert r["status"] == "APPROVED", [x["rule_id"] for x in r["results"] if x["blocking"]]
    x = rule(r, "XB_HOLDER_LEGAL")
    assert x["status"] == "PASS" and "proprietor" in x["evidence"].lower()
    assert rule(r, "XN_LEGAL_GST")["status"] == "PASS"       # proprietor vs GST legal name
    assert rule(r, "XN_TRADE_GST")["status"] == "PASS"       # firm vs GST trade name
    assert rule(r, "XN_LEGAL_PAN")["status"] == "PASS"       # proprietor vs PAN card


def test_e1_third_party_holder_stays_pending(env):
    r = env.run("E1_proprietor", bank_holder="Mahesh Kumar")   # not the proprietor
    assert r["status"] == "PENDING" and "BANK_HOLDER_MISMATCH" in flags(r)
    assert r["status"] != "REJECTED"


# ---- E3: one bank account, two vendors ----------------------------------------------
def test_e3_shared_bank_account_is_pending_and_reviewer(env):
    env.run("1_happy_path")                      # the prior vendor holding this account -> SUB-00001
    r = env.run("E3_shared_bank")
    assert r["status"] == "PENDING" and "BANK_SHARED" in flags(r)
    x = rule(r, "XR_BANK_SHARED")
    assert x["severity"] == "HIGH" and "SUB-00001" in x["evidence"]
    assert env.wf.decision_json(env.conn, r["submission_id"])["waiting_on"] == "REVIEWER"


def test_scenario_5_same_vendor_is_not_flagged_as_shared_bank(env):
    env.run("1_happy_path")
    r = env.run("5_duplicate_pan")               # same PAN+account -> same vendor, so NOT XR_BANK_SHARED
    assert "DUPLICATE_IDENTIFIER" in flags(r) and "BANK_SHARED" not in flags(r)


def test_e3b_blocklisted_account_is_rejected(env):
    env.wf.seed_demo(env.conn)
    r = env.run("E3b_blocklisted_bank")
    assert r["status"] == "REJECTED" and "POLICY_BLOCKLIST" in flags(r)
    assert "payment fraud" in r["decision"]["reasons"][0].lower()


# ---- E4: a resubmission that fixes the asked item and breaks another -----------------
def _cheque(co, ifsc, holder):
    return ["HDFC Bank", "CANCELLED CHEQUE / BANK ACCOUNT CONFIRMATION LETTER", "Bank: HDFC Bank",
            f"Account Holder: {holder}", f"Account Number: {co['account_number']}", f"IFSC: {ifsc}", "Branch: Main"]


def test_e4_resubmission_ledger_across_three_runs(env):
    from sample_data import generate as g
    co = env.sc["E4_resubmission"]["form"]
    legal = co["legal_name"]
    holder_rules = {"XB_HOLDER_DOC", "XB_DOCHOLDER_LEGAL"}

    # Run 1: bank-holder mismatch -> PENDING, a holder request is open.
    r1 = env.run("E4_resubmission")
    sid = r1["submission_id"]
    assert r1["status"] == "PENDING"
    open1 = {x["rule_id"] for x in db.rows(env.conn, "SELECT rule_id FROM follow_up_requests WHERE submission_id=? AND status='OPEN'", (sid,))}
    assert open1 & holder_rules

    # Run 2: correct holder, but a DIFFERENT IFSC than the form -> holder resolves, IFSC opens, still PENDING.
    p2 = os.path.join(env.dir, "e4_bank_run2.pdf"); g.make_pdf(p2, _cheque(co, "HDFC0002299", legal))
    with open(p2, "rb") as fh:
        out2 = env.wf.vendor_response(env.conn, sid, files={"BANK_PROOF": ("e4_bank_run2.pdf", fh.read())}, upload_dir=env.up)
    assert out2["run_no"] == 2 and out2["status"] == "PENDING"
    byrule = {x["rule_id"]: x for x in db.rows(env.conn, "SELECT * FROM follow_up_requests WHERE submission_id=?", (sid,))}
    assert any(r in byrule and byrule[r]["status"] == "RESOLVED" and byrule[r]["resolved_run"] == 2 for r in holder_rules)
    assert byrule.get("XB_IFSC") and byrule["XB_IFSC"]["status"] == "OPEN" and byrule["XB_IFSC"]["opened_run"] == 2

    # Run 3: clean cheque (right holder + right IFSC) -> APPROVED, no open requests.
    p3 = os.path.join(env.dir, "e4_bank_run3.pdf"); g.make_pdf(p3, _cheque(co, "HDFC0002200", legal))
    with open(p3, "rb") as fh:
        out3 = env.wf.vendor_response(env.conn, sid, files={"BANK_PROOF": ("e4_bank_run3.pdf", fh.read())}, upload_dir=env.up)
    assert out3["run_no"] == 3 and out3["status"] == "APPROVED"
    assert db.rows(env.conn, "SELECT * FROM follow_up_requests WHERE submission_id=? AND status='OPEN'", (sid,)) == []
    assert db.one(env.conn, "SELECT * FROM follow_up_requests WHERE submission_id=? AND rule_id='XB_IFSC'", (sid,))["resolved_run"] == 3


# ---- E2: bank-account change requested after approval (needs Part D change requests) -------
def test_e2_bank_change_after_approval(env):
    import portal as pt
    from sample_data import generate as g
    email = "anita@meridianprecision.example"
    legal = "Meridian Precision Components Private Limited"
    v = pt.signup(env.conn, "Domestic", email, domestic_type="PAN with GST", pan="AABCM4821K", gstin=g.gstin("29", "AABCM4821K"))
    pt.save_step(env.conn, v["id"], 1, {"legal_name": legal, "trading_name": "Meridian Precision", "contact_person": "Anita Rao",
                 "phone": "+91 98450 12345", "entity_type": "Private Limited Company", "address": "Plot 14, Bengaluru",
                 "state": "Karnataka", "city": "Bengaluru", "pincode": "560058", "proprietor_name": ""})
    pt.save_step(env.conn, v["id"], 2, {"bank_holder": legal, "account_number": "50200012345678", "bank_name": "HDFC Bank", "ifsc": "HDFC0001234"})
    pt.save_step(env.conn, v["id"], 3, {"service_category": "Manufacturing / Supply of goods", "service_description": "CNC parts"})
    pt.save_step(env.conn, v["id"], 4, {"terms_accepted": True})
    vendor = pt.get_vendor(env.conn, v["vendor_id"])
    r = pt.submit_registration(env.conn, vendor, g.scenario_files(env.dir, env.sc["1_happy_path"]), env.up)
    assert r["status"] == "APPROVED"
    sid = r["submission_id"]

    pt.seed_payments(env.conn, v["id"])
    assert len(pt.list_payments(env.conn, v["id"], "PENDING")) == 1
    cid = pt.create_change_request(env.conn, v["id"], "Bank details", "new account 99887766554433", "switching bank", "", True)
    assert pt.list_payments(env.conn, v["id"], "PENDING") == [] and len(pt.list_payments(env.conn, v["id"], "ON_HOLD")) == 1
    pt.decide_change_request(env.conn, cid, True, "ok to proceed", "admin")

    cheque = ["ICICI Bank", "CANCELLED CHEQUE / BANK ACCOUNT CONFIRMATION LETTER", "Bank: ICICI Bank",
              f"Account Holder: {legal}", "Account Number: 99887766554433", "IFSC: ICIC0005500", "Branch: Main"]
    p = os.path.join(env.dir, "e2_newbank.pdf"); g.make_pdf(p, cheque)
    new = {"bank_holder": legal, "account_number": "99887766554433", "bank_name": "ICICI Bank", "ifsc": "ICIC0005500"}
    with open(p, "rb") as fh:
        out = pt.apply_bank_change(env.conn, vendor, new, {"BANK_PROOF": ("e2_newbank.pdf", fh.read())}, env.up)
    assert out["status"] == "PENDING" and out["waiting_on"] == "REVIEWER"       # never auto-approved

    fl = {f["code"] for f in db.rows(env.conn, "SELECT code FROM risk_flags WHERE submission_id=?", (sid,))}
    assert "BANK_CHANGE" in fl
    draft = db.one(env.conn, "SELECT * FROM communication_drafts WHERE submission_id=? ORDER BY id DESC LIMIT 1", (sid,))
    assert draft["to_addr"] == email                                            # confirmation to the previously registered email
    ev = {e["event"] for e in db.rows(env.conn, "SELECT event FROM audit_events WHERE submission_id=?", (sid,))}
    assert "BANK_DETAILS_CHANGE_APPLIED" in ev                                  # old details preserved in the trail
    assert len(pt.list_payments(env.conn, v["id"], "ON_HOLD")) == 1            # still on hold

    env.wf.review_action(env.conn, sid, "Priya", "APPROVE", "Confirmed the new account with the vendor")
    assert pt.list_payments(env.conn, v["id"], "ON_HOLD") == [] and len(pt.list_payments(env.conn, v["id"], "PENDING")) == 1

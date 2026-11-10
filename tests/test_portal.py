"""Part D: vendor portal backend - sign-up, OTP, forgot ID, four-step registration, change requests, payments."""
import datetime as dt
import pytest
import database as db
import portal as pt
from sample_data import generate as g


def test_signup_issues_vendor_id_and_blocks_duplicate(env):
    v = pt.signup(env.conn, "Domestic", "anita@meridianprecision.example", domestic_type="PAN with GST",
                  pan="AABCM4821K", gstin=g.gstin("29", "AABCM4821K"))
    assert v["vendor_id"][0] == "V" and len(v["vendor_id"]) == 6
    with pytest.raises(ValueError):
        pt.signup(env.conn, "Domestic", "x@y.example", domestic_type="PAN with GST",
                  pan="AABCM4821K", gstin=g.gstin("29", "AABCM4821K"))


def test_otp_single_use_and_wrong_code(env):
    v = pt.signup(env.conn, "Domestic", "a@b.example", domestic_type="PAN without GST", pan="AAGFK7351M")
    otp = pt.issue_otp(env.conn, v["vendor_id"])
    bad = "000000" if otp != "000000" else "111111"
    assert not pt.verify_otp(env.conn, v["vendor_id"], bad)[0]
    assert pt.verify_otp(env.conn, v["vendor_id"], otp)[0]
    assert not pt.verify_otp(env.conn, v["vendor_id"], otp)[0]      # single use


def test_otp_expiry(env):
    v = pt.signup(env.conn, "Domestic", "a@b.example", domestic_type="PAN without GST", pan="AABPR1234K")
    otp = pt.issue_otp(env.conn, v["vendor_id"])
    future = dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=11)
    ok, msg = pt.verify_otp(env.conn, v["vendor_id"], otp, now=future)
    assert not ok and "expired" in msg.lower()


def test_forgot_vendor_id(env):
    gst = g.gstin("33", "AABCA7312P")
    v = pt.signup(env.conn, "Domestic", "a@b.example", domestic_type="PAN with GST", pan="AABCA7312P", gstin=gst)
    assert pt.forgot_vendor_id(env.conn, "Domestic", domestic_type="PAN with GST", gstin=gst) == v["vendor_id"]
    v2 = pt.signup(env.conn, "International", "intl@x.example", business_reg_number="HRB999000", trc_number="TRC-DE-1")
    assert pt.forgot_vendor_id(env.conn, "International", business_reg_number="HRB999000") == v2["vendor_id"]


def test_four_step_registration_submits_and_approves(env):
    v = pt.signup(env.conn, "Domestic", "anita@meridianprecision.example", domestic_type="PAN with GST",
                  pan="AABCM4821K", gstin=g.gstin("29", "AABCM4821K"))
    assert not pt.steps_complete(env.conn, v["id"])
    pt.save_step(env.conn, v["id"], 1, {"legal_name": "Meridian Precision Components Private Limited",
                 "trading_name": "Meridian Precision", "contact_person": "Anita Rao", "phone": "+91 98450 12345",
                 "entity_type": "Private Limited Company", "address": "Plot 14, Peenya, Bengaluru, Karnataka 560058",
                 "state": "Karnataka", "city": "Bengaluru", "pincode": "560058", "proprietor_name": ""})
    pt.save_step(env.conn, v["id"], 2, {"bank_holder": "Meridian Precision Components Private Limited",
                 "account_number": "50200012345678", "bank_name": "HDFC Bank", "ifsc": "HDFC0001234"})
    pt.save_step(env.conn, v["id"], 3, {"service_category": "Manufacturing / Supply of goods", "service_description": "CNC parts"})
    with pytest.raises(ValueError):
        pt.submit_registration(env.conn, pt.get_vendor(env.conn, v["vendor_id"]), {}, env.up)   # not complete yet
    pt.save_step(env.conn, v["id"], 4, {"terms_accepted": True})
    assert pt.steps_complete(env.conn, v["id"])
    files = g.scenario_files(env.dir, env.sc["1_happy_path"])
    r = pt.submit_registration(env.conn, pt.get_vendor(env.conn, v["vendor_id"]), files, env.up)
    assert r["status"] == "APPROVED"
    assert pt.vendor_status(env.conn, pt.get_vendor(env.conn, v["vendor_id"])) == "Approved"


def test_change_request_bank_holds_payments_and_admin_decides(env):
    v = pt.signup(env.conn, "Domestic", "a@b.example", domestic_type="PAN without GST", pan="AAHCS3108R")
    pt.seed_payments(env.conn, v["id"])
    assert len(pt.list_payments(env.conn, v["id"], "PENDING")) == 1
    pt.create_change_request(env.conn, v["id"], "Bank details", "new account 5566", "switched banks", "", True)
    assert pt.list_payments(env.conn, v["id"], "PENDING") == [] and len(pt.list_payments(env.conn, v["id"], "ON_HOLD")) == 1
    cr = pt.list_change_requests(env.conn, v["id"])[0]
    assert cr["status"] == "PENDING"
    with pytest.raises(ValueError):
        pt.decide_change_request(env.conn, cr["id"], False, "", "admin")   # reject needs a comment
    done = pt.decide_change_request(env.conn, cr["id"], True, "verified offline", "admin")
    assert done["status"] == "APPROVED"

"""OCR for photos and scans: end-to-end photo scenarios, identifier repair, the confidence rule, and the no-OCR fallback."""
import pytest
import database as db
import extraction as ex
import validation as va
from config import OCR_MIN_FIELD_CONF

needs_ocr = pytest.mark.skipif(not ex.ocr_available(), reason="Tesseract is not installed")

def flags(r): return {f["code"] for f in r["flags"]}
def rule(r, rid): return next((x for x in r["results"] if x["rule_id"] == rid), None)

# ---- end to end -------------------------------------------------------------------------
@needs_ocr
def test_10_phone_photos_read_by_ocr_and_approved(env):
    r = env.run("10_phone_photos")
    assert r["status"] == "APPROVED", sorted(flags(r))
    sid = r["submission_id"]
    states = {d["expected_type"]: d["extraction_state"] for d in db.rows(env.conn, "SELECT * FROM documents WHERE submission_id=?", (sid,))}
    assert states["PAN_PROOF"] == "OCR" and states["BANK_PROOF"] == "OCR" and states["GST_CERT"] == "TEXT"
    ocr_fields = db.rows(env.conn, "SELECT * FROM extracted_fields WHERE submission_id=? AND method='ocr'", (sid,))
    assert {f["field"] for f in ocr_fields} >= {"pan", "account_holder", "account_number", "ifsc"}
    assert all(0 < f["confidence"] <= 1 for f in ocr_fields)
    assert rule(r, "OCR_PAN_PROOF")["status"] == "PASS" and rule(r, "OCR_PAN_PROOF")["blocking"] == 0
    assert "by OCR" in rule(r, "XD_PAN_DOC")["source"]

@needs_ocr
def test_11_blurry_photo_held_as_unreadable(env):
    r = env.run("11_blurry_photo")
    assert r["status"] == "PENDING" and "UNREADABLE_DOCUMENT" in flags(r)
    d = db.one(env.conn, "SELECT * FROM documents WHERE submission_id=? AND expected_type='PAN_PROOF'", (r["submission_id"],))
    assert d["extraction_state"] == "UNREADABLE_IMAGE"
    assert "sharper photo" in r["draft"]["body"]

def test_no_ocr_engine_falls_back_to_review(env, monkeypatch):
    monkeypatch.setattr(ex, "ocr_available", lambda: False)
    r = env.run("10_phone_photos")
    assert r["status"] == "PENDING" and "UNREADABLE_DOCUMENT" in flags(r)
    d = db.one(env.conn, "SELECT * FROM documents WHERE submission_id=? AND expected_type='BANK_PROOF'", (r["submission_id"],))
    assert d["extraction_state"] == "UNSUPPORTED_IMAGE"

# ---- identifier repair by character position -----------------------------------------------
def test_repair_fixes_letter_digit_swaps_by_position():
    line, note = ex.repair_identifiers("IFSC: KKBKO004455")
    assert line == "IFSC: KKBK0004455" and "KKBKO004455" in note
    line, note = ex.repair_identifiers("PAN: AABCL55Z7Q")
    assert line == "PAN: AABCL5527Q" and note

def test_repair_keeps_gstin_only_when_checksum_passes():
    good = "36AABCL5527Q1Z" + va.gstin_checksum("36AABCL5527Q1Z")
    misread = good[:2] + "AABCL55Z7Q" + good[12:]               # digit read as letter inside the PAN segment
    line, note = ex.repair_identifiers(f"GSTIN: {misread}")
    assert line == f"GSTIN: {good}" and note
    wrong_check = good[:14] + ("A" if good[14] != "A" else "B")    # bad checksum: never "repaired" into a valid-looking value
    line, note = ex.repair_identifiers(f"GSTIN: {wrong_check}")
    assert line == f"GSTIN: {wrong_check}" and note is None

def test_repair_ignores_lines_without_an_identifier_label():
    assert ex.repair_identifiers("Branch: KKBKO004455") == ("Branch: KKBKO004455", None)

# ---- the confidence rule ------------------------------------------------------------------
FORM = dict(region="Domestic", domestic_type="PAN without GST", legal_name="Lotus Agro Foods Private Limited",
            bank_holder="Lotus Agro Foods Private Limited", account_number="6011223344", ifsc="KKBK0004455", pan="AABCL5527Q")

def bank_doc(value, conf, method="ocr"):
    f = dict(value=value, page=1, evidence=f"Account Number: {value}", provenance="extracted", method=method, confidence=conf)
    return {"BANK_PROOF": dict(filename="bank.jpg", classification_state="MATCH", fields={"account_number": f})}

def test_low_confidence_mismatch_is_a_warning_not_a_fail():
    r = next(x for x in va.reconcile(FORM, bank_doc("6011228344", OCR_MIN_FIELD_CONF - 0.2)) if x["rule_id"] == "XB_ACCT")
    assert r["status"] == "WARN" and r["blocking"] == 1 and r["flag_code"] == "LOW_CONFIDENCE_OCR"

def test_low_confidence_exact_match_passes_and_says_why():
    r = next(x for x in va.reconcile(FORM, bank_doc("6011223344", OCR_MIN_FIELD_CONF - 0.2)) if x["rule_id"] == "XB_ACCT")
    assert r["status"] == "PASS" and "equals the entered value exactly" in r["evidence"]

def test_high_confidence_mismatch_still_fails():
    r = next(x for x in va.reconcile(FORM, bank_doc("6011228344", 0.97)) if x["rule_id"] == "XB_ACCT")
    assert r["status"] == "FAIL" and r["flag_code"] == "ID_INCONSISTENT"

def test_reviewer_correction_is_never_treated_as_ocr():
    doc = bank_doc("6011223344", 0.1, method="manual")
    doc["BANK_PROOF"]["fields"]["account_number"]["provenance"] = "reviewer_corrected"
    r = next(x for x in va.reconcile(FORM, doc) if x["rule_id"] == "XB_ACCT")
    assert r["status"] == "PASS" and "OCR" not in r["evidence"]

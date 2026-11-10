"""Part H: measurement.

Extraction accuracy is measured per field and per read method (PDF text, OCR) against ground truth,
exact match after the same normalisation the rules use. Decision accuracy compares the expected status
to the actual one per scenario. Errors are split in two and never blended: unsafe errors (approved when
it should not be, which must be zero) and friction errors (held when it should have passed). A degradation
sweep re-renders the PAN card and bank proof as photos at several blur and resolution levels and shows
outcomes move from APPROVED to PENDING as reading degrades, never to a wrong APPROVED.
"""
import os, json, tempfile
import database as db
import workflow as wf
import validation as va
from sample_data import generate as gen

ID_FIELDS = {"pan", "gstin", "ifsc", "swift", "account_number", "tax_id", "reg_number"}
RUN_ORDER = ["1_happy_path", "2_wrong_document", "3_bank_mismatch", "4_country_tax_mismatch",
             "5_duplicate_pan", "6_pan_without_gst", "7_international_us", "8_unsupported_country",
             "9_scanned_bank_proof", "10_phone_photos", "11_blurry_photo",
             "E1_proprietor", "E3_shared_bank", "E3b_blocklisted_bank", "E4_resubmission"]


def _norm(field, value):
    if not value:
        return ""
    return va.norm_id(value) if field in ID_FIELDS else va.normalize_name(value)


def tesseract_available():
    try:
        import pytesseract
        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


def _fresh_conn():
    conn = db.connect(os.path.join(tempfile.mkdtemp(prefix="kyvo_eval_"), "e.db"))
    wf.seed_demo(conn)
    return conn


def _run_all(sample_dir):
    """Run every scenario in one DB in the required order (5 and E3 depend on scenario 1)."""
    conn = _fresh_conn()
    up = tempfile.mkdtemp(prefix="kyvo_eval_up_")
    scen = {s["id"]: s for s in gen.load_scenarios(sample_dir)}
    ids = {}
    for sid in RUN_ORDER:
        res = wf.run_submission(conn, scen[sid]["form"], gen.scenario_files(sample_dir, scen[sid]), up)
        ids[sid] = res["submission_id"]
    return conn, scen, ids


def _bump(d, key, ok):
    d.setdefault(key, [0, 0])
    d[key][0] += int(ok)
    d[key][1] += 1


def _acc(d):
    return {k: {"correct": v[0], "total": v[1], "accuracy": round(v[0] / v[1], 3) if v[1] else None}
            for k, v in sorted(d.items())}


def extraction_accuracy(conn, scen, ids, gt):
    by_method, by_field, excluded = {}, {}, {}
    for sid in RUN_ORDER:
        subid = ids[sid]
        docs = {d["id"]: d for d in db.rows(conn, "SELECT id, expected_type, extraction_state FROM documents WHERE submission_id=?", (subid,))}
        read = {}
        for f in db.rows(conn, "SELECT document_id, field, value FROM extracted_fields WHERE submission_id=? AND provenance IN ('extracted','vendor_resubmitted')", (subid,)):
            d = docs.get(f["document_id"])
            if d:
                read[(d["expected_type"], f["field"])] = f["value"]
        for doc_type, fields in gt.get(sid, {}).items():
            d = next((dd for dd in docs.values() if dd["expected_type"] == doc_type), None)
            method = "ocr" if (d and d["extraction_state"] == "OCR") else "pdf_text"
            for field, truth in fields.items():
                got = read.get((doc_type, field))
                ok = bool(got) and _norm(field, got) == _norm(field, truth)
                _bump(by_method, method, ok)
                _bump(by_field, field, ok)
    # fields a document does not print are excluded from accuracy; list them per scenario
    for sid in RUN_ORDER:
        miss = []
        for doc_type in gt.get(sid, {}):
            printed = set(gt[sid][doc_type])
            possible = {"PAN_PROOF": {"pan", "name"}, "GST_CERT": {"gstin", "pan", "name", "trade_name"},
                        "COMPANY_REG": {"name", "reg_number"},
                        "BANK_PROOF": {"account_holder", "account_number", "ifsc", "bank_name", "swift"},
                        "TAX_CERT": {"tax_id", "name"}, "TRC": {"name", "country"}}.get(doc_type, set())
            for f in sorted(possible - printed):
                miss.append(f"{doc_type}.{f}")
        if miss:
            excluded[sid] = miss
    return {"by_method": _acc(by_method), "by_field": _acc(by_field), "excluded_not_printed": excluded}


def decision_accuracy(conn, scen, ids):
    per, unsafe, friction, status_correct = [], 0, 0, 0
    for sid in RUN_ORDER:
        subid = ids[sid]
        d = db.latest_decision(conn, subid)
        actual = d["status"]
        expected = scen[sid]["expected"]
        flags = sorted({r["flag_code"] for r in db.rows(conn, "SELECT flag_code FROM validation_results WHERE submission_id=? AND run_no=? AND flag_code IS NOT NULL AND status!='PASS'", (subid, d["run_no"]))})
        is_unsafe = actual == "APPROVED" and expected != "APPROVED"
        is_friction = actual == "PENDING" and expected == "APPROVED"
        unsafe += int(is_unsafe)
        friction += int(is_friction)
        status_correct += int(actual == expected)
        per.append({"scenario": sid, "expected": expected, "actual": actual, "match": actual == expected,
                    "unsafe": is_unsafe, "friction": is_friction, "flags": flags})
    return {"scenarios": len(RUN_ORDER), "status_correct": status_correct,
            "status_accuracy": round(status_correct / len(RUN_ORDER), 3),
            "unsafe_errors": unsafe, "friction_errors": friction, "per_scenario": per}


def degradation_sweep(sample_dir, blurs=(0.0, 1.0, 2.0, 3.0, 4.0), scales=(1.0, 0.6, 0.3)):
    """Re-render the PAN card and bank proof as phone photos at each blur and resolution, run the full
    workflow, and record the outcome and whether the key values were read correctly."""
    scen = {s["id"]: s for s in gen.load_scenarios(sample_dir)}
    form = scen["1_happy_path"]["form"]
    base_files = gen.scenario_files(sample_dir, scen["1_happy_path"])   # clean GST + company reg reused
    truth = {"pan": form["pan"], "holder": form["bank_holder"], "account_number": form["account_number"], "ifsc": form["ifsc"]}
    pan_lines = ["INCOME TAX DEPARTMENT", "GOVT. OF INDIA", "Permanent Account Number Card",
                 f"PAN: {form['pan']}", f"Name: {form['legal_name']}", "Date of Incorporation: 12/03/2015"]
    bank_lines = [form["bank_name"], "CANCELLED CHEQUE / BANK ACCOUNT CONFIRMATION LETTER", f"Bank: {form['bank_name']}",
                  f"Account Holder: {form['bank_holder']}", f"Account Number: {form['account_number']}",
                  f"IFSC: {form['ifsc']}", "Branch: Main"]
    tmp = tempfile.mkdtemp(prefix="kyvo_sweep_")
    points, unsafe = [], 0
    for blur in blurs:
        for scale in scales:
            pan_p = os.path.join(tmp, f"pan_{blur}_{scale}.jpg")
            bank_p = os.path.join(tmp, f"bank_{blur}_{scale}.jpg")
            gen.make_photo(pan_p, pan_lines, seed=11, blur=blur, scale=scale)
            gen.make_photo(bank_p, bank_lines, seed=12, blur=blur, scale=scale)
            files = {"GST_CERT": base_files["GST_CERT"], "COMPANY_REG": base_files["COMPANY_REG"],
                     "PAN_PROOF": (os.path.basename(pan_p), open(pan_p, "rb").read()),
                     "BANK_PROOF": (os.path.basename(bank_p), open(bank_p, "rb").read())}
            conn = _fresh_conn()
            up = tempfile.mkdtemp(prefix="kyvo_sweep_up_")
            res = wf.run_submission(conn, dict(form), files, up)
            subid = res["submission_id"]
            read = {(d["expected_type"], f["field"]): f["value"]
                    for d in db.rows(conn, "SELECT id, expected_type FROM documents WHERE submission_id=?", (subid,))
                    for f in db.rows(conn, "SELECT field, value FROM extracted_fields WHERE document_id=?", (d["id"],))}
            pan_ok = _norm("pan", read.get(("PAN_PROOF", "pan"))) == _norm("pan", truth["pan"])
            # a wrong approve = APPROVED while the key identifier was not read correctly
            wrong_approve = res["status"] == "APPROVED" and not pan_ok
            unsafe += int(wrong_approve)
            points.append({"blur": blur, "scale": scale, "outcome": res["status"], "pan_read_correct": pan_ok,
                           "wrong_approve": wrong_approve})
    approved = sum(1 for p in points if p["outcome"] == "APPROVED")
    return {"levels": len(points), "approved": approved, "pending": len(points) - approved,
            "unsafe_errors": unsafe, "points": points}


def run(conn=None, sample_dir=None, out_root="out"):
    if sample_dir is None:
        sample_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sample_data")
    if not os.path.exists(os.path.join(sample_dir, "ground_truth.json")):
        gen.build(sample_dir)
    gt = gen.load_ground_truth(sample_dir)
    rconn, scen, ids = _run_all(sample_dir)
    extraction = extraction_accuracy(rconn, scen, ids, gt)
    decision = decision_accuracy(rconn, scen, ids)
    sweep = degradation_sweep(sample_dir)
    report = {
        "policy_version": __import__("config").POLICY_VERSION,
        "tesseract_available": tesseract_available(),
        "extraction": extraction,
        "decision": decision,
        "degradation_sweep": sweep,
        "summary": {"status_accuracy": decision["status_accuracy"], "unsafe_errors": decision["unsafe_errors"],
                    "friction_errors": decision["friction_errors"], "sweep_unsafe_errors": sweep["unsafe_errors"],
                    "pdf_text_accuracy": extraction["by_method"].get("pdf_text", {}).get("accuracy"),
                    "ocr_accuracy": extraction["by_method"].get("ocr", {}).get("accuracy")},
    }
    out_dir = os.path.join(out_root, "eval")
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "eval.json"), "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)
    with open(os.path.join(out_dir, "eval.md"), "w", encoding="utf-8") as fh:
        fh.write(_report_md(report))
    return report


def _report_md(r):
    s = r["summary"]
    L = ["# Kyvo evaluation", "",
         f"Policy: {r['policy_version']}. Tesseract OCR available: {'yes' if r['tesseract_available'] else 'no'}.", "",
         "## Headline", "",
         f"- Decision status accuracy: {s['status_accuracy']}",
         f"- Unsafe errors (approved when it should not be): {s['unsafe_errors']} (must be zero)",
         f"- Friction errors (held when it should have passed): {s['friction_errors']}",
         f"- Degradation sweep unsafe errors: {s['sweep_unsafe_errors']} (must be zero)",
         f"- Extraction accuracy, PDF text: {s['pdf_text_accuracy']}; OCR: {s['ocr_accuracy']}", "",
         "## Extraction accuracy by read method", ""]
    for m, v in r["extraction"]["by_method"].items():
        L.append(f"- {m}: {v['accuracy']} ({v['correct']}/{v['total']})")
    L += ["", "## Decision accuracy by scenario", "", "scenario | expected | actual | unsafe | friction", "--- | --- | --- | --- | ---"]
    for p in r["decision"]["per_scenario"]:
        L.append(f"{p['scenario']} | {p['expected']} | {p['actual']} | {'yes' if p['unsafe'] else ''} | {'yes' if p['friction'] else ''}")
    L += ["", "## Degradation sweep", "",
          f"{r['degradation_sweep']['approved']} approved, {r['degradation_sweep']['pending']} pending across "
          f"{r['degradation_sweep']['levels']} blur/resolution levels; "
          f"{r['degradation_sweep']['unsafe_errors']} wrong approvals.", ""]
    return "\n".join(L)

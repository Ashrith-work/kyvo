"""Orchestrates the real stages. The Streamlit UI and the tests call the SAME function (run_workflow)."""
import os, re, json, uuid, hashlib
from config import *
import database as db
import extraction as ex
import validation as va
import decision_engine as de
import communication as cm

STAGES = ["Submission received", "Document intake", "Classification", "Field extraction", "Validation",
          "Cross-document reconciliation", "Risk assessment", "Decision", "Email draft", "Audit persistence"]

def _safe_name(name):
    return re.sub(r"[^A-Za-z0-9._-]", "_", os.path.basename(name or "file"))[:80]

def normalize_form(form: dict) -> dict:
    f = {k: (v.strip() if isinstance(v, str) else v) for k, v in form.items()}
    for k in ("pan", "gstin", "ifsc", "swift", "tax_id", "reg_number"):
        if f.get(k):
            f[k] = re.sub(r"\s", "", f[k]).upper()
    if f.get("region") != "International":
        f["country"] = "India"
    return f

def vendor_key(f):
    if f.get("pan"): return f"PAN:{f['pan']}"
    if f.get("tax_id"): return f"TAX:{f.get('country')}:{f['tax_id']}"
    return f"NAME:{va.normalize_name(f.get('legal_name', ''))}"

def _load_docs(conn, sid) -> dict:
    eff = db.effective_fields(conn, sid)
    docs = {}
    for d in db.rows(conn, "SELECT * FROM documents WHERE submission_id=?", (sid,)):
        d["fields"] = eff.get(d["id"], {})
        docs[d["expected_type"]] = d
    return docs

def _persist_results(conn, sid, run_no, results):
    for r in results:
        db.insert(conn, "validation_results", submission_id=sid, run_no=run_no, stage=r["stage"], rule_id=r["rule_id"],
                  label=r["label"], status=r["status"], severity=r["severity"], blocking=r["blocking"], source=r["source"],
                  provenance=r["provenance"], evidence=r["evidence"], explanation=r["explanation"], action=r["action"],
                  flag_code=r["flag_code"], created_at=db.now())

def _summ(results):
    return {s: sum(1 for r in results if r["status"] == s) for s in ("PASS", "FAIL", "WARN", "NOT_CHECKED")}

def _make_flags(conn, sid, run_no, results):
    flags = []
    for r in results:
        if r["status"] != "PASS" and r["flag_code"] and r["blocking"]:
            db.insert(conn, "risk_flags", submission_id=sid, run_no=run_no, code=r["flag_code"], severity=r["severity"],
                      details=f"{r['label']}: {r['evidence']}", created_at=db.now())
            flags.append({"code": r["flag_code"], "severity": r["severity"], "details": r["label"]})
    return flags

def _decide_and_draft(conn, sid, run_no, form, results, actor):
    dec = de.decide(results)
    did = db.insert(conn, "decisions", submission_id=sid, run_no=run_no, status=dec["status"],
                    reasons_json=json.dumps(dec["reasons"]), risk_level=dec["risk_level"], next_action=dec["next_action"],
                    human_review_required=dec["human_review_required"], policy_version=dec["policy_version"],
                    decided_by=actor, created_at=db.now())
    conn.execute("UPDATE submissions SET status=?, run_no=? WHERE id=?", (dec["status"], run_no, sid)); conn.commit()
    db.audit(conn, sid, "DECISION_MADE", actor, {"status": dec["status"], "run_no": run_no, "reasons": dec["reasons"], "policy": dec["policy_version"]})
    return dec, did

def _draft(conn, sid, did, form, dec, results):
    d = cm.build_draft(form, db.ref(sid), dec, results)
    cid = db.insert(conn, "communication_drafts", submission_id=sid, decision_id=did, sent=0, created_at=db.now(), **d)
    db.audit(conn, sid, "EMAIL_DRAFTED", "system", {"draft_id": cid, "type": d["draft_type"], "sent": False})
    return d, cid

def run_workflow(conn, form: dict, files: dict, upload_dir: str, actor: str = "system"):
    """Generator of stage events. files: {expected_type: (filename, bytes)}. Final event has stage='complete'."""
    def ev(stage, status, **out): return {"stage": stage, "status": status, "ts": db.now(), "output": out}

    # 1 submission received
    yield ev(STAGES[0], "running")
    f = normalize_form(form)
    if f.get("terms_accepted") and not f.get("terms_accepted_at"):
        f["terms_accepted_at"] = db.now()
    key = vendor_key(f)
    v = db.one(conn, "SELECT id FROM vendors WHERE vendor_key=?", (key,))
    vid = v["id"] if v else db.insert(conn, "vendors", vendor_key=key, legal_name=f.get("legal_name"), region=f.get("region"),
                                       country=f.get("country"), created_at=db.now())
    sid = db.insert(conn, "submissions", vendor_id=vid, snapshot_json=json.dumps(f, default=str), status="IN_PROGRESS", run_no=1, created_at=db.now())
    for t, val in va.identifiers(f):
        db.insert(conn, "vendor_identifiers", vendor_id=vid, submission_id=sid, id_type=t, id_value=val)
    db.audit(conn, sid, "SUBMISSION_RECEIVED", actor, {"vendor_key": key, "region": f.get("region"), "type": f.get("domestic_type")})
    ref = db.ref(sid)
    yield ev(STAGES[0], "done", ref=ref, vendor_key=key, existing_vendor=bool(v), region=f.get("region"),
             vendor_type=f.get("domestic_type") if f.get("region") != "International" else f.get("country"),
             required_documents=[DOC_LABELS[d] for d in required_docs(f)])

    # 2 document intake
    yield ev(STAGES[1], "running")
    folder = os.path.join(upload_dir, ref); os.makedirs(folder, exist_ok=True)
    intake = []
    for dtype, (fname, data) in (files or {}).items():
        if dtype not in DOC_LABELS or dtype in ("INVOICE", "UNKNOWN") or not data:
            continue
        ext = os.path.splitext(fname)[1].lower()
        sha = hashlib.sha256(data).hexdigest()
        base = dict(submission_id=sid, expected_type=dtype, filename=_safe_name(fname), size_bytes=len(data), sha256=sha,
                    detected_type=None, classification_state=None, classification_score=0, classification_evidence="", created_at=db.now())
        if ext not in ALLOWED_EXT or len(data) > MAX_UPLOAD_MB * 1024 * 1024:
            note = f"Rejected upload: extension '{ext}' or size {len(data)} bytes violates demo limits ({', '.join(sorted(ALLOWED_EXT))}, <= {MAX_UPLOAD_MB} MB)."
            db.insert(conn, "documents", safe_path="", page_count=0, extraction_state="REJECTED_UPLOAD", text_chars=0, note=note, **base)
            intake.append({"document": DOC_LABELS[dtype], "file": fname, "state": "REJECTED_UPLOAD", "note": note})
            continue
        path = os.path.join(folder, f"{uuid.uuid4().hex[:8]}_{_safe_name(fname)}")
        with open(path, "wb") as fh: fh.write(data)
        rd = ex.read_document(path)
        did = db.insert(conn, "documents", safe_path=path, page_count=rd["page_count"], extraction_state=rd["state"],
                        text_chars=rd["chars"], note=rd.get("note", ""), **base)
        _cache.setdefault(sid, {})[did] = rd["pages"]
        intake.append({"document": DOC_LABELS[dtype], "file": _safe_name(fname), "bytes": len(data), "sha256": sha[:12],
                       "pages": rd["page_count"], "state": rd["state"], "note": rd.get("note", "")})
        db.audit(conn, sid, "DOCUMENT_STORED", actor, {"expected": dtype, "file": _safe_name(fname), "sha256": sha, "state": rd["state"]})
    yield ev(STAGES[1], "done", documents=intake)

    # 3 classification
    yield ev(STAGES[2], "running")
    cls = []
    for d in db.rows(conn, "SELECT * FROM documents WHERE submission_id=?", (sid,)):
        if d["extraction_state"] != "TEXT":
            db.audit(conn, sid, "DOCUMENT_UNREADABLE", "system", {"file": d["filename"], "state": d["extraction_state"]})
            cls.append({"document": DOC_LABELS[d["expected_type"]], "result": d["extraction_state"], "detail": d["note"]})
            conn.execute("UPDATE documents SET classification_state='UNREADABLE', detected_type='UNKNOWN' WHERE id=?", (d["id"],)); conn.commit()
            continue
        c = ex.classify(_cache[sid][d["id"]], d["expected_type"])
        conn.execute("UPDATE documents SET detected_type=?, classification_state=?, classification_score=?, classification_evidence=? WHERE id=?",
                     (c["detected"], c["state"], c["score"], c["evidence"], d["id"])); conn.commit()
        cls.append({"document": DOC_LABELS[d["expected_type"]], "expected": d["expected_type"], "detected": c["detected"],
                    "result": c["state"], "score": c["score"], "evidence": c["evidence"]})
    db.audit(conn, sid, "DOCUMENTS_CLASSIFIED", "system", {"results": cls})
    yield ev(STAGES[2], "done", classification=cls)

    # 4 extraction
    yield ev(STAGES[3], "running")
    ext_out = []
    for d in db.rows(conn, "SELECT * FROM documents WHERE submission_id=?", (sid,)):
        if d["classification_state"] not in ("MATCH", "UNCERTAIN"):
            ext_out.append({"document": DOC_LABELS[d["expected_type"]], "fields": "skipped (" + str(d["classification_state"]) + ")"})
            continue
        got = ex.extract_fields(d["expected_type"], _cache[sid][d["id"]])
        for g in got:
            db.insert(conn, "extracted_fields", submission_id=sid, document_id=d["id"], field=g["field"], value=g["value"],
                      page=g["page"], evidence=g["evidence"], method=g["method"], confidence=g["confidence"],
                      provenance="extracted", created_at=db.now())
        ext_out.append({"document": DOC_LABELS[d["expected_type"]], "fields": {g["field"]: g["value"] for g in got}})
    db.audit(conn, sid, "FIELDS_EXTRACTED", "system", {"documents": len(ext_out)})
    yield ev(STAGES[3], "done", extracted=ext_out)
    _cache.pop(sid, None)

    # 5-9 evaluation
    docs = _load_docs(conn, sid)
    yield ev(STAGES[4], "running")
    r_val = va.validate_form(f, docs); _persist_results(conn, sid, 1, r_val)
    db.audit(conn, sid, "VALIDATION_COMPLETED", "system", _summ(r_val))
    yield ev(STAGES[4], "done", counts=_summ(r_val), results=r_val)
    yield ev(STAGES[5], "running")
    r_rec = va.reconcile(f, docs); _persist_results(conn, sid, 1, r_rec)
    db.audit(conn, sid, "RECONCILIATION_COMPLETED", "system", _summ(r_rec))
    yield ev(STAGES[5], "done", counts=_summ(r_rec), results=r_rec)
    yield ev(STAGES[6], "running")
    idents = va.identifiers(f)
    r_risk = va.risk_checks(f, db.find_duplicates(conn, sid, idents), db.blocklist_hits(conn, idents)); _persist_results(conn, sid, 1, r_risk)
    allr = r_val + r_rec + r_risk
    flags = _make_flags(conn, sid, 1, allr)
    db.audit(conn, sid, "RISK_ASSESSED", "system", {"flags": [x["code"] for x in flags]})
    yield ev(STAGES[6], "done", flags=flags, results=r_risk)
    yield ev(STAGES[7], "running")
    dec, did = _decide_and_draft(conn, sid, 1, f, allr, "system")
    yield ev(STAGES[7], "done", decision_status=dec["status"], reasons=dec["reasons"], risk_level=dec["risk_level"],
             next_action=dec["next_action"], human_review_required=bool(dec["human_review_required"]), policy_version=dec["policy_version"])
    yield ev(STAGES[8], "running")
    draft, cid = _draft(conn, sid, did, f, dec, allr)
    yield ev(STAGES[8], "done", subject=draft["subject"], draft_id=cid, sent=False)

    # 10 audit persistence (verify rows really exist)
    yield ev(STAGES[9], "running")
    db.audit(conn, sid, "WORKFLOW_COMPLETED", actor, {"status": dec["status"]})
    persisted = {t: conn.execute(f"SELECT COUNT(*) FROM {t} WHERE submission_id=?", (sid,)).fetchone()[0]
                 for t in ("documents", "extracted_fields", "validation_results", "risk_flags", "decisions", "communication_drafts", "audit_events")}
    yield ev(STAGES[9], "done", persisted_rows=persisted)
    yield {"stage": "complete", "status": "done", "ts": db.now(), "output": {},
           "result": {"submission_id": sid, "ref": ref, "status": dec["status"], "decision": dec, "results": allr, "flags": flags,
                      "draft": draft, "persisted": persisted}}

_cache = {}   # in-memory page text between stages of one run (never persisted; files are on disk)

def run_submission(conn, form, files, upload_dir, actor="system"):
    last = None
    for e in run_workflow(conn, form, files, upload_dir, actor):
        last = e
    return last["result"]

# ---- reviewer actions & re-run -----------------------------------------------------
def correct_field(conn, sid, document_id, field, new_value, reviewer, reason):
    old = db.effective_fields(conn, sid).get(document_id, {}).get(field)
    db.insert(conn, "extracted_fields", submission_id=sid, document_id=document_id, field=field, value=new_value.strip(),
              page=(old or {}).get("page"), evidence=f"Reviewer correction by {reviewer}: {reason}", method="manual",
              confidence=1.0, provenance="reviewer_corrected", created_at=db.now())
    db.audit(conn, sid, "FIELD_CORRECTED", reviewer, {"document_id": document_id, "field": field, "old": (old or {}).get("value"), "new": new_value, "reason": reason})

def rerun_checks(conn, sid, reviewer):
    """Re-evaluate with current (possibly corrected) values. Appends a new run; earlier runs are preserved."""
    s = db.one(conn, "SELECT * FROM submissions WHERE id=?", (sid,))
    f, run_no = json.loads(s["snapshot_json"]), s["run_no"] + 1
    docs = _load_docs(conn, sid)
    idents = va.identifiers(f)
    allr = (va.validate_form(f, docs) + va.reconcile(f, docs) +
            va.risk_checks(f, db.find_duplicates(conn, sid, idents), db.blocklist_hits(conn, idents)))
    _persist_results(conn, sid, run_no, allr)
    _make_flags(conn, sid, run_no, allr)
    dec, did = _decide_and_draft(conn, sid, run_no, f, allr, reviewer)
    _draft(conn, sid, did, f, dec, allr)
    db.audit(conn, sid, "CHECKS_RERUN", reviewer, {"run_no": run_no, "status": dec["status"]})
    return {"status": dec["status"], "run_no": run_no, "reasons": dec["reasons"]}

def review_action(conn, sid, reviewer, action, comment):
    """action: APPROVE | REJECT | COMMENT. Comment mandatory for APPROVE/REJECT. Creates a new decision row."""
    if not reviewer.strip():
        raise ValueError("Reviewer name is required.")
    if action in ("APPROVE", "REJECT") and not comment.strip():
        raise ValueError("A comment is required for approve/reject.")
    s = db.one(conn, "SELECT * FROM submissions WHERE id=?", (sid,))
    if action == "COMMENT":
        db.audit(conn, sid, "REVIEW_COMMENT", reviewer, {"comment": comment}); return
    status = "APPROVED" if action == "APPROVE" else "REJECTED"
    if status == "APPROVED":
        conn.execute("UPDATE risk_flags SET resolution_state='resolved', resolved_by=?, resolution_comment=? WHERE submission_id=? AND run_no=? AND resolution_state='open'",
                     (reviewer, comment, sid, s["run_no"])); conn.commit()
    reasons = [f"Reviewer {reviewer}: {comment}"]
    did = db.insert(conn, "decisions", submission_id=sid, run_no=s["run_no"], status=status, reasons_json=json.dumps(reasons),
                    risk_level="LOW" if status == "APPROVED" else "HIGH",
                    next_action="Activate vendor." if status == "APPROVED" else "Communicate the confirmed rejection to the vendor.",
                    human_review_required=0, policy_version=POLICY_VERSION, decided_by=reviewer, comment=comment, created_at=db.now())
    conn.execute("UPDATE submissions SET status=? WHERE id=?", (status, sid)); conn.commit()
    db.audit(conn, sid, f"REVIEWER_{action}", reviewer, {"comment": comment, "decision_id": did})
    f = json.loads(s["snapshot_json"])
    results = db.rows(conn, "SELECT * FROM validation_results WHERE submission_id=? AND run_no=?", (sid, s["run_no"]))
    _draft(conn, sid, did, f, {"status": status}, results)
    return status

def save_draft_edit(conn, draft_id, subject, body, editor):
    conn.execute("UPDATE communication_drafts SET subject=?, body=?, edited_at=? WHERE id=?", (subject, body, db.now(), draft_id)); conn.commit()
    d = db.one(conn, "SELECT submission_id FROM communication_drafts WHERE id=?", (draft_id,))
    db.audit(conn, d["submission_id"], "EMAIL_DRAFT_EDITED", editor, {"draft_id": draft_id})

def add_blocklist(conn, id_type, id_value, reason, by):
    db.insert(conn, "policy_blocklist", id_type=id_type, id_value=id_value.strip().upper(), reason=reason, added_by=by, created_at=db.now())
    db.audit(conn, None, "BLOCKLIST_ADDED", by, {"id_type": id_type, "id_value": id_value, "reason": reason})

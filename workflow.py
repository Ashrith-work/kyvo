"""Orchestrates the real stages. The Streamlit UI and the tests call the SAME function (run_workflow)."""
import os, re, json, uuid, hashlib
from config import *
import database as db
import extraction as ex
import validation as va
import decision_engine as de
import communication as cm
import assurance as asr

STAGES = ["Submission received", "Document intake", "Classification", "Field extraction", "Validation",
          "Cross-document reconciliation", "Risk assessment", "Decision", "Email draft", "Audit persistence"]

def _safe_name(name):
    return re.sub(r"[^A-Za-z0-9._-]", "_", os.path.basename(name or "file"))[:80]

# Manual (vendor portal) field names accepted as aliases for Kyvo's internal keys.
FORM_ALIASES = {"firm_name": "legal_name", "ownership": "entity_type", "business_reg_number": "reg_number",
                "company_phone": "phone", "contact_name": "contact_person"}

def normalize_form(form: dict) -> dict:
    form = dict(form)
    for alias, internal in FORM_ALIASES.items():          # accept manual field names; internal keys win if both present
        if alias in form and not form.get(internal):
            form[internal] = form.pop(alias)
    if (form.get("address_line1") or form.get("address_line2")) and not form.get("address"):
        form["address"] = ", ".join(x for x in (form.get("address_line1"), form.get("address_line2")) if x)
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
                  flag_code=r["flag_code"], assurance=r.get("assurance"), created_at=db.now())

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
    augmented = asr.enforce_two_source(results, form)   # Part B two-source gate
    extra = augmented[len(results):]
    if extra:                                           # the gate fired: persist, flag and open its request too
        _persist_results(conn, sid, run_no, extra)
        _make_flags(conn, sid, run_no, extra)
        _sync_follow_ups(conn, sid, run_no, augmented)
    results = augmented
    dec = de.decide(results)
    dec["waiting_on"] = de.compute_waiting_on(results, dec["status"])
    did = db.insert(conn, "decisions", submission_id=sid, run_no=run_no, status=dec["status"],
                    reasons_json=json.dumps(dec["reasons"]), risk_level=dec["risk_level"], next_action=dec["next_action"],
                    human_review_required=dec["human_review_required"], policy_version=dec["policy_version"],
                    waiting_on=dec["waiting_on"], decided_by=actor, created_at=db.now())
    conn.execute("UPDATE submissions SET status=?, run_no=?, waiting_on=? WHERE id=?", (dec["status"], run_no, dec["waiting_on"], sid)); conn.commit()
    db.audit(conn, sid, "DECISION_MADE", actor, {"status": dec["status"], "run_no": run_no, "waiting_on": dec["waiting_on"], "reasons": dec["reasons"], "policy": dec["policy_version"]})
    return dec, did

def _draft(conn, sid, did, form, dec, results, open_requests=None):
    d = cm.build_draft(form, db.ref(sid), dec, results, open_requests)
    cid = db.insert(conn, "communication_drafts", submission_id=sid, decision_id=did, sent=0, created_at=db.now(), **d)
    db.audit(conn, sid, "EMAIL_DRAFTED", "system", {"draft_id": cid, "type": d["draft_type"], "sent": False})
    return d, cid

def _store_documents(conn, sid, ref, files, upload_dir, actor):
    """Stage 2 for one set of files. Returns (intake rows, list of new document ids). Shared by the first run and vendor responses."""
    folder = os.path.join(upload_dir, ref); os.makedirs(folder, exist_ok=True)
    intake, new_ids = [], []
    for dtype, (fname, data) in (files or {}).items():
        if dtype not in DOC_LABELS or dtype in ("INVOICE", "UNKNOWN") or not data:
            continue
        ext = os.path.splitext(fname)[1].lower()
        sha = hashlib.sha256(data).hexdigest()
        base = dict(submission_id=sid, expected_type=dtype, filename=_safe_name(fname), size_bytes=len(data), sha256=sha,
                    detected_type=None, classification_state=None, classification_score=0, classification_evidence="", created_at=db.now())
        if ext not in ALLOWED_EXT or len(data) > MAX_UPLOAD_MB * 1024 * 1024:
            note = f"Rejected upload: extension '{ext}' or size {len(data)} bytes violates demo limits ({', '.join(sorted(ALLOWED_EXT))}, <= {MAX_UPLOAD_MB} MB)."
            did = db.insert(conn, "documents", safe_path="", page_count=0, extraction_state="REJECTED_UPLOAD", text_chars=0, note=note, **base)
            new_ids.append(did)
            intake.append({"document": DOC_LABELS[dtype], "file": fname, "state": "REJECTED_UPLOAD", "note": note})
            continue
        path = os.path.join(folder, f"{uuid.uuid4().hex[:8]}_{_safe_name(fname)}")
        with open(path, "wb") as fh: fh.write(data)
        rd = ex.read_document(path)
        did = db.insert(conn, "documents", safe_path=path, page_count=rd["page_count"], extraction_state=rd["state"],
                        text_chars=rd["chars"], note=rd.get("note", ""), **base)
        new_ids.append(did)
        _cache.setdefault(sid, {})[did] = rd["pages"]
        if rd.get("line_conf"):
            _ocr.setdefault(sid, {})[did] = (rd["line_conf"], rd.get("line_notes"))
        intake.append({"document": DOC_LABELS[dtype], "file": _safe_name(fname), "bytes": len(data), "sha256": sha[:12],
                       "pages": rd["page_count"], "state": rd["state"], "note": rd.get("note", "")})
        db.audit(conn, sid, "DOCUMENT_STORED", actor, {"expected": dtype, "file": _safe_name(fname), "sha256": sha, "state": rd["state"]})
    return intake, new_ids

def _classify_documents(conn, sid, only_ids):
    """Stage 3 over the freshly stored documents (only_ids), which are the ones present in the page cache."""
    cls = []
    for d in db.rows(conn, "SELECT * FROM documents WHERE submission_id=?", (sid,)):
        if d["id"] not in only_ids:
            continue
        if d["extraction_state"] not in ("TEXT", "OCR"):
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
    return cls

def _extract_documents(conn, sid, only_ids, provenance="extracted"):
    """Stage 4 over the freshly stored documents (only_ids). provenance is 'extracted' on a first run, 'vendor_resubmitted' on a response."""
    ext_out = []
    for d in db.rows(conn, "SELECT * FROM documents WHERE submission_id=?", (sid,)):
        if d["id"] not in only_ids:
            continue
        if d["classification_state"] not in ("MATCH", "UNCERTAIN"):
            ext_out.append({"document": DOC_LABELS[d["expected_type"]], "fields": "skipped (" + str(d["classification_state"]) + ")"})
            continue
        lc, ln = _ocr.get(sid, {}).get(d["id"], (None, None))
        got = ex.extract_fields(d["expected_type"], _cache[sid][d["id"]], lc, ln)
        for g in got:
            db.insert(conn, "extracted_fields", submission_id=sid, document_id=d["id"], field=g["field"], value=g["value"],
                      page=g["page"], evidence=g["evidence"], method=g["method"], confidence=g["confidence"],
                      provenance=provenance, created_at=db.now())
        ext_out.append({"document": DOC_LABELS[d["expected_type"]], "fields": {g["field"]: g["value"] for g in got}})
    db.audit(conn, sid, "FIELDS_EXTRACTED", "system", {"documents": len(ext_out)})
    return ext_out

def _sync_follow_ups(conn, sid, run_no, results):
    """Open a follow-up request for every blocking result that carries a vendor-facing ask; resolve ones whose rule now passes.
    Append-only in spirit: rows are updated in place but history is preserved by opened_run / resolved_run and the audit trail."""
    wanted = {}
    for r in results:
        if r["blocking"] and r["status"] != "PASS" and r.get("action"):
            wanted.setdefault(r["rule_id"], (r["label"], r["action"]))
    open_by_rule = {r["rule_id"]: r for r in
                    db.rows(conn, "SELECT * FROM follow_up_requests WHERE submission_id=? AND status='OPEN'", (sid,))}
    for rule_id, row in open_by_rule.items():
        if rule_id not in wanted:
            conn.execute("UPDATE follow_up_requests SET status='RESOLVED', resolved_run=?, updated_at=? WHERE id=?",
                         (run_no, db.now(), row["id"]))
    for rule_id, (about, ask) in wanted.items():
        if rule_id not in open_by_rule:
            db.insert(conn, "follow_up_requests", submission_id=sid, rule_id=rule_id, about=about, ask=ask,
                      status="OPEN", opened_run=run_no, resolved_run=None, created_at=db.now(), updated_at=db.now())
        elif open_by_rule[rule_id]["ask"] != ask:
            conn.execute("UPDATE follow_up_requests SET ask=?, about=?, updated_at=? WHERE id=?",
                         (ask, about, db.now(), open_by_rule[rule_id]["id"]))
    conn.commit()

def _open_requests(conn, sid):
    return db.rows(conn, "SELECT * FROM follow_up_requests WHERE submission_id=? AND status='OPEN' ORDER BY id", (sid,))

def _risk_inputs(conn, sid, vendor_id, f):
    """Duplicate identifiers (PAN/GSTIN/...), blocklist hits (identifiers AND the bank account), and shared-bank hits."""
    idents, bank = va.identifiers(f), va.bank_identifiers(f)
    dups = db.find_duplicates(conn, sid, idents)
    blocked = db.blocklist_hits(conn, idents + bank)
    shared = db.find_shared_bank(conn, vendor_id, bank)
    return dups, blocked, shared

def seed_demo(conn):
    """Idempotent demo seed: load the fictional policy blocklist (E3b) from policy/blocklist.yaml."""
    import yaml
    try:
        with open(BLOCKLIST_PATH, "rb") as fh:
            entries = (yaml.safe_load(fh.read().decode("utf-8")) or {}).get("entries", [])
    except FileNotFoundError:
        entries = []
    for e in entries:
        val = str(e["id_value"]).strip().upper()
        if not db.one(conn, "SELECT id FROM policy_blocklist WHERE id_type=? AND id_value=?", (e["id_type"], val)):
            add_blocklist(conn, e["id_type"], val, e["reason"], e.get("added_by", "compliance.seed"))

def run_workflow(conn, form: dict, files: dict, upload_dir: str, actor: str = "system", vendor_row_id=None):
    """Generator of stage events. files: {expected_type: (filename, bytes)}. Final event has stage='complete'.
    vendor_row_id: attach to an existing vendor row (the portal pre-creates one at sign-up) instead of looking up / creating."""
    def ev(stage, status, **out): return {"stage": stage, "status": status, "ts": db.now(), "output": out}

    # 1 submission received
    yield ev(STAGES[0], "running")
    f = normalize_form(form)
    if f.get("terms_accepted") and not f.get("terms_accepted_at"):
        f["terms_accepted_at"] = db.now()
    key = vendor_key(f)
    if vendor_row_id:
        vid, existing = vendor_row_id, True
        conn.execute("UPDATE vendors SET legal_name=?, region=?, country=? WHERE id=?",
                     (f.get("legal_name"), f.get("region"), f.get("country"), vid)); conn.commit()
    else:
        v = db.one(conn, "SELECT id FROM vendors WHERE vendor_key=?", (key,))
        vid = v["id"] if v else db.insert(conn, "vendors", vendor_key=key, legal_name=f.get("legal_name"), region=f.get("region"),
                                          country=f.get("country"), created_at=db.now())
        existing = bool(v)
    sid = db.insert(conn, "submissions", vendor_id=vid, snapshot_json=json.dumps(f, default=str), status="IN_PROGRESS", run_no=1, created_at=db.now())
    for t, val in va.identifiers(f) + va.bank_identifiers(f):
        db.insert(conn, "vendor_identifiers", vendor_id=vid, submission_id=sid, id_type=t, id_value=val)
    db.audit(conn, sid, "SUBMISSION_RECEIVED", actor, {"vendor_key": key, "region": f.get("region"), "type": f.get("domestic_type")})
    ref = db.ref(sid)
    yield ev(STAGES[0], "done", ref=ref, vendor_key=key, existing_vendor=existing, region=f.get("region"),
             vendor_type=f.get("domestic_type") if f.get("region") != "International" else f.get("country"),
             required_documents=[DOC_LABELS[d] for d in required_docs(f)])

    # 2 document intake
    yield ev(STAGES[1], "running")
    intake, new_ids = _store_documents(conn, sid, ref, files, upload_dir, actor)
    yield ev(STAGES[1], "done", documents=intake)

    # 3 classification
    yield ev(STAGES[2], "running")
    cls = _classify_documents(conn, sid, set(new_ids))
    yield ev(STAGES[2], "done", classification=cls)

    # 4 extraction
    yield ev(STAGES[3], "running")
    ext_out = _extract_documents(conn, sid, set(new_ids))
    yield ev(STAGES[3], "done", extracted=ext_out)
    _cache.pop(sid, None); _ocr.pop(sid, None)

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
    dups, blocked, shared = _risk_inputs(conn, sid, vid, f)
    r_risk = va.risk_checks(f, dups, blocked, shared); _persist_results(conn, sid, 1, r_risk)
    allr = r_val + r_rec + r_risk
    flags = _make_flags(conn, sid, 1, allr)
    db.audit(conn, sid, "RISK_ASSESSED", "system", {"flags": [x["code"] for x in flags]})
    yield ev(STAGES[6], "done", flags=flags, results=r_risk)
    _sync_follow_ups(conn, sid, 1, allr)
    yield ev(STAGES[7], "running")
    dec, did = _decide_and_draft(conn, sid, 1, f, allr, "system")
    yield ev(STAGES[7], "done", decision_status=dec["status"], reasons=dec["reasons"], risk_level=dec["risk_level"],
             next_action=dec["next_action"], human_review_required=bool(dec["human_review_required"]), policy_version=dec["policy_version"])
    yield ev(STAGES[8], "running")
    draft, cid = _draft(conn, sid, did, f, dec, allr, _open_requests(conn, sid))
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
_ocr = {}     # OCR line confidences and repair notes between stages of one run

def run_submission(conn, form, files, upload_dir, actor="system", vendor_row_id=None):
    last = None
    for e in run_workflow(conn, form, files, upload_dir, actor, vendor_row_id=vendor_row_id):
        last = e
    return last["result"]

def decision_json(conn, sid) -> dict:
    """The decision.schema.json object for a submission's latest run (used by the CLI and the download button)."""
    s = db.one(conn, "SELECT * FROM submissions WHERE id=?", (sid,))
    d = db.latest_decision(conn, sid)
    run_no = d["run_no"]
    results = db.rows(conn, "SELECT * FROM validation_results WHERE submission_id=? AND run_no=?", (sid, run_no))
    v = db.one(conn, "SELECT * FROM vendors WHERE id=?", (s["vendor_id"],)) or {}
    vendor_id = v.get("vendor_id") or f"VENDOR-{s['vendor_id']:05d}"
    open_reqs = db.rows(conn, "SELECT rule_id, about, ask, status FROM follow_up_requests WHERE submission_id=? AND status='OPEN' ORDER BY id", (sid,))
    if open_reqs:
        requests = [{"id": r["rule_id"], "about": r["about"], "ask": r["ask"], "status": r["status"]} for r in open_reqs]
    else:
        requests = de.derive_requests(results)
    dec = {"status": d["status"], "risk_level": d["risk_level"], "policy_version": d["policy_version"],
           "next_action": d["next_action"], "decided_at": d["created_at"]}
    waiting_on = d["waiting_on"] or de.compute_waiting_on(results, d["status"])
    summary = asr.summarize(results, json.loads(s["snapshot_json"]))
    hold = db.one(conn, "SELECT COUNT(*) AS n FROM payments WHERE vendor_id=? AND status='ON_HOLD'", (s["vendor_id"],))
    payment_hold = 1 if (hold and hold["n"]) else (d.get("payment_hold") or 0)
    return de.decision_record(sid, vendor_id, run_no, results, dec, waiting_on, requests, summary, payment_hold)

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
    dups, blocked, shared = _risk_inputs(conn, sid, s["vendor_id"], f)
    allr = (va.validate_form(f, docs) + va.reconcile(f, docs) + va.risk_checks(f, dups, blocked, shared))
    _persist_results(conn, sid, run_no, allr)
    _make_flags(conn, sid, run_no, allr)
    _sync_follow_ups(conn, sid, run_no, allr)
    dec, did = _decide_and_draft(conn, sid, run_no, f, allr, reviewer)
    _draft(conn, sid, did, f, dec, allr, _open_requests(conn, sid))
    db.audit(conn, sid, "CHECKS_RERUN", reviewer, {"run_no": run_no, "status": dec["status"]})
    return {"status": dec["status"], "run_no": run_no, "reasons": dec["reasons"]}

def vendor_response(conn, sid, field_changes=None, files=None, upload_dir=None, actor="vendor", bank_change=False, confirm_to=None):
    """The vendor replies through 'Action needed': optional corrected form values and/or new document files.
    Records VENDOR_RESPONDED, stores new document fields with provenance 'vendor_resubmitted', then re-runs the FULL
    workflow at run_no + 1 on the SAME submission. Resolves requests whose rule now passes and opens any new ones.
    History is append-only (new run rows; earlier runs preserved).
    bank_change (E2): injects a blocking BANK_CHANGE check so the outcome is always PENDING / waiting_on REVIEWER (never
    auto-approved), and drafts the confirmation to confirm_to (the previously registered email, not any new one)."""
    s = db.one(conn, "SELECT * FROM submissions WHERE id=?", (sid,))
    f = json.loads(s["snapshot_json"])
    run_no = s["run_no"] + 1
    db.audit(conn, sid, "VENDOR_RESPONDED", actor,
             {"run_no": run_no, "fields": list((field_changes or {}).keys()), "files": list((files or {}).keys())})
    if field_changes:
        f.update(field_changes)
        f = normalize_form(f)
        conn.execute("UPDATE submissions SET snapshot_json=? WHERE id=?", (json.dumps(f, default=str), sid)); conn.commit()
        for t, val in va.identifiers(f) + va.bank_identifiers(f):   # keep the identifier index current for duplicate / shared / bank checks
            if not db.one(conn, "SELECT id FROM vendor_identifiers WHERE submission_id=? AND id_type=? AND id_value=?", (sid, t, val)):
                db.insert(conn, "vendor_identifiers", vendor_id=s["vendor_id"], submission_id=sid, id_type=t, id_value=val)
    else:
        f = normalize_form(f)

    ref = db.ref(sid)
    if files:
        _, new_ids = _store_documents(conn, sid, ref, files, upload_dir, actor)
        _classify_documents(conn, sid, set(new_ids))
        _extract_documents(conn, sid, set(new_ids), provenance="vendor_resubmitted")
        _cache.pop(sid, None); _ocr.pop(sid, None)

    docs = _load_docs(conn, sid)
    dups, blocked, shared = _risk_inputs(conn, sid, s["vendor_id"], f)
    allr = (va.validate_form(f, docs) + va.reconcile(f, docs) + va.risk_checks(f, dups, blocked, shared))
    if bank_change:
        allr = allr + [va.R("risk", "XR_BANK_CHANGE", "Bank details change pending reviewer confirmation", "WARN", "HIGH",
                            "change request", "A bank-account change was requested; payments are on hold until a reviewer confirms.",
                            "A change of bank account always needs human confirmation and a re-check against other vendors. It is never auto-approved.",
                            "A reviewer will confirm the new bank account against the previously registered email before any payment is released.",
                            flag="BANK_CHANGE", provenance="system-derived")]
    _persist_results(conn, sid, run_no, allr)
    _make_flags(conn, sid, run_no, allr)
    _sync_follow_ups(conn, sid, run_no, allr)
    dec, did = _decide_and_draft(conn, sid, run_no, f, allr, actor)
    if bank_change and confirm_to:
        dec["to_addr"] = confirm_to      # confirmation goes to the previously registered email, not any new one
    _draft(conn, sid, did, f, dec, allr, _open_requests(conn, sid))
    db.audit(conn, sid, "CHECKS_RERUN", actor, {"run_no": run_no, "status": dec["status"], "trigger": "bank_change" if bank_change else "vendor_response"})
    return {"status": dec["status"], "run_no": run_no, "reasons": dec["reasons"], "waiting_on": dec["waiting_on"]}

def _draft_reminder(conn, sid, which, day):
    """Draft (never send) a reminder of the given kind, once per kind per submission."""
    if db.one(conn, "SELECT id FROM communication_drafts WHERE submission_id=? AND draft_type=?", (sid, f"reminder_{which}")):
        return
    s = db.one(conn, "SELECT * FROM submissions WHERE id=?", (sid,))
    f = json.loads(s["snapshot_json"])
    d = db.latest_decision(conn, sid)
    draft = cm.build_reminder(f, db.ref(sid), which, day, _open_requests(conn, sid))
    db.insert(conn, "communication_drafts", submission_id=sid, decision_id=d["id"] if d else None, sent=0, created_at=db.now(), **draft)
    db.audit(conn, sid, "REMINDER_DRAFTED", "system", {"reminder": which, "day": day})

def reminders_due(conn, now=None):
    """Draft reminders (day 3, day 7) and flag STALE submissions (after day 14), based on the oldest OPEN follow-up
    request per PENDING submission. Nothing is ever sent. `now` may be an ISO string or datetime (tests pass a fixed value).
    Returns one row per pending submission with an open request: {submission_id, ref, days_open, action, stale}."""
    import datetime as _dt
    if now is None:
        now = _dt.datetime.now(_dt.timezone.utc)
    elif isinstance(now, str):
        now = _dt.datetime.fromisoformat(now)
    out = []
    for r in db.rows(conn, "SELECT id FROM submissions WHERE status='PENDING'"):
        sid = r["id"]
        oldest = db.one(conn, "SELECT MIN(created_at) AS t FROM follow_up_requests WHERE submission_id=? AND status='OPEN'", (sid,))
        if not oldest or not oldest["t"]:
            continue
        days = (now - _dt.datetime.fromisoformat(oldest["t"])).days
        if days >= 14:
            if not (db.one(conn, "SELECT stale FROM submissions WHERE id=?", (sid,)) or {}).get("stale"):
                conn.execute("UPDATE submissions SET stale=1 WHERE id=?", (sid,)); conn.commit()
                db.audit(conn, sid, "MARKED_STALE", "system", {"days_open": days})
            action = "Recommend: close as rejected (incomplete). A reviewer must confirm and comment."
        elif days >= 7:
            _draft_reminder(conn, sid, 2, days); action = "Second reminder drafted (day 7)."
        elif days >= 3:
            _draft_reminder(conn, sid, 1, days); action = "First reminder drafted (day 3)."
        else:
            continue
        out.append({"submission_id": sid, "ref": db.ref(sid), "days_open": days, "action": action, "stale": days >= 14})
    return out

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
        # E2: a reviewer's approval lifts any payment hold placed by a bank-details change.
        conn.execute("UPDATE payments SET status='PENDING' WHERE vendor_id=? AND status='ON_HOLD'", (s["vendor_id"],)); conn.commit()
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

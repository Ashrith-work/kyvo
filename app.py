"""Kyvo - Streamlit UI. Run:  streamlit run app.py"""
import os, json, shutil, datetime as dt
import pandas as pd
import streamlit as st
import database as db
import workflow as wf
import portal as pt
from config import *
from sample_data import generate as gen

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("KYVO_DB", os.path.join(BASE, "data", "kyvo.db"))
UPLOAD_DIR = os.environ.get("KYVO_UPLOADS", os.path.join(BASE, "uploads"))
SAMPLE_DIR = os.path.join(BASE, "sample_data")
ICON = {"APPROVED": "🟢", "PENDING": "🟠", "REJECTED": "🔴", "PASS": "✅", "FAIL": "❌", "WARN": "⚠️", "NOT_CHECKED": "➖"}

st.set_page_config(page_title="Kyvo", page_icon="🧾", layout="wide")

@st.cache_resource
def get_conn():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True); os.makedirs(UPLOAD_DIR, exist_ok=True)
    return db.connect(DB_PATH)
conn = get_conn()
wf.seed_demo(conn)
if not os.path.exists(os.path.join(SAMPLE_DIR, "scenarios.json")):
    gen.build(SAMPLE_DIR)
SCENARIOS = gen.load_scenarios(SAMPLE_DIR)

ADMIN_PAGES = ["Overview", "New Submission", "Live Run", "Verification Detail", "Review Queue", "Change requests", "Reminders", "History"]
if "goto" in st.session_state:                      # programmatic navigation (set before the radio is created)
    st.session_state["nav"] = st.session_state.pop("goto")
st.sidebar.title("🧾 Kyvo")
st.sidebar.caption("Vendor onboarding automation · independent build for the Zamp PS-2 case study. Deterministic rules; no LLM required.")
st.sidebar.caption("Modelled on a university vendor registration portal. All data is fictional.")
mode = st.sidebar.radio("Mode (demo control — no real authentication)", ["Vendor portal", "Admin review"], key="mode")
page = None
if mode == "Admin review":
    page = st.sidebar.radio("Navigate", ADMIN_PAGES, key="nav")
with st.sidebar.expander("Demo database"):
    st.caption(f"SQLite: `{os.path.relpath(DB_PATH, BASE)}`")
    if st.checkbox("I want to wipe all demo data"):
        if st.button("Reset database"):
            conn.close(); get_conn.clear()
            for p in (DB_PATH,): 
                if os.path.exists(p): os.remove(p)
            shutil.rmtree(UPLOAD_DIR, ignore_errors=True)
            for k in ("last_events", "last_result", "pending", "selected_sub", "vendor", "demo_inbox", "reg_files"):
                st.session_state.pop(k, None)
            st.rerun()

def goto(p, sub=None):
    st.session_state["goto"] = p
    if sub: st.session_state["selected_sub"] = sub
    st.rerun()

def badge(s): return f"{ICON.get(s, '')} **{s}**"

def results_df(rs):
    return pd.DataFrame([{"": ICON.get(r["status"], ""), "rule_id": r["rule_id"], "check": r["label"], "status": r["status"],
                          "severity": r["severity"], "blocks approval": bool(r["blocking"]), "evidence": r["evidence"],
                          "source": r["source"], "provenance": r["provenance"]} for r in rs])

# ---------------------------------------------------------------- stage rendering
def render_output(stage, o):
    if stage == wf.STAGES[0]:
        st.write({k: v for k, v in o.items()})
    elif stage == wf.STAGES[1]:
        st.dataframe(pd.DataFrame(o["documents"]), hide_index=True, width="stretch")
    elif stage == wf.STAGES[2]:
        st.dataframe(pd.DataFrame(o["classification"]), hide_index=True, width="stretch")
    elif stage == wf.STAGES[3]:
        for e in o["extracted"]:
            st.markdown(f"**{e['document']}**"); st.write(e["fields"])
    elif stage in (wf.STAGES[4], wf.STAGES[5], wf.STAGES[6]):
        if "counts" in o: st.caption(f"PASS {o['counts']['PASS']} · FAIL {o['counts']['FAIL']} · WARN {o['counts']['WARN']} · NOT_CHECKED {o['counts']['NOT_CHECKED']}")
        if "flags" in o: st.write("Risk flags:", [f"{f['code']} ({f['severity']})" for f in o["flags"]] or "none")
        st.dataframe(results_df(o["results"]), hide_index=True, width="stretch")
    elif stage == wf.STAGES[7]:
        st.markdown(f"### {badge(o['decision_status'])}  ·  risk {o['risk_level']}  ·  human review: {'required' if o['human_review_required'] else 'not required'}")
        for r in o["reasons"]: st.markdown(f"- {r}")
        st.info(f"Next action: {o['next_action']}  \nPolicy `{o['policy_version']}`")
    elif stage == wf.STAGES[8]:
        st.write(f"Draft created: **{o['subject']}** (sent = {o['sent']}; never auto-sent)")
    elif stage == wf.STAGES[9]:
        st.write("Rows actually persisted in SQLite for this submission:", o["persisted_rows"])

# ---------------------------------------------------------------- pages
def page_overview():
    st.header("Overview")
    c = db.overview_counts(conn)
    cols = st.columns(5)
    for col, (lab, k) in zip(cols, [("Submissions", "total"), ("Approved", "APPROVED"), ("Pending", "PENDING"), ("Rejected", "REJECTED"), ("Open risk flags", "open_flags")]):
        col.metric(lab, c[k])
    st.caption("All figures are live queries on SQLite.")
    subs = db.list_submissions(conn)
    st.subheader("Recent submissions")
    if subs:
        st.dataframe(pd.DataFrame(subs)[["ref", "legal_name", "region", "country", "status", "risk_level", "decided_by", "created_at"]].head(10), hide_index=True, width="stretch")
    else:
        st.info("No submissions yet. Use **New Submission** (or run a bundled sample scenario).")
    q = [s for s in subs if s["status"] == "PENDING"]
    st.subheader(f"Review queue ({len(q)})")
    if q: st.dataframe(pd.DataFrame(q)[["ref", "legal_name", "risk_level", "created_at"]], hide_index=True, width="stretch")

def start_run(form, files, source):
    st.session_state["pending"] = {"form": form, "files": files, "source": source}
    goto("Live Run")

def page_new():
    st.header("New vendor submission")
    with st.expander("▶ Run a bundled fictional sample scenario (fast path for demos)", expanded=False):
        group = st.radio("Scenario group", ["Baseline checks", "Edge cases"], horizontal=True)
        pool = [s for s in SCENARIOS if (s["id"][0] == "E") == (group == "Edge cases")]
        sc = st.selectbox("Scenario", pool, format_func=lambda s: f"{s['title']}  →  expect {s['expected']}")
        note = f"  **Note:** {sc['note']}" if sc.get("note") else ""
        st.caption(sc["description"] + note)
        if st.button("Run this scenario", type="primary"):
            start_run(sc["form"], gen.scenario_files(SAMPLE_DIR, sc), sc["id"])
    st.divider()
    region = st.radio("Vendor classification", ["Domestic", "International"], horizontal=True)
    dom_type = None
    if region == "Domestic":
        dom_type = st.radio("Domestic registration type", ["PAN with GST", "PAN without GST"], horizontal=True)
    else:
        st.caption("A Tax Residency Certificate (number and certificate) is required for every International vendor.")
    preview = {"region": region, "domestic_type": dom_type, "trc_applicable": region == "International"}
    photos = st.session_state.setdefault("photos", {})
    with st.expander("📷 Add a document with your camera", expanded=bool(photos)):
        st.caption("Take a photo of a document. Kyvo reads it with OCR when you submit. Values it reads with low confidence "
                   "only count if they match what you typed exactly; anything else goes to a reviewer.")
        needed = required_docs(preview)
        cam_doc = st.selectbox("Which document is this?", needed, format_func=lambda d: DOC_LABELS[d], key="cam_doc")
        shot = st.camera_input("Take the photo", key=f"cam_{cam_doc}")
        if shot is not None and st.button("Use this photo", key="cam_use"):
            photos[cam_doc] = (f"{cam_doc.lower()}_photo.jpg", shot.getvalue())
            st.success(f"Photo attached as your {DOC_LABELS[cam_doc]}.")
        for dt_, (fn_, data_) in list(photos.items()):
            a_, b_ = st.columns([4, 1])
            a_.write(f"📷 {DOC_LABELS.get(dt_, dt_)}: {fn_} ({len(data_) // 1024} KB)")
            if b_.button("Remove", key=f"rm_{dt_}"):
                photos.pop(dt_, None); st.rerun()
    with st.form("vendor_form"):
        st.subheader("Organisation")
        a, b = st.columns(2)
        legal = a.text_input("Firm / legal name *"); trade = b.text_input("Trading name (optional)")
        contact = a.text_input("Contact person *"); email = b.text_input("Organisation / contact email *")
        phone = a.text_input("Company phone *"); entity = b.selectbox("Ownership / entity type *", ENTITY_TYPES)
        proprietor = a.text_input("Proprietor name (required for Proprietorship)"); mobile = b.text_input("Contact mobile (optional)")
        address = st.text_area("Registered address *", height=70)
        state = city = pincode = ""
        if region == "Domestic":
            a, b, c = st.columns(3)
            state = a.text_input("State *"); city = b.text_input("City *"); pincode = c.text_input("PIN code (6 digits) *")
        country = "India"; pan = gstin = reg = tax_id = ifsc = swift = bank_country = trc_number = ""
        st.subheader("Tax & registration")
        if region == "Domestic":
            a, b = st.columns(2)
            pan = a.text_input("PAN *")
            if dom_type == "PAN with GST": gstin = b.text_input("GSTIN *")
            reg = st.text_input("CIN / LLPIN / registration no. (optional)")
        else:
            a, b = st.columns(2)
            country = a.selectbox("Country *", [c for c in COUNTRIES], index=list(COUNTRIES).index("United States"))
            tax_id = b.text_input("Tax identification number (EIN / VAT / TRN ...) *")
            reg = a.text_input("Business registration number *")
            trc_number = b.text_input("TRC number *")
        st.subheader("Bank")
        a, b = st.columns(2)
        holder = a.text_input("Account holder name *"); acct = b.text_input("Account number / IBAN *")
        bank = a.text_input("Bank name *")
        if region == "Domestic": ifsc = b.text_input("IFSC *")
        else:
            bank_country = b.selectbox("Bank country *", list(COUNTRIES), index=list(COUNTRIES).index("United States")); swift = a.text_input("SWIFT/BIC (optional)")
        st.subheader("Service / technical")
        a, b = st.columns(2)
        cat = a.selectbox("Service category *", SERVICE_CATEGORIES)
        desc = st.text_area("Products / services description *", height=70)
        st.subheader(f"Documents (PDF/JPG/PNG, ≤ {MAX_UPLOAD_MB} MB)")
        st.caption("Text-based PDFs are read directly. Photos and scanned PDFs are read with OCR.")
        uploads = {}
        for dt_ in required_docs(preview):
            uploads[dt_] = st.file_uploader(DOC_LABELS[dt_] + " *", type=[e[1:] for e in ALLOWED_EXT], key=f"up_{dt_}")
            if dt_ in photos:
                st.caption("📷 Using your camera photo for this document. Uploading a file here replaces it.")
        terms = st.checkbox("I confirm the details are correct and accept the (demo) vendor declaration. *")
        go = st.form_submit_button("Submit & run verification", type="primary")
    if go:
        form = dict(region=region, domestic_type=dom_type, trc_applicable=region == "International", legal_name=legal, trading_name=trade,
                    contact_person=contact, email=email, contact_email=email, phone=phone, contact_mobile=mobile, alt_phone="",
                    entity_type=entity, proprietor_name=proprietor, address=address, state=state, city=city, pincode=pincode,
                    country=country, pan=pan, gstin=gstin, reg_number=reg, tax_id=tax_id, trc_number=trc_number,
                    bank_holder=holder, account_number=acct, bank_name=bank, ifsc=ifsc, bank_country=bank_country, swift=swift,
                    service_category=cat, service_description=desc, terms_accepted=terms)
        files = {k: (u.name, u.getvalue()) for k, u in uploads.items() if u is not None}
        for dt_, photo in photos.items():
            if dt_ in uploads and dt_ not in files:
                files[dt_] = photo
        st.session_state["photos"] = {}
        start_run(form, files, "manual")

def page_live():
    st.header("Live run")
    pend = st.session_state.pop("pending", None)
    if pend:
        st.caption(f"Source: {pend['source']} · every stage below is a real function call; nothing is simulated.")
        prog = st.progress(0.0)
        slots = {s: st.empty() for s in wf.STAGES}
        events, finished = [], 0
        for e in wf.run_workflow(conn, pend["form"], pend["files"], UPLOAD_DIR):
            if e["stage"] == "complete":
                st.session_state["last_result"] = e["result"]; break
            events.append(e)
            if e["status"] == "running":
                slots[e["stage"]].info(f"⏳ {e['stage']} …")
            else:
                finished += 1; prog.progress(finished / len(wf.STAGES))
                with slots[e["stage"]].container():
                    with st.expander(f"✅ {e['stage']}  ·  {e['ts']}", expanded=e["stage"] in (wf.STAGES[7],)):
                        render_output(e["stage"], e["output"])
        st.session_state["last_events"] = [x for x in events if x["status"] == "done"]
        r = st.session_state["last_result"]
        st.success(f"Run complete: {r['ref']} → {r['status']}")
        if st.button("Open verification detail"): goto("Verification Detail", r["submission_id"])
    elif "last_events" in st.session_state:
        st.caption("Showing the most recent run (re-rendered from stored stage outputs).")
        for e in st.session_state["last_events"]:
            with st.expander(f"✅ {e['stage']}  ·  {e['ts']}", expanded=e["stage"] == wf.STAGES[7]): render_output(e["stage"], e["output"])
        r = st.session_state.get("last_result")
        if r and st.button("Open verification detail"): goto("Verification Detail", r["submission_id"])
    else:
        st.info("Nothing has run yet. Submit a vendor on **New Submission**, or run a sample scenario.")

def pick_submission(key):
    subs = db.list_submissions(conn)
    if not subs: st.info("No submissions yet."); return None
    ids = [s["id"] for s in subs]
    default = ids.index(st.session_state["selected_sub"]) if st.session_state.get("selected_sub") in ids else 0
    sid = st.selectbox("Submission", ids, index=default, key=key, format_func=lambda i: next(f"{s['ref']} · {s['legal_name']} · {s['status']}" for s in subs if s["id"] == i))
    st.session_state["selected_sub"] = sid
    return sid

def entered_vs_extracted(sid, snap):
    rows_ = []
    docs = {d["id"]: d for d in db.rows(conn, "SELECT * FROM documents WHERE submission_id=?", (sid,))}
    entered = {("pan"): snap.get("pan"), "gstin": snap.get("gstin"), "name": snap.get("legal_name"), "reg_number": snap.get("reg_number"),
               "tax_id": snap.get("tax_id"), "account_holder": snap.get("bank_holder"), "account_number": snap.get("account_number"),
               "ifsc": snap.get("ifsc"), "swift": snap.get("swift")}
    eff = db.effective_fields(conn, sid)
    for did, flds in eff.items():
        for k, f in flds.items():
            orig = [r for r in db.rows(conn, "SELECT value FROM extracted_fields WHERE document_id=? AND field=? AND provenance='extracted'", (did, k))]
            rows_.append({"document": DOC_LABELS[docs[did]["expected_type"]], "field": k, "vendor-entered": entered.get(k, ""),
                          "extracted": orig[-1]["value"] if orig else "", "current value": f["value"], "provenance": f["provenance"],
                          "read by": {"ocr": "OCR", "manual": "reviewer"}.get(f["method"], "PDF text"),
                          "confidence": f["confidence"], "page": f["page"], "evidence": f["evidence"]})
    return pd.DataFrame(rows_)

def page_detail():
    st.header("Verification detail")
    sid = pick_submission("detail_sel")
    if not sid: return
    s = db.one(conn, "SELECT * FROM submissions WHERE id=?", (sid,)); snap = json.loads(s["snapshot_json"])
    d = db.latest_decision(conn, sid)
    st.markdown(f"## {badge(d['status'])} — {snap.get('legal_name')}  `{db.ref(sid)}`")
    st.caption(f"Run {d['run_no']} · decided by **{d['decided_by']}** at {d['created_at']} · policy `{d['policy_version']}` · risk {d['risk_level']} · human review {'required' if d['human_review_required'] else 'not required'}")
    t = st.tabs(["Decision", "Entered vs extracted", "Documents", "Validation", "Risk flags", "Email draft", "Audit trail", "Decision history"])
    with t[0]:
        st.caption(f"Waiting on: **{d['waiting_on'] or 'n/a'}**")
        for r in json.loads(d["reasons_json"]): st.markdown(f"- {r}")
        st.info("Next action: " + d["next_action"])
        st.caption("Format/consistency checks only — they do not prove legal existence, tax registration status, sanctions status or bank-account ownership.")
        rec = wf.decision_json(conn, sid)
        st.download_button("Download decision JSON", json.dumps(rec, indent=2), file_name=f"{db.ref(sid)}_decision.json", mime="application/json")
    with t[1]:
        df = entered_vs_extracted(sid, snap)
        if len(df): st.dataframe(df, hide_index=True, width="stretch")
        else: st.write("No extracted fields.")
        with st.expander("Full submitted form snapshot"): st.json(snap)
    with t[2]:
        docs = db.rows(conn, "SELECT expected_type, detected_type, classification_state, classification_score, classification_evidence, filename, size_bytes, sha256, page_count, extraction_state, note FROM documents WHERE submission_id=?", (sid,))
        st.dataframe(pd.DataFrame(docs), hide_index=True, width="stretch")
    with t[3]:
        runs = [r["run_no"] for r in db.rows(conn, "SELECT DISTINCT run_no FROM validation_results WHERE submission_id=? ORDER BY run_no DESC", (sid,))]
        run = st.selectbox("Run", runs, key=f"runsel{sid}")
        res = db.rows(conn, "SELECT * FROM validation_results WHERE submission_id=? AND run_no=? ORDER BY id", (sid, run))
        st.dataframe(results_df(res), hide_index=True, width="stretch")
        for r in res:
            if r["status"] != "PASS" and r["blocking"]:
                with st.expander(f"{ICON[r['status']]} {r['rule_id']} — {r['label']}"):
                    st.write("**Why:**", r["explanation"]); st.write("**Evidence:**", r["evidence"]); st.write("**Source:**", r["source"]); st.write("**Recommended action:**", r["action"])
    with t[4]:
        fl = db.rows(conn, "SELECT run_no, code, severity, details, resolution_state, resolved_by, resolution_comment FROM risk_flags WHERE submission_id=? ORDER BY id DESC", (sid,))
        if fl: st.dataframe(pd.DataFrame(fl), hide_index=True, width="stretch")
        else: st.write("No risk flags.")
    with t[5]:
        dr = db.one(conn, "SELECT * FROM communication_drafts WHERE submission_id=? ORDER BY id DESC LIMIT 1", (sid,))
        if dr:
            st.caption(f"To: {dr['to_addr']} · type: {dr['draft_type']} · sent: {bool(dr['sent'])} (Kyvo never sends automatically)")
            subj = st.text_input("Subject", dr["subject"], key=f"subj{dr['id']}")
            body = st.text_area("Body", dr["body"], height=320, key=f"body{dr['id']}")
            by = st.text_input("Edited by", key=f"by{dr['id']}")
            if st.button("Save edits", key=f"sv{dr['id']}") and by.strip():
                wf.save_draft_edit(conn, dr["id"], subj, body, by); st.success("Draft updated (audited).")
    with t[6]:
        ev = db.rows(conn, "SELECT created_at, event, actor, details_json FROM audit_events WHERE submission_id=? ORDER BY id", (sid,))
        st.dataframe(pd.DataFrame(ev), hide_index=True, width="stretch")
    with t[7]:
        h = db.rows(conn, "SELECT id, run_no, status, decided_by, risk_level, comment, created_at FROM decisions WHERE submission_id=? ORDER BY id", (sid,))
        st.dataframe(pd.DataFrame(h), hide_index=True, width="stretch")

def page_review():
    st.header("Review queue")
    q = [s for s in db.list_submissions(conn) if s["status"] == "PENDING"]
    st.write(f"**{len(q)}** pending item(s).")
    if q: st.dataframe(pd.DataFrame(q)[["ref", "legal_name", "region", "country", "risk_level", "created_at"]], hide_index=True, width="stretch")
    sid = pick_submission("review_sel")
    if sid:
        d = db.latest_decision(conn, sid); snap = json.loads(db.one(conn, "SELECT snapshot_json FROM submissions WHERE id=?", (sid,))["snapshot_json"])
        st.markdown(f"### {badge(d['status'])} {snap.get('legal_name')}")
        for r in json.loads(d["reasons_json"]): st.markdown(f"- {r}")
        reviewer = st.text_input("Reviewer name (recorded in audit trail)", key="reviewer")
        c1, c2 = st.columns(2)
        with c1:
            st.subheader("Correct an extracted value")
            docs = db.rows(conn, "SELECT id, expected_type FROM documents WHERE submission_id=?", (sid,))
            eff = db.effective_fields(conn, sid)
            opts = [(did["id"], k) for did in docs for k in eff.get(did["id"], {})]
            if opts:
                pick = st.selectbox("Field", opts, format_func=lambda o: f"{DOC_LABELS[next(x['expected_type'] for x in docs if x['id'] == o[0])]} → {o[1]} (now: {eff[o[0]][o[1]]['value']})")
                nv = st.text_input("Corrected value"); why = st.text_input("Reason for correction")
                if st.button("Save correction & re-run checks"):
                    if reviewer.strip() and nv.strip() and why.strip():
                        wf.correct_field(conn, sid, pick[0], pick[1], nv, reviewer, why); out = wf.rerun_checks(conn, sid, reviewer)
                        st.success(f"Re-run {out['run_no']}: {out['status']}"); st.rerun()
                    else: st.error("Reviewer, value and reason are all required.")
            else: st.caption("No extracted values to correct.")
            if st.button("Re-run checks without changes"):
                if reviewer.strip(): out = wf.rerun_checks(conn, sid, reviewer); st.success(f"Re-run {out['run_no']}: {out['status']}"); st.rerun()
                else: st.error("Reviewer name required.")
        with c2:
            st.subheader("Reviewer decision")
            comment = st.text_area("Comment (required for approve / reject)", key="rv_comment", height=100)
            b1, b2, b3 = st.columns(3)
            for col, lab, act in ((b1, "Approve", "APPROVE"), (b2, "Reject", "REJECT"), (b3, "Comment only", "COMMENT")):
                if col.button(lab, key=f"act_{act}"):
                    try: wf.review_action(conn, sid, reviewer, act, comment); st.success(f"{lab} recorded."); st.rerun()
                    except ValueError as e: st.error(str(e))
            st.caption("Rejection should be reserved for a confirmed policy disqualification. Similar names or a bank-holder difference alone is not grounds for rejection.")
        for e in db.rows(conn, "SELECT created_at, actor, details_json FROM audit_events WHERE submission_id=? AND event='REVIEW_COMMENT' ORDER BY id", (sid,)):
            st.markdown(f"💬 **{e['actor']}** ({e['created_at']}): {json.loads(e['details_json']).get('comment')}")
    with st.expander("Policy blocklist (written-policy disqualifications only; empty by default)"):
        bl = db.rows(conn, "SELECT * FROM policy_blocklist")
        if bl: st.dataframe(pd.DataFrame(bl), hide_index=True)
        a, b, c = st.columns(3)
        t_ = a.selectbox("Identifier type", ["PAN", "GSTIN", "REG_NO", "TAX_ID"]); v_ = b.text_input("Value"); r_ = c.text_input("Policy reason / reference")
        who = st.text_input("Added by", key="bl_by")
        if st.button("Add to blocklist") and v_ and r_ and who: wf.add_blocklist(conn, t_, v_, r_, who); st.rerun()

def page_history():
    st.header("History")
    a, b = st.columns([3, 1])
    q = a.text_input("Search (ref, name, PAN, GSTIN, tax ID, status)"); stt = b.selectbox("Status", ["", "APPROVED", "PENDING", "REJECTED"])
    subs = db.list_submissions(conn, q, stt)
    if subs: st.dataframe(pd.DataFrame(subs)[["ref", "legal_name", "region", "country", "status", "risk_level", "decided_by", "created_at", "decided_at"]], hide_index=True, width="stretch")
    else: st.info("No matching submissions.")
    st.caption("History is append-only: re-runs and reviewer actions add new decision rows rather than overwriting. Open **Verification Detail** → *Decision history* / *Audit trail* for any submission.")

def save_uploaded(up):
    folder = os.path.join(UPLOAD_DIR, "portal"); os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"{dt.datetime.now().strftime('%H%M%S')}_{up.name}")
    with open(path, "wb") as fh:
        fh.write(up.getvalue())
    return path

# ---------------------------------------------------------------- admin: change requests & reminders
def page_admin_changes():
    st.header("Change requests (admin)")
    crs = db.rows(conn, "SELECT c.*, v.vendor_id AS vpub, v.legal_name FROM change_requests c JOIN vendors v ON v.id=c.vendor_id ORDER BY c.id DESC")
    if not crs:
        st.info("No change requests yet."); return
    st.dataframe(pd.DataFrame([{"id": c["id"], "vendor": c["vpub"], "type": c["cr_type"], "new value": c["new_value"],
                               "reason": c["reason"], "status": c["status"], "admin comment": c["admin_comment"], "filed": c["created_at"]}
                              for c in crs]), hide_index=True, width="stretch")
    pend = [c for c in crs if c["status"] == "PENDING"]
    if not pend:
        return
    st.subheader("Decide a pending request")
    cid = st.selectbox("Pending request", [c["id"] for c in pend],
                       format_func=lambda i: next(f"#{c['id']} · {c['vpub']} · {c['cr_type']}" for c in pend if c["id"] == i))
    admin = st.text_input("Admin name (recorded in audit)", key="cradmin")
    comment = st.text_area("Comment (required to reject)", key="crcomment")
    c1, c2 = st.columns(2)
    if c1.button("Approve"):
        if admin.strip():
            pt.decide_change_request(conn, cid, True, comment, admin)
            st.success("Approved. The vendor can now edit that section and resubmit.")
            st.rerun()
        else:
            st.error("Admin name is required.")
    if c2.button("Reject"):
        try:
            if not admin.strip():
                raise ValueError("Admin name is required.")
            pt.decide_change_request(conn, cid, False, comment, admin)
            st.success("Rejected.")
            st.rerun()
        except ValueError as e:
            st.error(str(e))

def page_reminders():
    st.header("Reminders & stale cases (admin)")
    st.caption("Reminders are drafted, never sent. Day 3 and day 7 draft a reminder; after day 14 a case is recommended for closure "
               "(a reviewer must still confirm with a comment).")
    d = st.date_input("Check reminders as of", value=dt.date.today())
    if st.button("Check reminders"):
        now = dt.datetime(d.year, d.month, d.day, tzinfo=dt.timezone.utc)
        rows = wf.reminders_due(conn, now.isoformat())
        if rows:
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
        else:
            st.info("Nothing due as of that date.")
    stale = db.rows(conn, "SELECT id FROM submissions WHERE stale=1")
    if stale:
        st.subheader("Flagged stale")
        st.write([db.ref(s["id"]) for s in stale])

# ---------------------------------------------------------------- vendor portal
def demo_inbox():
    box = st.session_state.get("demo_inbox")
    if box:
        st.info(f"📨 Demo inbox (no real email is sent): the OTP for {box['vid']} is **{box['otp']}** (valid 10 minutes, single use).")

def vendor_login_block():
    st.subheader("Login with Vendor ID and OTP")
    vid = st.text_input("Vendor ID", key="login_vid")
    if st.button("Generate OTP", key="login_gen"):
        v = pt.get_vendor(conn, vid)
        if not v:
            st.error("No vendor found with that Vendor ID.")
        else:
            st.session_state["demo_inbox"] = {"vid": v["vendor_id"], "otp": pt.issue_otp(conn, v["vendor_id"])}
            st.rerun()
    demo_inbox()
    otp = st.text_input("OTP", key="login_otp")
    robot = st.checkbox("I'm not a robot (demo)", key="login_captcha")
    if st.button("Login", key="login_btn"):
        if not robot:
            st.error("Please tick the (demo) CAPTCHA.")
        else:
            ok, msg = pt.verify_otp(conn, vid, otp)
            if ok:
                st.session_state["vendor"] = vid.strip().upper()
                st.session_state.pop("demo_inbox", None)
                st.rerun()
            else:
                st.error(msg)

def vendor_signup_block():
    st.subheader("Sign up for a Vendor ID")
    region = st.radio("Region", ["Domestic", "International"], horizontal=True, key="su_region")
    dtype = pan = gstin = regno = trc = ""
    if region == "Domestic":
        dtype = st.radio("Vendor type", ["PAN with GST", "PAN without GST"], horizontal=True, key="su_dtype")
        pan = st.text_input("PAN", key="su_pan")
        if dtype == "PAN with GST":
            gstin = st.text_input("GSTIN", key="su_gstin")
    else:
        regno = st.text_input("Business registration number (your region)", key="su_reg")
        trc = st.text_input("TRC number", key="su_trc")
    email = st.text_input("Organisation / contact email", key="su_email")
    robot = st.checkbox("I'm not a robot (demo)", key="su_captcha")
    st.caption("No OTP is emailed. After sign-up, note your Vendor ID and log in with a demo OTP.")
    if st.button("Create Vendor ID", key="su_btn"):
        if not robot:
            st.error("Please tick the (demo) CAPTCHA.")
        elif not email.strip():
            st.error("Email is required.")
        else:
            try:
                v = pt.signup(conn, region, email, domestic_type=dtype or None, pan=pan or None,
                              gstin=gstin or None, business_reg_number=regno or None, trc_number=trc or None)
                st.success(f"Your Vendor ID is **{v['vendor_id']}**. Note it down and use it to log in.")
            except ValueError as e:
                st.error(str(e))

def vendor_forgot_block():
    st.subheader("Forgot Vendor ID")
    region = st.radio("Region", ["Domestic", "International"], horizontal=True, key="fg_region")
    gstin = pan = regno = ""
    dtype = None
    if region == "Domestic":
        dtype = st.radio("Vendor type", ["PAN with GST", "PAN without GST"], horizontal=True, key="fg_dtype")
        if dtype == "PAN without GST":
            pan = st.text_input("Registered PAN", key="fg_pan")
        else:
            gstin = st.text_input("Registered GSTIN", key="fg_gstin")
    else:
        regno = st.text_input("Business registration number", key="fg_reg")
    st.checkbox("I'm not a robot (demo)", key="fg_captcha")
    if st.button("Find my Vendor ID", key="fg_btn"):
        found = pt.forgot_vendor_id(conn, region, domestic_type=dtype, gstin=gstin, pan=pan, business_reg_number=regno)
        if found:
            st.success(f"Your Vendor ID is **{found}**.")
        else:
            st.warning("No matching vendor found. Please check the details or sign up.")

def vendor_registration_tab(v):
    region, steps = v["region"], pt.get_steps(conn, v["id"])
    with st.expander("Step 1 — Organisation information", expanded=1 not in steps):
        with st.form("rf1"):
            s = steps.get(1, {})
            legal = st.text_input("Firm / legal name", s.get("legal_name", ""))
            trade = st.text_input("Trading name (optional)", s.get("trading_name", ""))
            entity = st.selectbox("Ownership / entity type", ENTITY_TYPES,
                                  index=ENTITY_TYPES.index(s["entity_type"]) if s.get("entity_type") in ENTITY_TYPES else 0)
            proprietor = st.text_input("Proprietor name (required for Proprietorship)", s.get("proprietor_name", ""))
            contact = st.text_input("Contact person", s.get("contact_person", ""))
            phone = st.text_input("Company phone", s.get("phone", ""))
            address = st.text_area("Registered address", s.get("address", ""), height=70)
            state = city = pincode = country = tax_id = ""
            if region == "Domestic":
                a, b, c = st.columns(3)
                state = a.text_input("State", s.get("state", "")); city = b.text_input("City", s.get("city", ""))
                pincode = c.text_input("PIN code", s.get("pincode", ""))
            else:
                country = st.selectbox("Country", list(COUNTRIES),
                                       index=list(COUNTRIES).index(s["country"]) if s.get("country") in COUNTRIES else 0)
                tax_id = st.text_input("Tax identification number (EIN / VAT / TRN ...)", s.get("tax_id", ""))
            if st.form_submit_button("Save step 1"):
                data = dict(legal_name=legal, trading_name=trade, entity_type=entity, proprietor_name=proprietor,
                            contact_person=contact, phone=phone, address=address, state=state, city=city, pincode=pincode)
                if region == "International":
                    data.update(country=country, tax_id=tax_id)
                pt.save_step(conn, v["id"], 1, data)
                st.success("Step 1 saved."); st.rerun()
    with st.expander("Step 2 — Bank details", expanded=(1 in steps and 2 not in steps)):
        with st.form("rf2"):
            s = steps.get(2, {})
            holder = st.text_input("Account holder name", s.get("bank_holder", ""))
            acct = st.text_input("Account number / IBAN", s.get("account_number", ""))
            bank = st.text_input("Bank name", s.get("bank_name", ""))
            ifsc = swift = bank_country = ""
            if region == "Domestic":
                ifsc = st.text_input("IFSC", s.get("ifsc", ""))
            else:
                swift = st.text_input("SWIFT / BIC (optional)", s.get("swift", ""))
                bank_country = st.selectbox("Bank country", list(COUNTRIES),
                                            index=list(COUNTRIES).index(s["bank_country"]) if s.get("bank_country") in COUNTRIES else 0)
            st.caption("A cancelled cheque or bank letter is uploaded in step 4.")
            if st.form_submit_button("Save step 2"):
                data = dict(bank_holder=holder, account_number=acct, bank_name=bank)
                if region == "Domestic":
                    data["ifsc"] = ifsc
                else:
                    data.update(swift=swift, bank_country=bank_country)
                pt.save_step(conn, v["id"], 2, data)
                st.success("Step 2 saved."); st.rerun()
    with st.expander("Step 3 — Services", expanded=(2 in steps and 3 not in steps)):
        with st.form("rf3"):
            s = steps.get(3, {})
            cat = st.selectbox("Service category", SERVICE_CATEGORIES,
                               index=SERVICE_CATEGORIES.index(s["service_category"]) if s.get("service_category") in SERVICE_CATEGORIES else 0)
            desc = st.text_area("Products / services description", s.get("service_description", ""), height=70)
            if st.form_submit_button("Save step 3"):
                pt.save_step(conn, v["id"], 3, dict(service_category=cat, service_description=desc))
                st.success("Step 3 saved."); st.rerun()
    preview = {"region": region, "domestic_type": v["signup_type"], "trc_applicable": region == "International"}
    with st.expander("Step 4 — Documents & terms", expanded=(3 in steps and 4 not in steps)):
        reg_files = st.session_state.setdefault("reg_files", {})
        for d_ in required_docs(preview):
            up = st.file_uploader(DOC_LABELS[d_], type=[e[1:] for e in ALLOWED_EXT], key=f"regup_{d_}")
            if up is not None:
                reg_files[d_] = (up.name, up.getvalue())
            if d_ in reg_files:
                st.caption(f"Attached: {reg_files[d_][0]}")
        terms = st.checkbox("I accept the Terms and Conditions", value=bool(steps.get(4, {}).get("terms_accepted")))
        if st.button("Save step 4"):
            pt.save_step(conn, v["id"], 4, {"terms_accepted": bool(terms), "docs": list(reg_files)})
            st.success("Step 4 saved."); st.rerun()
    st.divider()
    if pt.steps_complete(conn, v["id"]):
        if st.button("Submit registration & run verification", type="primary"):
            res = pt.submit_registration(conn, v, st.session_state.get("reg_files", {}), UPLOAD_DIR)
            st.session_state["reg_files"] = {}
            st.success(f"Submitted {res['ref']} → {res['status']}.")
            st.rerun()
    else:
        st.info("Complete all four steps and accept the terms to submit.")

def vendor_action_needed_tab(v):
    s = pt.latest_submission(conn, v["id"])
    if not s:
        st.info("No submission yet. Complete registration first."); return
    reqs = pt.open_requests(conn, v["id"])
    if not reqs:
        st.success("No open requests right now."); return
    st.write("Open requests:")
    for r in reqs:
        st.markdown(f"- **{r['about']}** — {r['ask']}")
    st.divider()
    st.caption("Reply with a corrected value and/or a replacement document. Kyvo re-runs every check.")
    field = st.text_input("Field to correct (e.g. ifsc, bank_name, state)", key="an_field")
    val = st.text_input("New value", key="an_val")
    preview = {"region": v["region"], "domestic_type": v["signup_type"], "trc_applicable": v["region"] == "International"}
    pick = st.selectbox("Replace a document (optional)", [""] + required_docs(preview),
                        format_func=lambda d_: DOC_LABELS.get(d_, "— none —"), key="an_doc")
    up = st.file_uploader("New document", type=[e[1:] for e in ALLOWED_EXT], key="an_up")
    if st.button("Submit response & re-run", key="an_btn"):
        changes = None
        if field.strip() and val.strip():
            changes = {field.strip(): val.strip()}
        files = None
        if pick and up is not None:
            files = {pick: (up.name, up.getvalue())}
        if not changes and not files:
            st.error("Provide a corrected value or a replacement document.")
        else:
            out = wf.vendor_response(conn, s["id"], field_changes=changes, files=files, upload_dir=UPLOAD_DIR, actor="vendor")
            st.success(f"Re-run {out['run_no']}: {out['status']} (waiting on {out['waiting_on']}).")
            st.rerun()

def vendor_change_requests_tab(v):
    with st.form("cr_form"):
        cr_type = st.selectbox("Change type", pt.CR_TYPES)
        new_value = st.text_area("New value / details")
        reason = st.text_input("Reason")
        up = st.file_uploader("Supporting document (optional)", type=[e[1:] for e in ALLOWED_EXT])
        terms = st.checkbox("I accept the terms")
        if st.form_submit_button("Submit change request"):
            if not terms:
                st.error("Please accept the terms.")
            elif not reason.strip():
                st.error("A reason is required.")
            else:
                path = save_uploaded(up) if up is not None else ""
                pt.create_change_request(conn, v["id"], cr_type, new_value, reason, path, terms)
                st.success("Change request submitted for admin review.")
                st.rerun()
    crs = pt.list_change_requests(conn, v["id"])
    if crs:
        st.subheader("Your change requests")
        st.dataframe(pd.DataFrame([{"id": c["id"], "type": c["cr_type"], "new value": c["new_value"], "status": c["status"],
                                    "admin comment": c["admin_comment"], "filed": c["created_at"]} for c in crs]),
                     hide_index=True, width="stretch")
    for c in [x for x in crs if x["status"] == "APPROVED"]:
        with st.expander(f"Apply approved change #{c['id']} — {c['cr_type']}"):
            if c["cr_type"] == "Bank details":
                st.caption("Approved. Enter the new bank details and upload a new bank proof. Payments stay on hold and a "
                           "reviewer confirms the change before any payment is released. The confirmation goes to your "
                           "previously registered email.")
                holder = st.text_input("New account holder", key=f"bh_{c['id']}")
                acct = st.text_input("New account number", key=f"ba_{c['id']}")
                bankname = st.text_input("New bank name", key=f"bn_{c['id']}")
                code = st.text_input("New IFSC / SWIFT", key=f"bi_{c['id']}")
                up = st.file_uploader("New bank proof", type=[e[1:] for e in ALLOWED_EXT], key=f"bp_{c['id']}")
                if st.button("Apply bank change & re-run", key=f"bb_{c['id']}"):
                    new = {"bank_holder": holder, "account_number": acct, "bank_name": bankname}
                    if v["region"] == "International":
                        new["swift"] = code
                    else:
                        new["ifsc"] = code
                    new = {k: val for k, val in new.items() if val.strip()}
                    files = None
                    if up is not None:
                        files = {"BANK_PROOF": (up.name, up.getvalue())}
                    out = pt.apply_bank_change(conn, v, new, files, UPLOAD_DIR)
                    st.success(f"Bank change submitted. Status {out['status']} (waiting on {out['waiting_on']}); payments stay on hold until a reviewer confirms.")
                    st.rerun()
            else:
                st.caption("Admin approved this change. Submit the new value to replace your current data and re-run verification. "
                           "The previous values stay in the audit trail.")
                field = st.text_input("Field to update (e.g. gstin, service_category)", key=f"ca_field_{c['id']}")
                val = st.text_input("New value", c["new_value"], key=f"ca_val_{c['id']}")
                if st.button("Apply & re-run", key=f"ca_btn_{c['id']}"):
                    s = pt.latest_submission(conn, v["id"])
                    if s and field.strip() and val.strip():
                        out = wf.vendor_response(conn, s["id"], field_changes={field.strip(): val.strip()}, upload_dir=UPLOAD_DIR, actor="vendor")
                        st.success(f"Re-run {out['run_no']}: {out['status']}.")
                        st.rerun()
                    else:
                        st.error("A field, a value and an existing submission are required.")

def vendor_payments_tab(v):
    pt.seed_payments(conn, v["id"])
    hold = pt.list_payments(conn, v["id"], "ON_HOLD")
    if hold:
        st.warning("Some payments are on hold pending review of a bank-details change request.")
        st.dataframe(pd.DataFrame([{"Reference": p["reference_no"], "Amount": p["amount"], "Status": "On hold"} for p in hold]),
                     hide_index=True, width="stretch")
    tabs = st.tabs(["Pending", "Completed"])
    with tabs[0]:
        rows = pt.list_payments(conn, v["id"], "PENDING")
        if rows:
            st.dataframe(pd.DataFrame([{"Reference Number": p["reference_no"], "Amount": p["amount"]} for p in rows]),
                         hide_index=True, width="stretch")
        else:
            st.info("No data available")
    with tabs[1]:
        rows = pt.list_payments(conn, v["id"], "COMPLETED")
        if rows:
            st.dataframe(pd.DataFrame([{"Reference Number": p["reference_no"], "Amount Paid": p["amount"],
                                        "Transaction ID": p["transaction_id"], "Payment Date": p["payment_date"]} for p in rows]),
                         hide_index=True, width="stretch")
        else:
            st.info("No data available")

def vendor_dashboard(vid):
    v = pt.get_vendor(conn, vid)
    if not v:
        st.session_state.pop("vendor", None); st.rerun(); return
    status = pt.vendor_status(conn, v)
    top = st.columns([4, 1])
    top[0].markdown(f"## {v['vendor_id']} — {v['legal_name'] or v['email']}")
    if top[1].button("Log out"):
        st.session_state.pop("vendor", None); st.rerun()
    st.write({"Vendor ID": v["vendor_id"], "Email": v["email"], "Name": v["legal_name"], "Region": v["region"],
              "Type": v["signup_type"], "PAN": v["pan"], "GSTIN": v["gstin"], "Status": status})
    steps = pt.get_steps(conn, v["id"])
    names = {1: "Organisation", 2: "Bank details", 3: "Services", 4: "Documents & terms"}
    cols = st.columns(4)
    for i in (1, 2, 3, 4):
        done = i in steps and (i != 4 or steps.get(4, {}).get("terms_accepted"))
        cols[i - 1].metric(f"Step {i}", "Completed" if done else "Pending")
        cols[i - 1].caption(names[i])
    if status == "Action needed":
        st.warning("Action needed: please open the **Action needed** tab and respond to the open requests.")
    tabs = st.tabs(["Registration", "Action needed", "Change requests", "Payment status"])
    with tabs[0]:
        vendor_registration_tab(v)
    with tabs[1]:
        vendor_action_needed_tab(v)
    with tabs[2]:
        vendor_change_requests_tab(v)
    with tabs[3]:
        vendor_payments_tab(v)

def page_vendor_portal():
    if st.session_state.get("vendor"):
        vendor_dashboard(st.session_state["vendor"])
        return
    st.header("Vendor portal")
    st.caption("Demo portal: no real authentication. OTPs are shown in an on-screen demo inbox; no email is sent.")
    t = st.tabs(["Login", "Sign up", "Forgot Vendor ID"])
    with t[0]:
        vendor_login_block()
    with t[1]:
        vendor_signup_block()
    with t[2]:
        vendor_forgot_block()

# ---------------------------------------------------------------- dispatch
if mode == "Admin review":
    {"Overview": page_overview, "New Submission": page_new, "Live Run": page_live, "Verification Detail": page_detail,
     "Review Queue": page_review, "Change requests": page_admin_changes, "Reminders": page_reminders,
     "History": page_history}[page]()
else:
    page_vendor_portal()

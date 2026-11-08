"""Verivend - Streamlit UI. Run:  streamlit run app.py"""
import os, json, shutil, datetime as dt
import pandas as pd
import streamlit as st
import database as db
import workflow as wf
from config import *
from sample_data import generate as gen

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("VERIVEND_DB", os.path.join(BASE, "data", "verivend.db"))
UPLOAD_DIR = os.environ.get("VERIVEND_UPLOADS", os.path.join(BASE, "uploads"))
SAMPLE_DIR = os.path.join(BASE, "sample_data")
ICON = {"APPROVED": "🟢", "PENDING": "🟠", "REJECTED": "🔴", "PASS": "✅", "FAIL": "❌", "WARN": "⚠️", "NOT_CHECKED": "➖"}

st.set_page_config(page_title="Verivend", page_icon="🧾", layout="wide")

@st.cache_resource
def get_conn():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True); os.makedirs(UPLOAD_DIR, exist_ok=True)
    return db.connect(DB_PATH)
conn = get_conn()
if not os.path.exists(os.path.join(SAMPLE_DIR, "scenarios.json")):
    gen.build(SAMPLE_DIR)
SCENARIOS = gen.load_scenarios(SAMPLE_DIR)

PAGES = ["Overview", "New Submission", "Live Run", "Verification Detail", "Review Queue", "History"]
if "goto" in st.session_state:                      # programmatic navigation (set before the radio is created)
    st.session_state["nav"] = st.session_state.pop("goto")
st.sidebar.title("🧾 Verivend")
st.sidebar.caption("Vendor onboarding automation · independent build for the Zamp PS-2 case study. Deterministic rules; no LLM required.")
page = st.sidebar.radio("Navigate", PAGES, key="nav")
with st.sidebar.expander("Demo database"):
    st.caption(f"SQLite: `{os.path.relpath(DB_PATH, BASE)}`")
    if st.checkbox("I want to wipe all demo data"):
        if st.button("Reset database"):
            conn.close(); get_conn.clear()
            for p in (DB_PATH,): 
                if os.path.exists(p): os.remove(p)
            shutil.rmtree(UPLOAD_DIR, ignore_errors=True)
            for k in ("last_events", "last_result", "pending", "selected_sub"): st.session_state.pop(k, None)
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
        sc = st.selectbox("Scenario", SCENARIOS, format_func=lambda s: f"{s['title']}  →  expect {s['expected']}")
        st.caption(sc["description"] + (f"  **Note:** {sc['note']}" if sc.get("note") else ""))
        if st.button("Run this scenario", type="primary"):
            start_run(sc["form"], gen.scenario_files(SAMPLE_DIR, sc), sc["id"])
    st.divider()
    region = st.radio("Vendor classification", ["Domestic", "International"], horizontal=True)
    dom_type, trc = None, False
    if region == "Domestic":
        dom_type = st.radio("Domestic registration type", ["PAN with GST", "PAN without GST"], horizontal=True)
    else:
        trc = st.checkbox("Tax Residency Certificate (TRC) applicable")
    preview = {"region": region, "domestic_type": dom_type, "trc_applicable": trc}
    with st.form("vendor_form"):
        st.subheader("Organisation")
        a, b = st.columns(2)
        legal = a.text_input("Legal name *"); trade = b.text_input("Trading name (optional)")
        contact = a.text_input("Contact person *"); email = b.text_input("Organisation email *")
        phone = a.text_input("Phone *"); entity = b.selectbox("Entity type *", ENTITY_TYPES)
        address = st.text_area("Registered address *", height=70)
        country = "India"; pan = gstin = reg = tax_id = ifsc = swift = bank_country = ""
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
            reg = st.text_input("Business registration number *")
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
        uploads = {}
        for dt_ in required_docs(preview):
            uploads[dt_] = st.file_uploader(DOC_LABELS[dt_] + " *", type=[e[1:] for e in ALLOWED_EXT], key=f"up_{dt_}")
        terms = st.checkbox("I confirm the details are correct and accept the (demo) vendor declaration. *")
        go = st.form_submit_button("Submit & run verification", type="primary")
    if go:
        form = dict(region=region, domestic_type=dom_type, trc_applicable=trc, legal_name=legal, trading_name=trade, contact_person=contact,
                    email=email, phone=phone, entity_type=entity, address=address, country=country, pan=pan, gstin=gstin, reg_number=reg,
                    tax_id=tax_id, bank_holder=holder, account_number=acct, bank_name=bank, ifsc=ifsc, bank_country=bank_country, swift=swift,
                    service_category=cat, service_description=desc, terms_accepted=terms)
        files = {k: (u.name, u.getvalue()) for k, u in uploads.items() if u is not None}
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
                          "page": f["page"], "evidence": f["evidence"]})
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
        for r in json.loads(d["reasons_json"]): st.markdown(f"- {r}")
        st.info("Next action: " + d["next_action"])
        st.caption("Format/consistency checks only — they do not prove legal existence, tax registration status, sanctions status or bank-account ownership.")
    with t[1]:
        df = entered_vs_extracted(sid, snap)
        st.dataframe(df, hide_index=True, width="stretch") if len(df) else st.write("No extracted fields.")
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
        st.dataframe(pd.DataFrame(fl), hide_index=True, width="stretch") if fl else st.write("No risk flags.")
    with t[5]:
        dr = db.one(conn, "SELECT * FROM communication_drafts WHERE submission_id=? ORDER BY id DESC LIMIT 1", (sid,))
        if dr:
            st.caption(f"To: {dr['to_addr']} · type: {dr['draft_type']} · sent: {bool(dr['sent'])} (Verivend never sends automatically)")
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

{"Overview": page_overview, "New Submission": page_new, "Live Run": page_live, "Verification Detail": page_detail,
 "Review Queue": page_review, "History": page_history}[page]()

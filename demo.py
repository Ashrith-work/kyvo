"""Scripted end-to-end demo on a simulated clock. Submits the baseline scenarios and the four edge
cases as inbound items, drives vendor replies, change requests, reviewer commands and the reminder /
stale lifecycle, and returns a timeline built from the audit trail. No real email is ever sent.
"""
import os, json, shutil, datetime as dt
import database as db
import workflow as wf
import inbox as ibx
import outputs
import lifecycle
import clock
from sample_data import generate as gen

START = "2026-02-02T09:00:00+00:00"


def ws_paths(root):
    return {k: os.path.join(root, k) for k in ("inbox", "processed", "failed", "uploads", "outbox", "out", "sample")} | {
        "db": os.path.join(root, "kyvo.db")}


def open_ws(root, fresh=False):
    if fresh and os.path.isdir(root):
        shutil.rmtree(root)
    p = ws_paths(root)
    for k in ("inbox", "processed", "failed", "uploads", "outbox", "out", "sample"):
        os.makedirs(p[k], exist_ok=True)
    if not os.path.exists(os.path.join(p["sample"], "scenarios.json")):
        gen.build(p["sample"])
    conn = db.connect(p["db"])
    wf.seed_demo(conn)
    return conn, p


def _write_item(inbox_dir, name, item, files=None):
    folder = os.path.join(inbox_dir, name)
    os.makedirs(folder, exist_ok=True)
    if files:
        docs = {}
        for t, (fn, data) in files.items():
            with open(os.path.join(folder, fn), "wb") as fh:
                fh.write(data)
            docs[t] = fn
        item = dict(item, documents=docs)
    with open(os.path.join(folder, "item.json"), "w", encoding="utf-8") as fh:
        json.dump(item, fh)


def _pdf_bytes(tmpdir, name, lines):
    p = os.path.join(tmpdir, name)
    gen.make_pdf(p, lines)
    with open(p, "rb") as fh:
        return (name, fh.read())


def _cheque(bank, holder, acct, ifsc):
    return [bank, "CANCELLED CHEQUE / BANK ACCOUNT CONFIRMATION LETTER", f"Bank: {bank}",
            f"Account Holder: {holder}", f"Account Number: {acct}", f"IFSC: {ifsc}", "Branch: Main"]


def _process(conn, p):
    return ibx.process_inbox(conn, p["inbox"], p["out"], p["uploads"], p["outbox"])


def run_demo(root, start=START):
    conn, p = open_ws(root, fresh=True)
    sc = {s["id"]: s for s in gen.load_scenarios(p["sample"])}
    c = clock.at(start)
    refs = {}

    def files(sid):
        return gen.scenario_files(p["sample"], sc[sid])

    # ---- day 0: all baseline scenarios and the edge cases arrive as new submissions -------------
    order = ["1_happy_path", "2_wrong_document", "3_bank_mismatch", "4_country_tax_mismatch",
             "5_duplicate_pan", "6_pan_without_gst", "7_international_us", "8_unsupported_country",
             "9_scanned_bank_proof", "10_phone_photos", "11_blurry_photo",
             "E1_proprietor", "E3_shared_bank", "E3b_blocklisted_bank", "E4_resubmission"]
    for i, sid in enumerate(order, 1):
        _write_item(p["inbox"], f"{i:03d}_{sid}", {"type": "NEW_SUBMISSION", "submission": sc[sid]["form"]}, files(sid))
    for r in _process(conn, p):
        refs[r["item"].split("_", 1)[1]] = r.get("ref")

    pinnacle = refs["E4_resubmission"]
    orchid = refs["9_scanned_bank_proof"]
    # E2 uses an approved, single-submission vendor with text PDFs (no OCR dependency; meridian also owns the duplicate).
    e2 = refs["6_pan_without_gst"]
    e2_form = sc["6_pan_without_gst"]["form"]
    e2_vid = db.one(conn, "SELECT v.vendor_id AS vid FROM vendors v JOIN submissions s ON s.vendor_id=v.id WHERE s.id=?",
                    (ibx.sid_from_ref(e2),))["vid"]

    # ---- day 1: a bank-details change request on the approved vendor (E2) -----------------------
    c.set((dt.datetime.fromisoformat(start) + dt.timedelta(days=1)).isoformat())
    new_cheque = _cheque("ICICI Bank", e2_form["legal_name"], "99887766554433", "ICIC0005500")
    _write_item(p["inbox"], "201_E2_change", {"type": "CHANGE_REQUEST", "vendor_id": e2_vid, "from": e2_form["email"],
                "change_type": "bank details", "reason": "switching bank",
                "new_values": {"bank_holder": e2_form["legal_name"], "account_number": "99887766554433",
                               "bank_name": "ICICI Bank", "ifsc": "ICIC0005500"}},
                {"BANK_PROOF": _pdf_bytes(p["uploads"], "e2_newbank.pdf", new_cheque)})
    _process(conn, p)

    # ---- day 2: E4 reply (right holder, wrong IFSC) and reviewer approves the E2 bank change -----
    c.set((dt.datetime.fromisoformat(start) + dt.timedelta(days=2)).isoformat())
    co = sc["E4_resubmission"]["form"]
    _write_item(p["inbox"], "301_E4_reply1", {"type": "VENDOR_REPLY", "ref": pinnacle, "from": co["email"]},
                {"BANK_PROOF": _pdf_bytes(p["uploads"], "e4_run2.pdf",
                 _cheque("HDFC Bank", co["legal_name"], co["account_number"], "HDFC0002299"))})
    _write_item(p["inbox"], "302_E2_approve", {"type": "REVIEWER_DECISION", "ref": e2,
                "from": "reviewer@kyvo.example", "command": "APPROVE: new bank account confirmed with the vendor"})
    _process(conn, p)

    # ---- day 4: E4 clean cheque -> approved ------------------------------------------------------
    c.set((dt.datetime.fromisoformat(start) + dt.timedelta(days=4)).isoformat())
    _write_item(p["inbox"], "401_E4_reply2", {"type": "VENDOR_REPLY", "ref": pinnacle, "from": co["email"]},
                {"BANK_PROOF": _pdf_bytes(p["uploads"], "e4_run3.pdf",
                 _cheque("HDFC Bank", co["legal_name"], co["account_number"], "HDFC0002200"))})
    _process(conn, p)

    # ---- reminders on day 3 (already past), day 7, then stale on day 14 --------------------------
    for day in (3, 7, 14):
        c.set((dt.datetime.fromisoformat(start) + dt.timedelta(days=day)).isoformat())
        lifecycle.tick(conn, outbox_dir=p["outbox"], out_root=p["out"])

    # ---- day 15: scenario 9 never replied; a reviewer closes it with a written reason ------------
    c.set((dt.datetime.fromisoformat(start) + dt.timedelta(days=15)).isoformat())
    _write_item(p["inbox"], "501_sc9_reject", {"type": "REVIEWER_DECISION", "ref": orchid,
                "from": "compliance@kyvo.example",
                "command": "REJECT: bank proof unreadable and no reply after the reminder window"})
    _process(conn, p)

    outputs.write_ledger(conn, p["out"])
    outputs.write_digest(conn, p["out"], dt.date.fromisoformat(start[:10]).isoformat())
    timeline = build_timeline(conn, start)
    clock.uninstall()
    return {"workspace": root, "paths": p, "refs": refs, "timeline": timeline, "conn": conn}


KEY_EVENTS = {"SUBMISSION_RECEIVED": "received", "DECISION_MADE": "decided", "VENDOR_RESPONDED": "vendor replied",
              "CHANGE_REQUEST_RECEIVED": "change request", "REMINDER_DRAFTED": "reminder", "MARKED_STALE": "stale",
              "REVIEWER_APPROVE": "reviewer approved", "REVIEWER_REJECT": "reviewer rejected",
              "ESCALATED_TO_REVIEWER": "escalated"}


def build_timeline(conn, start):
    start_d = dt.datetime.fromisoformat(start)
    rows = []
    for e in db.rows(conn, "SELECT submission_id, event, actor, details_json, created_at FROM audit_events "
                           "WHERE event IN ({}) ORDER BY id".format(",".join("?" * len(KEY_EVENTS))),
                     tuple(KEY_EVENTS)):
        sid = e["submission_id"]
        if not sid:
            continue
        raw = e["event"]
        day = (dt.datetime.fromisoformat(e["created_at"]) - start_d).days
        det = json.loads(e["details_json"] or "{}")
        if raw == "DECISION_MADE":
            status, waiting = det.get("status", ""), det.get("waiting_on", "")
        elif raw == "REVIEWER_APPROVE":
            status, waiting = "APPROVED", "NONE"
        elif raw == "REVIEWER_REJECT":
            status, waiting = "REJECTED", "NONE"
        else:
            status, waiting = "", ""
        note = det.get("comment") or det.get("reason") or det.get("kind") or det.get("type") or ""
        rows.append({"day": day, "ref": db.ref(sid), "event": KEY_EVENTS[raw],
                     "status": status or "", "waiting_on": waiting or "", "note": str(note)[:60]})
    return rows


def format_timeline(timeline) -> str:
    L = [f"{'day':>3}  {'submission':<10}  {'event':<16}  {'status':<9}  {'waiting':<8}  note",
         "-" * 78]
    for r in timeline:
        L.append(f"{r['day']:>3}  {r['ref']:<10}  {r['event']:<16}  {r['status']:<9}  {r['waiting_on']:<8}  {r['note']}")
    return "\n".join(L)

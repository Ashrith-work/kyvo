"""Part D: inbound processing.

Two adapters behind one interface:
  Folder (default, offline): items in inbox/ are either a folder holding item.json plus attachments,
    or a single .eml file. Processed items move to processed/, failures to failed/ with an error note.
    A bad item never blocks the queue.
  IMAP (optional, off by default): credentials only from environment variables (see fetch_imap).

Four inbound item types: NEW_SUBMISSION, VENDOR_REPLY, CHANGE_REQUEST, REVIEWER_DECISION.
Every inbound item is hashed; the same item processed twice never creates a second submission or reply.
"""
import os, re, json, shutil, hashlib, email
from email import policy as email_policy
import database as db
import workflow as wf
import outputs
import mailer
import config

DOC_TYPES = ["PAN_PROOF", "GST_CERT", "COMPANY_REG", "BANK_PROOF", "TAX_CERT", "TRC"]
SUBMISSION_NONFIELDS = {"type", "documents", "submission", "ref", "from", "fields", "vendor_id",
                        "change_type", "new_values", "reason", "command"}


class Ctx:
    def __init__(self, inbox_root, out_root, upload_dir, outbox_dir, processed_dir=None, failed_dir=None):
        base = os.path.dirname(os.path.abspath(inbox_root))
        self.inbox_root = inbox_root
        self.out_root = out_root
        self.upload_dir = upload_dir
        self.outbox_dir = outbox_dir
        self.processed_dir = processed_dir or os.path.join(base, "processed")
        self.failed_dir = failed_dir or os.path.join(base, "failed")


# ---- reading items ---------------------------------------------------------------
def _attachments_from_folder(folder, documents):
    out = {}
    for dtype, fname in (documents or {}).items():
        p = os.path.join(folder, fname)
        with open(p, "rb") as fh:
            out[dtype] = (fname, fh.read())
    return out


def _doc_type_from_name(name):
    up = name.upper()
    for t in DOC_TYPES:
        if t in up:
            return t
    return None


def read_item(path):
    """Return (item_dict, attachments{TYPE:(name,bytes)}). Supports a folder with item.json or a .eml file."""
    if os.path.isdir(path):
        with open(os.path.join(path, "item.json"), encoding="utf-8") as fh:
            item = json.load(fh)
        return item, _attachments_from_folder(path, item.get("documents"))
    # .eml
    with open(path, "rb") as fh:
        msg = email.message_from_binary_file(fh, policy=email_policy.default)
    item = {"from": (msg["From"] or ""), "subject": (msg["Subject"] or "")}
    item["type"] = msg["X-Kyvo-Type"] or _infer_type(item)
    if msg["X-Kyvo-Command"]:
        item["command"] = msg["X-Kyvo-Command"]
    if msg["X-Kyvo-Vendor-Id"]:
        item["vendor_id"] = msg["X-Kyvo-Vendor-Id"]
    body = msg.get_body(preferencelist=("plain",))
    text = body.get_content() if body else ""
    item["fields"] = _parse_fields(text)
    if not item.get("command") and item["type"] == "REVIEWER_DECISION":
        item["command"] = text.strip().splitlines()[0] if text.strip() else ""
    attachments = {}
    for part in msg.iter_attachments():
        name = part.get_filename() or "attachment"
        t = _doc_type_from_name(name)
        if t:
            attachments[t] = (name, part.get_payload(decode=True) or b"")
    return item, attachments


def _infer_type(item):
    subj = (item.get("subject") or "")
    if re.search(r"\[Kyvo SUB-\d+\]", subj):
        return "REVIEWER_DECISION" if (item.get("from", "").lower() in config.REVIEWER_ALLOWLIST) else "VENDOR_REPLY"
    return "NEW_SUBMISSION"


_FIELD_KEYS = set(wf.FORM_ALIASES) | {"legal_name", "entity_type", "reg_number", "phone", "contact_person",
                                      "pan", "gstin", "ifsc", "swift", "bank_holder", "account_number",
                                      "bank_name", "email", "state", "city", "pincode", "proprietor_name",
                                      "tax_id", "trc_number"}

def _parse_fields(text):
    out = {}
    for line in (text or "").splitlines():
        m = re.match(r"\s*([A-Za-z_ ]+?)\s*:\s*(.+?)\s*$", line)
        if not m:
            continue
        key = m.group(1).strip().lower().replace(" ", "_")
        if key in _FIELD_KEYS:
            out[key] = m.group(2).strip()
    return out


def item_hash(item, attachments):
    h = hashlib.sha256()
    h.update(json.dumps(item, sort_keys=True, default=str).encode("utf-8"))
    for t in sorted(attachments):
        h.update(t.encode("utf-8"))
        h.update(hashlib.sha256(attachments[t][1]).digest())
    return h.hexdigest()


# ---- helpers ---------------------------------------------------------------------
def sid_from_ref(ref):
    m = re.search(r"SUB-?0*(\d+)", str(ref or ""))
    return int(m.group(1)) if m else None


def registered_email(conn, sid):
    s = db.one(conn, "SELECT snapshot_json, vendor_id FROM submissions WHERE id=?", (sid,))
    if not s:
        return ""
    snap = json.loads(s["snapshot_json"] or "{}")
    if snap.get("email"):
        return snap["email"].strip().lower()
    v = db.one(conn, "SELECT email FROM vendors WHERE id=?", (s["vendor_id"],)) or {}
    return (v.get("email") or "").strip().lower()


def latest_submission_for_vendor(conn, vendor_id):
    v = db.one(conn, "SELECT id FROM vendors WHERE vendor_id=?", (str(vendor_id).strip().upper(),))
    if not v:
        return None, None
    s = db.one(conn, "SELECT id FROM submissions WHERE vendor_id=? ORDER BY id DESC LIMIT 1", (v["id"],))
    return (v["id"], s["id"]) if s else (v["id"], None)


def escalate(conn, sid, ctx, kind, detail):
    """Send an escalation to the reviewer allowlist with the submission report inlined."""
    report = outputs.report_md(conn, sid) if sid else ""
    body = (f"Reviewer attention needed: {kind}.\n\n{detail}\n\n"
            + ("--- submission report ---\n" + report if report else ""))
    draft = {"draft_type": "reviewer_escalation", "to_addr": ", ".join(config.REVIEWER_ALLOWLIST),
             "subject": f"[{db.ref(sid) if sid else 'Kyvo'}] Reviewer attention: {kind}", "body": body}
    mailer.deliver(conn, sid, draft, ctx.outbox_dir)
    db.audit(conn, sid, "ESCALATED_TO_REVIEWER", "system", {"kind": kind})


# ---- handlers --------------------------------------------------------------------
def handle_new_submission(conn, item, attachments, ctx):
    f = item.get("submission") or {k: v for k, v in item.items() if k not in SUBMISSION_NONFIELDS}
    res = wf.run_submission(conn, f, attachments, ctx.upload_dir)
    sid = res["submission_id"]
    srow = db.one(conn, "SELECT vendor_id FROM submissions WHERE id=?", (sid,))
    vid = db.issue_vendor_id(conn, srow["vendor_id"])
    ack = {"draft_type": "acknowledgement", "to_addr": (f.get("email") or ""),
           "subject": f"[{db.ref(sid)}] Vendor registration received, your Vendor ID is {vid}",
           "body": (f"Dear {f.get('contact_person') or f.get('contact_name') or 'Vendor team'},\n\n"
                    f"We have received your vendor registration (reference {db.ref(sid)}). "
                    f"Your Vendor ID is {vid}. Please quote it in any reply.\n\n"
                    f"We will review your submission and write to you if anything is needed.")}
    mailer.deliver(conn, sid, ack, ctx.outbox_dir)
    outputs.write_outputs(conn, sid, ctx.out_root)
    return {"ref": db.ref(sid), "vendor_id": vid, "status": res["status"]}


def handle_vendor_reply(conn, item, attachments, ctx):
    sid = sid_from_ref(item.get("ref") or item.get("subject"))
    if not sid or not db.one(conn, "SELECT id FROM submissions WHERE id=?", (sid,)):
        raise ValueError(f"vendor reply references an unknown submission: {item.get('ref') or item.get('subject')}")
    sender = (item.get("from") or "").strip().lower()
    reg = registered_email(conn, sid)
    if sender != reg:
        db.audit(conn, sid, "REPLY_FROM_UNREGISTERED_ADDRESS", "system", {"from": sender, "registered": reg})
        escalate(conn, sid, ctx, "reply from an unregistered address",
                 f"A reply for {db.ref(sid)} came from {sender}, not the registered email ({reg}). No change was applied.")
        return {"ref": db.ref(sid), "ignored": "unregistered_sender"}
    fields = item.get("fields") or None
    res = wf.vendor_response(conn, sid, field_changes=fields, files=attachments or None, upload_dir=ctx.upload_dir)
    outputs.write_outputs(conn, sid, ctx.out_root)
    return {"ref": db.ref(sid), "status": res["status"], "run_no": res["run_no"]}


def handle_change_request(conn, item, attachments, ctx):
    vid = (item.get("vendor_id") or "").strip().upper()
    vrow_id, sid = latest_submission_for_vendor(conn, vid)
    if not sid:
        raise ValueError(f"change request for an unknown or unsubmitted vendor: {vid}")
    ctype = (item.get("change_type") or "").strip().lower()
    new_values = item.get("new_values") or {}
    reason = item.get("reason") or ""
    db.insert(conn, "change_requests", vendor_id=vrow_id, submission_id=sid, cr_type=ctype,
              new_value=json.dumps(new_values, default=str), reason=reason, file_path="",
              terms_accepted=1, status="PENDING", created_at=db.now())
    db.audit(conn, sid, "CHANGE_REQUEST_RECEIVED", "vendor", {"vendor_id": vid, "type": ctype, "reason": reason})
    reg = registered_email(conn, sid)
    if "bank" in ctype:   # E2: a bank-details change always holds payments and needs a reviewer
        conn.execute("UPDATE payments SET status='ON_HOLD' WHERE vendor_id=?", (vrow_id,))
        if not db.one(conn, "SELECT id FROM payments WHERE vendor_id=?", (vrow_id,)):
            db.insert(conn, "payments", vendor_id=vrow_id, reference_no=f"HOLD-{db.ref(sid)}", amount="",
                      status="ON_HOLD", created_at=db.now())
        else:
            conn.execute("UPDATE payments SET status='ON_HOLD' WHERE vendor_id=?", (vrow_id,)); conn.commit()
        sender = (item.get("from") or "").strip().lower()
        if sender and sender != reg:
            db.audit(conn, sid, "BANK_CHANGE_FROM_OTHER_ADDRESS", "system", {"from": sender, "registered": reg})
        res = wf.vendor_response(conn, sid, field_changes=new_values or None, files=attachments or None,
                                 upload_dir=ctx.upload_dir, bank_change=True, confirm_to=reg)
        outputs.write_outputs(conn, sid, ctx.out_root)
        return {"ref": db.ref(sid), "status": res["status"], "payment_hold": True, "waiting_on": res["waiting_on"]}
    escalate(conn, sid, ctx, f"{ctype or 'organisation'} change request",
             f"Vendor {vid} ({db.ref(sid)}) requested a {ctype or 'organisation'} change. Reason: {reason}. Values: {new_values}.")
    outputs.write_outputs(conn, sid, ctx.out_root)
    return {"ref": db.ref(sid), "change_request": "PENDING"}


def handle_reviewer_decision(conn, item, attachments, ctx):
    sender = (item.get("from") or "").strip().lower()
    sid = sid_from_ref(item.get("ref") or item.get("subject"))
    cmd = (item.get("command") or "").strip()
    if sender not in config.REVIEWER_ALLOWLIST:
        db.audit(conn, sid, "REVIEWER_COMMAND_IGNORED", sender or "unknown",
                 {"reason": "sender not on the reviewer allowlist", "command": cmd})
        return {"ref": db.ref(sid) if sid else None, "ignored": "not_allowlisted"}
    if not sid or not db.one(conn, "SELECT id FROM submissions WHERE id=?", (sid,)):
        raise ValueError(f"reviewer decision references an unknown submission: {item.get('ref') or item.get('subject')}")
    head, _, rest = cmd.partition(":")
    head, rest = head.strip().upper(), rest.strip()
    if head == "APPROVE":
        wf.review_action(conn, sid, sender, "APPROVE", rest or "Approved by reviewer.")
        outputs.write_outputs(conn, sid, ctx.out_root)
        return {"ref": db.ref(sid), "status": "APPROVED"}
    if head == "REJECT":
        if not rest:
            db.audit(conn, sid, "REVIEWER_REJECT_REFUSED", sender, {"reason": "a written reason is required to reject"})
            raise ValueError("REJECT requires a written reason.")
        wf.review_action(conn, sid, sender, "REJECT", rest)
        outputs.write_outputs(conn, sid, ctx.out_root)
        return {"ref": db.ref(sid), "status": "REJECTED"}
    if head == "REQUEST":
        if not rest:
            raise ValueError("REQUEST requires the text of the ask.")
        db.insert(conn, "follow_up_requests", submission_id=sid, rule_id="REVIEWER_REQUEST", about="Reviewer request",
                  ask=rest, status="OPEN", opened_run=db.one(conn, "SELECT run_no FROM submissions WHERE id=?", (sid,))["run_no"],
                  resolved_run=None, created_at=db.now(), updated_at=db.now())
        db.audit(conn, sid, "REVIEWER_REQUEST", sender, {"ask": rest})
        draft = {"draft_type": "follow_up_request", "to_addr": registered_email(conn, sid),
                 "subject": f"[{db.ref(sid)}] Action needed to complete your vendor registration",
                 "body": f"Dear Vendor team,\n\nTo continue your registration please help us with the following:\n\n1. {rest}\n\nOnce you reply we will re-run the checks."}
        mailer.deliver(conn, sid, draft, ctx.outbox_dir)
        outputs.write_outputs(conn, sid, ctx.out_root)
        return {"ref": db.ref(sid), "reviewer_request": rest}
    raise ValueError(f"unrecognised reviewer command: {cmd!r} (expected APPROVE, REJECT: <reason>, or REQUEST: <ask>)")


HANDLERS = {"NEW_SUBMISSION": handle_new_submission, "VENDOR_REPLY": handle_vendor_reply,
            "CHANGE_REQUEST": handle_change_request, "REVIEWER_DECISION": handle_reviewer_decision}


# ---- the queue -------------------------------------------------------------------
def _move(src, dest_dir):
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, os.path.basename(src))
    if os.path.exists(dest):
        dest = dest + "_" + db.now().replace(":", "").replace("-", "")
    shutil.move(src, dest)
    return dest


def process_inbox(conn, inbox_root, out_root, upload_dir, outbox_dir):
    """Process every item in inbox_root once. Returns a list of per-item results. A bad item is moved to
    failed/ with an error note and never blocks the rest of the queue."""
    ctx = Ctx(inbox_root, out_root, upload_dir, outbox_dir)
    results = []
    if not os.path.isdir(inbox_root):
        return results
    for name in sorted(os.listdir(inbox_root)):
        path = os.path.join(inbox_root, name)
        if name.startswith("."):
            continue
        try:
            item, attachments = read_item(path)
            itype = item.get("type")
            h = item_hash({**item, "type": itype}, attachments)
            if db.seen_inbound(conn, h):
                results.append({"item": name, "status": "duplicate_skipped"})
                _move(path, ctx.processed_dir)
                continue
            if itype not in HANDLERS:
                raise ValueError(f"unknown inbound item type: {itype!r}")
            out = HANDLERS[itype](conn, item, attachments, ctx)
            db.record_inbound(conn, h, itype, name, ref=out.get("ref"), status="processed")
            results.append({"item": name, "type": itype, **out})
            _move(path, ctx.processed_dir)
        except Exception as e:   # one bad item must never block the queue
            os.makedirs(ctx.failed_dir, exist_ok=True)
            dest = _move(path, ctx.failed_dir)
            note = dest + ".error.txt" if os.path.isfile(dest) else os.path.join(dest, "error.txt")
            try:
                with open(note, "w", encoding="utf-8") as fh:
                    fh.write(f"{type(e).__name__}: {e}\n")
            except OSError:
                pass
            try:
                db.record_inbound(conn, hashlib.sha256((name + str(e)).encode()).hexdigest(),
                                  "UNKNOWN", name, status="failed", error=str(e))
            except Exception:
                pass
            results.append({"item": name, "status": "failed", "error": str(e)})
    return results


def fetch_imap(dest_dir):
    """Optional IMAP adapter (off by default). Fetches unseen messages into dest_dir as .eml files using
    IMAP_HOST/IMAP_USER/IMAP_PASSWORD/IMAP_FOLDER from the environment. Returns the number fetched."""
    host = os.environ.get("IMAP_HOST")
    if not host:
        raise RuntimeError("IMAP is off: set IMAP_HOST, IMAP_USER, IMAP_PASSWORD to enable it.")
    import imaplib
    os.makedirs(dest_dir, exist_ok=True)
    user, pw = os.environ.get("IMAP_USER"), os.environ.get("IMAP_PASSWORD")
    folder = os.environ.get("IMAP_FOLDER", "INBOX")
    M = imaplib.IMAP4_SSL(host)
    M.login(user, pw)
    M.select(folder)
    typ, data = M.search(None, "UNSEEN")
    n = 0
    for num in data[0].split():
        typ, msg = M.fetch(num, "(RFC822)")
        with open(os.path.join(dest_dir, f"imap_{num.decode()}.eml"), "wb") as fh:
            fh.write(msg[0][1])
        n += 1
    M.logout()
    return n

"""Vendor-portal backend: sign-up, simulated OTP/CAPTCHA, four-step registration, change requests and payments.
No real authentication and no email is ever sent (the OTP is shown in an on-screen demo inbox). All data is fictional."""
import os, json, hashlib, secrets, random
import datetime as dt
import database as db
import workflow as wf
import validation as va
from config import required_docs

OTP_TTL_MIN = 10
OTP_MAX_ATTEMPTS = 5

# ---- vendor id & sign-up ----------------------------------------------------------
def new_vendor_id(conn) -> str:
    for _ in range(50):
        vid = "V" + f"{random.randint(0, 99999):05d}"
        if not db.one(conn, "SELECT id FROM vendors WHERE vendor_id=?", (vid,)):
            return vid
    return "V" + f"{random.randint(0, 99999):05d}"

def _signup_key(region, pan, reg_number):
    if region != "International" and pan:
        return f"PAN:{pan.upper()}"
    if region == "International" and reg_number:
        return f"REG:{reg_number.upper()}"
    return f"SIGNUP:{secrets.token_hex(6)}"

def find_identifier_owner(conn, id_type, id_value):
    """A vendor that already holds this PAN / GSTIN / registration number, via sign-up fields or stored identifiers."""
    col = {"PAN": "pan", "GSTIN": "gstin", "REG_NO": "reg_number"}.get(id_type)
    if col and id_value:
        row = db.one(conn, f"SELECT * FROM vendors WHERE {col}=? AND vendor_id IS NOT NULL", (id_value.upper(),))
        if row:
            return row
    return None

def signup(conn, region, email, domestic_type=None, pan=None, gstin=None, business_reg_number=None, trc_number=None):
    """Create a vendor and issue a Vendor ID. Raises ValueError (pointing to Forgot Vendor ID) if an identifier is already registered."""
    pan = (pan or "").strip().upper() or None
    gstin = (gstin or "").strip().upper() or None
    reg = (business_reg_number or "").strip().upper() or None
    for id_type, val in (("PAN", pan), ("GSTIN", gstin), ("REG_NO", reg)):
        owner = find_identifier_owner(conn, id_type, val)
        if owner:
            raise ValueError(f"A vendor is already registered with this {id_type} (Vendor ID {owner['vendor_id']}). "
                             f"Please use Forgot Vendor ID instead of signing up again.")
    vid = new_vendor_id(conn)
    row_id = db.insert(conn, "vendors", vendor_key=_signup_key(region, pan, reg), vendor_id=vid, email=email.strip(),
                       region=region, country="India" if region != "International" else None, pan=pan, gstin=gstin,
                       reg_number=reg, signup_region=region, signup_type=domestic_type, created_at=db.now())
    db.audit(conn, None, "VENDOR_SIGNED_UP", vid, {"region": region, "type": domestic_type})
    # stash TRC number for International so the registration can prefill it
    if trc_number:
        save_step(conn, row_id, 0, {"trc_number": trc_number.strip()})
    return db.one(conn, "SELECT * FROM vendors WHERE id=?", (row_id,))

def get_vendor(conn, public_id):
    return db.one(conn, "SELECT * FROM vendors WHERE vendor_id=?", ((public_id or "").strip().upper(),))

def forgot_vendor_id(conn, region, domestic_type=None, gstin=None, pan=None, business_reg_number=None):
    """Domestic: by registered GSTIN (PAN for PAN-without-GST). International: by business registration number."""
    if region == "International":
        row = db.one(conn, "SELECT * FROM vendors WHERE reg_number=? AND vendor_id IS NOT NULL", ((business_reg_number or "").strip().upper(),))
    elif domestic_type == "PAN without GST":
        row = db.one(conn, "SELECT * FROM vendors WHERE pan=? AND vendor_id IS NOT NULL", ((pan or "").strip().upper(),))
    else:
        row = db.one(conn, "SELECT * FROM vendors WHERE gstin=? AND vendor_id IS NOT NULL", ((gstin or "").strip().upper(),))
    return row["vendor_id"] if row else None

# ---- OTP (hashed, single use, expiring) -------------------------------------------
def _hash_otp(vid, otp):
    return hashlib.sha256(f"{vid}:{otp}".encode()).hexdigest()

def issue_otp(conn, public_id, purpose="login"):
    """Create a 6-digit OTP, store only its hash, and RETURN the plaintext for the on-screen demo inbox. No email is sent."""
    otp = f"{secrets.randbelow(1000000):06d}"
    now = dt.datetime.now(dt.timezone.utc)
    db.insert(conn, "otp_tokens", vendor_id=public_id, otp_hash=_hash_otp(public_id, otp), purpose=purpose,
              created_at=now.isoformat(timespec="seconds"), expires_at=(now + dt.timedelta(minutes=OTP_TTL_MIN)).isoformat(timespec="seconds"),
              used=0, attempts=0)
    return otp

def verify_otp(conn, public_id, otp, now=None):
    """Return (ok, message). Enforces single use, 10-minute expiry and 5 attempts on the latest token."""
    now = now or dt.datetime.now(dt.timezone.utc)
    row = db.one(conn, "SELECT * FROM otp_tokens WHERE vendor_id=? ORDER BY id DESC LIMIT 1", (public_id,))
    if not row:
        return False, "Please request an OTP first."
    if row["used"]:
        return False, "That OTP was already used. Please request a new one."
    if dt.datetime.fromisoformat(row["expires_at"]) < now:
        return False, "That OTP has expired. Please request a new one."
    if row["attempts"] >= OTP_MAX_ATTEMPTS:
        return False, "Too many attempts. Please request a new OTP."
    conn.execute("UPDATE otp_tokens SET attempts=attempts+1 WHERE id=?", (row["id"],)); conn.commit()
    if _hash_otp(public_id, (otp or "").strip()) != row["otp_hash"]:
        left = OTP_MAX_ATTEMPTS - (row["attempts"] + 1)
        return False, f"Incorrect OTP. {max(left, 0)} attempt(s) left."
    conn.execute("UPDATE otp_tokens SET used=1 WHERE id=?", (row["id"],)); conn.commit()
    return True, "Verified."

# ---- four-step registration -------------------------------------------------------
def save_step(conn, vrow_id, step_no, data):
    row = db.one(conn, "SELECT id FROM registration_steps WHERE vendor_id=? AND step_no=?", (vrow_id, step_no))
    payload = json.dumps(data, default=str)
    if row:
        conn.execute("UPDATE registration_steps SET data_json=?, completed=1, updated_at=? WHERE id=?", (payload, db.now(), row["id"])); conn.commit()
    else:
        db.insert(conn, "registration_steps", vendor_id=vrow_id, step_no=step_no, data_json=payload, completed=1, updated_at=db.now())

def get_steps(conn, vrow_id):
    out = {}
    for r in db.rows(conn, "SELECT step_no, data_json FROM registration_steps WHERE vendor_id=?", (vrow_id,)):
        out[r["step_no"]] = json.loads(r["data_json"] or "{}")
    return out

def steps_complete(conn, vrow_id):
    steps = get_steps(conn, vrow_id)
    return all(n in steps for n in (1, 2, 3, 4)) and bool(steps.get(4, {}).get("terms_accepted"))

def assemble_form(conn, vendor):
    """Build the workflow form dict from the sign-up record and the four saved steps."""
    steps = get_steps(conn, vendor["id"])
    form = {"region": vendor["region"], "domestic_type": vendor["signup_type"],
            "trc_applicable": vendor["region"] == "International",
            "email": vendor["email"], "contact_email": vendor["email"],
            "pan": vendor["pan"], "gstin": vendor["gstin"], "reg_number": vendor["reg_number"],
            "trc_number": steps.get(0, {}).get("trc_number", "")}
    for n in (1, 2, 3):
        form.update(steps.get(n, {}))
    form["terms_accepted"] = bool(steps.get(4, {}).get("terms_accepted"))
    return form

def submit_registration(conn, vendor, files, upload_dir):
    """Final submit: only when all four steps are complete and terms accepted. Runs the one workflow on this vendor row."""
    if not steps_complete(conn, vendor["id"]):
        raise ValueError("All four steps must be completed and the terms accepted before submitting.")
    form = assemble_form(conn, vendor)
    return wf.run_submission(conn, form, files, upload_dir, actor="vendor", vendor_row_id=vendor["id"])

# ---- vendor status for the dashboard ----------------------------------------------
def latest_submission(conn, vrow_id):
    return db.one(conn, "SELECT * FROM submissions WHERE vendor_id=? ORDER BY id DESC LIMIT 1", (vrow_id,))

def vendor_status(conn, vendor):
    s = latest_submission(conn, vendor["id"])
    if not s:
        return "Registration in progress" if get_steps(conn, vendor["id"]) else "Sign-up completed"
    d = db.latest_decision(conn, s["id"])
    if not d:
        return "Under verification"
    if d["status"] == "APPROVED":
        return "Approved"
    if d["status"] == "REJECTED":
        return "Rejected"
    return "Action needed" if (d["waiting_on"] == "VENDOR") else "Under verification"

def open_requests(conn, vrow_id):
    s = latest_submission(conn, vrow_id)
    if not s:
        return []
    return db.rows(conn, "SELECT * FROM follow_up_requests WHERE submission_id=? AND status='OPEN' ORDER BY id", (s["id"],))

# ---- change requests --------------------------------------------------------------
CR_TYPES = ["GST / PAN", "Bank details", "Organisation information", "Technical services"]

def create_change_request(conn, vrow_id, cr_type, new_value, reason, file_path, terms):
    s = latest_submission(conn, vrow_id)
    cid = db.insert(conn, "change_requests", vendor_id=vrow_id, submission_id=s["id"] if s else None, cr_type=cr_type,
                    new_value=new_value, reason=reason, file_path=file_path or "", terms_accepted=1 if terms else 0,
                    status="PENDING", admin_comment="", created_at=db.now())
    db.audit(conn, s["id"] if s else None, "CHANGE_REQUEST_FILED", "vendor", {"cr_id": cid, "type": cr_type})
    # E2: a bank-details change puts payments on hold immediately, before any review.
    if cr_type == "Bank details":
        hold_payments(conn, vrow_id)
    return cid

def list_change_requests(conn, vrow_id):
    return db.rows(conn, "SELECT * FROM change_requests WHERE vendor_id=? ORDER BY id DESC", (vrow_id,))

def apply_bank_change(conn, vendor, new_bank_fields, files, upload_dir):
    """E2: apply an approved bank-details change. Old details are kept in the audit trail; payments stay on hold; the
    re-run is always PENDING / waiting_on REVIEWER and the confirmation drafts to the PREVIOUSLY registered email."""
    s = latest_submission(conn, vendor["id"])
    if not s:
        raise ValueError("No submission to change.")
    snap = json.loads(s["snapshot_json"])
    prev_email = snap.get("email") or vendor["email"]
    old = {k: snap.get(k) for k in ("bank_holder", "account_number", "bank_name", "ifsc", "swift", "bank_country")}
    db.audit(conn, s["id"], "BANK_DETAILS_CHANGE_APPLIED", "vendor", {"old": old, "new": new_bank_fields})
    hold_payments(conn, vendor["id"])
    return wf.vendor_response(conn, s["id"], field_changes=new_bank_fields, files=files, upload_dir=upload_dir,
                             actor="vendor", bank_change=True, confirm_to=prev_email)

def decide_change_request(conn, cr_id, approve, comment, admin):
    if not approve and not (comment or "").strip():
        raise ValueError("A comment is required to reject a change request.")
    status = "APPROVED" if approve else "REJECTED"
    conn.execute("UPDATE change_requests SET status=?, admin_comment=?, decided_at=?, decided_by=? WHERE id=?",
                 (status, comment, db.now(), admin, cr_id)); conn.commit()
    cr = db.one(conn, "SELECT * FROM change_requests WHERE id=?", (cr_id,))
    db.audit(conn, cr["submission_id"], f"CHANGE_REQUEST_{status}", admin, {"cr_id": cr_id, "comment": comment})
    return cr

# ---- payments ---------------------------------------------------------------------
def seed_payments(conn, vrow_id):
    if db.one(conn, "SELECT id FROM payments WHERE vendor_id=?", (vrow_id,)):
        return
    db.insert(conn, "payments", vendor_id=vrow_id, reference_no=f"PMT-{vrow_id:04d}-01", amount="INR 1,20,000",
              status="COMPLETED", transaction_id=f"TXN{vrow_id:04d}9001", payment_date="2026-09-18", created_at=db.now())
    db.insert(conn, "payments", vendor_id=vrow_id, reference_no=f"PMT-{vrow_id:04d}-02", amount="INR 85,000",
              status="PENDING", transaction_id="", payment_date="", created_at=db.now())

def list_payments(conn, vrow_id, status):
    return db.rows(conn, "SELECT * FROM payments WHERE vendor_id=? AND status=? ORDER BY id", (vrow_id, status))

def hold_payments(conn, vrow_id):
    conn.execute("UPDATE payments SET status='ON_HOLD' WHERE vendor_id=? AND status='PENDING'", (vrow_id,)); conn.commit()

def release_payments(conn, vrow_id):
    conn.execute("UPDATE payments SET status='PENDING' WHERE vendor_id=? AND status='ON_HOLD'", (vrow_id,)); conn.commit()

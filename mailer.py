"""Outbound mail. Default mode is 'outbox': every message is written as an .eml file and NEVER sent.
SMTP sending happens only when KYVO_SEND=1 and the SMTP_* environment variables are set, and never to a
demo (.example) or other never-send domain. Every message is also stored as a communication draft so it
appears in out/<ref>/emails and the audit trail.
"""
import os, smtplib
from email.message import EmailMessage
import database as db
import outputs
from config import MAILER


def send_mode() -> str:
    if os.environ.get("KYVO_SEND") == "1":
        return "smtp"
    return MAILER.get("default_send_mode", "outbox")


def never_send(addr: str) -> bool:
    dom = (addr or "").rsplit("@", 1)[-1].lower()
    return any(dom == d or dom.endswith("." + d) for d in MAILER.get("never_send_domains", []))


def _smtp_send(sender: str, draft: dict):
    host = os.environ.get("SMTP_HOST")
    if not host:
        raise RuntimeError("KYVO_SEND=1 but SMTP_HOST is not set.")
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = draft.get("to_addr", "")
    msg["Subject"] = draft.get("subject", "")
    msg.set_content(draft.get("body", "") or "")
    port = int(os.environ.get("SMTP_PORT", "587"))
    with smtplib.SMTP(host, port) as s:
        if os.environ.get("SMTP_STARTTLS", "1") == "1":
            s.starttls()
        user, pw = os.environ.get("SMTP_USER"), os.environ.get("SMTP_PASSWORD")
        if user:
            s.login(user, pw or "")
        s.send_message(msg)


def deliver(conn, sid, draft: dict, outbox_dir: str, sender: str = outputs.SENDER) -> dict:
    """Record the message as a draft, write it to the outbox as .eml, and send over SMTP only when
    explicitly enabled and the recipient is a real (non-demo) address. Returns {mode, path, sent, draft_id}."""
    cid = db.insert(conn, "communication_drafts", submission_id=sid, decision_id=draft.get("decision_id"),
                    draft_type=draft.get("draft_type", "message"), to_addr=draft.get("to_addr", ""),
                    subject=draft.get("subject", ""), body=draft.get("body", ""), sent=0, created_at=db.now())
    os.makedirs(outbox_dir, exist_ok=True)
    path = os.path.join(outbox_dir, f"{(sid or 0):05d}_{cid:04d}_{draft.get('draft_type', 'message')}.eml")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(outputs.eml({**draft, "created_at": db.now()}, sender))
    mode, sent = send_mode(), False
    if mode == "smtp" and not never_send(draft.get("to_addr")):
        _smtp_send(sender, draft)
        sent = True
        conn.execute("UPDATE communication_drafts SET sent=1 WHERE id=?", (cid,))
        conn.commit()
    db.audit(conn, sid, "EMAIL_SENT" if sent else "EMAIL_QUEUED", "mailer",
             {"to": draft.get("to_addr"), "type": draft.get("draft_type"), "mode": mode, "file": os.path.basename(path)})
    return {"mode": mode, "path": path, "sent": sent, "draft_id": cid}

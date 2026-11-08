"""Deterministic three-status policy. No LLM. APPROVED only if clean; PENDING for any uncertainty;
REJECTED only for a written policy disqualification (blocklist) or a human reviewer's confirmed rejection."""
from config import POLICY_VERSION
from database import now

SEV_RANK = {"LOW": 1, "MEDIUM": 2, "HIGH": 3}

def decide(results: list) -> dict:
    blocking = [r for r in results if r["blocking"] and r["status"] != "PASS"]
    disq = [r for r in blocking if r["flag_code"] == "POLICY_BLOCKLIST"]
    if disq:
        status = "REJECTED"
    elif blocking:
        status = "PENDING"
    else:
        status = "APPROVED"
    top = max((SEV_RANK[r["severity"]] for r in blocking), default=0)
    risk = {0: "LOW", 1: "LOW", 2: "MEDIUM", 3: "HIGH"}[top]
    if status == "APPROVED":
        reasons = ["All applicable fields and documents are present, document types match, formats pass, "
                   "cross-document checks agree and no duplicate was found."]
        nxt = "Proceed with vendor activation. No human review required (format/consistency checks only - not an official registry verification)."
    elif status == "REJECTED":
        reasons = [f"{r['label']}: {r['evidence']}" for r in disq]
        nxt = "Policy disqualification recorded - a reviewer must confirm and communicate the outcome."
    else:
        reasons = [f"[{r['severity']}] {r['label']} - {r['status']}: {r['explanation'] or r['evidence']}" for r in
                   sorted(blocking, key=lambda r: -SEV_RANK[r["severity"]])]
        nxt = "Send the drafted follow-up email (after human approval) and route to the review queue; re-run checks when the vendor responds."
    return {"status": status, "reasons": reasons, "risk_level": risk, "next_action": nxt,
            "human_review_required": int(status != "APPROVED"), "policy_version": POLICY_VERSION,
            "blocking_count": len(blocking),
            "counts": {s: sum(1 for r in results if r["status"] == s) for s in ("PASS", "FAIL", "WARN", "NOT_CHECKED")},
            "decided_at": now()}

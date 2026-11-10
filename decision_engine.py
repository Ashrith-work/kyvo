"""Deterministic three-status policy. No LLM. APPROVED only if clean; PENDING for any uncertainty;
REJECTED only for a written policy disqualification (blocklist) or a human reviewer's confirmed rejection."""
from config import POLICY_VERSION, RISK_RANK, RANK_TO_RISK, flag_is_reviewer
from database import now

SEV_RANK = RISK_RANK   # severity name -> rank, from policy/kyvo_policy.yaml (risk_weights)

def compute_waiting_on(results: list, status: str) -> str:
    """NONE when approved/clean, REVIEWER when any blocking item routes to a reviewer in flags.yaml, else VENDOR."""
    blocking = [r for r in results if r["blocking"] and r["status"] != "PASS"]
    if status == "APPROVED" or not blocking:
        return "NONE"
    if status == "REJECTED" or any(flag_is_reviewer(r.get("flag_code")) for r in blocking):
        return "REVIEWER"
    return "VENDOR"

def derive_requests(results: list) -> list:
    """Vendor-facing asks derived from blocking results (deduplicated). Part C backs these with the ledger."""
    seen, out = set(), []
    for r in results:
        if r["blocking"] and r["status"] != "PASS" and r.get("action"):
            if r["action"] in seen:
                continue
            seen.add(r["action"])
            out.append({"id": r["rule_id"], "about": r["label"], "ask": r["action"], "status": "OPEN"})
    return out

def decision_record(submission_id, vendor_id, run_no, results, dec, waiting_on, requests) -> dict:
    """The decision.schema.json object. reasons[] are the blocking drivers; requests_to_vendor[] the open asks."""
    blocking = [r for r in results if r["blocking"] and r["status"] != "PASS"]
    reasons = [{"rule_id": r["rule_id"], "label": r["label"], "status": r["status"], "severity": r["severity"],
                "evidence": r["evidence"], "source": r["source"], "provenance": r["provenance"]} for r in blocking]
    return {"submission_id": submission_id, "vendor_id": vendor_id, "status": dec["status"], "waiting_on": waiting_on,
            "risk_level": dec["risk_level"], "policy_version": dec["policy_version"], "run_no": run_no,
            "decided_at": dec.get("decided_at"), "reasons": reasons, "requests_to_vendor": requests,
            "next_action": dec["next_action"]}

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
    risk = RANK_TO_RISK[top]
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

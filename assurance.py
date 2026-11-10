"""Part B: assurance levels.

Every check result carries an assurance level:
  FORMAT        syntax and checksums, or two fields of the same form agreeing.
  CONSISTENCY   two independent sources agree (form vs document, or document vs document).
  NOT_VERIFIED  needs an outside registry we do not call (GST status, PAN-name, bank ownership, sanctions).

decision.json lists, per identifier, the highest level reached (counting only checks that passed) and
what was not verified. APPROVED requires CONSISTENCY on the identities in the policy approval_rule
(domestic: PAN, legal name, bank account; international: registration number, legal name, bank account).
This is the two-source rule.
"""
import config
import validation as va
from config import level_for   # the per-check classifier lives in config (no import cycle)

# Which identity each rule speaks to (for the per-identifier roll-up).
IDENTIFIER_RULES = {
    "pan": {"FMT_PAN", "FMT_PAN_ENTITY", "XD_PAN_DOC", "XD_PAN_GST", "XF_GSTIN_PAN"},
    "gstin": {"FMT_GSTIN", "FMT_GSTIN_STATE", "FMT_GSTIN_CHECK", "XD_GSTIN_DOC", "XF_GSTIN_UNEXPECTED", "XB_GSTIN_STATE"},
    "legal_name": {"XN_LEGAL_PAN", "XN_LEGAL_GST", "XN_LEGAL_REG", "XN_LEGAL_TAX", "XN_LEGAL_TRC", "XN_TRADE_GST"},
    "registration_number": {"FMT_CIN", "XR_REGNO"},
    "bank_account": {"FMT_IFSC", "FMT_ACCT_IN", "FMT_ACCT_INTL", "FMT_SWIFT", "XB_IFSC", "XB_ACCT",
                     "XB_HOLDER_LEGAL", "XB_HOLDER_DOC", "XB_DOCHOLDER_LEGAL", "XB_IFSC_BANK"},
    "tax_id": {"VAL_TAXID_COUNTRY", "XD_TAXID_DOC"},
    "trc": {"VAL_TRC_NUMBER"},
}
NOT_VERIFIED_NOTE = {
    "pan": "PAN is not matched to its registered name in the income tax registry",
    "gstin": "live GST registration status is not checked with the GST portal",
    "legal_name": "legal existence is not confirmed with the company registry",
    "registration_number": "registration is not confirmed with the company registry",
    "bank_account": "bank account ownership is not verified with the bank",
    "tax_id": "the tax identifier is not checked with the foreign tax authority",
    "trc": "tax residency is not confirmed with the issuing authority",
}
RANK = {None: 0, "NONE": 0, "NOT_VERIFIED": 0, "FORMAT": 1, "CONSISTENCY": 2}
RANK_NAME = {0: "NONE", 1: "FORMAT", 2: "CONSISTENCY"}
ORDER = ["pan", "gstin", "legal_name", "registration_number", "bank_account", "tax_id", "trc"]


def identifier_of(rule_id: str):
    for ident, rules in IDENTIFIER_RULES.items():
        if rule_id in rules:
            return ident
    return None


def _required(form: dict) -> list:
    region = "international" if form.get("region") == "International" else "domestic"
    return config.APPROVAL_RULE[f"{region}_requires_consistency_on"]


def reached(results: list) -> dict:
    """Highest assurance level actually reached per identifier, counting only checks that passed."""
    best = {}
    for r in results:
        if r.get("status") != "PASS":
            continue
        ident = identifier_of(r["rule_id"])
        if not ident:
            continue
        lvl = r.get("assurance") or level_for(r["rule_id"], r.get("flag_code"), r.get("status"))
        best[ident] = max(best.get(ident, 0), RANK.get(lvl, 0))
    return best


def summarize(results: list, form: dict) -> dict:
    """Per-identifier {level, not_verified} for decision.json. Only identifiers in play are listed."""
    best = reached(results)
    seen = {identifier_of(r["rule_id"]) for r in results} - {None}
    out = {}
    for ident in ORDER:
        if ident not in seen:
            continue
        out[ident] = {"level": RANK_NAME[best.get(ident, 0)], "not_verified": [NOT_VERIFIED_NOTE[ident]]}
    return out


def two_source_gap(results: list, form: dict) -> list:
    """Required identities (policy approval_rule) that have not reached CONSISTENCY. Empty when the gate passes."""
    best = reached(results)
    return [ident for ident in _required(form) if best.get(ident, 0) < RANK["CONSISTENCY"]]


def enforce_two_source(results: list, form: dict) -> list:
    """If a submission would otherwise pass but a required identity never reached CONSISTENCY, append a
    blocking result so it is held for the vendor rather than approved. A safety net on top of the per-check rules."""
    if form is None:
        return results
    blocking = [r for r in results if r.get("blocking") and r.get("status") != "PASS"]
    if blocking:
        return results   # already not approvable; no need to add the gate
    gap = two_source_gap(results, form)
    if not gap:
        return results
    pretty = ", ".join(g.replace("_", " ") for g in gap)
    return results + [va.R("decision", "XA_TWO_SOURCE", "Two-source rule: key identities confirmed by a document",
                           "FAIL", "MEDIUM", "decision",
                           f"not confirmed against a document: {pretty}",
                           "Approval needs each key identity (PAN or registration number, legal name and bank account) "
                           "confirmed by at least two independent sources.",
                           f"Please provide a document that confirms your {pretty} so we can complete the checks.",
                           flag="UNCERTAIN_EXTRACTION", provenance="system-derived")]

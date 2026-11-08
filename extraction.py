"""PDF text extraction, document classification and candidate field extraction (offline, deterministic)."""
import os, re
import pdfplumber
from config import DOC_KEYWORDS, CLASSIFY_MIN_SCORE, MIN_TEXT_CHARS, PAN_RE, GSTIN_RE, IFSC_RE

def read_document(path: str) -> dict:
    """Return {state, pages:[text], page_count, chars}. state: TEXT | UNREADABLE_SCAN | UNSUPPORTED_IMAGE | ERROR."""
    ext = os.path.splitext(path)[1].lower()
    if ext in (".png", ".jpg", ".jpeg"):
        return {"state": "UNSUPPORTED_IMAGE", "pages": [], "page_count": 1, "chars": 0,
                "note": "Image upload: no OCR configured, manual review required."}
    try:
        with pdfplumber.open(path) as pdf:
            pages = [(p.extract_text() or "") for p in pdf.pages]
    except Exception as e:  # corrupt / encrypted
        return {"state": "ERROR", "pages": [], "page_count": 0, "chars": 0, "note": f"Could not open PDF: {e}"}
    chars = sum(len(t.strip()) for t in pages)
    if chars < MIN_TEXT_CHARS:
        return {"state": "UNREADABLE_SCAN", "pages": pages, "page_count": len(pages), "chars": chars,
                "note": "No usable text layer (scanned/image PDF). OCR not configured; request a searchable copy or review manually."}
    return {"state": "TEXT", "pages": pages, "page_count": len(pages), "chars": chars, "note": ""}

def classify(pages: list, expected: str) -> dict:
    """Weighted keyword scoring. MATCH / MISMATCH / UNCERTAIN, always with the evidence keywords."""
    text = "\n".join(pages).lower()
    scores, hits = {}, {}
    for dtype, kws in DOC_KEYWORDS.items():
        found = {k: w for k, w in kws.items() if k in text}
        scores[dtype], hits[dtype] = sum(found.values()), list(found)
    best = max(scores, key=scores.get)
    best_score = scores[best]
    exp_score = scores.get(expected, 0)
    if exp_score >= CLASSIFY_MIN_SCORE and exp_score >= best_score:
        state, detected = "MATCH", expected
    elif best_score >= CLASSIFY_MIN_SCORE:
        state, detected = "MISMATCH", best
    else:
        state, detected = "UNCERTAIN", "UNKNOWN"
    return {"state": state, "detected": detected, "score": scores.get(detected, 0),
            "evidence": ", ".join(hits.get(detected, [])) or "no recognised keywords", "all_scores": scores}

# ---- field extraction ------------------------------------------------------------
NAME_LABELS = r"(?:legal\s+name(?:\s+of\s+business)?|company\s+name|llp\s+name|firm\s+name|name\s+of\s+(?:the\s+)?(?:company|llp|firm)|registered\s+name|name\s+of\s+entity|entity\s+name|name)"
def _find(pages, pattern, flags=re.I | re.M, group=1):
    for i, t in enumerate(pages, 1):
        m = re.search(pattern, t, flags)
        if m:
            line = t[max(0, t.rfind("\n", 0, m.start()) + 1): (t.find("\n", m.end()) if t.find("\n", m.end()) != -1 else len(t))]
            return m.group(group).strip(), i, line.strip()
    return None

def _add(out, field, hit, method="regex", conf=0.9):
    if hit:
        v, page, ev = hit
        out.append({"field": field, "value": v, "page": page, "evidence": ev, "method": method, "confidence": conf})

def extract_fields(doc_type: str, pages: list) -> list:
    out = []
    name = _find(pages, rf"^\s*{NAME_LABELS}\s*[:\-]\s*(.+?)\s*$")
    if doc_type == "PAN_PROOF":
        _add(out, "pan", _find(pages, r"\b([A-Z]{5}\d{4}[A-Z])\b", flags=0))
        _add(out, "name", name)
    elif doc_type == "GST_CERT":
        _add(out, "gstin", _find(pages, r"GSTIN[^A-Z0-9\n]*\s*[:\-]?\s*(\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z])", flags=re.I))
        _add(out, "pan", _find(pages, r"\bPAN\s*[:\-]\s*([A-Z]{5}\d{4}[A-Z])\b"))
        _add(out, "name", name)
    elif doc_type == "COMPANY_REG":
        _add(out, "name", name)
        _add(out, "reg_number", _find(pages, r"(?:CIN|LLPIN|Registration\s+(?:Number|No\.?))[^:\n]*[:\-]\s*([A-Z0-9\-/ ]{5,30})\s*$"))
    elif doc_type == "BANK_PROOF":
        _add(out, "account_holder", _find(pages, r"^\s*(?:account\s+holder(?:\s+name)?|beneficiary(?:\s+name)?)\s*[:\-]\s*(.+?)\s*$"))
        _add(out, "account_number", _find(pages, r"account\s+(?:number|no\.?)\s*[:\-]\s*([A-Z0-9 \-]{6,40}?)\s*$"))
        _add(out, "ifsc", _find(pages, r"IFSC(?:\s+code)?\s*[:\-]\s*([A-Z]{4}0[A-Z0-9]{6})", flags=re.I))
        _add(out, "bank_name", _find(pages, r"^\s*bank(?:\s+name)?\s*[:\-]\s*(.+?)\s*$"))
        _add(out, "swift", _find(pages, r"(?:swift|bic)(?:\s*/\s*bic)?(?:\s+code)?\s*[:\-]\s*([A-Z0-9]{8,11})", flags=re.I))
    elif doc_type == "TAX_CERT":
        _add(out, "tax_id", _find(pages, r"\b(?:tax\s*id|ein|vat\s*(?:id|number|no\.?)?|trn|ust-?idnr\.?|tax\s+registration\s+number)\b[^:\n]*[:\-]\s*([A-Z0-9\-./]{6,24})"))
        _add(out, "name", name)
    elif doc_type == "TRC":
        _add(out, "name", name)
        _add(out, "country", _find(pages, r"^\s*(?:country(?:\s+of\s+residence)?)\s*[:\-]\s*(.+?)\s*$"))
    # normalise identifier values
    for f in out:
        if f["field"] in ("pan", "gstin", "ifsc", "swift", "tax_id"):
            f["value"] = f["value"].upper().replace(" ", "")
        if f["field"] == "account_number":
            f["value"] = re.sub(r"[ \-]", "", f["value"]).upper()
    return out

"""Document reading (PDF text layer, or OCR for photos and scans), classification and field extraction.
Offline and deterministic for a given Tesseract build; every OCR value keeps its line confidence."""
import os, re
import pdfplumber
from config import (DOC_KEYWORDS, CLASSIFY_MIN_SCORE, MIN_TEXT_CHARS, PAN_RE, GSTIN_RE, IFSC_RE,
                    OCR_DPI, OCR_MIN_DOC_CONF)

IMAGE_EXT = (".png", ".jpg", ".jpeg")

# ---- OCR ----------------------------------------------------------------------------
def ocr_available() -> bool:
    try:
        import pytesseract
        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False

def _prepare(img):
    """Phone photos: honour EXIF rotation, greyscale, enlarge small captures, stretch contrast."""
    from PIL import ImageOps, Image
    img = ImageOps.exif_transpose(img).convert("L")
    if max(img.size) < 1600:
        k = 1600 / max(img.size)
        img = img.resize((int(img.width * k), int(img.height * k)), Image.LANCZOS)
    return ImageOps.autocontrast(img, cutoff=1)

# OCR commonly swaps these; identifiers have fixed letter/digit positions, so the position decides.
_TO_LETTER = {"0": "O", "1": "I", "2": "Z", "5": "S", "6": "G", "8": "B"}
_TO_DIGIT = {"O": "0", "Q": "0", "D": "0", "I": "1", "L": "1", "Z": "2", "S": "5", "G": "6", "B": "8"}
# mask: A = letter, 9 = digit, X = any, other characters are literal
_ID_SPECS = [("GSTIN", r"\bGST\s*IN\b", "99AAAAA9999AXZX", GSTIN_RE),
             ("IFSC", r"\bIFSC\b", "AAAA0XXXXXX", IFSC_RE),
             ("PAN", r"\bPAN\b|permanent\s+account", "AAAAA9999A", PAN_RE)]

def _coerce(token, mask):
    out = []
    for ch, m in zip(token, mask):
        if m == "A":
            ch = _TO_LETTER.get(ch, ch)
        elif m == "9":
            ch = _TO_DIGIT.get(ch, ch)
        elif m not in "X":
            ch = m if (ch == m or _TO_DIGIT.get(ch) == m or _TO_LETTER.get(ch) == m) else ch
        out.append(ch)
    return "".join(out)

def repair_identifiers(line: str):
    """Fix OCR letter/digit swaps in PAN, GSTIN and IFSC values by position. Only a repair that then passes the
    strict format (and, for GSTIN, the checksum) is kept. Returns (line, note or None)."""
    from validation import gstin_checksum
    notes = []
    for name, label, mask, rx in _ID_SPECS:
        if not re.search(label, line, re.I):
            continue
        for tok in re.findall(r"\b[A-Za-z0-9]{%d}\b" % len(mask), line):
            up = tok.upper()
            if rx.match(up) and (name != "GSTIN" or gstin_checksum(up[:14]) == up[14]):
                if up != tok:
                    line = line.replace(tok, up)
                continue
            fixed = _coerce(up, mask)
            ok = rx.match(fixed) and (name != "GSTIN" or gstin_checksum(fixed[:14]) == fixed[14])
            if ok and fixed != up:
                line = line.replace(tok, fixed)
                notes.append(f"OCR read {name} as '{tok}', repaired by character position to '{fixed}'")
    return line, ("; ".join(notes) or None)

_LABEL_PUNCT = re.compile(r"^(\s*(?:account\s+(?:number|no)|account\s+holder(?:\s+name)?|beneficiary(?:\s+name)?|ifsc(?:\s+code)?|"
                          r"pan|gstin|legal\s+name|trade\s+name|company\s+name|llp\s+name|name|bank(?:\s+name)?|branch|"
                          r"registration\s+number|tax\s+id))\s*[.;,]\s+", re.I)

def _ocr_image(img) -> dict:
    """One page image -> {text, lines:{line: conf 0-1}, notes:{line: note}, words, mean}."""
    import pytesseract
    d = pytesseract.image_to_data(_prepare(img), output_type=pytesseract.Output.DICT)
    grouped, order = {}, []
    for i, word in enumerate(d["text"]):
        conf = float(d["conf"][i])
        if conf < 0 or not word.strip():
            continue
        key = (d["block_num"][i], d["par_num"][i], d["line_num"][i])
        if key not in grouped:
            grouped[key] = []; order.append(key)
        grouped[key].append((word, conf))
    lines, notes, text_lines, confs = {}, {}, [], []
    for key in order:
        words = grouped[key]
        raw = " ".join(w for w, _ in words)
        conf = sum(c for _, c in words) / len(words) / 100
        confs += [c / 100 for _, c in words]
        line = _LABEL_PUNCT.sub(lambda m: m.group(1) + ": ", raw)
        line, note = repair_identifiers(line)
        text_lines.append(line)
        lines[line.strip()] = min(conf, lines.get(line.strip(), 1.0))
        if note:
            notes[line.strip()] = note
    return {"text": "\n".join(text_lines), "lines": lines, "notes": notes,
            "mean": (sum(confs) / len(confs)) if confs else 0.0}

def _ocr_pages(images, unreadable_state: str, kind: str) -> dict:
    res = [_ocr_image(im) for im in images]
    pages = [r["text"] for r in res]
    chars = sum(len(re.sub(r"\W", "", p)) for p in pages)
    words = sum(len(r["lines"]) for r in res)
    mean = (sum(r["mean"] * max(len(r["lines"]), 1) for r in res) / max(words, 1)) if res else 0.0
    meta = {"line_conf": [r["lines"] for r in res], "line_notes": [r["notes"] for r in res], "ocr_mean": round(mean, 2)}
    if chars < MIN_TEXT_CHARS or mean < OCR_MIN_DOC_CONF:
        why = (f"OCR found no readable text in this {kind.lower()}" if words == 0 else
               f"{kind} read by OCR at mean confidence {mean:.2f}, below the {OCR_MIN_DOC_CONF:.2f} needed")
        return {"state": unreadable_state, "pages": pages, "page_count": len(pages), "chars": chars, **meta,
                "note": why + ". Please upload a sharper photo or a text-based PDF."}
    return {"state": "OCR", "pages": pages, "page_count": len(pages), "chars": chars, **meta,
            "note": f"{kind} read by OCR (Tesseract), mean word confidence {mean:.2f}."}

def _read_docx(path: str) -> dict:
    """Word .docx via python-docx: paragraphs plus table cells, treated like a text-layer document."""
    try:
        import docx
    except Exception:
        return {"state": "UNREADABLE_DOC", "pages": [], "page_count": 1, "chars": 0,
                "note": "python-docx is not installed here, so this Word file cannot be read. Held for manual review."}
    try:
        d = docx.Document(path)
        parts = [p.text for p in d.paragraphs]
        for tbl in d.tables:
            for row in tbl.rows:
                parts.append(" ".join(c.text for c in row.cells))
        text = "\n".join(parts)
        chars = len(re.sub(r"\s", "", text))
        if chars >= MIN_TEXT_CHARS:
            return {"state": "TEXT", "pages": [text], "page_count": 1, "chars": chars, "note": "Read from Word document (.docx)."}
        return {"state": "UNREADABLE_SCAN", "pages": [text], "page_count": 1, "chars": chars,
                "note": "This Word file held little readable text; held for manual review."}
    except Exception as e:
        return {"state": "ERROR", "pages": [], "page_count": 0, "chars": 0, "note": f"Could not open Word file: {e}"}

def read_document(path: str) -> dict:
    """Return {state, pages:[text], page_count, chars, note[, line_conf, line_notes, ocr_mean]}.
    state: TEXT | OCR | UNREADABLE_SCAN | UNREADABLE_IMAGE | UNSUPPORTED_IMAGE | UNREADABLE_DOC | ERROR."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".docx":
        return _read_docx(path)
    if ext == ".doc":
        # Legacy binary .doc is not read: the brief requires it be held for a reviewer, never silently passed.
        return {"state": "UNREADABLE_DOC", "pages": [], "page_count": 1, "chars": 0,
                "note": "Legacy .doc files are not read automatically; held for manual review. Please upload a PDF or .docx."}
    if ext in IMAGE_EXT:
        if not ocr_available():
            return {"state": "UNSUPPORTED_IMAGE", "pages": [], "page_count": 1, "chars": 0,
                    "note": "Image upload, but no OCR engine is installed here. Manual review required."}
        from PIL import Image
        try:
            with Image.open(path) as im:
                im.load()
                return _ocr_pages([im.copy()], "UNREADABLE_IMAGE", "Photo")
        except Exception as e:
            return {"state": "ERROR", "pages": [], "page_count": 0, "chars": 0, "note": f"Could not open image: {e}"}
    try:
        with pdfplumber.open(path) as pdf:
            pages = [(p.extract_text() or "") for p in pdf.pages]
            chars = sum(len(t.strip()) for t in pages)
            if chars >= MIN_TEXT_CHARS:
                return {"state": "TEXT", "pages": pages, "page_count": len(pages), "chars": chars, "note": ""}
            if not ocr_available():
                return {"state": "UNREADABLE_SCAN", "pages": pages, "page_count": len(pages), "chars": chars,
                        "note": "No usable text layer (scanned/image PDF) and no OCR engine installed here. Request a searchable copy or review manually."}
            images = [p.to_image(resolution=OCR_DPI).original for p in pdf.pages]
    except Exception as e:  # corrupt / encrypted
        return {"state": "ERROR", "pages": [], "page_count": 0, "chars": 0, "note": f"Could not open PDF: {e}"}
    return _ocr_pages(images, "UNREADABLE_SCAN", "Scanned PDF")

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

def extract_fields(doc_type: str, pages: list, line_conf: list = None, line_notes: list = None) -> list:
    """line_conf / line_notes come from OCR (one dict per page, keyed by line). When present, every field is marked
    method='ocr' with the confidence of the line it was read from, and any identifier repair is kept in the evidence."""
    out = _extract(doc_type, pages)
    if line_conf:
        for f in out:
            i = (f["page"] or 1) - 1
            conf = (line_conf[i] if i < len(line_conf) else {}).get(f["evidence"])
            note = ((line_notes or [])[i] if i < len(line_notes or []) else {}).get(f["evidence"])
            f["method"], f["confidence"] = "ocr", round(conf if conf is not None else 0.0, 2)
            if note:
                f["evidence"] = f"{f['evidence']} [{note}]"
    out = _llm_pass(doc_type, pages) + out    # Part K: optional, prepended so deterministic reads win
    return out

def _gate_norm(s):
    return re.sub(r"[^A-Za-z0-9]", "", str(s or "")).upper()

def _llm_pass(doc_type, pages):
    """Optional LLM proposals, accepted only when the value appears in the document's own text/OCR after
    normalisation (otherwise ignored and logged). Returns [] when the reader is off. The rules still decide."""
    import llm_reader
    proposals = llm_reader.propose(doc_type, pages)
    if not proposals:
        return []
    doc_norm = _gate_norm("\n".join(pages or []))
    rows = []
    for field, value in proposals.items():
        vnorm = _gate_norm(value)
        if vnorm and vnorm in doc_norm:
            val = str(value).upper().replace(" ", "") if field in ("pan", "gstin", "ifsc", "swift", "tax_id") else value
            if field == "account_number":
                val = re.sub(r"[ \-]", "", str(value)).upper()
            rows.append({"field": field, "value": val, "page": 1,
                         "evidence": "LLM proposal confirmed in document text", "method": "llm", "confidence": 0.9})
        else:
            llm_reader._log_ignored(doc_type, {"field": field, "value": str(value)[:40], "reason": "not in document text"})
    return rows

def _extract(doc_type: str, pages: list) -> list:
    out = []
    name = _find(pages, rf"^\s*{NAME_LABELS}\s*[:\-]\s*(.+?)\s*$")
    if doc_type == "PAN_PROOF":
        _add(out, "pan", _find(pages, r"\b([A-Z]{5}\d{4}[A-Z])\b", flags=0))
        _add(out, "name", name)
    elif doc_type == "GST_CERT":
        _add(out, "gstin", _find(pages, r"GSTIN[^A-Z0-9\n]*\s*[:\-]?\s*(\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z])", flags=re.I))
        _add(out, "pan", _find(pages, r"\bPAN\s*[:\-]\s*([A-Z]{5}\d{4}[A-Z])\b"))
        _add(out, "name", name)
        _add(out, "trade_name", _find(pages, r"^\s*trade\s+name\s*[:\-]\s*(.+?)\s*$"))
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

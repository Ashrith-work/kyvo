"""Deterministic rules: conditional fields, country registry, syntax, cross-document reconciliation, duplicates.
Each result: rule_id, label, status (PASS/FAIL/WARN/NOT_CHECKED), severity, source, evidence, explanation, action."""
import re, difflib
from config import *

CH36 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
GST_STATES = set(range(1, 39)) | {97, 99}

def gstin_checksum(first14: str) -> str:
    total = 0
    for i, c in enumerate(first14):
        p = CH36.index(c) * (1 if i % 2 == 0 else 2)
        total += p // 36 + p % 36
    return CH36[(36 - total % 36) % 36]

def R(stage, rule_id, label, status, severity="MEDIUM", source="", evidence="", explanation="", action="",
      flag=None, blocking=True, provenance="vendor-entered", assurance=None):
    return dict(stage=stage, rule_id=rule_id, label=label, status=status, severity=severity, source=source,
                evidence=evidence, explanation=explanation, action=action, flag_code=flag,
                blocking=int(blocking and status != "PASS"), provenance=provenance,
                assurance=assurance or level_for(rule_id, flag, status))

# ---- names -----------------------------------------------------------------------
def normalize_name(s: str) -> str:
    s = (s or "").lower().replace("&", " and ")
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    toks = [t for t in s.split() if t not in LEGAL_SUFFIXES]
    return " ".join(sorted(toks))

def compare_names(a: str, b: str):
    na, nb = normalize_name(a), normalize_name(b)
    if not na or not nb:
        return "MISSING", 0.0
    if na == nb:
        return "EXACT", 1.0
    score = difflib.SequenceMatcher(None, na, nb).ratio()
    return ("SIMILAR" if score >= NAME_SIMILAR else "DIFFERENT"), round(score, 2)

def norm_id(v):
    return re.sub(r"[\s]", "", (v or "")).upper()

def identifiers(form: dict) -> list:
    ids = [("PAN", norm_id(form.get("pan"))), ("GSTIN", norm_id(form.get("gstin"))),
           ("REG_NO", norm_id(form.get("reg_number")))]
    if form.get("tax_id"):
        ids.append(("TAX_ID", f"{form.get('country')}:{norm_id(form.get('tax_id'))}"))
    return [(t, v) for t, v in ids if v]

def bank_identifiers(form: dict) -> list:
    """The bank account identity (IFSC-or-SWIFT + account number). Kept SEPARATE from identifiers() so a shared bank
    account is handled by its own rule (XR_BANK_SHARED) and never shows up as a generic duplicate identifier."""
    code = norm_id(form.get("ifsc") or form.get("swift"))
    acct = norm_id(form.get("account_number"))
    if acct:
        return [("BANK_ACCT", f"{code}:{acct}")]
    return []

FIELD_LABELS = {"legal_name": "legal name", "contact_person": "contact person", "email": "organisation email",
                "phone": "phone number", "address": "address", "entity_type": "entity type", "pan": "PAN",
                "gstin": "GSTIN", "bank_holder": "bank account holder name", "account_number": "bank account number",
                "bank_name": "bank name", "ifsc": "IFSC code", "service_category": "service category",
                "service_description": "products/services description", "country": "country",
                "reg_number": "business registration number", "tax_id": "tax identification number",
                "bank_country": "bank country", "state": "state", "city": "city", "pincode": "PIN code",
                "proprietor_name": "proprietor name", "trc_number": "TRC number",
                "contact_email": "contact email", "alt_phone": "alternate phone", "contact_mobile": "contact mobile"}

def usable_fields(doc):
    """Fields of a document that may be used for reconciliation (not from wrong/unreadable documents)."""
    if not doc:
        return None
    f = doc.get("fields") or {}
    if doc["classification_state"] in ("MATCH", "UNCERTAIN"):
        return f
    return {k: v for k, v in f.items() if v.get("provenance") == "reviewer_corrected"} or None

# ---- stage 1: validation (completeness, syntax, country rules, document type) -------
def validate_form(form: dict, docs: dict) -> list:
    out, v = [], lambda k: (form.get(k) or "").strip() if isinstance(form.get(k), str) else form.get(k)
    intl = form.get("region") == "International"
    with_gst = (not intl) and form.get("domestic_type") == "PAN with GST"

    # required fields (conditional on region / type)
    req = ["legal_name", "contact_person", "email", "phone", "address", "entity_type", "bank_holder",
           "account_number", "bank_name", "service_category", "service_description"]
    req += (["country", "reg_number", "tax_id", "bank_country", "trc_number"] if intl
            else ["pan", "ifsc", "state", "city", "pincode"] + (["gstin"] if with_gst else []))
    if form.get("entity_type") == "Proprietorship":
        req.append("proprietor_name")
    missing = [k for k in req if not v(k)]
    for k in missing:
        out.append(R("validation", f"REQ_{k.upper()}", f"Required field: {FIELD_LABELS[k]}", "FAIL", "MEDIUM",
                     "form", "empty", f"{FIELD_LABELS[k]} is required for this vendor type.",
                     f"Please provide your {FIELD_LABELS[k]}.", "MISSING_FIELD"))
    if not missing:
        out.append(R("validation", "REQ_FIELDS", "All applicable required fields present", "PASS", source="form",
                     evidence=f"{len(req)} fields checked ({'international' if intl else 'domestic, ' + str(form.get('domestic_type'))})"))
    if not form.get("terms_accepted"):
        out.append(R("validation", "REQ_TERMS", "Declaration / terms accepted", "FAIL", "MEDIUM", "form", "not ticked",
                     "Terms acceptance is mandatory.", "Please accept the vendor declaration and resubmit.", "TERMS_NOT_ACCEPTED"))
    else:
        out.append(R("validation", "REQ_TERMS", "Declaration / terms accepted", "PASS", source="form",
                     evidence=f"accepted at {form.get('terms_accepted_at', 'n/a')}"))

    # region / country consistency
    country = v("country")
    if intl and country == "India":
        out.append(R("validation", "VAL_REGION_COUNTRY", "Region matches country", "FAIL", "HIGH", "form",
                     "International + India", "Vendor classed International but country is India.",
                     "Please re-register as a Domestic vendor, or correct the country.", "COUNTRY_TAX_CONFLICT"))
    elif not intl and country and country != "India":
        out.append(R("validation", "VAL_REGION_COUNTRY", "Region matches country", "FAIL", "HIGH", "form",
                     f"Domestic + {country}", "Vendor classed Domestic but country is not India.",
                     "Please re-register as an International vendor, or correct the country.", "COUNTRY_TAX_CONFLICT"))

    # contact formats
    if v("email"):
        ok = bool(EMAIL_RE.match(v("email")))
        out.append(R("validation", "FMT_EMAIL", "Email format", "PASS" if ok else "FAIL", "LOW", "form", v("email"),
                     "Email must look like name@domain.tld.", "Please provide a valid organisation email.", "INVALID_FORMAT"))
    if v("phone"):
        digits = re.sub(r"\D", "", v("phone"))
        ok = 7 <= len(digits) <= 15
        out.append(R("validation", "FMT_PHONE", "Phone format", "PASS" if ok else "FAIL", "LOW", "form", v("phone"),
                     "Phone should contain 7-15 digits.", "Please provide a valid phone number.", "INVALID_FORMAT"))

    if not intl:
        out += _india_rules(form, v, with_gst)
    else:
        out += _international_rules(form, v)

    # documents: required, type, readability
    for dtype in required_docs(form):
        d = docs.get(dtype)
        label = DOC_LABELS[dtype]
        if not d:
            out.append(R("validation", f"DOC_{dtype}", f"Document present: {label}", "FAIL", "MEDIUM", "documents",
                         "not uploaded", f"{label} is required for this vendor type.", f"Please upload your {label}.", "MISSING_DOCUMENT"))
            continue
        cs = d["classification_state"]
        ev = f"file '{d['filename']}'; detected {DOC_LABELS.get(d['detected_type'], d['detected_type'])}; keywords: {d.get('classification_evidence')}"
        if d["extraction_state"] in ("UNREADABLE_SCAN", "UNREADABLE_IMAGE", "UNSUPPORTED_IMAGE", "UNREADABLE_DOC", "ERROR", "REJECTED_UPLOAD"):
            out.append(R("validation", f"DOC_{dtype}", f"Document readable: {label}", "WARN", "MEDIUM", f"doc:{d['filename']}",
                         f"state={d['extraction_state']}; {d.get('note') or ''}",
                         "No reliable text could be read from this file, so it cannot be verified automatically.",
                         f"The file '{d['filename']}' could not be read reliably. Please upload a sharper photo or a clear, searchable (text-based) PDF of your {label}.",
                         "UNREADABLE_DOCUMENT", provenance="system-derived"))
            continue
        if d["extraction_state"] == "OCR":
            out.append(R("validation", f"OCR_{dtype}", f"Read by OCR: {label}", "PASS", "LOW", f"doc:{d['filename']}",
                         d.get("note") or "read by OCR", "Photo or scan read by OCR. Each value it supplies carries its own confidence.",
                         "", None, blocking=False, provenance="system-derived"))
        if cs == "MATCH":
            out.append(R("validation", f"DOC_{dtype}", f"Document type: {label}", "PASS", source=f"doc:{d['filename']}", evidence=ev, provenance="extracted"))
        elif cs == "MISMATCH":
            out.append(R("validation", f"DOC_{dtype}", f"Document type: {label}", "FAIL", "HIGH", f"doc:{d['filename']}", ev,
                         f"Expected {label} but the content looks like {DOC_LABELS.get(d['detected_type'], d['detected_type'])}.",
                         f"The file uploaded as your {label} ('{d['filename']}') appears to be {DOC_LABELS.get(d['detected_type'], d['detected_type']).lower()}. Please upload the correct {label}.",
                         "WRONG_DOCUMENT", provenance="extracted"))
        else:
            out.append(R("validation", f"DOC_{dtype}", f"Document type: {label}", "WARN", "MEDIUM", f"doc:{d['filename']}", ev,
                         "Could not confirm the document category from its text.",
                         f"We could not confirm that '{d['filename']}' is a {label}. Please re-upload a clear copy.",
                         "UNCERTAIN_DOCUMENT", provenance="extracted"))
    for dtype in docs:
        if dtype not in required_docs(form):
            out.append(R("validation", f"DOC_EXTRA_{dtype}", f"Extra document ignored: {DOC_LABELS[dtype]}", "PASS",
                         source="documents", evidence="not required for this vendor type"))
    return out

def _india_rules(form, v, with_gst):
    out, pan, gstin = [], norm_id(v("pan")), norm_id(v("gstin"))
    if pan:
        ok = bool(PAN_RE.match(pan))
        out.append(R("validation", "FMT_PAN", "PAN format (AAAAA9999A)", "PASS" if ok else "FAIL", "MEDIUM", "form", pan,
                     "PAN must be 5 letters, 4 digits, 1 letter. Syntax only - registration is not verified.",
                     "The PAN entered is not in a valid format. Please correct it.", "INVALID_FORMAT"))
        et = form.get("entity_type")
        if ok and et in PAN_4TH:
            good = pan[3] == PAN_4TH[et]
            out.append(R("validation", "FMT_PAN_ENTITY", "PAN 4th character matches entity type",
                         "PASS" if good else "WARN", "MEDIUM", "form", f"PAN 4th char '{pan[3]}', entity '{et}'",
                         f"4th PAN character encodes holder type; '{et}' normally uses '{PAN_4TH[et]}'.",
                         f"The PAN ({pan[3]}) does not look consistent with the entity type '{et}'. Please confirm the entity type and PAN.",
                         "ENTITY_PAN_INCONSISTENT"))
    if with_gst and gstin:
        ok = bool(GSTIN_RE.match(gstin))
        out.append(R("validation", "FMT_GSTIN", "GSTIN format (15 characters)", "PASS" if ok else "FAIL", "MEDIUM", "form", gstin,
                     "GSTIN: 2-digit state, PAN, entity no., 'Z', checksum. Syntax only.",
                     "The GSTIN entered is not in a valid format. Please correct it.", "INVALID_FORMAT"))
        if ok:
            out.append(R("validation", "FMT_GSTIN_STATE", "GSTIN state code valid", "PASS" if int(gstin[:2]) in GST_STATES else "FAIL",
                         "MEDIUM", "form", f"state code {gstin[:2]}", "State code must be a valid GST state code.",
                         "The state code in the GSTIN is invalid. Please correct the GSTIN.", "INVALID_FORMAT"))
            if pan and PAN_RE.match(pan):
                al = gstin[2:12] == pan
                out.append(R("validation", "XF_GSTIN_PAN", "GSTIN embeds the entered PAN", "PASS" if al else "FAIL", "HIGH", "form",
                             f"GSTIN PAN segment {gstin[2:12]} vs PAN {pan}", "Characters 3-12 of a GSTIN are the holder's PAN.",
                             f"The PAN inside your GSTIN ({gstin[2:12]}) differs from the PAN entered ({pan}). Please confirm which is correct.",
                             "ID_INCONSISTENT"))
            cs = gstin_checksum(gstin[:14]) == gstin[14]
            out.append(R("validation", "FMT_GSTIN_CHECK", "GSTIN check character", "PASS" if cs else "WARN", "MEDIUM", "form", gstin,
                         "Last character is a computed checksum.", "The GSTIN check character is invalid - likely a typo. Please re-check the GSTIN.",
                         "INVALID_FORMAT"))
    if not with_gst and gstin:
        out.append(R("validation", "XF_GSTIN_UNEXPECTED", "GSTIN only for 'PAN with GST' vendors", "WARN", "MEDIUM", "form", gstin,
                     "Vendor declared 'PAN without GST' but supplied a GSTIN.",
                     "You selected 'PAN without GST' but entered a GSTIN. Please confirm your GST status.", "ID_INCONSISTENT"))
    ifsc = norm_id(v("ifsc"))
    if ifsc:
        ok = bool(IFSC_RE.match(ifsc))
        out.append(R("validation", "FMT_IFSC", "IFSC format", "PASS" if ok else "FAIL", "MEDIUM", "form", ifsc,
                     "IFSC: 4 letters, '0', 6 alphanumerics.", "The IFSC code is not valid. Please correct it.", "INVALID_FORMAT"))
    acct = norm_id(v("account_number"))
    if acct:
        ok = acct.isdigit() and 9 <= len(acct) <= 18
        out.append(R("validation", "FMT_ACCT_IN", "Indian bank account number (9-18 digits)", "PASS" if ok else "FAIL", "MEDIUM", "form", acct,
                     "Plausibility only - ownership is not verified.", "The bank account number looks invalid. Please re-check it.", "INVALID_FORMAT"))
    pin = re.sub(r"\s", "", v("pincode") or "")
    if pin:
        ok = bool(re.fullmatch(r"\d{6}", pin))
        out.append(R("validation", "FMT_PINCODE", "PIN code format (6 digits)", "PASS" if ok else "FAIL", "LOW", "form", pin,
                     "Indian PIN code is 6 digits.", "The PIN code should be 6 digits. Please correct it.", "INVALID_FORMAT"))
    state = (v("state") or "").strip()
    # Part B: GSTIN state code (first 2 digits) vs the billing state. Blocking WARN, never a rejection.
    if with_gst and gstin and GSTIN_RE.match(gstin) and state:
        expected = GST_STATE_CODE.get(gstin[:2])
        ok = bool(expected) and expected.lower() == state.lower()
        out.append(R("validation", "XB_GSTIN_STATE", "GSTIN state code matches billing state", "PASS" if ok else "WARN",
                     "MEDIUM", "form", f"GSTIN state code {gstin[:2]} = {expected or 'unknown'}; billing state '{state}'",
                     "The first two digits of a GSTIN encode the state of registration.",
                     f"Your GSTIN is registered in {expected or 'another state'} but your billing state is '{state}'. "
                     f"Please provide the GSTIN registered in {state}, or confirm you hold GST in more than one state.",
                     "GSTIN_STATE_MISMATCH"))
    # Part B: PIN code first digit (postal zone) vs state. Blocking WARN.
    if pin and re.fullmatch(r"\d{6}", pin) and state:
        ok = state.lower() in PIN_ZONE.get(pin[0], set())
        out.append(R("validation", "XB_PINCODE_STATE", "PIN code zone matches state", "PASS" if ok else "WARN",
                     "MEDIUM", "form", f"PIN {pin} (zone {pin[0]}); state '{state}'",
                     "The first digit of an Indian PIN code maps to a postal zone of states.",
                     f"The PIN code {pin} is not in the postal zone for '{state}'. Please check the PIN code and the state.",
                     "PINCODE_STATE_MISMATCH"))
    cin = norm_id(v("reg_number"))
    if cin and form.get("entity_type") in ("Private Limited Company", "Public Limited Company"):
        ok = bool(CIN_RE.match(cin))
        out.append(R("validation", "FMT_CIN", "CIN format", "PASS" if ok else "WARN", "LOW", "form", cin,
                     "CIN is 21 characters (L/U + 5 digits + state + year + type + 6 digits).",
                     "The company registration number (CIN) does not match the expected format. Please re-check it.", "INVALID_FORMAT"))
    return out

def _international_rules(form, v):
    out, country, tid = [], v("country"), norm_id(v("tax_id"))
    # NOTE: no PAN / GSTIN / IFSC rules are ever applied to an international vendor.
    if country and tid:
        matches = [s for s, rx in INDIA_SCHEMES.items() if re.match(rx, tid)]
        matches += [r["scheme"] for c, r in TAX_RULES.items() if c != country and re.match(r["regex"], tid)]
        rule = TAX_RULES.get(country)
        own = bool(rule and re.match(rule["regex"], tid))
        if matches and not own:
            out.append(R("validation", "VAL_TAXID_COUNTRY", f"Tax ID scheme matches country ({country})", "FAIL", "HIGH", "form",
                         f"'{tid}' looks like: {', '.join(matches)}", f"Identifier format belongs to a different country's scheme than the claimed country '{country}'.",
                         f"The tax ID '{tid}' is in a format used by another country's tax system, which doesn't match your stated country ({country}). Please confirm your country and provide the correct tax identifier.",
                         "COUNTRY_TAX_CONFLICT"))
        elif rule:
            out.append(R("validation", "VAL_TAXID_COUNTRY", f"Tax ID matches {rule['scheme']} format", "PASS" if own else "FAIL",
                         "MEDIUM", "form", tid, f"{country} rule: {rule['scheme']} e.g. {rule['example']}. Syntax only.",
                         f"The tax ID doesn't match the {rule['scheme']} format (e.g. {rule['example']}). Please correct it.", "INVALID_FORMAT"))
        else:
            out.append(R("validation", "VAL_TAXID_COUNTRY", f"Tax ID rule for {country}", "NOT_CHECKED", "MEDIUM", "country registry",
                         tid, f"No automated identifier rule is configured for {country}; it was NOT validated and is routed to manual review.",
                         f"We cannot automatically verify tax identifiers for {country}; our team will review '{tid}' manually. No action needed unless we contact you.",
                         "UNSUPPORTED_COUNTRY_RULE"))
    trc = (v("trc_number") or "").strip()
    if trc:
        out.append(R("validation", "VAL_TRC_NUMBER", "TRC number recorded", "PASS", "LOW", "form", trc,
                     "Tax Residency Certificate number captured. Syntax varies by country and is not verified against an authority.",
                     "", None, blocking=False))
    acct = norm_id(v("account_number"))
    if acct:
        ok = bool(re.fullmatch(r"[A-Z0-9]{6,34}", acct))
        out.append(R("validation", "FMT_ACCT_INTL", "Bank account / IBAN plausibility (6-34 alphanumerics)", "PASS" if ok else "FAIL",
                     "MEDIUM", "form", acct, "Plausibility only.", "The bank account number/IBAN looks invalid. Please re-check it.", "INVALID_FORMAT"))
    if v("swift"):
        ok = bool(BIC_RE.match(norm_id(v("swift"))))
        out.append(R("validation", "FMT_SWIFT", "SWIFT/BIC format", "PASS" if ok else "FAIL", "LOW", "form", norm_id(v("swift")),
                     "BIC: 8 or 11 characters.", "The SWIFT/BIC code is not valid. Please correct it.", "INVALID_FORMAT"))
    if v("bank_country") and country and v("bank_country") != country:
        out.append(R("validation", "XF_BANK_COUNTRY", "Bank country matches vendor country", "WARN", "LOW", "form",
                     f"vendor {country}; bank {v('bank_country')}", "Cross-border bank accounts are possible but need a reviewer's confirmation.",
                     f"Your bank is in {v('bank_country')} but your company is registered in {country}. Please confirm this is intended.",
                     "BANK_COUNTRY_MISMATCH"))
    return out

# ---- stage 2: cross-document reconciliation -----------------------------------------
def reconcile(form: dict, docs: dict) -> list:
    out, intl = [], form.get("region") == "International"
    legal = form.get("legal_name", "")
    # E1: a sole proprietor's PAN, GST legal name and bank account are in the PERSON's name, while the firm/trade
    # name sits on the GST trade-name line. When ownership is Proprietorship and the PAN 4th character is 'P',
    # compare the proprietor name (not the firm name) with the PAN card, GST legal name and bank holder.
    pan4 = norm_id(form.get("pan"))[3:4]
    prop = (not intl) and form.get("entity_type") == "Proprietorship" and pan4 == "P"
    identity_name = (form.get("proprietor_name") or "") if prop else legal
    who = "proprietor name" if prop else "legal name"

    def fld(dtype, field):
        f = usable_fields(docs.get(dtype))
        return (f or {}).get(field)

    def ocr_conf(f):
        """Confidence of an OCR-read value, or None when the value did not come from OCR."""
        if f.get("provenance") in ("extracted", "vendor_resubmitted") and str(f.get("method") or "").startswith("ocr"):
            return float(f.get("confidence") or 0)
        return None

    def low_ocr(f):
        c = ocr_conf(f)
        return c is not None and c < OCR_MIN_FIELD_CONF

    def prov(f):
        c = ocr_conf(f)
        return f["provenance"] if c is None else f"{f['provenance']} by OCR, confidence {c:.2f}"

    def accepted_note(f):
        return (f"; read by OCR at {ocr_conf(f):.2f}, below {OCR_MIN_FIELD_CONF:.2f}, accepted only because it equals the entered value exactly"
                if low_ocr(f) else "")

    def low_ocr_warn(rule_id, label, dtype, f, src, ev, what):
        out.append(R("reconciliation", rule_id, label, "WARN", "MEDIUM", src,
                     ev + f"; OCR confidence {ocr_conf(f):.2f} is below {OCR_MIN_FIELD_CONF:.2f}",
                     "The value was read by OCR with low confidence and does not match exactly, so it could be a misread. A reviewer must check the document.",
                     f"We couldn't read the {what} on your {DOC_LABELS[dtype]} clearly. Please upload a sharper photo or a text-based PDF.",
                     "LOW_CONFIDENCE_OCR", provenance=f["provenance"]))

    def skip(rule_id, label, dtype):
        d = docs.get(dtype)
        why = "document not provided" if not d else "document unusable (wrong type / unreadable) - see document check"
        out.append(R("reconciliation", rule_id, label, "NOT_CHECKED", "LOW", f"doc:{DOC_LABELS[dtype]}", why,
                     f"Skipped: {why}.", "", None, blocking=False))

    def names(rule_id, label, dtype, field, a_label="legal name on form", a=legal, flag="NAME_MISMATCH", docname=None):
        f = fld(dtype, field)
        if not f:
            if docs.get(dtype) and usable_fields(docs.get(dtype)) is not None:
                out.append(R("reconciliation", rule_id, label, "WARN", "LOW", f"doc:{docs[dtype]['filename']}", "field not found in document",
                             "Expected value could not be extracted from the document.",
                             f"We couldn't read the name on your {DOC_LABELS[dtype]}. Please upload a clearer copy.", "UNCERTAIN_EXTRACTION", provenance="extracted"))
            else:
                skip(rule_id, label, dtype)
            return
        state, score = compare_names(a, f["value"])
        src = f"{DOC_LABELS[dtype]} p{f['page'] or '-'}: \"{f['evidence']}\" ({prov(f)})"
        ev = f"{a_label}: '{a}' vs document: '{f['value']}' (similarity {score})"
        if low_ocr(f) and state != "EXACT":
            low_ocr_warn(rule_id, label, dtype, f, src, ev, "name")
            return
        ev += accepted_note(f)
        if state == "EXACT":
            out.append(R("reconciliation", rule_id, label, "PASS", source=src, evidence=ev, provenance=f["provenance"]))
        elif state == "SIMILAR":
            out.append(R("reconciliation", rule_id, label, "WARN", "LOW", src, ev,
                         "Names differ only slightly after normalisation - reviewer should confirm. Never auto-rejected.",
                         f"The name on your {DOC_LABELS[dtype]} ('{f['value']}') is slightly different from the name you entered ('{a}'). Please confirm the exact legal name.",
                         flag, provenance=f["provenance"]))
        else:
            out.append(R("reconciliation", rule_id, label, "FAIL", "HIGH", src, ev,
                         "Names are materially different after normalisation.",
                         f"The name on your {DOC_LABELS[dtype]} ('{f['value']}') does not match the legal name you entered ('{a}'). Please correct the name or upload the matching document.",
                         flag, provenance=f["provenance"]))

    def ids(rule_id, label, a_label, a, dtype, field, normalise=norm_id):
        f = fld(dtype, field)
        if not a:
            return
        if not f:
            if usable_fields(docs.get(dtype)) is not None and docs.get(dtype):
                out.append(R("reconciliation", rule_id, label, "WARN", "LOW", f"doc:{docs[dtype]['filename']}", "not found in document",
                             "Could not extract this value from the document.", f"We couldn't read the {field.replace('_', ' ')} on your {DOC_LABELS[dtype]}. Please upload a clearer copy.",
                             "UNCERTAIN_EXTRACTION", provenance="extracted"))
            else:
                skip(rule_id, label, dtype)
            return
        same = normalise(a) == normalise(f["value"])
        src = f"{DOC_LABELS[dtype]} p{f['page'] or '-'}: \"{f['evidence']}\" ({prov(f)})"
        if low_ocr(f) and not same:
            low_ocr_warn(rule_id, label, dtype, f, src, f"{a_label}: '{a}' vs document: '{f['value']}'", field.replace("_", " "))
            return
        out.append(R("reconciliation", rule_id, label, "PASS" if same else "FAIL", "HIGH", src,
                     f"{a_label}: '{a}' vs document: '{f['value']}'" + accepted_note(f), "Identifiers must match exactly after removing spaces.",
                     f"The {field.replace('_', ' ')} on your {DOC_LABELS[dtype]} ('{f['value']}') differs from the one entered ('{a}'). Please correct whichever is wrong.",
                     "ID_INCONSISTENT", provenance=f["provenance"]))

    if not intl:
        ids("XD_PAN_DOC", "PAN: form vs PAN proof", "form", form.get("pan"), "PAN_PROOF", "pan")
        names("XN_LEGAL_PAN", f"{who.capitalize()}: form vs PAN proof", "PAN_PROOF", "name",
              a_label=f"{who} on form", a=identity_name)
        if form.get("domestic_type") == "PAN with GST":
            ids("XD_GSTIN_DOC", "GSTIN: form vs GST certificate", "form", form.get("gstin"), "GST_CERT", "gstin")
            ids("XD_PAN_GST", "PAN: form vs PAN printed on GST certificate", "form", form.get("pan"), "GST_CERT", "pan")
            names("XN_LEGAL_GST", f"{who.capitalize()}: form vs GST legal name", "GST_CERT", "name",
                  a_label=f"{who} on form", a=identity_name)
            if prop:
                names("XN_TRADE_GST", "Firm / trade name: form vs GST trade name", "GST_CERT", "trade_name",
                      a_label="firm name on form", a=legal)
        names("XN_LEGAL_REG", "Legal name: form vs company registration", "COMPANY_REG", "name")
        ids("XR_REGNO", "Registration no.: form vs company registration", "form", form.get("reg_number"), "COMPANY_REG", "reg_number")
        ids("XB_IFSC", "IFSC: form vs bank proof", "form", form.get("ifsc"), "BANK_PROOF", "ifsc")
    else:
        names("XN_LEGAL_REG", "Legal name: form vs company registration", "COMPANY_REG", "name")
        names("XN_LEGAL_TAX", "Legal name: form vs tax certificate", "TAX_CERT", "name")
        names("XN_LEGAL_TRC", "Legal name: form vs TRC", "TRC", "name")   # TRC is mandatory for every International vendor
        ids("XD_TAXID_DOC", "Tax ID: form vs tax certificate", "form", form.get("tax_id"), "TAX_CERT", "tax_id")
        ids("XR_REGNO", "Registration no.: form vs company registration", "form", form.get("reg_number"), "COMPANY_REG", "reg_number")

    # bank: entered holder vs identity name (proprietor name for E1, else legal name), and bank proof holder vs identity name
    holder = form.get("bank_holder", "")
    if holder:
        st, sc = compare_names(identity_name, holder)
        ev = f"{who} '{identity_name}' vs bank account holder '{holder}' (similarity {sc})"
        label = f"Bank holder matches {who}"
        if st == "EXACT":
            if prop:
                ev += " - the account is in the proprietor's own name and matches the PAN card"
            out.append(R("reconciliation", "XB_HOLDER_LEGAL", label, "PASS", source="form (vendor-entered)", evidence=ev))
        else:
            sev, status = ("LOW", "WARN") if st == "SIMILAR" else ("HIGH", "FAIL")
            out.append(R("reconciliation", "XB_HOLDER_LEGAL", label, status, sev, "form (vendor-entered)", ev,
                         f"Bank account holder differs from the {who}" + (" slightly." if st == "SIMILAR" else " materially. Possible third-party account; reviewer must confirm - not a fraud finding."),
                         f"The bank account holder ('{holder}') does not match the {who} ('{identity_name}'). Please provide a bank account held in that name, or a bank letter explaining the difference.",
                         "BANK_HOLDER_MISMATCH"))
    f = fld("BANK_PROOF", "account_holder")
    if f:
        for rid, lab, a_lab, a in (("XB_HOLDER_DOC", "Bank holder: form vs bank proof", "form holder", holder),
                                   ("XB_DOCHOLDER_LEGAL", f"Bank proof holder matches {who}", who, identity_name)):
            if not a:
                continue
            st, sc = compare_names(a, f["value"])
            src = f"Bank proof p{f['page'] or '-'}: \"{f['evidence']}\" ({prov(f)})"
            ev = f"{a_lab}: '{a}' vs bank proof: '{f['value']}' (similarity {sc})"
            if low_ocr(f) and st != "EXACT":
                low_ocr_warn(rid, lab, "BANK_PROOF", f, src, ev, "account holder name")
                continue
            ev += accepted_note(f)
            if st == "EXACT":
                out.append(R("reconciliation", rid, lab, "PASS", source=src, evidence=ev, provenance=f["provenance"]))
            else:
                out.append(R("reconciliation", rid, lab, "WARN" if st == "SIMILAR" else "FAIL", "LOW" if st == "SIMILAR" else "HIGH", src, ev,
                             "Account holder on the bank document differs.",
                             f"The account holder on your bank proof ('{f['value']}') differs from '{a}'. Please provide matching bank proof.",
                             "BANK_HOLDER_MISMATCH", provenance=f["provenance"]))
    else:
        skip("XB_HOLDER_DOC", "Bank holder: form vs bank proof", "BANK_PROOF")
    ids("XB_ACCT", "Account no.: form vs bank proof", "form", form.get("account_number"), "BANK_PROOF", "account_number")

    # Part B: IFSC bank code (first 4 letters) vs bank name on the form and on the bank proof.
    if not intl:
        ifsc = norm_id(form.get("ifsc"))
        if ifsc and IFSC_RE.match(ifsc):
            code = ifsc[:4]
            expected = IFSC_BANK.get(code)
            if not expected:
                out.append(R("reconciliation", "XB_IFSC_BANK", "IFSC bank code vs bank name", "NOT_CHECKED", "LOW", "form",
                             f"IFSC bank code '{code}' is not in the known-bank table", "Bank code is not in our small lookup table, so it was not checked.",
                             "", None, blocking=False, provenance="system-derived"))
            else:
                proof = fld("BANK_PROOF", "bank_name")
                mismatches = []
                if form.get("bank_name") and compare_names(expected, form["bank_name"])[0] == "DIFFERENT":
                    mismatches.append(f"form bank '{form['bank_name']}'")
                if proof and compare_names(expected, proof["value"])[0] == "DIFFERENT":
                    mismatches.append(f"bank proof '{proof['value']}'")
                if mismatches:
                    out.append(R("reconciliation", "XB_IFSC_BANK", "IFSC bank code matches bank name", "WARN", "MEDIUM", "form",
                                 f"IFSC '{ifsc}' (code {code} = {expected}); " + ", ".join(mismatches),
                                 "The first four letters of an IFSC identify the bank.",
                                 f"Your IFSC ({ifsc}) belongs to {expected}, which does not match the bank name provided. Please check the IFSC and the bank name.",
                                 "IFSC_BANK_MISMATCH", provenance="system-derived"))
                else:
                    out.append(R("reconciliation", "XB_IFSC_BANK", "IFSC bank code matches bank name", "PASS", source="form",
                                 evidence=f"IFSC code {code} = {expected}; bank name agrees", provenance="system-derived"))
    return out

# ---- stage 3: risk checks (duplicates, policy blocklist) ---------------------------
def risk_checks(form: dict, dups: list, blocked: list, shared: list = None) -> list:
    out = []
    for sh in (shared or []):
        out.append(R("risk", "XR_BANK_SHARED", "Bank account already registered to another vendor", "WARN", "HIGH", "local database",
                     f"account {sh['id_value']} already on {sh['ref']} ({sh['legal_name']}, status {sh['prior_status']})",
                     "The same bank account (IFSC/SWIFT + account number) is registered to a different vendor. A reviewer must confirm this is intended.",
                     f"The bank account provided is already registered to another vendor (reference {sh['ref']}). Please confirm this is correct, or provide the vendor's own account.",
                     "BANK_SHARED", provenance="system-derived"))
    if dups:
        for d in dups:
            out.append(R("risk", f"DUP_{d['id_type']}", f"Duplicate identifier: {d['id_type']}", "WARN", "HIGH", "local database",
                         f"{d['id_type']} {d['id_value']} already on {d['ref']} ({d['legal_name']}, status {d['prior_status']})",
                         "Could be a resubmission, profile update or related entity. Automatic approval is blocked.",
                         f"Our records already contain a registration using the same {d['id_type']} (reference {d['ref']}). Please tell us whether this is an update to that registration or a different entity.",
                         "DUPLICATE_IDENTIFIER", provenance="system-derived"))
    else:
        out.append(R("risk", "DUP_NONE", "No duplicate identifiers in local database", "PASS", source="local database",
                     evidence=f"checked {', '.join(t for t, _ in identifiers(form)) or 'none'}", provenance="system-derived"))
    for b in blocked:
        out.append(R("risk", f"BLOCK_{b['id_type']}", f"Policy blocklist hit: {b['id_type']}", "FAIL", "HIGH", "policy_blocklist",
                     f"{b['id_value']} - {b['reason']} (added by {b['added_by']})",
                     "Identifier appears on the organisation's written blocklist (policy disqualification).",
                     "Your registration cannot proceed under our vendor policy.", "POLICY_BLOCKLIST", provenance="system-derived"))
    # Part B: a free (personal) email domain for a company or LLP is a soft, non-blocking note.
    email = (form.get("email") or "").strip().lower()
    if "@" in email and form.get("entity_type") in COMPANY_ENTITY_TYPES:
        domain = email.rsplit("@", 1)[1]
        base = domain.split(".")[0]
        if base in FREE_EMAIL_DOMAINS:
            out.append(R("risk", "FREE_EMAIL_DOMAIN", "Free email domain for a company/LLP", "WARN", "LOW", "form",
                         f"'{email}' uses the free email domain '{domain}'",
                         "A registered company or LLP using a personal/free email domain is worth noting, but is not a blocking issue.",
                         "", None, blocking=False, provenance="system-derived"))
    out.append(R("risk", "SCOPE_EXTERNAL", "Government / sanctions / bank-ownership verification", "NOT_CHECKED", "LOW", "scope",
                 "no external API configured", "Format and consistency checks only; official registries are not queried in this build.",
                 "", None, blocking=False, provenance="system-derived"))
    return out

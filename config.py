"""Central configuration: policy version, document types, country registry."""
import re

POLICY_VERSION = "VV-POLICY-1.0"
MAX_UPLOAD_MB = 5                       # deliberate demo constraint (not inherited from IITM portal)
ALLOWED_EXT = {".pdf", ".png", ".jpg", ".jpeg"}
MIN_TEXT_CHARS = 40                     # below this a PDF is treated as scanned / unreadable

DOC_LABELS = {
    "PAN_PROOF": "PAN proof",
    "GST_CERT": "GST registration certificate",
    "COMPANY_REG": "Company / business registration proof",
    "BANK_PROOF": "Bank proof (cancelled cheque / bank letter)",
    "TAX_CERT": "Foreign tax registration certificate",
    "TRC": "Tax Residency Certificate (TRC)",
    "INVOICE": "Invoice",
    "UNKNOWN": "Unrecognised document",
}

# keyword -> weight. Strong phrases = 3, weak hints = 1. Matching is on lower-cased text.
DOC_KEYWORDS = {
    "GST_CERT": {"registration certificate": 3, "goods and services tax": 3, "form gst reg-06": 3,
                 "gst reg-06": 3, "gstin": 1, "date of liability": 1, "legal name": 1},
    "PAN_PROOF": {"permanent account number": 3, "income tax department": 3, "pan card": 3,
                  "pan:": 1, "date of birth": 1, "date of incorporation": 1},
    "COMPANY_REG": {"certificate of incorporation": 3, "corporate identification number": 3,
                    "ministry of corporate affairs": 3, "limited liability partnership": 2,
                    "llpin": 3, "partnership deed": 3, "udyam registration": 3,
                    "business registration certificate": 3, "commercial register": 3,
                    "incorporation": 1, "registrar of companies": 2, "registration number": 1},
    "BANK_PROOF": {"cancelled cheque": 3, "canceled cheque": 3, "bank account confirmation": 3,
                   "bank statement": 3, "bank letter": 3, "account holder": 2, "ifsc": 1,
                   "iban": 1, "swift": 1, "account number": 1, "beneficiary": 1},
    "TAX_CERT": {"tax registration certificate": 3, "vat registration": 3, "employer identification number": 3,
                 "ein confirmation": 3, "tax id": 1, "umsatzsteuer": 3, "trn certificate": 3,
                 "tax registration number": 3},
    "TRC": {"tax residency certificate": 3, "certificate of residence": 3, "resident of": 1},
    "INVOICE": {"tax invoice": 3, "invoice no": 3, "invoice number": 3, "bill to": 3,
                "amount due": 2, "total amount": 1, "unit price": 2, "qty": 1, "due date": 1},
}
CLASSIFY_MIN_SCORE = 3

# ---- Country rule registry -------------------------------------------------------
# Only countries listed here have an automated identifier rule. Everything else -> NOT_CHECKED -> PENDING.
COUNTRIES = {
    "India": {"code": "IN"}, "United States": {"code": "US"}, "United Kingdom": {"code": "GB"},
    "Germany": {"code": "DE"}, "Singapore": {"code": "SG"}, "United Arab Emirates": {"code": "AE"},
    "Brazil": {"code": "BR"}, "Japan": {"code": "JP"}, "Nigeria": {"code": "NG"},
    "Australia": {"code": "AU"}, "Canada": {"code": "CA"}, "France": {"code": "FR"},
}
TAX_RULES = {   # keyed by country name
    "United States": {"scheme": "US EIN", "regex": r"^\d{2}-\d{7}$", "example": "12-3456789"},
    "United Kingdom": {"scheme": "UK VAT number", "regex": r"^GB(\d{9}|\d{12})$", "example": "GB123456789"},
    "Germany": {"scheme": "German USt-IdNr (VAT ID)", "regex": r"^DE\d{9}$", "example": "DE123456789"},
    "Singapore": {"scheme": "Singapore GST registration no.", "regex": r"^[MT]\d{8}[A-Z]$", "example": "M12345678X"},
    "United Arab Emirates": {"scheme": "UAE TRN", "regex": r"^\d{15}$", "example": "100123456789003"},
}
# India schemes are used ONLY to detect scheme/country conflicts for non-India vendors,
# and to validate domestic vendors. They are never applied to a foreign vendor's own checks.
INDIA_SCHEMES = {
    "India PAN": r"^[A-Z]{5}\d{4}[A-Z]$",
    "India GSTIN": r"^\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]$",
}
PAN_RE = re.compile(INDIA_SCHEMES["India PAN"])
GSTIN_RE = re.compile(INDIA_SCHEMES["India GSTIN"])
IFSC_RE = re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$")
CIN_RE = re.compile(r"^[LU]\d{5}[A-Z]{2}\d{4}[A-Z]{3}\d{6}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")
BIC_RE = re.compile(r"^[A-Z]{4}[A-Z]{2}[A-Z0-9]{2}([A-Z0-9]{3})?$")

ENTITY_TYPES = ["Private Limited Company", "Public Limited Company", "LLP", "Partnership Firm",
                "Proprietorship", "Trust / Society", "Other"]
PAN_4TH = {"Private Limited Company": "C", "Public Limited Company": "C", "LLP": "F",
           "Partnership Firm": "F", "Proprietorship": "P", "Trust / Society": "T"}
SERVICE_CATEGORIES = ["Manufacturing / Supply of goods", "IT & software services", "Logistics & transport",
                      "Professional / consulting services", "Facilities & maintenance", "Marketing & media", "Other"]

LEGAL_SUFFIXES = {"private", "pvt", "limited", "ltd", "llp", "llc", "inc", "incorporated", "corp", "corporation",
                  "co", "company", "plc", "gmbh", "ag", "pte", "bv", "sa", "fze", "fzco", "lp", "the"}
NAME_EXACT, NAME_SIMILAR = 0.999, 0.85   # normalized equality / fuzzy threshold (fuzzy => WARN, never auto-reject)

def required_docs(form: dict) -> list:
    if form.get("region") == "International":
        docs = ["COMPANY_REG", "BANK_PROOF", "TAX_CERT"]
        if form.get("trc_applicable"):
            docs.append("TRC")
        return docs
    docs = ["PAN_PROOF"]
    if form.get("domestic_type") == "PAN with GST":
        docs.append("GST_CERT")
    return docs + ["COMPANY_REG", "BANK_PROOF"]

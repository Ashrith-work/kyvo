"""Central configuration: policy version, document types, country registry."""
import re

POLICY_VERSION = "KYVO-POLICY-1.1"      # 1.1: photos and scanned PDFs are read by OCR, with a confidence gate
MAX_UPLOAD_MB = 1                       # portal upload limit: PDF/JPG/PNG/DOC, max 1 MB each
ALLOWED_EXT = {".pdf", ".png", ".jpg", ".jpeg", ".doc", ".docx"}
MIN_TEXT_CHARS = 40                     # below this a PDF is treated as scanned / unreadable

# ---- OCR (Tesseract) ---------------------------------------------------------------
OCR_DPI = 300                           # render resolution for image-only PDF pages
OCR_MIN_DOC_CONF = 0.60                 # mean word confidence below this: the whole document is unreadable
OCR_MIN_FIELD_CONF = 0.80               # a value read below this only counts if it equals the vendor-entered value exactly

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

# ---- Part B cross-check tables (India) -------------------------------------------
# GSTIN first two digits -> state name (used to cross-check the billing state).
GST_STATE_CODE = {
    "01": "Jammu and Kashmir", "02": "Himachal Pradesh", "03": "Punjab", "04": "Chandigarh",
    "05": "Uttarakhand", "06": "Haryana", "07": "Delhi", "08": "Rajasthan", "09": "Uttar Pradesh",
    "10": "Bihar", "11": "Sikkim", "12": "Arunachal Pradesh", "13": "Nagaland", "14": "Manipur",
    "15": "Mizoram", "16": "Tripura", "17": "Meghalaya", "18": "Assam", "19": "West Bengal",
    "20": "Jharkhand", "21": "Odisha", "22": "Chhattisgarh", "23": "Madhya Pradesh", "24": "Gujarat",
    "25": "Daman and Diu", "26": "Dadra and Nagar Haveli", "27": "Maharashtra", "28": "Andhra Pradesh",
    "29": "Karnataka", "30": "Goa", "31": "Lakshadweep", "32": "Kerala", "33": "Tamil Nadu",
    "34": "Puducherry", "35": "Andaman and Nicobar Islands", "36": "Telangana", "37": "Andhra Pradesh",
    "38": "Ladakh", "97": "Other Territory", "99": "Centre Jurisdiction",
}
# Indian PIN code first digit -> the postal zone's states (lower-cased for matching).
PIN_ZONE = {
    "1": {"delhi", "haryana", "punjab", "himachal pradesh", "jammu and kashmir", "ladakh", "chandigarh"},
    "2": {"uttar pradesh", "uttarakhand"},
    "3": {"rajasthan", "gujarat", "daman and diu", "dadra and nagar haveli"},
    "4": {"maharashtra", "madhya pradesh", "chhattisgarh", "goa"},
    "5": {"andhra pradesh", "telangana", "karnataka"},
    "6": {"tamil nadu", "kerala", "puducherry", "lakshadweep"},
    "7": {"west bengal", "odisha", "assam", "sikkim", "arunachal pradesh", "nagaland", "manipur",
          "mizoram", "tripura", "meghalaya", "andaman and nicobar islands"},
    "8": {"bihar", "jharkhand"},
}
# IFSC first four letters -> bank name.
IFSC_BANK = {
    "SBIN": "State Bank of India", "HDFC": "HDFC Bank", "ICIC": "ICICI Bank", "UTIB": "Axis Bank",
    "KKBK": "Kotak Mahindra Bank", "PUNB": "Punjab National Bank", "BARB": "Bank of Baroda",
    "CNRB": "Canara Bank", "UBIN": "Union Bank of India", "IDIB": "Indian Bank", "IOBA": "Indian Overseas Bank",
    "FDRL": "Federal Bank", "YESB": "Yes Bank", "INDB": "IndusInd Bank", "IDFB": "IDFC First Bank",
}
FREE_EMAIL_DOMAINS = {"gmail", "yahoo", "outlook", "hotmail", "rediffmail"}
COMPANY_ENTITY_TYPES = {"Private Limited Company", "Public Limited Company", "LLP"}

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
        # TRC (number + certificate) is mandatory for every International vendor.
        return ["COMPANY_REG", "BANK_PROOF", "TAX_CERT", "TRC"]
    docs = ["PAN_PROOF"]
    if form.get("domestic_type") == "PAN with GST":
        docs.append("GST_CERT")
    return docs + ["COMPANY_REG", "BANK_PROOF"]

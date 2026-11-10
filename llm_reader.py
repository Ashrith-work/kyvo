"""Part K: optional LLM extraction reader.

Off by default. The provider and key come from environment variables, never from code:
  KYVO_LLM=1              turns it on (otherwise propose() returns nothing and nothing changes)
  KYVO_LLM_PROVIDER      anthropic (default)
  KYVO_LLM_MODEL         model id (default claude-opus-4-8)
  KYVO_LLM_KEY           the API key (or ANTHROPIC_API_KEY)

It proposes field values for a document. A proposed value is used ONLY if the same value appears in the
document's own text or OCR output after the rules' normalisation; otherwise it is ignored and logged.
The rules still decide. Every extraction is cached by document-content hash in sample_data/llm_cache/
so the demo, the evaluation and CI make no model calls.
"""
import os, json, re, hashlib, urllib.request, urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(HERE, "sample_data", "llm_cache")

# Fields worth asking the model for, per expected document type (same names the rules use).
FIELDS = {
    "PAN_PROOF": ["pan", "name"],
    "GST_CERT": ["gstin", "pan", "name", "trade_name"],
    "COMPANY_REG": ["name", "reg_number"],
    "BANK_PROOF": ["account_holder", "account_number", "ifsc", "bank_name", "swift"],
    "TAX_CERT": ["tax_id", "name"],
    "TRC": ["name", "country"],
}


def enabled():
    return os.environ.get("KYVO_LLM", "0") == "1"


def _key():
    return os.environ.get("KYVO_LLM_KEY") or os.environ.get("ANTHROPIC_API_KEY") or ""


def _cache_path(doc_type, text):
    h = hashlib.sha256(("v1\n" + doc_type + "\n" + text).encode("utf-8")).hexdigest()
    return os.path.join(CACHE_DIR, f"{doc_type}_{h[:16]}.json")


def _call_model(doc_type, text):
    provider = os.environ.get("KYVO_LLM_PROVIDER", "anthropic").lower()
    fields = FIELDS.get(doc_type, [])
    prompt = ("You read a vendor onboarding document and return a strict JSON object. "
              f"The document is a {doc_type.replace('_', ' ').lower()}. "
              f"Return only these keys: {fields}. Use the exact text as printed in the document; "
              "if a field is not present, use an empty string. Return JSON only, no prose.\n\n"
              "Document text:\n" + text[:6000])
    if provider != "anthropic":
        raise RuntimeError(f"KYVO_LLM_PROVIDER={provider} is not supported in this build (anthropic only).")
    model = os.environ.get("KYVO_LLM_MODEL", "claude-opus-4-8")
    body = json.dumps({"model": model, "max_tokens": 400,
                       "messages": [{"role": "user", "content": prompt}]}).encode("utf-8")
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=body, method="POST")
    req.add_header("content-type", "application/json")
    req.add_header("x-api-key", _key())
    req.add_header("anthropic-version", "2023-06-01")
    with urllib.request.urlopen(req, timeout=40) as r:
        data = json.loads(r.read().decode("utf-8"))
    parts = data.get("content") or []
    out_text = "".join(p.get("text", "") for p in parts if p.get("type") == "text")
    m = re.search(r"\{.*\}", out_text, re.S)
    return json.loads(m.group(0)) if m else {}


def propose(doc_type, pages):
    """Return {field: value} the model proposes for this document, from the cache when present. Returns {}
    when the reader is off (so the default pipeline is unchanged). Never raises into the pipeline."""
    if not enabled():
        return {}                      # off by default: the standard pipeline is unchanged
    text = "\n".join(pages or [])
    cache = _cache_path(doc_type, text)
    if os.path.exists(cache):
        try:
            with open(cache, encoding="utf-8") as fh:
                return json.load(fh).get("fields", {})
        except Exception:
            return {}
    try:
        fields = {k: v for k, v in _call_model(doc_type, text).items() if v}
    except Exception as e:
        _log_ignored(doc_type, {"_error": str(e)[:120]})
        return {}
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(cache, "w", encoding="utf-8") as fh:
        json.dump({"doc_type": doc_type, "fields": fields}, fh, indent=1)
    return fields


def _log_ignored(doc_type, detail):
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(os.path.join(CACHE_DIR, "ignored.log"), "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"doc_type": doc_type, **detail}) + "\n")
    except OSError:
        pass

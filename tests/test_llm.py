"""Part K: the optional LLM reader is off by default, and when on a proposed value is used only if it
appears in the document's own text after normalisation. No network: a stub cache stands in for the model."""
import os, json
import extraction as ex
import llm_reader

PAN_PAGES = ["INCOME TAX DEPARTMENT", "Permanent Account Number Card",
             "PAN: AABCM4821K", "Name: Meridian Precision Components Private Limited"]


def test_llm_is_off_by_default(monkeypatch):
    monkeypatch.delenv("KYVO_LLM", raising=False)
    out = ex.extract_fields("PAN_PROOF", PAN_PAGES)
    assert out and all(f["method"] != "llm" for f in out)


def test_llm_value_counts_only_if_in_document(monkeypatch):
    monkeypatch.setenv("KYVO_LLM", "1")
    path = llm_reader._cache_path("PAN_PROOF", "\n".join(PAN_PAGES))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # the model proposes a correct PAN (present in the document) and a fabricated name (absent)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"doc_type": "PAN_PROOF", "fields": {"pan": "AABCM4821K", "name": "Totally Fabricated Pvt Ltd"}}, fh)
    try:
        out = ex.extract_fields("PAN_PROOF", PAN_PAGES)
        llm = [f for f in out if f["method"] == "llm"]
        assert any(f["field"] == "pan" and f["value"] == "AABCM4821K" for f in llm)   # confirmed in the document
        assert not any(f["field"] == "name" for f in llm)                              # not in the document, ignored
    finally:
        os.remove(path)


def test_cache_is_not_consulted_when_off(monkeypatch):
    monkeypatch.delenv("KYVO_LLM", raising=False)
    path = llm_reader._cache_path("PAN_PROOF", "\n".join(PAN_PAGES))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"doc_type": "PAN_PROOF", "fields": {"pan": "AABCM4821K"}}, fh)
    try:
        out = ex.extract_fields("PAN_PROOF", PAN_PAGES)
        assert all(f["method"] != "llm" for f in out)   # a committed cache never activates the reader when off
    finally:
        os.remove(path)

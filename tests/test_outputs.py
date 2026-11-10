"""Part C: the submission schema matches every scenario, the out/<ref>/ artefacts are written,
and manual portal field names map onto the internal keys."""
import os, json
import jsonschema
import outputs
import workflow as wf

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SUB_SCHEMA = json.load(open(os.path.join(ROOT, "schemas", "submission.schema.json")))
DEC_SCHEMA = json.load(open(os.path.join(ROOT, "schemas", "decision.schema.json")))


def test_submission_schema_validates_every_scenario(env):
    for sid, s in env.sc.items():
        jsonschema.validate(s["form"], SUB_SCHEMA)


def test_write_outputs_creates_the_contract_files(env, tmp_path):
    r = env.run("2_wrong_document")
    out = str(tmp_path / "out")
    folder = outputs.write_outputs(env.conn, r["submission_id"], out)
    assert os.path.exists(os.path.join(folder, "decision.json"))
    rec = json.load(open(os.path.join(folder, "decision.json")))
    jsonschema.validate(rec, DEC_SCHEMA)
    assert os.path.exists(os.path.join(folder, "report.md"))
    assert os.path.exists(os.path.join(folder, "audit.jsonl"))
    emls = sorted(e for e in os.listdir(os.path.join(folder, "emails")) if e.endswith(".eml"))
    assert emls, "at least one outgoing email written as .eml"
    body = open(os.path.join(folder, "emails", emls[0]), encoding="utf-8").read()
    assert "X-Kyvo-Sent: no" in body and "Subject:" in body


def test_ledger_and_digest(env, tmp_path):
    env.run("1_happy_path")
    env.run("2_wrong_document")
    out = str(tmp_path / "out")
    outputs.write_ledger(env.conn, out)
    outputs.write_digest(env.conn, out, "2026-01-01")
    assert os.path.exists(os.path.join(out, "ledger.csv"))
    assert os.path.exists(os.path.join(out, "digest-2026-01-01.md"))
    rows = open(os.path.join(out, "ledger.csv"), encoding="utf-8").read().splitlines()
    assert rows[0].startswith("ref,") and len(rows) == 3   # header + two submissions


def test_manual_portal_field_aliases_map_to_internal_keys(env):
    base = dict(env.sc["1_happy_path"]["form"])
    manual = dict(base)
    manual["firm_name"] = manual.pop("legal_name")
    manual["ownership"] = manual.pop("entity_type")
    manual["company_phone"] = manual.pop("phone")
    manual["contact_name"] = manual.pop("contact_person")
    f = wf.normalize_form(manual)
    assert f["legal_name"] == base["legal_name"]
    assert f["entity_type"] == base["entity_type"]
    assert f["phone"] == base["phone"]
    assert f["contact_person"] == base["contact_person"]

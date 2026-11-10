"""Part B: every result carries an assurance level, decision.json rolls it up per identifier,
and APPROVED requires CONSISTENCY on the policy's required identities (the two-source rule)."""
import json, os
import jsonschema
import assurance as asr

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DECISION_SCHEMA = json.load(open(os.path.join(ROOT, "schemas", "decision.schema.json")))


def test_every_result_has_a_valid_assurance_level(env):
    r = env.run("1_happy_path")
    assert r["results"]
    for x in r["results"]:
        assert x["assurance"] in ("FORMAT", "CONSISTENCY", "NOT_VERIFIED"), x["rule_id"]


def test_decision_json_rolls_up_assurance_per_identifier(env):
    r = env.run("1_happy_path")
    rec = env.wf.decision_json(env.conn, r["submission_id"])
    a = rec["assurance"]
    for ident in ("pan", "legal_name", "bank_account"):
        assert a.get(ident, {}).get("level") == "CONSISTENCY", ident
        assert a[ident]["not_verified"], ident


def test_two_source_rule_met_for_approved(env):
    r = env.run("1_happy_path")
    assert r["status"] == "APPROVED"
    assert asr.two_source_gap(r["results"], env.sc["1_happy_path"]["form"]) == []


def test_not_verified_is_stated(env):
    r = env.run("1_happy_path")
    rec = env.wf.decision_json(env.conn, r["submission_id"])
    text = json.dumps(rec["assurance"]).lower()
    assert "registry" in text or "not verified" in text or "ownership" in text


def test_every_decision_json_validates_against_schema(env):
    for sid in ("1_happy_path", "2_wrong_document", "3_bank_mismatch", "6_pan_without_gst",
                "7_international_us", "8_unsupported_country"):
        r = env.run(sid)
        rec = env.wf.decision_json(env.conn, r["submission_id"])
        jsonschema.validate(rec, DECISION_SCHEMA)

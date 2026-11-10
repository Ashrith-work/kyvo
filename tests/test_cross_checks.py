"""Part B: subtle cross-checks (GSTIN state, PIN zone, IFSC bank code, free email). None may cause REJECTED."""

def flags(r): return {f["code"] for f in r["flags"]}
def rule(r, rid): return next((x for x in r["results"] if x["rule_id"] == rid), None)


def test_consistent_submission_passes_cross_checks(env):
    r = env.run("1_happy_path")
    assert rule(r, "XB_GSTIN_STATE")["status"] == "PASS"
    assert rule(r, "XB_PINCODE_STATE")["status"] == "PASS"
    assert rule(r, "XB_IFSC_BANK")["status"] == "PASS"
    assert r["status"] == "APPROVED"


def test_gstin_state_mismatch_is_blocking_warn(env):
    r = env.run("1_happy_path", state="Maharashtra")       # GSTIN state code 29 = Karnataka
    x = rule(r, "XB_GSTIN_STATE")
    assert x["status"] == "WARN" and x["blocking"] == 1
    assert r["status"] == "PENDING" and "GSTIN_STATE_MISMATCH" in flags(r) and r["status"] != "REJECTED"


def test_pincode_zone_mismatch_is_blocking_warn(env):
    r = env.run("1_happy_path", pincode="400001")          # zone 4 (Maharashtra), state is Karnataka
    x = rule(r, "XB_PINCODE_STATE")
    assert x["status"] == "WARN" and x["blocking"] == 1 and r["status"] == "PENDING"


def test_ifsc_bank_mismatch_is_blocking_warn(env):
    r = env.run("1_happy_path", bank_name="ICICI Bank")    # IFSC HDFC0001234 but bank says ICICI
    x = rule(r, "XB_IFSC_BANK")
    assert x["status"] == "WARN" and "IFSC_BANK_MISMATCH" in flags(r) and r["status"] != "REJECTED"


def test_ifsc_unknown_code_is_not_checked(env):
    r = env.run("1_happy_path", ifsc="ZZZZ0123456")        # valid IFSC syntax, code not in the table
    x = rule(r, "XB_IFSC_BANK")
    assert x["status"] == "NOT_CHECKED" and x["blocking"] == 0


def test_free_email_for_company_is_low_non_blocking(env):
    r = env.run("1_happy_path", email="anita@gmail.com")   # Private Limited + free domain
    x = rule(r, "FREE_EMAIL_DOMAIN")
    assert x is not None and x["severity"] == "LOW" and x["blocking"] == 0
    assert r["status"] == "APPROVED"                        # a soft note must not change the outcome

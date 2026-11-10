"""Part A: the policy file is the single source of thresholds, the version is hash-pinned by the
lock, and flags.yaml covers every flag code the engine can emit."""
import os, re
import config


def test_policy_lock_matches_policy_file():
    ok, msg = config.lock_status()
    assert ok, msg


def test_policy_version_is_hash_pinned():
    assert config.POLICY_VERSION.startswith(f"KYVO-POLICY-{config.POLICY_SEMVER}+")
    assert config.POLICY_VERSION.endswith(config.POLICY_SHA256[:12])


def _emitted_flag_codes():
    """Every flag code the engine can attach to a result (statically, from the source)."""
    codes = set()
    for name in ("validation.py", "workflow.py"):
        src = open(os.path.join(config.BASE_DIR, name), encoding="utf-8").read()
        codes |= set(re.findall(r'flag="([A-Z_]+)"', src))
    return codes


def test_flags_cover_every_emitted_code():
    missing = _emitted_flag_codes() - set(config.FLAGS)
    assert not missing, f"flag codes emitted by the engine but absent from flags.yaml: {sorted(missing)}"


def test_every_flag_entry_is_complete():
    required = {"severity", "blocking", "waiting_on", "assurance", "vendor_ask", "reviewer_step"}
    for code, spec in config.FLAGS.items():
        assert required <= set(spec), f"{code} is missing {required - set(spec)}"
        assert spec["assurance"] in ("FORMAT", "CONSISTENCY", "NOT_VERIFIED"), code
        assert spec["waiting_on"] in ("VENDOR", "REVIEWER", "NONE"), code

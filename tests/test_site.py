"""Part I: the static report site builds from the database with matching styling, per-submission
pages, policy, flags and eval, and no em-dashes."""
import os
import site_build


def test_site_builds_from_the_database(env, tmp_path):
    env.run("1_happy_path")
    env.run("3_bank_mismatch")
    out = str(tmp_path / "out")
    site_build.build(env.conn, out)
    for f in ("index.html", "policy.html", "flags.html", "eval.html", "style.css"):
        assert os.path.exists(os.path.join(out, "site", f))
    idx = open(os.path.join(out, "site", "index.html"), encoding="utf-8").read()
    assert "Vendor onboarding" in idx and "SUB-00001" in idx
    assert "—" not in idx            # no em-dashes anywhere
    sub = open(os.path.join(out, "site", "sub", "SUB-00001.html"), encoding="utf-8").read()
    assert "Identity assurance" in sub and "—" not in sub
    pol = open(os.path.join(out, "site", "policy.html"), encoding="utf-8").read()
    assert "name_similar" in pol and "owner" in pol.lower()
    flg = open(os.path.join(out, "site", "flags.html"), encoding="utf-8").read()
    assert "BANK_HOLDER_MISMATCH" in flg

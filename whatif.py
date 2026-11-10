"""Part H: policy what-if. Re-run every scenario under a modified policy in a temporary workspace and
report which outcomes and flags change. The real policy file and workspace are never touched. Each run
happens in a separate subprocess with KYVO_POLICY pointing at the policy in force, so the live process
keeps the real policy.
"""
import os, sys, json, tempfile, shutil, subprocess
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))


def _write_modified_policy(dst, overrides):
    base = os.environ.get("KYVO_POLICY", os.path.join(HERE, "policy", "kyvo_policy.yaml"))
    doc = yaml.safe_load(open(base, encoding="utf-8").read())
    changed = {}
    for key, raw in overrides.items():
        try:
            val = yaml.safe_load(raw)
        except Exception:
            val = raw
        if key in doc.get("thresholds", {}):
            doc["thresholds"][key]["value"] = val
        elif key in doc.get("risk_weights", {}):
            doc["risk_weights"][key]["value"] = val
        else:
            doc.setdefault("thresholds", {})[key] = {"value": val, "unit": "", "why": "what-if override",
                                                     "owner": "whatif", "if_raised": "", "if_lowered": ""}
        changed[key] = val
    doc["semver"] = str(doc.get("semver", "0.0.0")) + "-whatif"
    with open(dst, "w", encoding="utf-8") as fh:
        yaml.safe_dump(doc, fh, sort_keys=False)
    return changed


def _outcomes(sample_dir, policy_path=None):
    env = dict(os.environ)
    if policy_path:
        env["KYVO_POLICY"] = policy_path
    out = subprocess.run([sys.executable, "-I", os.path.join(HERE, "whatif.py"), "--run", sample_dir],
                         capture_output=True, text=True, env=env)
    if out.returncode != 0:
        raise RuntimeError(f"whatif subprocess failed: {out.stderr[-400:]}")
    return json.loads(out.stdout)


def run(overrides: dict):
    """Returns {changed, differences:[{scenario, before, after, flags_added, flags_removed}]}."""
    base_sample = os.path.join(HERE, "sample_data")
    work = tempfile.mkdtemp(prefix="kyvo_whatif_")
    sample = os.path.join(work, "sample")
    shutil.copytree(base_sample, sample)
    before = _outcomes(sample)
    mod_policy = os.path.join(work, "kyvo_policy.yaml")
    changed = _write_modified_policy(mod_policy, overrides)
    after = _outcomes(sample, mod_policy)
    diffs = []
    for sid in before:
        bs, brisk, bf = before[sid]
        as_, arisk, af = after[sid]
        if (bs, brisk, bf) != (as_, arisk, af):
            diffs.append({"scenario": sid, "before": bs, "after": as_, "risk_before": brisk, "risk_after": arisk,
                          "flags_added": sorted(set(af) - set(bf)), "flags_removed": sorted(set(bf) - set(af))})
    return {"changed": changed, "differences": diffs}


def _run_scenarios(sample_dir):
    """Subprocess entry point: run every scenario under the policy in KYVO_POLICY, print {sid:[status,flags]}."""
    sys.path.insert(0, HERE)
    import database as db
    import workflow as wf
    import evaluation
    from sample_data import generate as gen
    conn = db.connect(os.path.join(tempfile.mkdtemp(prefix="kyvo_wi_"), "w.db"))
    wf.seed_demo(conn)
    up = tempfile.mkdtemp(prefix="kyvo_wi_up_")
    scen = {s["id"]: s for s in gen.load_scenarios(sample_dir)}
    out = {}
    for sid in evaluation.RUN_ORDER:
        res = wf.run_submission(conn, scen[sid]["form"], gen.scenario_files(sample_dir, scen[sid]), up)
        d = db.latest_decision(conn, res["submission_id"])
        flags = sorted({r["flag_code"] for r in db.rows(conn, "SELECT flag_code FROM validation_results WHERE submission_id=? AND run_no=? AND flag_code IS NOT NULL AND status!='PASS'", (res["submission_id"], d["run_no"]))})
        out[sid] = [d["status"], d["risk_level"], flags]
    print(json.dumps(out))


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--run":
        _run_scenarios(sys.argv[2])

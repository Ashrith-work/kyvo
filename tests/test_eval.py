"""Part H: ground truth, eval (zero unsafe errors in decisions and in the degradation sweep),
and whatif leaving the real policy and workspace untouched."""
import os
import config
import evaluation
import whatif
from sample_data import generate as gen


def test_ground_truth_covers_every_scenario(env):
    gt = gen.load_ground_truth(env.dir)
    assert len(gt) == 15
    assert gt["1_happy_path"]["PAN_PROOF"]["pan"] == env.sc["1_happy_path"]["form"]["pan"]
    assert gt["1_happy_path"]["BANK_PROOF"]["account_number"] == env.sc["1_happy_path"]["form"]["account_number"]


def test_eval_has_zero_unsafe_errors(env, tmp_path):
    r = evaluation.run(sample_dir=env.dir, out_root=str(tmp_path / "out"))
    assert os.path.exists(os.path.join(str(tmp_path), "out", "eval", "eval.json"))
    assert r["summary"]["unsafe_errors"] == 0
    assert r["summary"]["sweep_unsafe_errors"] == 0
    assert r["decision"]["status_accuracy"] >= 0.9


def test_degradation_sweep_never_wrong_approves(env):
    sweep = evaluation.degradation_sweep(env.dir, blurs=(0.0, 4.0), scales=(1.0, 0.3))
    assert sweep["unsafe_errors"] == 0
    assert all(not p["wrong_approve"] for p in sweep["points"])


def test_whatif_leaves_the_real_policy_untouched(env):
    before = config.POLICY_VERSION
    r = whatif.run({"name_similar": "0.3"})
    assert config.POLICY_VERSION == before            # the live process still holds the real policy
    assert config.lock_status()[0]                     # the real policy file was not modified
    assert isinstance(r["differences"], list)

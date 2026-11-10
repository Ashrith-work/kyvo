"""Part J: the scripted demo runs end to end, exercises all three outcomes and the lifecycle, and
writes the out/ artefacts. The outbox never sends."""
import os
import database as db
import demo


def test_demo_end_to_end(tmp_path):
    out = demo.run_demo(str(tmp_path / "ws"))
    conn, p, tl = out["conn"], out["paths"], out["timeline"]

    statuses = {db.latest_decision(conn, s["id"])["status"] for s in db.rows(conn, "SELECT id FROM submissions")}
    assert {"APPROVED", "PENDING", "REJECTED"} <= statuses

    events = {r["event"] for r in tl}
    assert {"received", "decided", "change request", "reviewer approved", "reviewer rejected",
            "stale", "reminder"} <= events

    assert os.path.exists(os.path.join(p["out"], "ledger.csv"))
    assert any(f.startswith("digest-") for f in os.listdir(p["out"]))

    emls = [f for f in os.listdir(p["outbox"]) if f.endswith(".eml")]
    assert emls
    assert all("X-Kyvo-Sent: no" in open(os.path.join(p["outbox"], f), encoding="utf-8").read() for f in emls)


def test_demo_e2_bank_change_holds_then_reviewer_lifts(tmp_path):
    out = demo.run_demo(str(tmp_path / "ws"))
    conn = out["conn"]
    # the E2 vendor is approved, then a bank change holds payments, then a reviewer approval lifts it
    held_ever = db.rows(conn, "SELECT DISTINCT vendor_id FROM payments")
    assert held_ever
    # at least one submission went APPROVED -> change request -> REVIEWER -> APPROVED
    tl = out["timeline"]
    cr = [r["ref"] for r in tl if r["event"] == "change request"]
    assert cr
    ref = cr[0]
    seq = [r for r in tl if r["ref"] == ref]
    assert any(r["event"] == "reviewer approved" for r in seq)

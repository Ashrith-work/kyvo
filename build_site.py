"""Generate the published demo site into public/ for static hosting (for example Vercel).

A focused dataset: one happy path plus the four edge cases (E1 sole proprietor, E2 bank change after
approval, E3 shared bank account incl. the blocklisted variant, E4 resubmission across three runs).
Run: python build_site.py   ->   writes ./public/
"""
import os, sys, shutil, tempfile, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import demo, clock, database as db, outputs, evaluation, site_build
from sample_data import generate as gen

HERE = os.path.dirname(os.path.abspath(__file__))


def build(public=os.path.join(HERE, "public")):
    root = tempfile.mkdtemp(prefix="kyvo_site_")
    conn, p = demo.open_ws(root, fresh=True)
    sc = {s["id"]: s for s in gen.load_scenarios(p["sample"])}
    c = clock.at(demo.START)

    def files(sid):
        return gen.scenario_files(p["sample"], sc[sid])

    # day 0: one happy path + the four edge cases (scenario 1 first so E3 sees the shared account)
    order = ["1_happy_path", "E1_proprietor", "E3_shared_bank", "E3b_blocklisted_bank", "E4_resubmission"]
    for i, sid in enumerate(order, 1):
        demo._write_item(p["inbox"], f"{i:02d}_{sid}", {"type": "NEW_SUBMISSION", "submission": sc[sid]["form"]}, files(sid))
    refs = {r["item"].split("_", 1)[1]: r["ref"] for r in demo._process(conn, p)}
    happy, e4 = refs["1_happy_path"], refs["E4_resubmission"]
    happy_form = sc["1_happy_path"]["form"]
    vid = db.one(conn, "SELECT v.vendor_id AS vid FROM vendors v JOIN submissions s ON s.vendor_id=v.id WHERE s.id=?",
                 (int(happy[-5:]),))["vid"]

    # day 1: E2 bank-details change on the approved happy-path vendor
    c.set((dt.datetime.fromisoformat(demo.START) + dt.timedelta(days=1)).isoformat())
    demo._write_item(p["inbox"], "11_E2_change", {"type": "CHANGE_REQUEST", "vendor_id": vid, "from": happy_form["email"],
                     "change_type": "bank details", "reason": "switching bank",
                     "new_values": {"bank_holder": happy_form["legal_name"], "account_number": "99887766554433",
                                    "bank_name": "ICICI Bank", "ifsc": "ICIC0005500"}},
                     {"BANK_PROOF": demo._pdf_bytes(p["uploads"], "e2_newbank.pdf",
                      demo._cheque("ICICI Bank", happy_form["legal_name"], "99887766554433", "ICIC0005500"))})
    demo._process(conn, p)

    # day 2: E4 first reply (right holder, wrong IFSC) and the reviewer approves the E2 change
    c.set((dt.datetime.fromisoformat(demo.START) + dt.timedelta(days=2)).isoformat())
    co = sc["E4_resubmission"]["form"]
    demo._write_item(p["inbox"], "21_E4_reply1", {"type": "VENDOR_REPLY", "ref": e4, "from": co["email"]},
                     {"BANK_PROOF": demo._pdf_bytes(p["uploads"], "e4_run2.pdf",
                      demo._cheque("HDFC Bank", co["legal_name"], co["account_number"], "HDFC0002299"))})
    demo._write_item(p["inbox"], "22_E2_approve", {"type": "REVIEWER_DECISION", "ref": happy,
                     "from": "reviewer@kyvo.example", "command": "APPROVE: new bank account confirmed with the vendor"})
    demo._process(conn, p)

    # day 4: E4 clean cheque -> approved
    c.set((dt.datetime.fromisoformat(demo.START) + dt.timedelta(days=4)).isoformat())
    demo._write_item(p["inbox"], "31_E4_reply2", {"type": "VENDOR_REPLY", "ref": e4, "from": co["email"]},
                     {"BANK_PROOF": demo._pdf_bytes(p["uploads"], "e4_run3.pdf",
                      demo._cheque("HDFC Bank", co["legal_name"], co["account_number"], "HDFC0002200"))})
    demo._process(conn, p)

    outputs.write_ledger(conn, p["out"])
    outputs.write_digest(conn, p["out"], dt.date.fromisoformat(demo.START[:10]).isoformat())
    clock.uninstall()

    evaluation.run(conn, p["sample"], p["out"])
    site_build.build(conn, p["out"])
    shutil.rmtree(public, ignore_errors=True)
    shutil.copytree(os.path.join(p["out"], "site"), public)
    # the interactive upload page (served by the api/verify.py serverless function) and its sample PDFs
    shutil.copy(os.path.join(HERE, "web", "verify.html"), os.path.join(public, "verify.html"))
    samples = os.path.join(public, "samples")
    os.makedirs(samples, exist_ok=True)
    for name in ("m_pan.pdf", "m_gst.pdf", "m_reg.pdf", "m_bank.pdf", "b_bank.pdf"):
        src = os.path.join(HERE, "sample_data", name)
        if os.path.exists(src):
            shutil.copy(src, os.path.join(samples, name))
    print(f"Built {public} ({len(os.listdir(os.path.join(public, 'sub')))} submission pages)")
    return public


if __name__ == "__main__":
    build()

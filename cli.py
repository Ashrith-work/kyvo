"""Kyvo command line. The same workflow as the automation and the tests.

Commands:
  demo                 reset a demo workspace, run the scripted end-to-end demo, print the timeline,
                       then run eval and publish.
  run                  process the inbox once (inbound items -> decisions, emails, out/<ref>/).
  watch --every 60     process the inbox on a loop.
  tick [--now DATE]    draft reminders, mark stale, escalate (time from --now or the system clock).
  verify PATH          verify one submission JSON and print decision.json (exit 0/1/2).
  status SUB-00012     print the current decision for a submission.
  eval                 extraction and decision accuracy, unsafe vs friction, degradation sweep.
  whatif --set k=v     re-run every scenario under a modified policy in a temporary workspace.
  publish              build the static report site under out/site/.
  policy --lock        regenerate policy/kyvo_policy.lock from the current policy file.
"""
import sys, os, json, argparse, time, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import database as db
import workflow as wf
import outputs
import inbox as ibx
import lifecycle
import config

EXIT = {"APPROVED": 0, "PENDING": 1, "REJECTED": 2}
DEFAULT_WS = os.environ.get("KYVO_WORKSPACE", os.path.join(os.path.dirname(os.path.abspath(__file__)), "workspace"))


def _open_ws(root, fresh=False):
    import demo
    return demo.open_ws(root, fresh=fresh)


# ---- verify (unchanged contract) -------------------------------------------------
def _load_submission(path):
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    base = os.path.dirname(os.path.abspath(path))
    files = {}
    for dtype, rel in (data.pop("documents", {}) or {}).items():
        fpath = rel if os.path.isabs(rel) else os.path.normpath(os.path.join(base, rel))
        with open(fpath, "rb") as fh:
            files[dtype] = (os.path.basename(fpath), fh.read())
    return data, files


def verify(path, db_path=None):
    """Verify one submission JSON; print decision.json and return 0 (approved), 1 (pending), 2 (rejected)."""
    conn = db.connect(db_path or os.path.join(tempfile.mkdtemp(prefix="kyvo_cli_"), "kyvo_cli.db"))
    wf.seed_demo(conn)
    form, files = _load_submission(path)
    res = wf.run_submission(conn, form, files, tempfile.mkdtemp(prefix="kyvo_up_"))
    rec = wf.decision_json(conn, res["submission_id"])
    print(json.dumps(rec, indent=2))
    return EXIT.get(rec["status"], 1)


def cmd_verify(args):
    return verify(args.path, args.db)


def cmd_run(args):
    conn, p = _open_ws(args.workspace)
    res = ibx.process_inbox(conn, p["inbox"], p["out"], p["uploads"], p["outbox"])
    outputs.write_ledger(conn, p["out"])
    outputs.write_digest(conn, p["out"])
    print(json.dumps(res, indent=2))
    return 0


def cmd_watch(args):
    conn, p = _open_ws(args.workspace)
    print(f"Watching {p['inbox']} every {args.every}s. Ctrl-C to stop.")
    try:
        while True:
            res = ibx.process_inbox(conn, p["inbox"], p["out"], p["uploads"], p["outbox"])
            if res:
                outputs.write_ledger(conn, p["out"])
                print(json.dumps(res))
            time.sleep(args.every)
    except KeyboardInterrupt:
        return 0


def cmd_tick(args):
    conn, p = _open_ws(args.workspace)
    rows = lifecycle.tick(conn, now=args.now, outbox_dir=p["outbox"], out_root=p["out"])
    for r in rows:
        print(f"{r['ref']}  day {r['days_open']}  {r['action']}")
    return 0


def cmd_status(args):
    conn, p = _open_ws(args.workspace)
    sid = ibx.sid_from_ref(args.ref)
    if not sid or not db.one(conn, "SELECT id FROM submissions WHERE id=?", (sid,)):
        print(f"No such submission: {args.ref}")
        return 1
    print(json.dumps(wf.decision_json(conn, sid), indent=2))
    return 0


def cmd_demo(args):
    import demo
    out = demo.run_demo(args.workspace)
    print(demo.format_timeline(out["timeline"]))
    conn, p = out["conn"], out["paths"]
    try:
        import evaluation
        ev = evaluation.run(conn, p["sample"], p["out"])
        print("\nEval:", json.dumps(ev["summary"], indent=2))
    except Exception as e:
        print(f"\n(eval skipped: {e})")
    try:
        import site_build
        site_build.build(conn, p["out"])
        print(f"Report site: {os.path.join(p['out'], 'site', 'index.html')}")
    except Exception as e:
        print(f"(publish skipped: {e})")
    return 0


def cmd_eval(args):
    import evaluation
    conn, p = _open_ws(args.workspace)
    ev = evaluation.run(conn, p["sample"], p["out"])
    print(json.dumps(ev["summary"], indent=2))
    return 0


def cmd_whatif(args):
    import whatif
    overrides = dict(kv.split("=", 1) for kv in (args.set or []))
    print(json.dumps(whatif.run(overrides), indent=2))
    return 0


def cmd_publish(args):
    import site_build
    conn, p = _open_ws(args.workspace)
    path = site_build.build(conn, p["out"])
    print(f"Built {path}")
    return 0


def cmd_policy(args):
    if args.lock:
        lock = config.write_lock()
        print(f"Wrote policy/kyvo_policy.lock: {lock['version']}")
    else:
        ok, msg = config.lock_status()
        print(("ok: " if ok else "stale: ") + msg)
    return 0 if (args.lock or config.lock_status()[0]) else 1


def main(argv=None):
    pp = argparse.ArgumentParser(prog="cli.py", description="Kyvo vendor onboarding automation.")
    sub = pp.add_subparsers(dest="cmd", required=True)

    def ws(sp):
        sp.add_argument("--workspace", default=DEFAULT_WS, help="workspace directory (inbox, out, db, ...)")
        return sp

    ws(sub.add_parser("demo", help="run the scripted end-to-end demo")).set_defaults(fn=cmd_demo)
    ws(sub.add_parser("run", help="process the inbox once")).set_defaults(fn=cmd_run)
    w = sub.add_parser("watch", help="process the inbox on a loop"); ws(w)
    w.add_argument("--every", type=int, default=60); w.set_defaults(fn=cmd_watch)
    t = sub.add_parser("tick", help="reminders / stale / escalation"); ws(t)
    t.add_argument("--now", default=None, help="ISO date/time (default: system clock)"); t.set_defaults(fn=cmd_tick)
    v = sub.add_parser("verify", help="verify one submission JSON")
    v.add_argument("path"); v.add_argument("--db", default=None); v.set_defaults(fn=cmd_verify)
    s = sub.add_parser("status", help="show a submission's decision"); ws(s)
    s.add_argument("ref"); s.set_defaults(fn=cmd_status)
    ws(sub.add_parser("eval", help="extraction and decision accuracy")).set_defaults(fn=cmd_eval)
    wi = sub.add_parser("whatif", help="re-run scenarios under a modified policy");
    wi.add_argument("--set", action="append", metavar="key=value"); wi.set_defaults(fn=cmd_whatif)
    ws(sub.add_parser("publish", help="build the static report site")).set_defaults(fn=cmd_publish)
    po = sub.add_parser("policy", help="policy lock"); po.add_argument("--lock", action="store_true"); po.set_defaults(fn=cmd_policy)

    args = pp.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())

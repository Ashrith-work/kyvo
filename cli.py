"""Command-line entry point. Same workflow as the UI and the tests.

Usage:
    python cli.py verify path/to/submission.json [--db path/to.db]

The submission JSON holds the form fields plus a "documents" object mapping each expected
document type to a file path relative to the JSON file. Prints the decision JSON (decision.schema.json)
and exits 0 (APPROVED), 1 (PENDING) or 2 (REJECTED)."""
import sys, os, json, argparse, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import database as db
import workflow as wf

EXIT = {"APPROVED": 0, "PENDING": 1, "REJECTED": 2}

def load_submission(path: str):
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    base = os.path.dirname(os.path.abspath(path))
    docs = data.pop("documents", {}) or {}
    files = {}
    for dtype, rel in docs.items():
        fpath = rel if os.path.isabs(rel) else os.path.normpath(os.path.join(base, rel))
        with open(fpath, "rb") as fh:
            files[dtype] = (os.path.basename(fpath), fh.read())
    return data, files

def verify(path: str, db_path: str = None) -> int:
    conn = db.connect(db_path or os.path.join(tempfile.mkdtemp(prefix="kyvo_cli_"), "kyvo_cli.db"))
    up = tempfile.mkdtemp(prefix="kyvo_up_")
    form, files = load_submission(path)
    result = wf.run_submission(conn, form, files, up, actor="cli")
    record = wf.decision_json(conn, result["submission_id"])
    print(json.dumps(record, indent=2))
    return EXIT.get(record["status"], 1)

def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="cli.py", description="Run the Kyvo vendor-verification workflow on a submission JSON.")
    sub = p.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("verify", help="verify a submission JSON and print the decision")
    v.add_argument("path", help="path to the submission JSON")
    v.add_argument("--db", default=None, help="SQLite DB path (default: a throwaway temp DB). Use a shared path to exercise duplicate detection across runs.")
    args = p.parse_args(argv)
    if args.cmd == "verify":
        return verify(args.path, args.db)
    return 1

if __name__ == "__main__":
    sys.exit(main())

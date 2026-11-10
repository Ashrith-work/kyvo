"""Vercel Python serverless function: run the real Kyvo engine on uploaded documents.

POST JSON: {"scenario": "<id>"}  OR  {"form": {...}, "documents": {"PAN_PROOF": {"filename","b64"}, ...}}
Returns the stage-by-stage run, the decision (status, waiting_on, risk, assurance, reasons, requests)
and every check. PDF text documents are read directly; OCR (photos/scans) is unavailable in this
environment, so such documents are held for a reviewer, exactly as the policy requires.
"""
import os, sys, json, base64, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from http.server import BaseHTTPRequestHandler


def _run(data):
    import database as db
    import workflow as wf

    form = dict(data.get("form") or {})
    files = {}
    if data.get("scenario"):
        from sample_data import generate as gen
        sample = os.path.join(ROOT, "sample_data")
        scen = {s["id"]: s for s in gen.load_scenarios(sample)}
        s = scen[data["scenario"]]
        form = dict(s["form"])
        files = gen.scenario_files(sample, s)
    for dtype, f in (data.get("documents") or {}).items():
        if f and f.get("b64"):
            files[dtype] = (f.get("filename", f"{dtype}.pdf"), base64.b64decode(f["b64"]))

    conn = db.connect(os.path.join(tempfile.mkdtemp(), "v.db"))
    wf.seed_demo(conn)
    stages, sid = [], None
    for ev in wf.run_workflow(conn, form, files, tempfile.mkdtemp()):
        if ev["stage"] != "complete" and ev["status"] == "done":
            stages.append(ev["stage"])
        if ev.get("stage") == "complete":
            sid = ev["result"]["submission_id"]
    rec = wf.decision_json(conn, sid)
    checks = db.rows(conn, "SELECT label, status, severity, assurance, evidence, source FROM validation_results "
                           "WHERE submission_id=? AND run_no=1 ORDER BY id", (sid,))
    return {"stages": stages, "decision": rec, "checks": checks}


class handler(BaseHTTPRequestHandler):
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "content-type")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"ok": True, "post": "form+documents or scenario"}).encode())

    def do_POST(self):
        try:
            length = int(self.headers.get("content-length", 0))
            data = json.loads(self.rfile.read(length) or b"{}")
            out = _run(data)
            code = 200
        except Exception as e:
            out = {"error": f"{type(e).__name__}: {e}"}
            code = 400
        body = json.dumps(out).encode()
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

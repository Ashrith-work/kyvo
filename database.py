"""SQLite schema, helpers and audit events. Nothing is overwritten: decisions/validation runs are appended."""
import json, sqlite3, datetime as dt

SCHEMA = """
CREATE TABLE IF NOT EXISTS vendors(
  id INTEGER PRIMARY KEY, vendor_key TEXT UNIQUE, legal_name TEXT, region TEXT, country TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS submissions(
  id INTEGER PRIMARY KEY, vendor_id INTEGER REFERENCES vendors(id), snapshot_json TEXT,
  status TEXT DEFAULT 'IN_PROGRESS', run_no INTEGER DEFAULT 1, created_at TEXT);
CREATE TABLE IF NOT EXISTS vendor_identifiers(
  id INTEGER PRIMARY KEY, vendor_id INTEGER, submission_id INTEGER, id_type TEXT, id_value TEXT);
CREATE TABLE IF NOT EXISTS documents(
  id INTEGER PRIMARY KEY, submission_id INTEGER, expected_type TEXT, detected_type TEXT,
  classification_state TEXT, classification_score INTEGER, classification_evidence TEXT,
  filename TEXT, safe_path TEXT, size_bytes INTEGER, sha256 TEXT, page_count INTEGER,
  extraction_state TEXT, text_chars INTEGER, note TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS extracted_fields(
  id INTEGER PRIMARY KEY, submission_id INTEGER, document_id INTEGER, field TEXT, value TEXT,
  page INTEGER, evidence TEXT, method TEXT, confidence REAL, provenance TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS validation_results(
  id INTEGER PRIMARY KEY, submission_id INTEGER, run_no INTEGER, stage TEXT, rule_id TEXT, label TEXT,
  status TEXT, severity TEXT, blocking INTEGER, source TEXT, provenance TEXT, evidence TEXT,
  explanation TEXT, action TEXT, flag_code TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS risk_flags(
  id INTEGER PRIMARY KEY, submission_id INTEGER, run_no INTEGER, code TEXT, severity TEXT, details TEXT,
  resolution_state TEXT DEFAULT 'open', resolved_by TEXT, resolution_comment TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS decisions(
  id INTEGER PRIMARY KEY, submission_id INTEGER, run_no INTEGER, status TEXT, reasons_json TEXT,
  risk_level TEXT, next_action TEXT, human_review_required INTEGER, policy_version TEXT,
  decided_by TEXT, comment TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS communication_drafts(
  id INTEGER PRIMARY KEY, submission_id INTEGER, decision_id INTEGER, draft_type TEXT, to_addr TEXT,
  subject TEXT, body TEXT, sent INTEGER DEFAULT 0, created_at TEXT, edited_at TEXT);
CREATE TABLE IF NOT EXISTS audit_events(
  id INTEGER PRIMARY KEY, submission_id INTEGER, event TEXT, actor TEXT, details_json TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS policy_blocklist(
  id INTEGER PRIMARY KEY, id_type TEXT, id_value TEXT, reason TEXT, added_by TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS follow_up_requests(
  id INTEGER PRIMARY KEY, submission_id INTEGER, rule_id TEXT, about TEXT, ask TEXT,
  status TEXT DEFAULT 'OPEN', opened_run INTEGER, resolved_run INTEGER, created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS change_requests(
  id INTEGER PRIMARY KEY, vendor_id INTEGER, submission_id INTEGER, cr_type TEXT, new_value TEXT, reason TEXT,
  file_path TEXT, terms_accepted INTEGER, status TEXT DEFAULT 'PENDING', admin_comment TEXT,
  created_at TEXT, decided_at TEXT, decided_by TEXT);
CREATE TABLE IF NOT EXISTS payments(
  id INTEGER PRIMARY KEY, vendor_id INTEGER, reference_no TEXT, amount TEXT, status TEXT DEFAULT 'PENDING',
  transaction_id TEXT, payment_date TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS otp_tokens(
  id INTEGER PRIMARY KEY, vendor_id INTEGER, otp_hash TEXT, purpose TEXT, created_at TEXT, expires_at TEXT,
  used INTEGER DEFAULT 0, attempts INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS registration_steps(
  id INTEGER PRIMARY KEY, vendor_id INTEGER, step_no INTEGER, data_json TEXT, completed INTEGER DEFAULT 0, updated_at TEXT);
CREATE INDEX IF NOT EXISTS ix_ident ON vendor_identifiers(id_type, id_value);
CREATE INDEX IF NOT EXISTS ix_val ON validation_results(submission_id, run_no);
CREATE INDEX IF NOT EXISTS ix_fur ON follow_up_requests(submission_id, status);
"""

def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")

# Columns added after the first release. Guarded so an existing kyvo.db keeps opening.
MIGRATIONS = [
    ("submissions", "waiting_on", "TEXT"),
    ("submissions", "stale", "INTEGER DEFAULT 0"),
    ("decisions", "waiting_on", "TEXT"),
    ("vendors", "vendor_id", "TEXT"),
    ("vendors", "email", "TEXT"),
    ("vendors", "pan", "TEXT"),
    ("vendors", "gstin", "TEXT"),
    ("vendors", "reg_number", "TEXT"),
    ("vendors", "signup_region", "TEXT"),
    ("vendors", "signup_type", "TEXT"),
]

def migrate(conn):
    for table, col, decl in MIGRATIONS:
        have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if col not in have:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
    conn.commit()

def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    migrate(conn)
    conn.commit()
    return conn

def ref(sid: int) -> str:
    return f"SUB-{sid:05d}"

def insert(conn, table: str, **cols) -> int:
    keys = ",".join(cols)
    cur = conn.execute(f"INSERT INTO {table}({keys}) VALUES({','.join('?' * len(cols))})", list(cols.values()))
    conn.commit()
    return cur.lastrowid

def audit(conn, submission_id, event, actor="system", details=None):
    return insert(conn, "audit_events", submission_id=submission_id, event=event, actor=actor,
                  details_json=json.dumps(details or {}, default=str), created_at=now())

def rows(conn, sql, args=()):
    return [dict(r) for r in conn.execute(sql, args).fetchall()]

def one(conn, sql, args=()):
    r = conn.execute(sql, args).fetchone()
    return dict(r) if r else None

# ---- query helpers ---------------------------------------------------------------
LATEST_DECISION = """(SELECT d.* FROM decisions d WHERE d.id = (SELECT MAX(id) FROM decisions WHERE submission_id = d.submission_id))"""

def latest_decision(conn, sid):
    return one(conn, "SELECT * FROM decisions WHERE submission_id=? ORDER BY id DESC LIMIT 1", (sid,))

def overview_counts(conn) -> dict:
    out = {"total": conn.execute("SELECT COUNT(*) FROM submissions").fetchone()[0]}
    for s in ("APPROVED", "PENDING", "REJECTED"):
        out[s] = conn.execute(f"SELECT COUNT(*) FROM {LATEST_DECISION} x WHERE x.status=?", (s,)).fetchone()[0]
    out["open_flags"] = conn.execute(
        "SELECT COUNT(*) FROM risk_flags f JOIN submissions s ON s.id=f.submission_id "
        "WHERE f.resolution_state='open' AND f.run_no=s.run_no").fetchone()[0]
    return out

def list_submissions(conn, search: str = "", status: str = "") -> list:
    sql = f"""SELECT s.id, v.legal_name, v.region, v.country, s.created_at, x.status, x.risk_level,
                     x.decided_by, x.human_review_required, x.created_at AS decided_at, s.snapshot_json
              FROM submissions s JOIN vendors v ON v.id=s.vendor_id
              LEFT JOIN {LATEST_DECISION} x ON x.submission_id=s.id ORDER BY s.id DESC"""
    out = []
    for r in rows(conn, sql):
        r["ref"] = ref(r["id"])
        snap = json.loads(r.pop("snapshot_json") or "{}")
        hay = " ".join(str(v) for v in (r["ref"], r["legal_name"], r["country"], r["status"],
                                        snap.get("pan"), snap.get("gstin"), snap.get("tax_id"))).lower()
        if search and search.lower() not in hay:
            continue
        if status and r["status"] != status:
            continue
        out.append(r)
    return out

def effective_fields(conn, sid) -> dict:
    """{document_id: {field: row}} - latest row wins (a reviewer correction supersedes extraction)."""
    out = {}
    for r in rows(conn, "SELECT * FROM extracted_fields WHERE submission_id=? ORDER BY id", (sid,)):
        out.setdefault(r["document_id"], {})[r["field"]] = r
    return out

def find_duplicates(conn, sid, identifiers: list) -> list:
    hits = []
    for id_type, id_value in identifiers:
        if not id_value:
            continue
        for r in rows(conn, """SELECT i.submission_id, i.id_type, i.id_value, v.legal_name FROM vendor_identifiers i
                               JOIN vendors v ON v.id=i.vendor_id
                               WHERE i.id_type=? AND i.id_value=? AND i.submission_id<>?""", (id_type, id_value, sid)):
            d = latest_decision(conn, r["submission_id"])
            hits.append({**r, "ref": ref(r["submission_id"]), "prior_status": d["status"] if d else "IN_PROGRESS"})
    return hits

def find_shared_bank(conn, vendor_id, bank_idents: list) -> list:
    """Other vendors (not this one) whose stored BANK_ACCT identifier matches. Separate from find_duplicates on purpose."""
    hits = []
    for _id_type, id_value in bank_idents:
        for r in rows(conn, """SELECT DISTINCT i.vendor_id, i.id_value, v.legal_name,
                                      (SELECT MAX(s.id) FROM submissions s WHERE s.vendor_id=i.vendor_id) AS submission_id
                               FROM vendor_identifiers i JOIN vendors v ON v.id=i.vendor_id
                               WHERE i.id_type='BANK_ACCT' AND i.id_value=? AND i.vendor_id<>?""", (id_value, vendor_id)):
            d = latest_decision(conn, r["submission_id"]) if r["submission_id"] else None
            hits.append({**r, "ref": ref(r["submission_id"]) if r["submission_id"] else "-",
                         "prior_status": d["status"] if d else "IN_PROGRESS"})
    return hits

def blocklist_hits(conn, identifiers: list) -> list:
    hits = []
    for id_type, id_value in identifiers:
        if id_value:
            hits += rows(conn, "SELECT * FROM policy_blocklist WHERE id_type=? AND id_value=?", (id_type, id_value))
    return hits

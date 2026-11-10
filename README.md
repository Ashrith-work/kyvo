# Kyvo

[![CI](https://github.com/Ashrith-work/kyvo/actions/workflows/ci.yml/badge.svg)](https://github.com/Ashrith-work/kyvo/actions/workflows/ci.yml)

**Headless vendor onboarding automation for the Zamp PS-2 case study.**
Live report site: **https://kyvo-onboarding.vercel.app**

Kyvo is not an app. Vendor submissions arrive in an inbox, get verified, and the automation replies to
the vendor, waits, chases, re-verifies and escalates on its own. A person only steps in for judgement
calls. Everything it does is recorded, measured and published as a static report site.

It is modelled on a university vendor registration portal; the institution's name, branding, colours and
helpdesk are not used, and all data is fictional. Deterministic Python rules decide. No LLM and no API
key are required.

---

## What it does

A submission (a form plus PDF, JPG, PNG, DOC or DOCX documents, up to 1 MB each) is read, validated,
reconciled across documents, risk-checked, and turned into one of three statuses:

- **APPROVED** when every blocking check passes and the two-source rule is met.
- **PENDING** when anything is missing or uncertain, with the evidence and a request to the vendor or a
  reviewer.
- **REJECTED** only on a written policy blocklist hit, or a reviewer's rejection with a written reason.

Three rules never change: a fuzzy name difference never rejects; a low-confidence OCR value counts only
on an exact match with what the vendor typed; APPROVED requires two independent sources to agree on the
key identities.

The verification engine (`workflow.run_workflow`) runs intake, classification, extraction (pdfplumber
plus Tesseract OCR with per-line confidence, python-docx for Word), validation, cross-document
reconciliation, risk, decision, an editable email draft and an append-only SQLite audit trail. Every
inbound type goes through this one path.

---

## How it runs

```
python -m venv .venv && .venv\Scripts\activate        # Windows
pip install -r requirements.txt

python cli.py demo        # scripted end-to-end demo: timeline, eval, and the report site
python cli.py run         # process the inbox once
python cli.py watch --every 60
python cli.py tick --now 2026-02-16
python cli.py verify examples/1_happy_path.json       # exit 0 approved, 1 pending, 2 rejected
python cli.py status SUB-00012
python cli.py eval        # extraction and decision accuracy, unsafe vs friction, degradation sweep
python cli.py whatif --set name_similar=0.3
python cli.py publish     # build the static report site under out/site/
python -m pytest -q       # the test suite
```

`app.py` (Streamlit) is an optional viewer over the same engine and database, not the product. Install
`requirements-viewer.txt` for it; the automation needs only `requirements.txt`.

### The inbound protocol

Items arrive in `inbox/` as a folder (an `item.json` plus attachments) or a single `.eml` file.
Processed items move to `processed/`, failures to `failed/` with an error note; a bad item never blocks
the queue. Every item is hashed, so the same item processed twice never creates a second submission or
reply. Four types:

- **NEW_SUBMISSION** — a submission form and documents. Issues a Vendor ID (`V#####`) and sends an
  acknowledgement quoting it.
- **VENDOR_REPLY** — subject carries `[Kyvo SUB-00012]`; attachments and `Field: value` lines answer
  open requests. Applied only if the sender is the registered email, otherwise nothing changes and it
  escalates as a reply from an unregistered address.
- **CHANGE_REQUEST** — a Vendor ID, a change type (GST/PAN, bank details, organisation, services), new
  values, a reason and documents. A bank-details change is handled as E2 (below).
- **REVIEWER_DECISION** — from a reviewer on the policy allowlist; a subject token plus one command:
  `APPROVE`, `REJECT: <reason>`, or `REQUEST: <ask>`. Anyone else's commands are ignored and logged, and
  `REJECT` without a reason is refused.

Outbound mail defaults to an outbox: every message is written as a `.eml` file and never sent. SMTP
sending happens only with `KYVO_SEND=1` and the `SMTP_*` environment variables, and never to a demo
(`.example`) address. An optional IMAP adapter (off by default) reads a real mailbox using
`IMAP_HOST`/`IMAP_USER`/`IMAP_PASSWORD`.

### The decision contract

Every run writes `out/<ref>/`:

- `decision.json` (valid against `schemas/decision.schema.json`): `status`, `waiting_on`
  (VENDOR / REVIEWER / NONE), `risk_level`, `policy_version`, `run_no`, `decided_at`, `payment_hold`,
  `assurance` (per identifier), `reasons[]`, `requests_to_vendor[]`, `next_action`.
- `report.md` (the same decision for a person), `emails/` (every outgoing message as `.eml`),
  `audit.jsonl` (append-only events).

Plus `out/digest-<date>.md` and `out/ledger.csv`, and the published site under `out/site/`.

---

## Assurance levels and the two-source rule

Every check result carries an assurance level:

- **FORMAT** — syntax and checksums, or two fields of the same form agreeing.
- **CONSISTENCY** — two independent sources agree (form vs document, or document vs document).
- **NOT_VERIFIED** — needs an outside registry Kyvo does not call (GST status, PAN-to-name, bank account
  ownership, sanctions).

`decision.json` lists, per identifier (PAN, GSTIN, company registration, bank account, TRC), the highest
level reached and what was not verified. **The two-source rule:** APPROVED requires CONSISTENCY on PAN,
legal name and bank account (international: registration number, legal name, bank account). Kyvo states
plainly what it does not verify, rather than implying a registry check it never made.

---

## Policy in one file

Every threshold and rule switch lives in `policy/kyvo_policy.yaml`: name similarity bands, OCR confidence
gates, upload limits, reminder and stale days, risk weights, the reviewer allowlist, the IFSC bank-code
table and free-mail domains. Each entry records its value, unit, why it is set there, the team that would
own it (procurement, finance or compliance), and what changes if it is raised or lowered. `config.py`
loads it; nothing tunable is hard-coded.

The policy version stored on every decision is `KYVO-POLICY-<semver>+<sha256[:12]>` of the policy file.
`policy/kyvo_policy.lock` pins the hash, and a test fails if the file changes without the semver being
bumped (`python cli.py policy --lock` regenerates it). `policy/flags.yaml` holds one entry per flag code
(severity, blocking, waiting_on, assurance, the vendor message and the reviewer step), so the vendor
message and the reviewer instruction always come from one source.

`python cli.py whatif --set name_similar=0.3` re-runs every scenario under a modified policy in a
temporary workspace and prints what changes. Lowering the name similarity threshold reclassifies E4's
material bank-holder mismatch as a minor variant, dropping its risk from HIGH to LOW; the real policy and
workspace are untouched.

---

## Scenarios

### Baseline checks (1 to 11)

Happy path; wrong document; bank account holder mismatch; country / tax-ID scheme conflict; duplicate
PAN; domestic PAN without GST; international (US EIN); unsupported country rule; scanned bank proof too
poor to read; phone-photo documents read by OCR; a blurred photo held as unreadable.

### Edge cases

**E1. Sole proprietor paid into the owner's personal account.**
*Situation:* the firm is "Sri Varahi Traders", the proprietor is "Ravi Kumar Reddy"; the PAN and the GST
legal name are the proprietor, the bank account is in the proprietor's name, and the firm sits on the GST
trade-name line.
*Why naive automation fails:* it compares the firm name to the bank holder and flags a mismatch.
*What Kyvo does:* on a proprietorship with a PAN 4th character of P it compares the proprietor name to
the PAN, GST legal name and bank holder, and the firm name to the GST trade name.
*Outcome:* APPROVED, with evidence that the account is in the proprietor's own name. A third-party holder
still gives PENDING.

**E2. Bank change after approval (the common payment-fraud route).**
*Situation:* an approved vendor sends a bank-details change request with a new account.
*Why naive automation fails:* it updates the account and keeps paying.
*What Kyvo does:* payment_hold turns on at once, new bank proof is required, all bank checks re-run, the
new account is checked against every other vendor, and the confirmation goes to the previously registered
email, not the sender; a request from any other address raises risk to HIGH.
*Outcome:* always PENDING, waiting on a reviewer, never auto-approved; the hold lifts only on a reviewer
approval, and the old values stay in the audit trail.

**E3. One bank account, two vendors.**
*Situation:* a new vendor with a different PAN uses an IFSC and account already registered to another
vendor.
*Why naive automation fails:* it treats the two as unrelated.
*What Kyvo does:* a dedicated `XR_BANK_SHARED` check (separate from identifier duplicates) references both
vendors and routes to a reviewer. E3b: if that account is on the blocklist, it is REJECTED automatically
with the written reason.
*Outcome:* PENDING and HIGH risk (E3), or REJECTED (E3b).

**E4. A reply that fixes the asked item and breaks another.**
*Situation:* day 0 a bank-holder mismatch holds the submission; day 2 the vendor sends a cheque in the
right name but with a different IFSC; day 4 a clean cheque arrives.
*Why naive automation fails:* it either re-approves on the first reply or loses track of the open asks.
*What Kyvo does:* run 2 resolves the holder ask and opens an IFSC ask (still PENDING); run 3 approves.
The follow-up ledger records the whole sequence across the three runs.
*Outcome:* PENDING, then APPROVED.

---

## Evaluation

`python cli.py eval` writes `out/eval/eval.json` and `eval.md`:

- **Extraction accuracy** per field and per read method (PDF text, OCR), exact match after the same
  normalisation the rules use, against `sample_data/ground_truth.json`. Fields a document does not print
  are excluded and listed.
- **Decision accuracy**: the expected status versus the actual one, per scenario.
- **Errors split in two, never blended**: unsafe errors (approved when it should not be, which must be
  zero) and friction errors (held when it should have passed).
- **Degradation sweep**: the PAN card and bank proof are re-rendered as photos at five blur levels and
  three resolutions and run through the full workflow. As reading degrades, outcomes move from APPROVED
  to PENDING, never to a wrong APPROVED. Unsafe errors stay at zero.

Tesseract OCR is optional. Without it, photo and scan documents are held for a reviewer rather than
guessed at, so the scanned and phone-photo scenarios are PENDING and the eval reports it.

---

## Connecting a real mailbox

By default nothing leaves the machine. To read a real inbox, set `IMAP_HOST`, `IMAP_USER` and
`IMAP_PASSWORD` and fetch with the IMAP adapter; to actually send, set `KYVO_SEND=1` and the `SMTP_*`
variables. Kyvo still refuses to send to `.example` and other never-send domains. Reviewer commands are
only honoured from the addresses on the policy allowlist.

---

## What it does not verify

Kyvo performs format and consistency checks only. It does not confirm a PAN or GSTIN against a government
registry, it does not establish bank-account ownership, and it does not run a sanctions check. Every
decision says so, and the per-identifier assurance makes the limits explicit. These are deliberate
boundaries, not missing features.

---

## Layout

| Area | Files |
| --- | --- |
| Policy | `policy/kyvo_policy.yaml`, `policy/flags.yaml`, `policy/blocklist.yaml`, `policy/kyvo_policy.lock` |
| Engine | `config.py`, `extraction.py`, `validation.py`, `assurance.py`, `decision_engine.py`, `workflow.py`, `communication.py`, `database.py` |
| Automation | `inbox.py`, `mailer.py`, `clock.py`, `lifecycle.py`, `outputs.py`, `demo.py`, `cli.py` |
| Measurement and site | `evaluation.py`, `whatif.py`, `site_build.py`, `build_site.py` |
| Contracts and data | `schemas/`, `examples/`, `sample_data/` (with `ground_truth.json`) |
| Optional viewer | `app.py`, `portal.py` (`requirements-viewer.txt`) |

MIT licensed. All names, identifiers and documents are fictional test data.

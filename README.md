# Kyvo — vendor onboarding automation (Zamp PS-2 case study)

[![CI](https://github.com/Ashrith-work/kyvo/actions/workflows/ci.yml/badge.svg)](https://github.com/Ashrith-work/kyvo/actions/workflows/ci.yml)

**Website: [ashrith-work.github.io/kyvo](https://ashrith-work.github.io/kyvo/)** — project overview, pipeline, decision policy and demo scenarios.

[![Kyvo website](docs/screenshot.png)](https://ashrith-work.github.io/kyvo/)

Independent build for the Zamp PS-2 case study. It is modelled on a university vendor registration portal; the institution's name, branding and helpdesk are not used, and all data is fictional. Kyvo takes a vendor submission (form plus documents) and returns **APPROVED / PENDING / REJECTED** with visible reasoning, evidence, a vendor-facing request list and an audit trail. Deterministic Python rules decide; no LLM and no API key are needed.

## Run (Windows PowerShell)
```powershell
cd kyvo
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python sample_data\generate.py        # regenerates the fictional scenarios + examples (also auto-built on first start)
streamlit run app.py                  # opens http://localhost:8501
python -m pytest -q                   # 69 tests, offline (2 OCR tests skip if Tesseract is missing)
```
macOS/Linux: use `source .venv/bin/activate` instead of the Activate line. A fresh SQLite database is created at `data/kyvo.db` on first start (sidebar → *Demo database* → reset). Environment overrides: `KYVO_DB`, `KYVO_UPLOADS`.

### Command line
```powershell
python cli.py verify examples\1_happy_path.json      # prints the decision JSON; exit 0 APPROVED, 1 PENDING, 2 REJECTED
python cli.py verify examples\E3_shared_bank.json --db data\kyvo.db   # --db shares state so duplicate / shared-bank checks see prior vendors
```
Each file in `examples/` is one submission per scenario, with document paths relative to the file.

### Demo controls
The sidebar has a demo **mode switch** (no real authentication):
* **Vendor portal** — sign-up (issues a Vendor ID `V#####`), login with a simulated OTP shown in an on-screen demo inbox, four-step registration, a dashboard, change requests and payment status.
* **Admin review** — the verification pages: Overview, New Submission, Live Run, Verification Detail, Review Queue, Change requests, Reminders, History.

## How it works
`workflow.run_workflow` is the single verification path used by the UI, the bundled scenarios, the CLI, vendor responses and change requests. It runs ten stages: Submission received → Document intake → Classification → Field extraction → Validation → Cross-document reconciliation → Risk assessment → Decision → Email draft → Audit persistence. The Live Run page renders each stage as it happens.

| File | Role |
|---|---|
| `app.py` | Streamlit UI: vendor portal and admin review |
| `portal.py` | Sign-up, simulated OTP, four-step registration, change requests, payments |
| `workflow.py` | Stage orchestration, re-run, vendor responses, reminders, reviewer actions |
| `extraction.py` | pdfplumber / python-docx text, OCR for photos and scans, keyword classifier, regex field extraction |
| `validation.py` | Conditional fields, India rules, country registry, cross-document reconciliation, subtle cross-checks, duplicates, shared bank, blocklist |
| `decision_engine.py` | Deterministic three-status policy (`KYVO-POLICY-1.1`), `waiting_on` and the decision record |
| `communication.py` | Email and reminder drafts (`sent=0`, never auto-sent) |
| `database.py` / `config.py` | SQLite schema, migrations and audit; policy, patterns, country and cross-check tables |
| `schemas/` | `submission.schema.json`, `decision.schema.json` |
| `sample_data/`, `examples/`, `tests/` | Generator + scenarios; example submissions; unit and end-to-end tests |

## Decision policy
* **APPROVED** — only if every blocking check is PASS (all applicable fields and documents, types match, formats pass, cross-checks agree, no duplicate, no shared account).
* **PENDING** — any FAIL/WARN/NOT_CHECKED that blocks approval (missing/wrong/unreadable document, unsupported country, invalid format, name or bank mismatch, duplicate, shared account, uncertain extraction, subtle cross-check mismatch, bank change).
* **REJECTED** — only a hit on the written `policy_blocklist` (empty by default) or a reviewer's confirmed rejection with a mandatory comment. A fuzzy name difference never rejects; a low-confidence OCR value counts only on an exact match.

`waiting_on` is **VENDOR** when every blocking item has an open request, **REVIEWER** when any needs human judgement (similar names, duplicates, shared bank account, bank change, blocklist; REJECTED also routes to a reviewer), and **NONE** when approved.

---

# Design decisions

## Submission shape
The submission follows the portal manual and is defined in `schemas/submission.schema.json`. Sign-up chooses a region (Domestic or International). Domestic collects a vendor type (PAN with GST / PAN without GST), PAN, GSTIN (only for PAN with GST) and email; International collects a business registration number, a TRC number and email. Registration then has four steps: organisation (firm name, ownership, proprietor name when the ownership is Proprietorship, address, state, city, PIN code, contact details), bank (holder, account number, IFSC or SWIFT, bank name, and a cancelled cheque or bank letter), services (category and description), and documents (PAN card, GST certificate, company registration, and for International the tax registration certificate and the TRC) plus terms. The manual's field names are mapped onto Kyvo's internal keys (for example firm name to `legal_name`, ownership to `entity_type`, company phone to `phone`), and the older keys keep working.

## Rules
| Rule id | What it checks | On a problem |
|---|---|---|
| `REQ_*` | Required fields for the region and type | FAIL, blocking |
| `FMT_PAN` / `FMT_GSTIN` / `FMT_IFSC` / `FMT_PINCODE` / `FMT_SWIFT` | Identifier and PIN formats (syntax only) | FAIL, blocking |
| `FMT_GSTIN_CHECK` | GSTIN check character | WARN, blocking |
| `XF_GSTIN_PAN` | PAN embedded in GSTIN vs entered PAN | FAIL, blocking |
| `VAL_TAXID_COUNTRY` | Foreign tax ID scheme vs claimed country | FAIL or NOT_CHECKED |
| `DOC_*` | Required document present, correct type, readable | FAIL/WARN, blocking |
| `XN_*` / `XD_*` | Names and identifiers: form vs each document | PASS/WARN/FAIL |
| `XB_HOLDER_*` | Bank holder vs legal (or proprietor) name | WARN/FAIL |
| `XB_GSTIN_STATE` | GSTIN state code vs billing state | WARN, blocking |
| `XB_PINCODE_STATE` | PIN code postal zone vs state | WARN, blocking |
| `XB_IFSC_BANK` | IFSC bank code vs bank name (form and proof) | WARN, blocking; unknown code NOT_CHECKED |
| `FREE_EMAIL_DOMAIN` | Free email domain for a company or LLP | LOW, non-blocking note |
| `DUP_*` | Duplicate PAN/GSTIN/registration/tax ID | WARN, blocking |
| `XR_BANK_SHARED` | Bank account registered to another vendor | WARN, blocking, reviewer |
| `XR_BANK_CHANGE` | A bank-account change after approval | WARN, blocking, reviewer |
| `BLOCK_*` | Identifier or account on the policy blocklist | FAIL, REJECTED |
| `LOW_CONFIDENCE_OCR` | OCR value read below the confidence gate and not an exact match | WARN, blocking |

## Output contract
Every run produces a decision matching `schemas/decision.schema.json`: `submission_id`, `vendor_id`, `status`, `waiting_on`, `risk_level`, `policy_version`, `run_no`, `decided_at`, `reasons[]` (each with `rule_id`, `label`, `status`, `severity`, `evidence`, `source`, `provenance`), `requests_to_vendor[]` (each with `id`, `about`, `ask`, `status`), and `next_action`. The CLI prints this JSON, and the Verification Detail page has a *Download decision JSON* button.

## Follow-up loop
Every blocking result that carries a vendor-facing message opens a row in `follow_up_requests`. The email draft lists only the open items. A vendor reply (`workflow.vendor_response`) stores the corrected values, re-runs the full workflow at the next run number, resolves requests whose rule now passes and opens any new ones. `reminders_due` drafts a reminder on day 3 and day 7 after the oldest open request, and after day 14 marks the submission stale and recommends closing it as rejected (incomplete); closing still needs a reviewer and a comment. History is append-only.

## Edge cases

**E1 — sole proprietor paid into the owner's personal account.** *Situation:* the firm is a trade name, the proprietor's PAN has a 4th character of P, the GST certificate shows the proprietor as the legal name and the firm as the trade name, and the bank account is in the proprietor's name. *Why naive automation gets it wrong:* comparing the firm name against the bank holder reads this as a bank-name mismatch and holds a legitimate vendor. *What Kyvo does:* when the ownership is Proprietorship and the PAN 4th character is P, it compares the proprietor name (not the firm name) against the PAN card, the GST legal name and the bank holder, and compares the firm name against the GST trade name. A proprietorship whose bank holder is a third person still goes to PENDING. *Outcome:* APPROVED, with evidence that the account is in the proprietor's own name and matches the PAN card.

**E2 — bank account change requested after approval.** *Situation:* an approved vendor files a bank-details change with a new account. *Why naive automation gets it wrong:* silently updating the account is the most common payment-fraud route. *What Kyvo does:* payments go on hold as soon as the request is filed; a new bank proof is required; every bank check and the shared-account check re-run; a confirmation draft goes to the previously registered email, not the one in the request; the result is always PENDING with `waiting_on` REVIEWER and is never auto-approved. Old details stay in the audit trail, and the hold lifts only on a reviewer's approval. *Outcome:* PENDING, reviewer.

**E3 — one bank account, two vendors.** *Situation:* a new vendor with a different PAN uses an IFSC and account number already registered to another vendor. *Why naive automation gets it wrong:* identifier duplicate checks look at PAN/GSTIN, so a shared bank account slips through. *What Kyvo does:* a dedicated rule `XR_BANK_SHARED`, separate from the identifier duplicates, references both vendors and routes to a reviewer. *Outcome:* PENDING, HIGH risk, reviewer. **E3b:** if that account is on the policy blocklist with the reason "rejected for payment fraud", the outcome is REJECTED automatically with that written reason.

**E4 — a resubmission that fixes the asked item and breaks another.** *Situation:* a vendor is PENDING for a bank-holder mismatch and replies with a new cancelled cheque in the right name, but the new cheque carries a different IFSC from the form. *Why naive automation gets it wrong:* marking the case resolved because the asked item is fixed misses the new inconsistency. *What Kyvo does:* the holder request resolves, an IFSC request opens, and the status stays PENDING; a second, clean cheque gives APPROVED. The request ledger records all three runs. *Outcome:* PENDING then APPROVED.

## Baseline checks (scenarios 1 to 11)
| # | Scenario | Expected |
|---|---|---|
| 1 | Domestic PAN+GST, everything consistent | APPROVED |
| 2 | Invoice uploaded as GST certificate | PENDING (WRONG_DOCUMENT) |
| 3 | Bank holder differs from legal name | PENDING (BANK_HOLDER_MISMATCH) |
| 4 | Germany + Indian PAN-format tax ID (with TRC) | PENDING (COUNTRY_TAX_CONFLICT, no India rules applied) |
| 5 | Duplicate PAN/GSTIN (run after #1) | PENDING (DUPLICATE_IDENTIFIER) |
| 6 | Domestic PAN without GST (LLP) | APPROVED, no GST document requested |
| 7 | International, US EIN (with TRC) | APPROVED, no PAN/GST/IFSC required |
| 8 | Brazil (no rule configured, with TRC) | PENDING (NOT_CHECKED, never a false pass) |
| 9 | Scanned bank proof too poor to read | PENDING (UNREADABLE_DOCUMENT) |
| 10 | PAN card and bank proof as phone photos | APPROVED, read by OCR |
| 11 | Blurred, distant photo of the PAN card | PENDING (UNREADABLE_DOCUMENT) |

## Edge cases (bundled)
| id | Scenario | Expected |
|---|---|---|
| E1 | Sole proprietor paid into the owner's account | APPROVED |
| E3 | One bank account, two vendors (run after #1) | PENDING (BANK_SHARED, reviewer) |
| E3b | Shared account on the policy blocklist | REJECTED (payment fraud) |
| E4 | Resubmission fixes one item, breaks another | PENDING (run 1), driven to APPROVED |

E2 is exercised through the vendor portal (file a Bank details change request, approve it as admin, apply the change) and in `tests/test_edge_cases.py`.

## Photos and scans (OCR)
Install Tesseract to read photos and scanned PDFs: `apt-get install tesseract-ocr` (Linux), `brew install tesseract` (macOS), or the UB Mannheim installer on Windows; `packages.txt` installs it on Streamlit Community Cloud. A document read below 0.60 mean confidence is held as unreadable. A value read below 0.80 counts only if it equals the vendor-entered value exactly, otherwise it is a blocking warning (LOW_CONFIDENCE_OCR), never a pass or a fail. PAN, GSTIN and IFSC letter/digit swaps are repaired by position only when the result passes the format check (and the GSTIN checksum). Without Tesseract, photos and scans fall back to manual review.

## Assumptions
* The product is named Kyvo and the policy label is `KYVO-POLICY-1.1`; this is a label, the decision behaviour is unchanged.
* Modelled on a university vendor registration portal; the institution's name, branding, colours, helpdesk email and phone numbers are not used. All data is fictional.
* Forgot Vendor ID looks up a Domestic vendor by registered GSTIN, or by PAN for a "PAN without GST" vendor, and an International vendor by business registration number.
* The OTP is simulated and shown in an on-screen demo inbox; no email is ever sent and no copy says one was. Sign-up issues the Vendor ID; the OTP is verified at login. The CAPTCHA is a demo checkbox.
* Uploads accept PDF, JPG, PNG, DOC and DOCX, up to 1 MB each. `.docx` is read with python-docx; `.doc` is treated as unreadable and held for a reviewer.
* A TRC (number and certificate) is required for every International vendor.
* The bank account identity is the IFSC or SWIFT code plus the account number; shared-account detection is cross-vendor only, so a vendor resubmitting with the same account is not flagged as sharing.
* The GitHub and Pages URLs point at `…/kyvo`; rename the repository for the badge and links to resolve.
* The CLI uses a throwaway database by default. Scenarios that depend on prior or seeded state (5, E3, E3b) need a shared `--db` or the app, which seeds the blocklisted account on start.

## Limitations
* Format and consistency checks only: no verification of GST/PAN registry status, sanctions, legal existence or bank-account ownership.
* The country registry covers US, UK, DE, SG, AE (plus India for domestic). Any other country is NOT_CHECKED and goes to PENDING.
* Classification and extraction are keyword and regex based and tuned to the synthetic documents; real-world layouts need more patterns.
* The duplicate policy is strict by design: any earlier submission with the same identifier blocks auto-approval, including legitimate resubmissions.
* Email and reminder drafts are never sent. The portal is a single-user demo with no authentication or roles, and payments, change requests and OTP use fictional seeded data. Not production hardened.

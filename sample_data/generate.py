"""Generates FICTIONAL vendor submissions + PDFs for the demo scenarios (no real data).
Usage: python sample_data/generate.py   (writes into sample_data/ : pdfs + scenarios.json)"""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
from PIL import Image, ImageDraw
from validation import gstin_checksum

def gstin(state, pan, ent="1"):
    base = f"{state}{pan}{ent}Z"
    return base + gstin_checksum(base)

def make_pdf(path, lines, scanned=False):
    c = canvas.Canvas(path, pagesize=A4)
    w, h = A4
    if scanned:   # image-only PDF: no text layer, simulates a scan
        img = Image.new("RGB", (1240, 1754), "white"); d = ImageDraw.Draw(img)
        y = 120
        for ln in lines:
            d.text((100, y), ln, fill="black"); y += 28
        c.drawImage(ImageReader(img), 0, 0, width=w, height=h)
    else:
        y = h - 70
        for i, ln in enumerate(lines):
            c.setFont("Helvetica-Bold" if i == 0 else "Helvetica", 15 if i == 0 else 11)
            c.drawString(60, y, ln); y -= 22
    c.showPage(); c.save()

def pan_doc(co):   return ["INCOME TAX DEPARTMENT", "GOVT. OF INDIA", "Permanent Account Number Card", f"PAN: {co['pan']}", f"Name: {co['legal']}", "Date of Incorporation: 12/03/2015"]
def gst_doc(co):   return ["Government of India", "FORM GST REG-06", "Registration Certificate", "(Goods and Services Tax)", f"GSTIN: {co['gstin']}", f"Legal Name: {co['legal']}", f"Trade Name: {co['trade']}", f"PAN: {co['pan']}", f"Address: {co['address']}", "Date of Liability: 01/07/2017"]
def reg_doc(co):   return ["CERTIFICATE OF INCORPORATION", "Ministry of Corporate Affairs - Registrar of Companies", f"Corporate Identification Number (CIN): {co['reg']}", f"Company Name: {co['legal']}", f"Registered Office: {co['address']}"]
def llp_doc(co):   return ["CERTIFICATE OF INCORPORATION", "Limited Liability Partnership Act - Ministry of Corporate Affairs", f"LLPIN: {co['reg']}", f"LLP Name: {co['legal']}", f"Registered Office: {co['address']}"]
def bank_doc(co, holder=None): return [f"{co['bank']}", "CANCELLED CHEQUE / BANK ACCOUNT CONFIRMATION LETTER", f"Bank: {co['bank']}", f"Account Holder: {holder or co['legal']}", f"Account Number: {co['acct']}", f"IFSC: {co['ifsc']}", "Branch: Main"]
def invoice_doc(co): return ["TAX INVOICE", "Invoice No: INV-2026-0098", "Bill To: Zenith Retail Pvt Ltd", f"GSTIN: {co['gstin']}", "Description    Qty    Unit Price    Amount", "Cold storage handling   200   450.00   90,000.00", "Total Amount Due: INR 1,06,200.00", "Due Date: 30/11/2026"]
def intl_reg(co):  return ["CERTIFICATE OF INCORPORATION", co["registry"], f"Company Name: {co['legal']}", f"Registration Number: {co['reg']}", f"Registered Office: {co['address']}"]
def intl_tax(co):  return ["TAX REGISTRATION CERTIFICATE", co["tax_authority"], f"Tax ID: {co['tax_id']}", f"Legal Name: {co['legal']}"]
def intl_bank(co): return [co["bank"], "BANK ACCOUNT CONFIRMATION LETTER", f"Bank: {co['bank']}", f"Account Holder: {co['legal']}", f"Account Number: {co['acct']}", f"SWIFT/BIC: {co.get('swift', '')}"]

COMMON = dict(terms_accepted=True, service_category="Manufacturing / Supply of goods", trading_name="")
def dom_form(co, with_gst=True, **kw):
    f = dict(COMMON, region="Domestic", domestic_type="PAN with GST" if with_gst else "PAN without GST", legal_name=co["legal"],
             trading_name=co["trade"], contact_person=co["contact"], email=co["email"], phone=co["phone"], address=co["address"],
             country="India", entity_type=co["entity"], pan=co["pan"], gstin=co.get("gstin", "") if with_gst else "", reg_number=co["reg"],
             bank_holder=co["legal"], account_number=co["acct"], bank_name=co["bank"], ifsc=co["ifsc"],
             service_description=co["services"])
    f.update(kw); return f
def intl_form(co, **kw):
    f = dict(COMMON, region="International", legal_name=co["legal"], trading_name="", contact_person=co["contact"], email=co["email"],
             phone=co["phone"], address=co["address"], country=co["country"], entity_type="Private Limited Company",
             tax_id=co["tax_id"], reg_number=co["reg"], trc_applicable=False, bank_holder=co["legal"], account_number=co["acct"],
             bank_name=co["bank"], bank_country=co["country"], swift=co.get("swift", ""), service_category="IT & software services",
             service_description=co["services"])
    f.update(kw); return f

def D(legal, trade, pan, state, reg, bank, ifsc, acct, contact, dom, city, ent="Private Limited Company", services="Supply of goods and services"):
    co = dict(legal=legal, trade=trade, pan=pan, reg=reg, bank=bank, ifsc=ifsc, acct=acct, contact=contact, entity=ent,
              email=f"{contact.split()[0].lower()}@{dom}", phone="+91 98450 12345", address=city, services=services)
    if state: co["gstin"] = gstin(state, pan)
    return co

def build(out_dir):
    os.makedirs(out_dir, exist_ok=True)
    meridian = D("Meridian Precision Components Private Limited", "Meridian Precision", "AABCM4821K", "29", "U29299KA2015PTC081234", "HDFC Bank", "HDFC0001234", "50200012345678", "Anita Rao", "meridianprecision.example", "Plot 14, Peenya Industrial Area, Bengaluru, Karnataka 560058", services="CNC machined components for automotive customers")
    aster = D("Aster Cold Chain Logistics Private Limited", "Aster Cold Chain", "AABCA7312P", "33", "U63090TN2018PTC123456", "ICICI Bank", "ICIC0001045", "004501234567", "Vikram Nair", "astercoldchain.example", "No. 8, Ambattur Estate, Chennai, Tamil Nadu 600058", services="Temperature-controlled logistics")
    bright = D("BrightPath Facility Services Private Limited", "BrightPath", "AAECB9054N", "27", "U74999MH2019PTC324567", "State Bank of India", "SBIN0004321", "31245678901", "Meera Joshi", "brightpath.example", "Unit 5, Andheri East, Mumbai, Maharashtra 400069", services="Facility management")
    kaveri = D("Kaveri Packaging Solutions LLP", "Kaveri Packaging", "AAGFK7351M", None, "AAB-1234", "Axis Bank", "UTIB0000456", "917020012345678", "Suresh Iyer", "kaveripack.example", "12, SIDCO Estate, Coimbatore, Tamil Nadu 641021", ent="LLP", services="Corrugated packaging")
    orchid = D("Orchid Textile Mills Private Limited", "Orchid Textiles", "AAACO6120D", "24", "U17110GJ2016PTC456789", "Kotak Mahindra Bank", "KKBK0001122", "7412345678", "Hetal Shah", "orchidtextiles.example", "Survey 41, GIDC Vatva, Ahmedabad, Gujarat 382445", services="Woven fabric supply")
    north = dict(legal="Northwind Analytics Inc.", country="United States", tax_id="84-3920157", reg="7741920", bank="JPMorgan Chase Bank", acct="000123456789", swift="CHASUS33", contact="Daniel Carter", email="daniel@northwindanalytics.example", phone="+1 302 555 0142", address="251 Little Falls Dr, Wilmington, DE 19808, USA", services="Data analytics consulting", registry="State of Delaware - Division of Corporations", tax_authority="Internal Revenue Service - Employer Identification Number (EIN) Confirmation")
    rhein = dict(north, legal="Rheinwerk Logistik GmbH", country="Germany", tax_id="AABCR5512L", reg="HRB123456", bank="Deutsche Bank AG", acct="DE89370400440532013000", swift="DEUTDEFF", contact="Jonas Weber", email="jonas@rheinwerk.example", phone="+49 30 5550142", address="Hafenstrasse 22, 20457 Hamburg, Germany", registry="Commercial Register Extract - Amtsgericht Hamburg", tax_authority="Finanzamt Hamburg - Tax Registration Certificate")
    verde = dict(north, legal="Verde Vale Exportadora Ltda", country="Brazil", tax_id="12.345.678/0001-95", reg="NIRE3520012345", bank="Banco do Brasil", acct="000123456789", swift="BRASBRRJ", contact="Camila Souza", email="camila@verdevale.example", phone="+55 11 5550142", address="Rua das Flores 100, Sao Paulo, Brazil", registry="Junta Comercial do Estado de Sao Paulo", tax_authority="Receita Federal - Tax Registration Certificate")

    scen, P = [], lambda name, lines, **k: (make_pdf(os.path.join(out_dir, name), lines, **k), name)[1]
    def add(id_, title, desc, expected, form, files, note=""):
        scen.append(dict(id=id_, title=title, description=desc, expected=expected, form=form, files=files, note=note))

    add("1_happy_path", "1. Happy path - domestic, PAN with GST", "Complete, consistent submission with all four matching documents.", "APPROVED",
        dom_form(meridian), dict(PAN_PROOF=P("m_pan.pdf", pan_doc(meridian)), GST_CERT=P("m_gst.pdf", gst_doc(meridian)), COMPANY_REG=P("m_reg.pdf", reg_doc(meridian)), BANK_PROOF=P("m_bank.pdf", bank_doc(meridian))))
    add("2_wrong_document", "2. Wrong document - invoice instead of GST certificate", "An invoice is uploaded in the GST certificate slot.", "PENDING",
        dom_form(aster), dict(PAN_PROOF=P("a_pan.pdf", pan_doc(aster)), GST_CERT=P("a_invoice_as_gst.pdf", invoice_doc(aster)), COMPANY_REG=P("a_reg.pdf", reg_doc(aster)), BANK_PROOF=P("a_bank.pdf", bank_doc(aster))))
    add("3_bank_mismatch", "3. Bank account holder differs from company", "Account holder is 'Rajesh Kumar Enterprises' (form and bank proof) but the legal name is BrightPath Facility Services.", "PENDING",
        dom_form(bright, bank_holder="Rajesh Kumar Enterprises"), dict(PAN_PROOF=P("b_pan.pdf", pan_doc(bright)), GST_CERT=P("b_gst.pdf", gst_doc(bright)), COMPANY_REG=P("b_reg.pdf", reg_doc(bright)), BANK_PROOF=P("b_bank.pdf", bank_doc(bright, holder="Rajesh Kumar Enterprises"))))
    add("4_country_tax_mismatch", "4. Country / tax-ID scheme conflict", "German vendor supplies an Indian PAN-format tax ID. German rules are selected; no India rules are applied.", "PENDING",
        intl_form(rhein), dict(COMPANY_REG=P("r_reg.pdf", intl_reg(rhein)), TAX_CERT=P("r_tax.pdf", intl_tax(rhein)), BANK_PROOF=P("r_bank.pdf", intl_bank(rhein))))
    add("5_duplicate_pan", "5. Duplicate PAN / GSTIN (run AFTER scenario 1)", "Same PAN and GSTIN as the happy-path vendor, name written with 'Pvt Ltd'.", "PENDING",
        dom_form(meridian, legal_name="Meridian Precision Components Pvt Ltd", bank_holder="Meridian Precision Components Pvt Ltd"),
        dict(PAN_PROOF=P("md_pan.pdf", pan_doc(meridian)), GST_CERT=P("md_gst.pdf", gst_doc(meridian)), COMPANY_REG=P("md_reg.pdf", reg_doc(meridian)), BANK_PROOF=P("md_bank.pdf", bank_doc(meridian))),
        note="Needs scenario 1 to have been run first (duplicate lookup is against the local DB).")
    add("6_pan_without_gst", "6. Domestic PAN without GST (LLP)", "No GST certificate is required or requested.", "APPROVED",
        dom_form(kaveri, with_gst=False, service_category="Manufacturing / Supply of goods"), dict(PAN_PROOF=P("k_pan.pdf", pan_doc(kaveri)), COMPANY_REG=P("k_reg.pdf", llp_doc(kaveri)), BANK_PROOF=P("k_bank.pdf", bank_doc(kaveri))))
    add("7_international_us", "7. International vendor - United States (EIN)", "Foreign vendor: EIN rule applied; IFSC/PAN/GST never required.", "APPROVED",
        intl_form(north), dict(COMPANY_REG=P("n_reg.pdf", intl_reg(north)), TAX_CERT=P("n_tax.pdf", intl_tax(north)), BANK_PROOF=P("n_bank.pdf", intl_bank(north))))
    add("8_unsupported_country", "8. Unsupported country rule - Brazil", "No automated rule for Brazil: NOT_CHECKED, never a false pass.", "PENDING",
        intl_form(verde), dict(COMPANY_REG=P("v_reg.pdf", intl_reg(verde)), TAX_CERT=P("v_tax.pdf", intl_tax(verde)), BANK_PROOF=P("v_bank.pdf", intl_bank(verde))))
    add("9_scanned_bank_proof", "9. Scanned (image-only) bank proof", "Bank proof PDF has no text layer; marked unreadable, not silently approved.", "PENDING",
        dom_form(orchid), dict(PAN_PROOF=P("o_pan.pdf", pan_doc(orchid)), GST_CERT=P("o_gst.pdf", gst_doc(orchid)), COMPANY_REG=P("o_reg.pdf", reg_doc(orchid)), BANK_PROOF=P("o_bank_scan.pdf", bank_doc(orchid), scanned=True)))
    with open(os.path.join(out_dir, "scenarios.json"), "w") as fh:
        json.dump(scen, fh, indent=1)
    return scen

def load_scenarios(out_dir):
    with open(os.path.join(out_dir, "scenarios.json")) as fh:
        return json.load(fh)

def scenario_files(out_dir, sc):
    out = {}
    for t, name in sc["files"].items():
        with open(os.path.join(out_dir, name), "rb") as fh:
            out[t] = (name, fh.read())
    return out

if __name__ == "__main__":
    d = os.path.dirname(os.path.abspath(__file__))
    print(f"Generated {len(build(d))} scenarios in {d}")

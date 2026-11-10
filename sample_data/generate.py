"""Generates FICTIONAL vendor submissions + PDFs for the demo scenarios (no real data).
Usage: python sample_data/generate.py   (writes into sample_data/ : pdfs + scenarios.json)"""
import os, sys, json, random
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from validation import gstin_checksum

def gstin(state, pan, ent="1"):
    base = f"{state}{pan}{ent}Z"
    return base + gstin_checksum(base)

def make_pdf(path, lines, scanned=False):
    c = canvas.Canvas(path, pagesize=A4)
    w, h = A4
    if scanned:   # image-only PDF, no text layer: a low-resolution, blurred scan that OCR cannot read reliably
        img = Image.new("RGB", (1240, 1754), "white"); d = ImageDraw.Draw(img)
        y = 120
        for ln in lines:
            d.text((100, y), ln, fill="black"); y += 28
        small = img.resize((img.width // 5, img.height // 5), Image.BILINEAR).filter(ImageFilter.GaussianBlur(1.2))
        img = small.resize(img.size, Image.BILINEAR)
        c.drawImage(ImageReader(img), 0, 0, width=w, height=h)
    else:
        y = h - 70
        for i, ln in enumerate(lines):
            c.setFont("Helvetica-Bold" if i == 0 else "Helvetica", 15 if i == 0 else 11)
            c.drawString(60, y, ln); y -= 22
    c.showPage(); c.save()

def make_photo(path, lines, seed=7, blur=0.0, scale=1.0):
    """A phone-style JPEG of a document: off-white paper, slight tilt, uneven light, sensor noise, JPEG compression.
    blur/scale degrade it further to simulate a shaky or distant shot."""
    rnd = random.Random(seed)
    W, H = 1600, 160 + 62 * len(lines)
    font, bold = ImageFont.load_default(size=36), ImageFont.load_default(size=44)
    img = Image.new("RGB", (W, H), (246, 244, 236)); d = ImageDraw.Draw(img)
    y = 90
    for i, ln in enumerate(lines):
        d.text((110, y), ln, fill=(28, 28, 32), font=bold if i == 0 else font); y += 70 if i == 0 else 58
    img = img.rotate(1.6, resample=Image.BICUBIC, expand=True, fillcolor=(120, 112, 98))
    px = img.load(); w, h = img.size
    for yy in range(0, h, 2):
        for xx in range(0, w, 2):
            shade, n = 1 - 0.10 * (xx / w), rnd.randint(-6, 6)
            px[xx, yy] = tuple(max(0, min(255, int(v * shade) + n)) for v in px[xx, yy])
    if scale != 1.0:
        img = img.resize((int(w * scale), int(h * scale)), Image.BILINEAR)
    if blur:
        img = img.filter(ImageFilter.GaussianBlur(blur))
    img.save(path, "JPEG", quality=82)

def pan_doc(co):   return ["INCOME TAX DEPARTMENT", "GOVT. OF INDIA", "Permanent Account Number Card", f"PAN: {co['pan']}", f"Name: {co['legal']}", "Date of Incorporation: 12/03/2015"]
def gst_doc(co):   return ["Government of India", "FORM GST REG-06", "Registration Certificate", "(Goods and Services Tax)", f"GSTIN: {co['gstin']}", f"Legal Name: {co['legal']}", f"Trade Name: {co['trade']}", f"PAN: {co['pan']}", f"Address: {co['address']}", "Date of Liability: 01/07/2017"]
def reg_doc(co):   return ["CERTIFICATE OF INCORPORATION", "Ministry of Corporate Affairs - Registrar of Companies", f"Corporate Identification Number (CIN): {co['reg']}", f"Company Name: {co['legal']}", f"Registered Office: {co['address']}"]
def llp_doc(co):   return ["CERTIFICATE OF INCORPORATION", "Limited Liability Partnership Act - Ministry of Corporate Affairs", f"LLPIN: {co['reg']}", f"LLP Name: {co['legal']}", f"Registered Office: {co['address']}"]
def bank_doc(co, holder=None): return [f"{co['bank']}", "CANCELLED CHEQUE / BANK ACCOUNT CONFIRMATION LETTER", f"Bank: {co['bank']}", f"Account Holder: {holder or co['legal']}", f"Account Number: {co['acct']}", f"IFSC: {co['ifsc']}", "Branch: Main"]
def invoice_doc(co): return ["TAX INVOICE", "Invoice No: INV-2026-0098", "Bill To: Zenith Retail Pvt Ltd", f"GSTIN: {co['gstin']}", "Description    Qty    Unit Price    Amount", "Cold storage handling   200   450.00   90,000.00", "Total Amount Due: INR 1,06,200.00", "Due Date: 30/11/2026"]
def intl_reg(co):  return ["CERTIFICATE OF INCORPORATION", co["registry"], f"Company Name: {co['legal']}", f"Registration Number: {co['reg']}", f"Registered Office: {co['address']}"]
def intl_tax(co):  return ["TAX REGISTRATION CERTIFICATE", co["tax_authority"], f"Tax ID: {co['tax_id']}", f"Legal Name: {co['legal']}"]
def intl_bank(co): return [co["bank"], "BANK ACCOUNT CONFIRMATION LETTER", f"Bank: {co['bank']}", f"Account Holder: {co['legal']}", f"Account Number: {co['acct']}", f"SWIFT/BIC: {co.get('swift', '')}"]
def intl_trc(co):  return ["TAX RESIDENCY CERTIFICATE", "Certificate of Residence", co.get("tax_authority", ""), f"Legal Name: {co['legal']}", f"Country of Residence: {co['country']}", f"TRC Number: {co.get('trc_number', '')}"]

COMMON = dict(terms_accepted=True, service_category="Manufacturing / Supply of goods", trading_name="")
def dom_form(co, with_gst=True, **kw):
    f = dict(COMMON, region="Domestic", domestic_type="PAN with GST" if with_gst else "PAN without GST", legal_name=co["legal"],
             trading_name=co["trade"], contact_person=co["contact"], contact_email=co["email"], email=co["email"], phone=co["phone"],
             contact_mobile=co["phone"], alt_phone="", address=co["address"], state=co.get("state", ""), city=co.get("city", ""),
             pincode=co.get("pincode", ""), country="India", entity_type=co["entity"], proprietor_name="", pan=co["pan"],
             gstin=co.get("gstin", "") if with_gst else "", reg_number=co["reg"],
             bank_holder=co["legal"], account_number=co["acct"], bank_name=co["bank"], ifsc=co["ifsc"],
             service_description=co["services"])
    f.update(kw); return f
def intl_form(co, **kw):
    f = dict(COMMON, region="International", legal_name=co["legal"], trading_name="", contact_person=co["contact"],
             contact_email=co["email"], email=co["email"], phone=co["phone"], contact_mobile=co["phone"], alt_phone="",
             address=co["address"], state="", city="", pincode="", country=co["country"], entity_type="Private Limited Company",
             tax_id=co["tax_id"], reg_number=co["reg"], trc_applicable=True, trc_number=co.get("trc_number", "TRC-" + co["reg"]),
             bank_holder=co["legal"], account_number=co["acct"], bank_name=co["bank"], bank_country=co["country"],
             swift=co.get("swift", ""), service_category="IT & software services", service_description=co["services"])
    f.update(kw); return f

def D(legal, trade, pan, state, reg, bank, ifsc, acct, contact, dom, city, state_name, city_name, pincode,
      ent="Private Limited Company", services="Supply of goods and services"):
    co = dict(legal=legal, trade=trade, pan=pan, reg=reg, bank=bank, ifsc=ifsc, acct=acct, contact=contact, entity=ent,
              email=f"{contact.split()[0].lower()}@{dom}", phone="+91 98450 12345", address=city, services=services,
              state=state_name, city=city_name, pincode=pincode)
    if state: co["gstin"] = gstin(state, pan)
    return co

def build(out_dir):
    os.makedirs(out_dir, exist_ok=True)
    meridian = D("Meridian Precision Components Private Limited", "Meridian Precision", "AABCM4821K", "29", "U29299KA2015PTC081234", "HDFC Bank", "HDFC0001234", "50200012345678", "Anita Rao", "meridianprecision.example", "Plot 14, Peenya Industrial Area, Bengaluru, Karnataka 560058", "Karnataka", "Bengaluru", "560058", services="CNC machined components for automotive customers")
    aster = D("Aster Cold Chain Logistics Private Limited", "Aster Cold Chain", "AABCA7312P", "33", "U63090TN2018PTC123456", "ICICI Bank", "ICIC0001045", "004501234567", "Vikram Nair", "astercoldchain.example", "No. 8, Ambattur Estate, Chennai, Tamil Nadu 600058", "Tamil Nadu", "Chennai", "600058", services="Temperature-controlled logistics")
    bright = D("BrightPath Facility Services Private Limited", "BrightPath", "AAECB9054N", "27", "U74999MH2019PTC324567", "State Bank of India", "SBIN0004321", "31245678901", "Meera Joshi", "brightpath.example", "Unit 5, Andheri East, Mumbai, Maharashtra 400069", "Maharashtra", "Mumbai", "400069", services="Facility management")
    kaveri = D("Kaveri Packaging Solutions LLP", "Kaveri Packaging", "AAGFK7351M", None, "AAB-1234", "Axis Bank", "UTIB0000456", "917020012345678", "Suresh Iyer", "kaveripack.example", "12, SIDCO Estate, Coimbatore, Tamil Nadu 641021", "Tamil Nadu", "Coimbatore", "641021", ent="LLP", services="Corrugated packaging")
    orchid = D("Orchid Textile Mills Private Limited", "Orchid Textiles", "AAACO6120D", "24", "U17110GJ2016PTC456789", "Kotak Mahindra Bank", "KKBK0001122", "7412345678", "Hetal Shah", "orchidtextiles.example", "Survey 41, GIDC Vatva, Ahmedabad, Gujarat 382445", "Gujarat", "Ahmedabad", "382445", services="Woven fabric supply")
    north = dict(legal="Northwind Analytics Inc.", country="United States", tax_id="84-3920157", reg="7741920", trc_number="TRC-US-7741920", bank="JPMorgan Chase Bank", acct="000123456789", swift="CHASUS33", contact="Daniel Carter", email="daniel@northwindanalytics.example", phone="+1 302 555 0142", address="251 Little Falls Dr, Wilmington, DE 19808, USA", services="Data analytics consulting", registry="State of Delaware - Division of Corporations", tax_authority="Internal Revenue Service - Employer Identification Number (EIN) Confirmation")
    rhein = dict(north, legal="Rheinwerk Logistik GmbH", country="Germany", tax_id="AABCR5512L", reg="HRB123456", trc_number="TRC-DE-HRB123456", bank="Deutsche Bank AG", acct="DE89370400440532013000", swift="DEUTDEFF", contact="Jonas Weber", email="jonas@rheinwerk.example", phone="+49 30 5550142", address="Hafenstrasse 22, 20457 Hamburg, Germany", registry="Commercial Register Extract - Amtsgericht Hamburg", tax_authority="Finanzamt Hamburg - Tax Registration Certificate")
    verde = dict(north, legal="Verde Vale Exportadora Ltda", country="Brazil", tax_id="12.345.678/0001-95", reg="NIRE3520012345", trc_number="TRC-BR-NIRE3520012345", bank="Banco do Brasil", acct="000123456789", swift="BRASBRRJ", contact="Camila Souza", email="camila@verdevale.example", phone="+55 11 5550142", address="Rua das Flores 100, Sao Paulo, Brazil", registry="Junta Comercial do Estado de Sao Paulo", tax_authority="Receita Federal - Tax Registration Certificate")

    lotus = D("Lotus Agro Foods Private Limited", "Lotus Agro", "AABCL5527Q", "36", "U15400TG2017PTC118822", "Kotak Mahindra Bank", "KKBK0004455", "6011223344", "Ravi Teja", "lotusagro.example", "Plot 9, IDA Uppal, Hyderabad, Telangana 500039", "Telangana", "Hyderabad", "500039", services="Packaged rice and pulses")
    saffron = D("Saffron Hospitality Supplies Private Limited", "Saffron Supplies", "AAHCS3108R", "32", "U55101KL2020PTC061234", "Federal Bank", "FDRL0001567", "15670200045678", "Anjali Menon", "saffronsupplies.example", "Door 21/4, MG Road, Kochi, Kerala 682016", "Kerala", "Kochi", "682016", services="Hotel linen and amenities")
    # E1: sole proprietor. co['legal'] is the PROPRIETOR (as printed on PAN and the GST legal-name line); co['trade'] is the FIRM.
    varahi = D("Ravi Kumar Reddy", "Sri Varahi Traders", "AABPR1234K", "36", "UDYAM-TS-20-1234567", "Kotak Mahindra Bank", "KKBK0004455", "6677889900", "Ravi Kumar Reddy", "srivarahi.example", "5-9-22, Ranigunj, Hyderabad, Telangana 500081", "Telangana", "Hyderabad", "500081", ent="Proprietorship", services="Hardware and building materials supply")
    # E3: a DIFFERENT vendor (different PAN) using Meridian's IFSC + account number (shared bank account).
    zephyr = D("Zephyr Components Private Limited", "Zephyr Components", "AAICZ4821K", "29", "U29100KA2019PTC091234", "HDFC Bank", "HDFC0001234", "50200012345678", "Nisha Rao", "zephyrcomp.example", "Plot 22, Peenya Industrial Area, Bengaluru, Karnataka 560058", "Karnataka", "Bengaluru", "560058", services="Precision machined components")
    # E3b: a vendor using the fictional blocklisted account (seeded by workflow.seed_demo, reason 'rejected for payment fraud').
    nimbus = D("Nimbus Trading Private Limited", "Nimbus Trading", "AAJCN5533Q", "27", "U51909MH2020PTC334455", "HDFC Bank", "HDFC0009999", "9999000011112222", "Rohan Shah", "nimbustrading.example", "Office 3, Lower Parel, Mumbai, Maharashtra 400013", "Maharashtra", "Mumbai", "400013", services="Trading of goods")
    # E4: starts as a bank-holder mismatch (the first cheque is in a different name). The test drives the two resubmissions.
    pinnacle = D("Pinnacle Interiors Private Limited", "Pinnacle Interiors", "AAFCP6612R", "29", "U74110KA2018PTC077889", "HDFC Bank", "HDFC0002200", "12345678901", "Kiran Rao", "pinnacleint.example", "No. 7, Residency Road, Bengaluru, Karnataka 560025", "Karnataka", "Bengaluru", "560025", services="Interior fit-out services")

    scen, P = [], lambda name, lines, **k: (make_pdf(os.path.join(out_dir, name), lines, **k), name)[1]
    J = lambda name, lines, **k: (make_photo(os.path.join(out_dir, name), lines, **k), name)[1]
    def add(id_, title, desc, expected, form, files, note=""):
        scen.append(dict(id=id_, title=title, description=desc, expected=expected, form=form, files=files, note=note))

    add("1_happy_path", "1. Happy path - domestic, PAN with GST", "Complete, consistent submission with all four matching documents.", "APPROVED",
        dom_form(meridian), dict(PAN_PROOF=P("m_pan.pdf", pan_doc(meridian)), GST_CERT=P("m_gst.pdf", gst_doc(meridian)), COMPANY_REG=P("m_reg.pdf", reg_doc(meridian)), BANK_PROOF=P("m_bank.pdf", bank_doc(meridian))))
    add("2_wrong_document", "2. Wrong document - invoice instead of GST certificate", "An invoice is uploaded in the GST certificate slot.", "PENDING",
        dom_form(aster), dict(PAN_PROOF=P("a_pan.pdf", pan_doc(aster)), GST_CERT=P("a_invoice_as_gst.pdf", invoice_doc(aster)), COMPANY_REG=P("a_reg.pdf", reg_doc(aster)), BANK_PROOF=P("a_bank.pdf", bank_doc(aster))))
    add("3_bank_mismatch", "3. Bank account holder differs from company", "Account holder is 'Rajesh Kumar Enterprises' (form and bank proof) but the legal name is BrightPath Facility Services.", "PENDING",
        dom_form(bright, bank_holder="Rajesh Kumar Enterprises"), dict(PAN_PROOF=P("b_pan.pdf", pan_doc(bright)), GST_CERT=P("b_gst.pdf", gst_doc(bright)), COMPANY_REG=P("b_reg.pdf", reg_doc(bright)), BANK_PROOF=P("b_bank.pdf", bank_doc(bright, holder="Rajesh Kumar Enterprises"))))
    add("4_country_tax_mismatch", "4. Country / tax-ID scheme conflict", "German vendor supplies an Indian PAN-format tax ID. German rules are selected; no India rules are applied.", "PENDING",
        intl_form(rhein), dict(COMPANY_REG=P("r_reg.pdf", intl_reg(rhein)), TAX_CERT=P("r_tax.pdf", intl_tax(rhein)), BANK_PROOF=P("r_bank.pdf", intl_bank(rhein)), TRC=P("r_trc.pdf", intl_trc(rhein))))
    add("5_duplicate_pan", "5. Duplicate PAN / GSTIN (run AFTER scenario 1)", "Same PAN and GSTIN as the happy-path vendor, name written with 'Pvt Ltd'.", "PENDING",
        dom_form(meridian, legal_name="Meridian Precision Components Pvt Ltd", bank_holder="Meridian Precision Components Pvt Ltd"),
        dict(PAN_PROOF=P("md_pan.pdf", pan_doc(meridian)), GST_CERT=P("md_gst.pdf", gst_doc(meridian)), COMPANY_REG=P("md_reg.pdf", reg_doc(meridian)), BANK_PROOF=P("md_bank.pdf", bank_doc(meridian))),
        note="Needs scenario 1 to have been run first (duplicate lookup is against the local DB).")
    add("6_pan_without_gst", "6. Domestic PAN without GST (LLP)", "No GST certificate is required or requested.", "APPROVED",
        dom_form(kaveri, with_gst=False, service_category="Manufacturing / Supply of goods"), dict(PAN_PROOF=P("k_pan.pdf", pan_doc(kaveri)), COMPANY_REG=P("k_reg.pdf", llp_doc(kaveri)), BANK_PROOF=P("k_bank.pdf", bank_doc(kaveri))))
    add("7_international_us", "7. International vendor - United States (EIN)", "Foreign vendor: EIN rule applied; IFSC/PAN/GST never required.", "APPROVED",
        intl_form(north), dict(COMPANY_REG=P("n_reg.pdf", intl_reg(north)), TAX_CERT=P("n_tax.pdf", intl_tax(north)), BANK_PROOF=P("n_bank.pdf", intl_bank(north)), TRC=P("n_trc.pdf", intl_trc(north))))
    add("8_unsupported_country", "8. Unsupported country rule - Brazil", "No automated rule for Brazil: NOT_CHECKED, never a false pass.", "PENDING",
        intl_form(verde), dict(COMPANY_REG=P("v_reg.pdf", intl_reg(verde)), TAX_CERT=P("v_tax.pdf", intl_tax(verde)), BANK_PROOF=P("v_bank.pdf", intl_bank(verde)), TRC=P("v_trc.pdf", intl_trc(verde))))
    add("9_scanned_bank_proof", "9. Scanned bank proof too poor to read", "Bank proof is a low-resolution, blurred scan with no text layer. OCR cannot read it reliably, so it is held for a reviewer, never silently approved.", "PENDING",
        dom_form(orchid), dict(PAN_PROOF=P("o_pan.pdf", pan_doc(orchid)), GST_CERT=P("o_gst.pdf", gst_doc(orchid)), COMPANY_REG=P("o_reg.pdf", reg_doc(orchid)), BANK_PROOF=P("o_bank_scan.pdf", bank_doc(orchid), scanned=True)))
    add("10_phone_photos", "10. PAN card and bank proof as phone photos", "Two documents are JPG photos taken on a phone. OCR reads them, repairs letter/digit swaps in identifiers by position, and every value matches the form.", "APPROVED",
        dom_form(lotus), dict(PAN_PROOF=J("l_pan_photo.jpg", pan_doc(lotus), seed=11), GST_CERT=P("l_gst.pdf", gst_doc(lotus)), COMPANY_REG=P("l_reg.pdf", reg_doc(lotus)), BANK_PROOF=J("l_bank_photo.jpg", bank_doc(lotus), seed=12)))
    add("11_blurry_photo", "11. Blurred, distant photo of the PAN card", "The PAN card photo is too small and blurred for OCR. The document is held as unreadable and the vendor is asked for a sharper photo.", "PENDING",
        dom_form(saffron), dict(PAN_PROOF=J("s_pan_blurry.jpg", pan_doc(saffron), seed=13, scale=0.3, blur=2.2), GST_CERT=P("s_gst.pdf", gst_doc(saffron)), COMPANY_REG=P("s_reg.pdf", reg_doc(saffron)), BANK_PROOF=P("s_bank.pdf", bank_doc(saffron))))
    # ---- edge cases -----------------------------------------------------------------
    varahi_reg = ["UDYAM REGISTRATION CERTIFICATE", "Ministry of Micro, Small and Medium Enterprises",
                  f"Registration Number: {varahi['reg']}", f"Company Name: {varahi['trade']}", f"Registered Office: {varahi['address']}"]
    add("E1_proprietor", "E1. Sole proprietor paid into the owner's personal account",
        "Firm 'Sri Varahi Traders' (trade name), proprietor 'Ravi Kumar Reddy', PAN 4th character P. The GST certificate shows "
        "Legal Name = the proprietor and Trade Name = the firm, and the bank account is in the proprietor's own name. Kyvo compares "
        "the proprietor name (not the firm name) with the PAN, GST legal name and bank holder, so this is correctly APPROVED.", "APPROVED",
        dom_form(varahi, legal_name="Sri Varahi Traders", proprietor_name="Ravi Kumar Reddy"),
        dict(PAN_PROOF=P("e1_pan.pdf", pan_doc(varahi)), GST_CERT=P("e1_gst.pdf", gst_doc(varahi)),
             COMPANY_REG=P("e1_reg.pdf", varahi_reg), BANK_PROOF=P("e1_bank.pdf", bank_doc(varahi, holder="Ravi Kumar Reddy"))),
        note="Naive logic reads this as a bank-name mismatch; Kyvo recognises a proprietorship and checks the proprietor's name.")
    add("E3_shared_bank", "E3. One bank account, two vendors (run AFTER scenario 1)",
        "A new vendor with a different PAN uses the IFSC and account number already registered to the happy-path vendor. "
        "Kyvo raises XR_BANK_SHARED (separate from identifier duplicates), references both vendors, and routes to a reviewer.", "PENDING",
        dom_form(zephyr), dict(PAN_PROOF=P("e3_pan.pdf", pan_doc(zephyr)), GST_CERT=P("e3_gst.pdf", gst_doc(zephyr)),
                               COMPANY_REG=P("e3_reg.pdf", reg_doc(zephyr)), BANK_PROOF=P("e3_bank.pdf", bank_doc(zephyr))),
        note="Needs scenario 1 to have been run first (the shared-account lookup is against the local DB).")
    add("E3b_blocklisted_bank", "E3b. Shared account on the policy blocklist",
        "The bank account is on the policy blocklist with the reason 'rejected for payment fraud'. The outcome is REJECTED "
        "automatically, with that written reason. This is the demo's REJECTED outcome.", "REJECTED",
        dom_form(nimbus), dict(PAN_PROOF=P("e3b_pan.pdf", pan_doc(nimbus)), GST_CERT=P("e3b_gst.pdf", gst_doc(nimbus)),
                               COMPANY_REG=P("e3b_reg.pdf", reg_doc(nimbus)), BANK_PROOF=P("e3b_bank.pdf", bank_doc(nimbus))),
        note="Needs the seeded blocklisted account (workflow.seed_demo). In the app this is seeded on startup.")
    add("E4_resubmission", "E4. A resubmission that fixes the asked item and breaks another",
        "Run 1 is a bank-holder mismatch (the cancelled cheque is in a different name). The vendor replies with a cheque in the "
        "right name but carrying a different IFSC, so the holder request resolves and an IFSC request opens (still PENDING). A "
        "second, clean cheque then gives APPROVED. The test asserts the request ledger across all three runs.", "PENDING",
        dom_form(pinnacle), dict(PAN_PROOF=P("e4_pan.pdf", pan_doc(pinnacle)), GST_CERT=P("e4_gst.pdf", gst_doc(pinnacle)),
                                 COMPANY_REG=P("e4_reg.pdf", reg_doc(pinnacle)),
                                 BANK_PROOF=P("e4_bank.pdf", bank_doc(pinnacle, holder="Pinnacle Designs"))),
        note="Run 1 only. The two vendor responses are driven by the E4 test / the vendor portal's Action needed page.")

    with open(os.path.join(out_dir, "scenarios.json"), "w") as fh:
        json.dump(scen, fh, indent=1)
    return scen

def load_scenarios(out_dir):
    with open(os.path.join(out_dir, "scenarios.json")) as fh:
        return json.load(fh)

def export_examples(sample_dir, examples_dir):
    """Write one examples/<id>.json per scenario: the form plus document paths relative to the JSON file."""
    os.makedirs(examples_dir, exist_ok=True)
    rel = os.path.relpath(sample_dir, examples_dir).replace("\\", "/")
    scen = load_scenarios(sample_dir)
    for s in scen:
        obj = dict(s["form"])
        obj["documents"] = {t: f"{rel}/{name}" for t, name in s["files"].items()}
        with open(os.path.join(examples_dir, f"{s['id']}.json"), "w", encoding="utf-8") as fh:
            json.dump(obj, fh, indent=2)
    return len(scen)

def scenario_files(out_dir, sc):
    out = {}
    for t, name in sc["files"].items():
        with open(os.path.join(out_dir, name), "rb") as fh:
            out[t] = (name, fh.read())
    return out

if __name__ == "__main__":
    d = os.path.dirname(os.path.abspath(__file__))
    n = len(build(d))
    root = os.path.dirname(d)
    ne = export_examples(d, os.path.join(root, "examples"))
    print(f"Generated {n} scenarios in {d} and {ne} example submissions in {os.path.join(root, 'examples')}")

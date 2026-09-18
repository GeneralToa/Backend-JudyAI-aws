"""
Build the test contract corpus for validating the AI agents (JAAPESWM-41).

The SOW requires AI behaviour to be "validated manually against sample
contracts", capped at 10 documents. The client supplied blank legal templates
rather than filled, negotiated contracts, and confirmed on 2026-09-16 that
building test cases from them is fine.

Method
------
Each test contract is one of the client's own templates with a scenario clause
inserted - a situation that should cause a specific playbook rule to be raised.
Every document ships with its expected findings, so recall can be measured
instead of eyeballed.

Why the scenario text is written from scratch
---------------------------------------------
The inserted clauses are deliberately NOT copied from playbook_rules.json. If
the test text were lifted from the rules the model is given, the exercise would
only prove it can match text against itself. Each scenario is written as
ordinary contract language describing the situation, phrased differently from
the rule that should catch it.

Output mimics Bedrock Data Automation: markdown with [page N] markers, which is
what the agents actually receive.

Usage:
    python build_corpus.py
"""

import json
import os
import xml.etree.ElementTree as ET
import zipfile

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATES = os.path.join(HERE, "..", "..", "..", "local_context", "Templates")
OUT_DIR = os.path.join(HERE, "corpus")

# Roughly how many characters of source text sit on one page of these documents.
CHARS_PER_PAGE = 3000


# --- Scenario clauses -------------------------------------------------------
# Written independently of the playbook wording. Each should cause exactly the
# rule(s) listed in expects_rules to be raised.

FACILITIES = (
    "**Site Attendance.** Vendor personnel will require unescorted badge access to "
    "Company offices, warehouses and data centre floor space in order to carry out "
    "scheduled hardware maintenance. Vendor personnel may attend site outside normal "
    "business hours, including weekends, and will not be accompanied by Company staff."
)

NETWORKS = (
    "**Systems Access.** Vendor personnel will be issued Company laptops and granted "
    "credentials to Company internal networks, including the finance file share, for "
    "the duration of the engagement. Guest network access is not sufficient for the "
    "Services."
)

PERSONAL_INFO = (
    "**Employee Records.** In performing the Services, Vendor will receive and process "
    "the names, home addresses, national insurance numbers and occupational health "
    "records of Company employees."
)

CUSTOMER_DATA = (
    "**Customer Content.** Vendor will be able to view and export files that Company "
    "customers have uploaded into their accounts, including any content those customers "
    "submit by email."
)

SUBCONTRACTORS = (
    "**Resourcing.** Vendor may engage independent contractors and third party agencies "
    "to deliver part of the Services. Such personnel will not be employees of Vendor and "
    "will not work under the day to day supervision of Company."
)

OWNERSHIP = (
    "**Training Delivery.** Vendor will design and deliver bespoke training workshops for "
    "Company staff, and will prepare course workbooks and slide decks specifically for "
    "Company as part of the Services."
)

SOFTWARE_DEV = (
    "**Platform Work.** Vendor will write and deliver software modules that Company will "
    "embed directly into its customer facing web platform and core billing systems."
)

TRADEMARK = (
    "**Publicity.** Vendor intends to display the Company name, logo and screenshots of "
    "the Company application on the Vendor website and in Vendor sales decks and case "
    "studies."
)

MARKETING = (
    "**Digital Services.** Vendor will provide search engine optimisation and link "
    "building services for Company web properties, and will deploy tracking pixels to "
    "monitor visitor behaviour across Company sites."
)

CONTACT_LISTS = (
    "**Prospect Data.** Company will provide Vendor with a list of current and "
    "prospective customers, including business email addresses and telephone numbers, "
    "for use in the campaign."
)

CORRESPONDENCE = (
    "**Outbound Contact.** Vendor will send emails and place telephone calls to Company "
    "customers and prospects using Company branding, and will do so on Company's behalf."
)

DATES_AND_NUMBERS = (
    "**Term.** This Agreement commences on 1 March 2026 and continues for an initial "
    "period of three (3) years, expiring on 28 February 2028. The Agreement renews "
    "automatically for successive twelve (12) month terms unless either party gives "
    "ninety (90) days written notice.\n\n"
    "**Commission.** Vendor shall be paid commission of six percent (6%) of net revenue "
    "in year one, rising to nine percent (9%) from the second anniversary. Any single "
    "purchase above $500 requires a purchase order raised in advance."
)


def scenario_docs():
    """The corpus definition: base template, inserted clauses, expected rules."""
    return [
        {
            "id": "01_vendor_site_and_systems",
            "base": "}[Judefly][Vendor Agreement].docx",
            "title": "Vendor Agreement - on-site maintenance",
            "insert": [FACILITIES, NETWORKS],
            "expects_rules": ["access_to_facilities", "access_to_networks_and_equipment"],
            "expects_criteria": ["facility or access prerequisites"],
        },
        {
            "id": "02_vendor_subcontractors_and_pii",
            "base": "}[Judefly][Vendor Agreement].docx",
            "title": "Vendor Agreement - subcontracted, handles employee records",
            "insert": [SUBCONTRACTORS, PERSONAL_INFO],
            "expects_rules": ["supplier_personnel", "access_to_personal_information"],
            "expects_criteria": [],
        },
        {
            "id": "03_msa_marketing_and_brand",
            "base": "template.md",
            "title": "MSA - SEO and brand usage",
            "insert": [MARKETING, TRADEMARK],
            "expects_rules": ["online_marketing_activities", "trademark_use"],
            "expects_criteria": [],
        },
        {
            "id": "04_msa_contacts_and_outreach",
            "base": "template.md",
            "title": "MSA - customer lists and outbound contact",
            "insert": [CONTACT_LISTS, CORRESPONDENCE],
            "expects_rules": ["contact_lists", "correspondence"],
            "expects_criteria": [],
        },
        {
            "id": "05_vendor_platform_and_training",
            "base": "}[Judefly][Vendor Agreement].docx",
            "title": "Vendor Agreement - core platform work and bespoke training",
            "insert": [SOFTWARE_DEV, OWNERSHIP],
            "expects_rules": ["software_development", "ownership"],
            "expects_criteria": [],
        },
        {
            "id": "06_vendor_customer_content",
            "base": "}[Judefly][Vendor Agreement].docx",
            "title": "Vendor Agreement - access to customer uploaded content",
            "insert": [CUSTOMER_DATA],
            "expects_rules": ["access_to_customer_data"],
            "expects_criteria": [],
        },
        {
            "id": "07_msa_dates_and_thresholds",
            "base": "template.md",
            "title": "MSA - renewal window, escalating commission, PO threshold",
            "insert": [DATES_AND_NUMBERS],
            "expects_rules": [],
            "expects_criteria": [
                "renewal or expiry dates",
                "commission percentage that changes",
                "monetary threshold",
            ],
        },
        {
            "id": "08_control_advisor_agreement",
            "base": "advisor-agreement.docx",
            "title": "Advisor Agreement - unmodified control",
            "insert": [],
            "expects_rules": [],
            "expects_criteria": [],
            "note": (
                "Negative control. An advisory agreement engages almost none of the "
                "playbook. Findings here are mostly false positives, so this measures "
                "precision rather than recall."
            ),
        },
    ]


# --- Source text ------------------------------------------------------------
def docx_paragraphs(path):
    with zipfile.ZipFile(path) as archive:
        xml = archive.read("word/document.xml")
    body = ET.fromstring(xml).find(W + "body")
    paragraphs = []
    for para in body.iter(W + "p"):
        text = "".join(node.text or "" for node in para.iter(W + "t")).strip()
        if text:
            paragraphs.append(text)
    return paragraphs


def base_paragraphs(filename):
    path = os.path.join(TEMPLATES, filename)
    if filename.lower().endswith(".md"):
        with open(path, encoding="utf-8", errors="replace") as fh:
            return [p.strip() for p in fh.read().split("\n\n") if p.strip()]
    return docx_paragraphs(path)


def paginate(paragraphs):
    """Join paragraphs with [page N] markers, mimicking BDA output."""
    pages, current, size = [], [], 0
    for para in paragraphs:
        if size + len(para) > CHARS_PER_PAGE and current:
            pages.append(current)
            current, size = [], 0
        current.append(para)
        size += len(para)
    if current:
        pages.append(current)

    return "\n\n".join(
        f"[page {i + 1}]\n" + "\n\n".join(page) for i, page in enumerate(pages)
    )


def build():
    os.makedirs(OUT_DIR, exist_ok=True)
    manifest = []

    for spec in scenario_docs():
        paragraphs = base_paragraphs(spec["base"])

        # Insert scenario clauses a third of the way in, so they sit in the body
        # of the agreement rather than at the very top where they would be
        # trivially prominent.
        if spec["insert"]:
            at = max(1, len(paragraphs) // 3)
            paragraphs = paragraphs[:at] + list(spec["insert"]) + paragraphs[at:]

        text = paginate(paragraphs)
        filename = f"{spec['id']}.md"
        with open(os.path.join(OUT_DIR, filename), "w", encoding="utf-8") as fh:
            fh.write(text)

        manifest.append({
            "id": spec["id"],
            "file": filename,
            "title": spec["title"],
            "base_template": spec["base"],
            "scenarios_inserted": len(spec["insert"]),
            "expects_rules": spec["expects_rules"],
            "expects_criteria": spec["expects_criteria"],
            "note": spec.get("note"),
            "chars": len(text),
        })

    covered = sorted({r for m in manifest for r in m["expects_rules"]})
    document = {
        "purpose": "Validation corpus for the AI agents. See JAAPESWM-41.",
        "document_count": len(manifest),
        "playbook_rules_covered": covered,
        "documents": manifest,
    }

    with open(os.path.join(OUT_DIR, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(document, fh, indent=2, ensure_ascii=False)

    print(f"wrote {len(manifest)} documents to {OUT_DIR}")
    for entry in manifest:
        expects = ", ".join(entry["expects_rules"]) or "(control / criteria only)"
        print(f"  {entry['id']:34} {entry['chars']:>6} chars  expects: {expects}")
    print(f"\nplaybook rules covered: {len(covered)}")
    return document


if __name__ == "__main__":
    build()

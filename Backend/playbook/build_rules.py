"""
Convert the client's contract playbook (.docx) into machine-readable rules.

The playbook is a three-column Word table:

    col 1  section name
    col 2  the standard sentence, then "Because" + the objection condition
    col 3  what to do about it - replace text, insert text, attach an exhibit,
           require approval, or escalate to a legal professional

The risk & clause analysis agent needs rules, not prose. Re-reading a .docx on
every contract would be slow, costly and non-deterministic, and the agent has to
emit a playbook_section identifier on every finding (see 001_ai_schema.sql).

Design notes
------------
Clause text is copied verbatim. The playbook is the client's legal IP and its
wording is not ours to tidy - suspected typos are reported by
report_quality_issues() for the client to confirm rather than silently
corrected. Only wording the client has confirmed in writing is corrected, from
CLIENT_CONFIRMED_CORRECTIONS, and every correction is recorded in the output.

Structural parsing is mechanical so it stays reproducible if the playbook is ever
reissued. The parts the document's own markers cannot express reliably - a stable
rule id, and which contract types a section applies to - are curated in
SECTION_METADATA below.

Usage:
    python build_rules.py [path/to/Playbook_part2_full.docx]

Writes playbook_rules.json next to this script, and an identical copy next to the
risk & clause agent's handler - Terraform packages each function's own folder, so
the agent ships only what sits beside it. Both files are committed;
Backend/tests/test_playbook_rules_in_sync.py fails if they ever differ.
"""

import json
import os
import re
import sys
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timezone

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

DEFAULT_SOURCE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "..", "..", "local_context", "playbook", "Playbook_part2_full.docx",
)
OUTPUT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "playbook_rules.json")
AGENT_COPY_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "lambdas", "agent_risk_clause", "playbook_rules.json",
)

# Stable ids and contract-type applicability. The document only states contract
# types in one row ("SOW, PA" under Access to Personal information), so the rest
# default to all three types confirmed by the client on 2026-09-16.
ALL_TYPES = ["sow", "purchase_agreement", "msa"]
SECTION_METADATA = {
    "Access to facilities":             ("access_to_facilities", ALL_TYPES),
    "Access to Networks and Equipment": ("access_to_networks_and_equipment", ALL_TYPES),
    "Access to Personal information":   ("access_to_personal_information", ["sow", "purchase_agreement"]),
    "Access to Customer data":          ("access_to_customer_data", ALL_TYPES),
    "Supplier Personnel":               ("supplier_personnel", ALL_TYPES),
    "ownership":                        ("ownership", ALL_TYPES),
    "Software\nDevelopment":            ("software_development", ALL_TYPES),
    "Trademark Use":                    ("trademark_use", ALL_TYPES),
    "Online marketing Activities":      ("online_marketing_activities", ALL_TYPES),
    "Contact Lists":                    ("contact_lists", ALL_TYPES),
    "Correspondence":                   ("correspondence", ALL_TYPES),
}

# Instruction markers appearing in column 3, mapped to an action type.
# Ordered longest-first so specific phrases win over general ones.
ACTION_MARKERS = [
    ("replace this section with the following", "replace"),
    ("replace sentence with the following", "replace"),
    ("replace section 8 with the following", "replace"),
    ("replace sentence", "replace"),
    ("replace", "replace"),
    ("input the following", "insert"),
    ("connect to legal  provider for  appropriate language", "escalate_to_legal"),
    ("connect to legal provider for appropriate language", "escalate_to_legal"),
    ("connect with legal agent", "escalate_to_legal"),
    ("suggested consult legal expert regarding information gained via behavior tracking", "escalate_to_legal"),
    ("contact legal experts", "escalate_to_legal"),
    ("suggest contact legal", "escalate_to_legal"),
    ("contact legal", "escalate_to_legal"),
    ("identify contract in use", "identify_contract_type"),
    ("multiple levels of approvals are suggested", "require_approval"),
    ("request written confirmation from stakeholders", "require_approval"),
    ("request", "require_approval"),
    # Attachments and optional guidance are also expressed as braced markers,
    # not only as running text.
    ("attach", "attach_exhibit"),
    ("option add", "optional"),
    ("suggested option", "optional"),
    ("this section modifies", "cross_reference"),
    ("section 9", "cross_reference"),
    ("link", "placeholder"),
]

# Braced conjunctions carry no instruction.
IGNORED_MARKERS = {"and", "or", "because"}

BECAUSE = re.compile(r"^\{?\s*because\s*\}?$", re.IGNORECASE)
SEPARATOR = re.compile(r"^_{3,}$")
# One level of nesting is allowed: the ownership section has a marker that
# contains "{section 9}", and a flat pattern stopped at the inner brace and left
# the rest of the marker glued to the clause text.
MARKER = re.compile(r"\{((?:[^{}]|\{[^{}]*\})*)\}")
# The source is hand-written and at least one marker is never closed, e.g.
# "{Replace this section with the following:" - treat a leading unclosed brace
# as a marker running to the end of the line.
UNCLOSED_MARKER = re.compile(r"^\{([^}]*)$")
CONJUNCTION = re.compile(r"^\{?\s*(and|or)\s*\}?$", re.IGNORECASE)
ATTACH = re.compile(r"attach(?:ed)?\s+the\s+(.+?exhibit)", re.IGNORECASE)

# Markers that are an instruction in themselves and carry no clause text.
NOTE_ONLY_TYPES = ("escalate_to_legal", "identify_contract_type", "cross_reference",
                   "placeholder", "require_approval")

# Wording the client confirmed in writing as typing errors: Eean Patterson's email
# of 2 Oct 2026, answering our list of 16 Sep. Applied to the source text before
# parsing, so triggers, actions and the raw copy all carry the corrected wording.
# Matches include enough context to be unique (", on-transferable" would
# otherwise also hit the correct "non-transferable"). A correction that no longer
# matches - the playbook was reissued - is reported as a warning.
CLIENT_CONFIRMED_CORRECTIONS = (
    ("shall email the sole property", "shall be the sole property"),  # the client chose "be"
    ("supplier grans the", "supplier grants the"),
    (", on-transferable", ", non-transferable"),
    ("of tis own", "of its own"),
    ("Develiverables", "Deliverables"),
    ("links will not be places", "links will not be placed"),
    ("I comply with all applicable laws", "and comply with all applicable laws"),  # the client's wording
    ("cannot be preformed", "cannot be performed"),
    ("Supplier o perform", "Supplier to perform"),
    ("othewise", "otherwise"),
)


def cell_text(tc):
    """All non-empty paragraphs in a table cell, in order."""
    paragraphs = []
    for p in tc.iter(W + "p"):
        text = "".join(node.text or "" for node in p.iter(W + "t")).strip()
        if text:
            paragraphs.append(text)
    return paragraphs


def read_table(docx_path):
    """Return the playbook table as a list of rows, each a list of cells."""
    with zipfile.ZipFile(docx_path) as archive:
        xml = archive.read("word/document.xml")

    body = ET.fromstring(xml).find(W + "body")
    tables = [el for el in body if el.tag == W + "tbl"]
    if not tables:
        raise RuntimeError(f"No table found in {docx_path}")

    rows = []
    for tr in tables[0]:
        if tr.tag != W + "tr":
            continue
        rows.append([cell_text(tc) for tc in tr if tc.tag == W + "tc"])
    return rows


CONJUNCTION_WORD = re.compile(r"^(and|or)$", re.IGNORECASE)
CLOSING_QUOTES = ("”", "’", '"')


def is_note_fragment(para):
    """A braced instruction such as {contact legal}, including one split across lines."""
    stripped = para.strip()
    return stripped.startswith("{") or stripped.endswith("}")


def starts_new_scenario(para, previous):
    """
    Decide whether a paragraph after a condition opens the next scenario.

    In this document the standard sentence is the only paragraph type that
    carries the playbook's own quotation marks - it opens with a curly quote or
    closes with one. Conditions do not.

    A paragraph following a bare conjunction (AND / OR) is always the rest of
    the condition, never a new standard sentence. The first version of this
    function keyed on "Supplier will not ..." and so split the ownership rule's
    condition in half at "AND / Supplier will not be creating custom training
    materials" - leaving the model a rule that ended mid-sentence and never
    mentioned custom materials at all.
    """
    if previous is not None and CONJUNCTION_WORD.match(previous.strip()):
        return False
    stripped = para.strip()
    return stripped.startswith(("“", '"')) or stripped.endswith(CLOSING_QUOTES)


def parse_triggers(paragraphs):
    """
    Split column 2 into (default_clause, condition, notes) scenarios.

    Layout is: a standard sentence, optionally "Because", then the condition
    that makes the standard sentence wrong. Sections with several scenarios
    repeat the pattern, sometimes divided by a line of underscores. Braced
    instructions that appear in this column ({contact legal}) are kept as
    notes rather than being glued into clause or condition text.
    """
    triggers = []
    current_clause = []
    current_condition = []
    current_notes = []
    after_because = False
    previous = None

    def flush():
        if current_clause or current_condition:
            triggers.append({
                "default_clause": " ".join(current_clause).strip(),
                "condition": " ".join(current_condition).strip() or None,
                "notes": [n for n in current_notes if n],
            })

    for para in paragraphs:
        if SEPARATOR.match(para):
            flush()
            current_clause, current_condition, current_notes = [], [], []
            after_because, previous = False, None
            continue

        if BECAUSE.match(para):
            after_because = True
            previous = para
            continue

        if is_note_fragment(para):
            current_notes.append(para.strip("{} ").strip())
            previous = para
            continue

        if CONJUNCTION_WORD.match(para.strip()):
            if after_because:
                current_condition.append(para.strip().lower())
            previous = para
            continue

        if after_because and starts_new_scenario(para, previous):
            flush()
            current_clause, current_condition, current_notes = [para], [], []
            after_because = False
        elif after_because:
            current_condition.append(para)
        else:
            current_clause.append(para)

        previous = para

    flush()
    return [t for t in triggers if t["default_clause"] or t["condition"]]


def classify_marker(marker_text):
    """Map an instruction marker to an action type, or None if unrecognized."""
    normalized = marker_text.strip().lower()
    if normalized in IGNORED_MARKERS:
        return "ignore"
    for phrase, action in ACTION_MARKERS:
        if phrase in normalized:
            return action
    return None


def parse_actions(paragraphs):
    """
    Split column 3 into actions.

    A marker in braces announces what to do, and the paragraphs that follow it
    carry the text to apply. Unmarked trailing text is attached to the action
    it follows.
    """
    actions = []
    pending = None
    unmatched_markers = []

    for para in paragraphs:
        # A conjunction between actions carries no text, whether braced or not,
        # and must not be absorbed into the preceding clause.
        if CONJUNCTION.match(para.strip()):
            continue

        markers = MARKER.findall(para)
        stripped = MARKER.sub("", para).strip()

        if not markers:
            unclosed = UNCLOSED_MARKER.match(para.strip())
            if unclosed:
                markers = [unclosed.group(1).rstrip(": ")]
                stripped = ""

        exhibit = ATTACH.search(para)
        if exhibit and not markers:
            if pending:
                actions.append(pending)
                pending = None
            actions.append({"type": "attach_exhibit", "text": exhibit.group(1).strip()})
            continue

        if markers:
            types = [classify_marker(m) for m in markers]
            opens_text_action = any(t not in (None, "ignore", "attach_exhibit") + NOTE_ONLY_TYPES
                                    for t in types)
            if pending is not None and not pending["text"] and stripped and not opens_text_action:
                # The clause announced by the previous marker, with a placeholder or
                # cross-reference inside it. It is that action's text. Without this
                # the ownership and software development clauses were filed as notes
                # and never reached the user as suggested wording.
                pending["text"] = stripped
                stripped = ""
            if pending:
                actions.append(pending)
                pending = None
            for marker, action_type in zip(markers, types):
                if action_type == "ignore":
                    continue
                if action_type is None:
                    if marker.strip():
                        unmatched_markers.append(marker.strip())
                    continue
                if action_type == "attach_exhibit":
                    exhibit_in_marker = ATTACH.search(marker)
                    text = exhibit_in_marker.group(1).strip() if exhibit_in_marker else marker.strip()
                    actions.append({"type": "attach_exhibit", "text": text})
                elif action_type in NOTE_ONLY_TYPES:
                    actions.append({"type": action_type, "note": marker.strip()})
                else:
                    pending = {"type": action_type, "text": "", "note": marker.strip()}
            if stripped and pending is not None:
                pending["text"] = stripped
            elif stripped:
                actions.append({"type": "note", "text": stripped})
            continue

        if pending is not None:
            pending["text"] = (pending["text"] + "\n" + para).strip()
        elif para.strip().lower() in ("and", "or"):
            continue
        else:
            actions.append({"type": "note", "text": para})

    if pending:
        actions.append(pending)

    return actions, unmatched_markers


def apply_confirmed_corrections(rows):
    """Apply CLIENT_CONFIRMED_CORRECTIONS to every paragraph. Returns (rows, applied, warnings)."""
    counts = {wrong: 0 for wrong, _ in CLIENT_CONFIRMED_CORRECTIONS}
    corrected = []
    for row in rows:
        new_row = []
        for cell in row:
            new_cell = []
            for para in cell:
                for wrong, right in CLIENT_CONFIRMED_CORRECTIONS:
                    if wrong in para:
                        counts[wrong] += para.count(wrong)
                        para = para.replace(wrong, right)
                new_cell.append(para)
            new_row.append(new_cell)
        corrected.append(new_row)
    applied = [{"from": wrong, "to": right, "occurrences": counts[wrong]}
               for wrong, right in CLIENT_CONFIRMED_CORRECTIONS if counts[wrong]]
    warnings = [f"confirmed correction no longer matches the source: {wrong!r}"
                for wrong, _ in CLIENT_CONFIRMED_CORRECTIONS if not counts[wrong]]
    return corrected, applied, warnings


def build(docx_path):
    rows, corrections, warnings = apply_confirmed_corrections(read_table(docx_path))
    header, body_rows = rows[0], rows[1:]

    rules = []

    for row in body_rows:
        if len(row) < 3:
            warnings.append(f"Row with {len(row)} cells skipped: {row[:1]}")
            continue

        section = "\n".join(row[0]).strip()
        meta = SECTION_METADATA.get(section)
        if meta is None:
            warnings.append(f"No metadata for section {section!r} - using defaults")
            rule_id = re.sub(r"[^a-z0-9]+", "_", section.lower()).strip("_")
            contract_types = ALL_TYPES
        else:
            rule_id, contract_types = meta

        triggers = parse_triggers(row[1])
        actions, unmatched = parse_actions(row[2])

        for marker in unmatched:
            warnings.append(f"{rule_id}: unrecognized marker {{{marker}}}")
        if not triggers:
            warnings.append(f"{rule_id}: no triggers parsed")
        if not actions:
            warnings.append(f"{rule_id}: no actions parsed")

        rules.append({
            "id": rule_id,
            "section": section.replace("\n", " "),
            "contract_types": contract_types,
            "escalates_to_legal": any(a["type"] == "escalate_to_legal" for a in actions),
            "triggers": triggers,
            "actions": actions,
            "raw": {
                "if_supplier_objects": row[1],
                "then": row[2],
            },
        })

    return {
        "source_document": os.path.basename(docx_path),
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "generated_by": "Backend/playbook/build_rules.py",
        "column_headers": [" ".join(c) for c in header],
        "client_confirmed_corrections": {
            "confirmed_by": "Eean Patterson, email of 2 Oct 2026",
            "applied": corrections,
        },
        "rule_count": len(rules),
        "rules": rules,
    }, warnings


def report_quality_issues(document):
    """
    Collect wording in the source that looks like a typo.

    These are NOT corrected. Replacement clauses are shown to users as proposed
    contract wording, so the client should confirm the intended text rather than
    have us edit their legal language.
    """
    # Not yet confirmed by the client (the ten sent on 16 Sep are corrected above).
    suspects = [
        ("contemplated by his agreement", "likely 'this agreement'"),
        ("prior to o independent", "likely 'prior to or independent'"),
        ("Hower,", "likely 'However,'"),
        ("consulting o training", "likely 'consulting or training'"),
        ("give o provide", "likely 'give or provide'"),
        ("to provide the severs", "likely 'the Services'"),
        ("[company] other wise", "likely 'otherwise'"),
        ("and y privacy", "likely 'any privacy'"),
        ("described herin", "likely 'herein'"),
        ("completion of the Servies", "likely 'Services'"),
        ("not be placed o blogs", "likely 'placed on blogs'"),
        ("keywording stuffing", "likely 'keyword stuffing'"),
        ("buil or places by supplier", "likely 'built or placed'"),
        ("that the lik be removed", "likely 'the link'"),
        ("distribution o contact list", "likely 'distribution of contact lists'"),
        ("shall be the considered the", "likely 'shall be considered the'"),
    ]

    found = []
    for rule in document["rules"]:
        blob = json.dumps(rule["raw"])
        for phrase, suggestion in suspects:
            if phrase.lower() in blob.lower():
                found.append({"rule": rule["id"], "text": phrase, "suggestion": suggestion})
    return found


def main():
    source = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_SOURCE
    source = os.path.abspath(source)

    document, warnings = build(source)

    for path in (OUTPUT_PATH, AGENT_COPY_PATH):
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(document, fh, indent=2, ensure_ascii=False)

    print(f"source : {source}")
    print(f"output : {OUTPUT_PATH}")
    print(f"copy   : {os.path.normpath(AGENT_COPY_PATH)}")
    print(f"rules  : {document['rule_count']}")

    triggers = sum(len(r["triggers"]) for r in document["rules"])
    actions = sum(len(r["actions"]) for r in document["rules"])
    escalating = sum(1 for r in document["rules"] if r["escalates_to_legal"])
    print(f"triggers: {triggers} | actions: {actions} | sections escalating to legal: {escalating}")

    if warnings:
        print(f"\nwarnings ({len(warnings)}):")
        for warning in warnings:
            print(f"  - {warning}")

    issues = report_quality_issues(document)
    if issues:
        print(f"\nsource wording to confirm with the client ({len(issues)}) - not corrected here:")
        for issue in issues:
            print(f"  - [{issue['rule']}] \"{issue['text']}\" -> {issue['suggestion']}")


if __name__ == "__main__":
    main()

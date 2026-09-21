"""
agent_template_prepopulation Lambda function - Agent 2 of 4, pre-signing.

SOW scope, verbatim:
    "Template pre-population agent that automatically populates signature
     blocks, form fields, and signers for up to 3 predefined template types."

The three types, confirmed by the client on 2026-09-16: MSA (master services
agreement), PA (purchase agreement - software/product) and SOW (statement of
work - actual services, with change orders hanging off it). Anything else is
'unknown'. A fourth type is a change order, not a freebie.

Trigger:
    Async invoke from document_extraction once text is available, or from
    analysis-api on a re-run. Payload: {"contract_id": ..., "extraction_id": ...}

Writes judy_ai.agent_runs, judy_ai.template_detections and judy_ai.contract_fields.

Two model calls, one job each
-----------------------------
1. Classification - which of the three template types the document is, with a
   one-sentence rationale the reviewer can check.
2. Field detection - every signature block and fill-in field, as the document
   labels it (label, field type, signer role, page, column).

Where the coordinates come from
-------------------------------
The model never estimates positions. It only names the label as printed and
which column it sits in. Positions come from Bedrock Data Automation: the
extraction step runs a BDA project with bounding boxes enabled, whose
result.json carries text_lines[] with a normalized {left, top, width, height}
box per printed line. Each detected field is anchored to its label's line and
the fill-in box is derived from that anchor - to the right of the label, up to
the next thing on the same row. That is the {x, y, width, height} the API
contract promises Lloyd, in 0-1 page coordinates with a top-left origin.

If the extraction was produced without bounding boxes (the public-default BDA
project, or an older run), fields are still stored with page and label but
position is null. The UI can fall back to "page N, label X" for those.

Model access note
-----------------
Nova must be invoked through an inference profile ("us.amazon.nova-pro-v1:0").
A plain foundation-model ARN fails with ValidationException. Bedrock exceptions
are logged explicitly rather than swallowed.
"""

import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

# --- Clients ---
retry_config = Config(retries={"max_attempts": 3, "mode": "adaptive"})
bedrock_runtime = boto3.client("bedrock-runtime", config=retry_config)
rds_data_client = boto3.client("rds-data", config=retry_config)
s3_client = boto3.client("s3")

# --- Environment ---
AURORA_CLUSTER_ARN = os.environ["AURORA_CLUSTER_ARN"]
AURORA_SECRET_ARN = os.environ["AURORA_SECRET_ARN"]
AURORA_DATABASE = os.environ["AURORA_DATABASE"]

# Inference profile id, not a foundation-model ARN. See module docstring.
MODEL_ID = os.environ.get("MODEL_ID", "us.amazon.nova-pro-v1:0")
PROMPT_VERSION = os.environ.get("PROMPT_VERSION", "template-prepopulation-v1")

# The bucket the extraction step wrote BDA output to. document_extractions only
# records the key, so the bucket has to be known here.
BDA_OUTPUT_BUCKET = os.environ.get("BDA_OUTPUT_BUCKET")

MAX_CONTRACT_CHARS = int(os.environ.get("MAX_CONTRACT_CHARS", "120000"))

TEMPLATE_TYPES = ("msa", "purchase_agreement", "sow", "unknown")
FIELD_TYPES = ("signature", "initial", "date", "full_name", "title", "company", "text")
COLUMNS = ("left", "right", "single")

# Fill-in box geometry, in page fractions. Derived from the label's line box:
# a signature line needs room for a drawn signature, everything else is a
# single line of text. These are starting values for the POC; the reviewer sees
# them drawn on the page and can say if they are wrong.
FIELD_HEIGHT = {"signature": 0.040, "initial": 0.030}
DEFAULT_FIELD_HEIGHT = 0.022
FIELD_GAP = 0.008           # horizontal gap between label and fill-in box
DEFAULT_FIELD_WIDTH = 0.30
MIN_FIELD_WIDTH = 0.08
ROW_TOLERANCE = 0.012       # lines whose tops differ by less than this share a row


# =============================================================
# Aurora (RDS Data API - no VPC required)
# =============================================================
def execute_sql(sql, parameters=None):
    result = rds_data_client.execute_statement(
        resourceArn=AURORA_CLUSTER_ARN,
        secretArn=AURORA_SECRET_ARN,
        database=AURORA_DATABASE,
        sql=sql,
        parameters=parameters or [],
    )
    return result.get("records", [])


def string_param(name, value):
    if value is None:
        return {"name": name, "value": {"isNull": True}}
    return {"name": name, "value": {"stringValue": str(value)}}


def long_param(name, value):
    if value is None:
        return {"name": name, "value": {"isNull": True}}
    return {"name": name, "value": {"longValue": int(value)}}


def bool_param(name, value):
    return {"name": name, "value": {"booleanValue": bool(value)}}


def fetch_extraction(extraction_id):
    """Return (extracted_text, raw_output_s3_key) for a succeeded extraction."""
    records = execute_sql(
        """
        SELECT extracted_text, status, raw_output_s3_key
          FROM judy_ai.document_extractions
         WHERE id = :id::uuid
        """,
        [string_param("id", extraction_id)],
    )
    if not records:
        raise RuntimeError(f"No extraction {extraction_id}")

    text = records[0][0].get("stringValue")
    status = records[0][1].get("stringValue")
    raw_key = records[0][2].get("stringValue")

    if status != "succeeded":
        raise RuntimeError(f"Extraction {extraction_id} is '{status}', not 'succeeded'")
    if not text or not text.strip():
        raise RuntimeError(f"Extraction {extraction_id} has no text")

    return text, raw_key


def start_run(contract_id, extraction_id):
    records = execute_sql(
        """
        INSERT INTO judy_ai.agent_runs
            (contract_id, extraction_id, agent_type, status, model_id, prompt_version)
        VALUES
            (:contract_id::uuid, :extraction_id::uuid, 'template_prepopulation', 'running',
             :model_id, :prompt_version)
        RETURNING id::text
        """,
        [
            string_param("contract_id", contract_id),
            string_param("extraction_id", extraction_id),
            string_param("model_id", MODEL_ID),
            string_param("prompt_version", PROMPT_VERSION),
        ],
    )
    return records[0][0]["stringValue"]


def complete_run(run_id, raw_response, usage, latency_ms):
    execute_sql(
        """
        UPDATE judy_ai.agent_runs
           SET status        = 'succeeded',
               raw_response  = :raw::jsonb,
               input_tokens  = :input_tokens,
               output_tokens = :output_tokens,
               latency_ms    = :latency_ms,
               completed_at  = now()
         WHERE id = :id::uuid
        """,
        [
            string_param("id", run_id),
            string_param("raw", json.dumps(raw_response)),
            long_param("input_tokens", usage.get("inputTokens")),
            long_param("output_tokens", usage.get("outputTokens")),
            long_param("latency_ms", latency_ms),
        ],
    )


def fail_run(run_id, message):
    execute_sql(
        """
        UPDATE judy_ai.agent_runs
           SET status = 'failed', error_message = :error, completed_at = now()
         WHERE id = :id::uuid
        """,
        [string_param("id", run_id), string_param("error", message[:2000])],
    )


def insert_detection(run_id, contract_id, detection):
    execute_sql(
        """
        INSERT INTO judy_ai.template_detections
            (run_id, contract_id, detected_template_type, rationale)
        VALUES
            (:run_id::uuid, :contract_id::uuid, :template_type, :rationale)
        """,
        [
            string_param("run_id", run_id),
            string_param("contract_id", contract_id),
            string_param("template_type", detection["template_type"]),
            string_param("rationale", detection.get("rationale")),
        ],
    )


def insert_fields(run_id, contract_id, fields):
    """Insert fields one at a time so a single bad row cannot lose the rest."""
    inserted = 0
    for field in fields:
        try:
            execute_sql(
                """
                INSERT INTO judy_ai.contract_fields
                    (run_id, contract_id, field_type, label, signer_role, page,
                     position, is_required)
                VALUES
                    (:run_id::uuid, :contract_id::uuid, :field_type, :label, :signer_role,
                     :page, :position::jsonb, :is_required)
                """,
                [
                    string_param("run_id", run_id),
                    string_param("contract_id", contract_id),
                    string_param("field_type", field["field_type"]),
                    string_param("label", field.get("label")),
                    string_param("signer_role", field.get("signer_role")),
                    long_param("page", field.get("page")),
                    string_param(
                        "position",
                        json.dumps(field["position"]) if field.get("position") else None,
                    ),
                    bool_param("is_required", field.get("is_required", True)),
                ],
            )
            inserted += 1
        except ClientError as e:
            print(f"Failed to insert field {field.get('label')!r}: {e}")
    return inserted


# =============================================================
# Bedrock
# =============================================================
def invoke_model(system_prompt, user_prompt):
    """One Converse call. Bedrock errors are printed before re-raising."""
    try:
        response = bedrock_runtime.converse(
            modelId=MODEL_ID,
            system=[{"text": system_prompt}],
            messages=[{"role": "user", "content": [{"text": user_prompt}]}],
            inferenceConfig={"maxTokens": 4096, "temperature": 0},
        )
    except ClientError as e:
        print(f"BEDROCK ERROR modelId={MODEL_ID}: {e}")
        raise

    text = "".join(
        block.get("text", "") for block in response["output"]["message"]["content"]
    )
    return text, response.get("usage", {})


def parse_json(text, expect):
    """
    Pull a JSON object or array out of a model response.

    expect is "object" or "array". Models wrap JSON in prose or fences often
    enough that a bare json.loads would make the agent flaky for no reason.
    """
    open_char, close_char = ("{", "}") if expect == "object" else ("[", "]")

    fenced = re.search(r"```(?:json)?\s*(.+?)```", text, re.DOTALL)
    candidate = fenced.group(1).strip() if fenced else text.strip()

    if not candidate.startswith(open_char):
        start = candidate.find(open_char)
        end = candidate.rfind(close_char)
        if start == -1 or end == -1:
            print(f"No JSON {expect} in model response: {text[:400]}")
            return None
        candidate = candidate[start:end + 1]

    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError as e:
        print(f"Could not parse model response as JSON ({e}): {candidate[:400]}")
        return None

    wanted = dict if expect == "object" else list
    return parsed if isinstance(parsed, wanted) else None


# =============================================================
# Prompts
# =============================================================
def classification_prompt(contract_text):
    """Call 1 - which of the three predefined template types is this?"""
    system = (
        "You classify commercial contracts into a fixed set of document types. "
        "You answer only from what the document says about itself and its structure. "
        "You are precise and you do not guess."
    )

    user = f"""Classify the contract below as exactly one of these types:

- "msa" - a master agreement: sets the general terms (liability, confidentiality, payment
  mechanics, IP, termination) under which the parties will later agree specific work or
  purchases through statements of work, order forms or similar documents attached to or
  referenced by it. Titles like "Master Services Agreement", "Master Subscription
  Agreement" or "Master Agreement", or a body that repeatedly refers to future SOWs or
  Order Forms governed by it.
- "purchase_agreement" - an agreement to buy a specific product, software licence or
  subscription: a defined deliverable or licence, a price, delivery or activation terms.
  Titles like "Purchase Agreement", "Software License Agreement", "Subscription Agreement".
- "sow" - a statement of work: a document that sits UNDER a governing master agreement and
  describes one specific engagement - scope, deliverables, timeline or milestones, and the
  fees for that engagement. It refers to the master agreement for the general terms
  instead of restating them. Change orders hang off this type.
- "unknown" - anything else. Non-disclosure agreements, data processing addenda, privacy
  policies, terms of use, consent forms, advisor agreements, vendor agreements, consulting
  or services agreements that stand on their own, all belong here. Do not force a document
  into one of the three types because it is loosely commercial.

The structural test that matters most: a self-contained agreement that carries its own
full set of general terms (liability, indemnity, confidentiality, termination, governing
law) and does not reference a separate governing master agreement is NOT a "sow", however
much it describes services. A vendor agreement or services agreement of that kind is
"unknown". A "sow" is short on general terms because the master agreement holds them.

Return ONLY a JSON object:
{{
  "template_type": "msa" | "purchase_agreement" | "sow" | "unknown",
  "rationale": "one or two sentences pointing at the title, structure or wording that decided it",
  "confidence": "high" | "medium" | "low"
}}
No prose outside the JSON object.

=== CONTRACT ===
{contract_text}"""

    return system, user


def fields_prompt(contract_text):
    """Call 2 - every signature block and fill-in field, as the document labels it."""
    system = (
        "You locate signature blocks and fill-in fields in contracts so a signing tool can "
        "place them. You report what is printed on the page. You never invent fields that "
        "are not there and you never estimate coordinates."
    )

    user = f"""Find every signature block and fill-in field in the contract below.

What counts:
- signature lines ("Signature:", "By:", "(Signature)", "Authorized Signature")
- initials lines
- date lines next to a signature ("Date:", "Date (as of):")
- printed-name lines ("Name:", "Name (Please Print)", "Printed Name")
- title lines ("Title:")
- company or party-name lines in a signature block ("Company:", "Vendor:", "Customer:")
- other blanks a party must complete before or at signing (address, email, order form
  values such as "Billing Contact Name: ____"), as "text"

What does not count:
- defined-term placeholders in the body such as "[Company]" or "[Customer]"
- table headings or clause headings
- anything you cannot quote as a label printed in the document

Signature blocks are usually laid out as two columns, one per party, with the same labels
repeated side by side. In the text this shows up as two labels on one line ("Name: ____
Name: ____"), as the same label printed twice in a row ("Signature:" then "Signature:"
again), or as one party's labels inside a table and the other's as plain lines. Report
each party's field separately and say which column it is in: "left" for the first party's
column, "right" for the second, "single" when a label appears once across the page.

Count repeated labels. If "Signature:" is printed twice on a page there are two signature
fields, one per party; if "Date:" is printed twice there are two date fields. Never merge
them into one. Every party that has a signature block gets its own full set of fields.

"By:" inside a signature block is the signature line for that party. Always report it as
"signature", never skip it.

Fill-in blocks can appear on the first page (a cover block with the vendor's name, title
and date) as well as on the execution page at the end. Report every page's blocks.

signer_role is who fills the field in: use the role the document uses ("supplier",
"vendor", "company", "customer", "advisor", "contractor", "witness"), lower case. Work it
out from the party name or heading above that column.

Page numbers come from the nearest [page N] marker above the field.

Return ONLY a JSON array. Each element must be:
{{
  "field_type": "signature" | "initial" | "date" | "full_name" | "title" | "company" | "text",
  "label": "the label exactly as printed, including its colon, e.g. \\"Name:\\"",
  "signer_role": "lower-case role, or null if it cannot be determined",
  "party_heading": "the party name or heading printed at the top of this field's block, exactly as printed, e.g. \\"Judefly, Inc.\\" or \\"ADVISOR:\\", or null",
  "page": <integer page number>,
  "column": "left" | "right" | "single",
  "is_required": true | false
}}
Return [] if the document has no signature blocks or fill-in fields. No prose outside the
JSON array.

=== CONTRACT ===
{contract_text}"""

    return system, user


# =============================================================
# Positions from BDA text lines
# =============================================================
def normalise_label(text):
    """Strip markdown emphasis, underscores, punctuation and case for matching."""
    text = re.sub(r"[*_\\]+", "", text or "")
    text = re.sub(r"\s+", " ", text).strip().lower()
    return text.rstrip(":").strip()


def load_text_lines(raw_output_s3_key):
    """
    Read BDA's result.json and index its text_lines by page.

    Returns {page_number (1-indexed): [line, ...]} or {} when the output has
    no bounding boxes - which is what the public-default BDA project produces.
    """
    if not raw_output_s3_key or not BDA_OUTPUT_BUCKET:
        print("No BDA output key or bucket - positions will be null")
        return {}
    if not raw_output_s3_key.endswith("result.json"):
        print(f"BDA output key is not a result.json ({raw_output_s3_key}) - positions will be null")
        return {}

    body = s3_client.get_object(Bucket=BDA_OUTPUT_BUCKET, Key=raw_output_s3_key)["Body"].read()
    return index_text_lines(json.loads(body))


def index_text_lines(document):
    """Group a BDA standard-output document's text_lines by 1-indexed page."""
    by_page = {}
    for line in document.get("text_lines", []):
        locations = line.get("locations") or []
        if not locations:
            continue
        box = locations[0].get("bounding_box") or {}
        if not all(k in box for k in ("left", "top", "width", "height")):
            continue
        page = int(locations[0].get("page_index", line.get("page_index", 0))) + 1
        by_page.setdefault(page, []).append({
            "id": line.get("id"),
            "text": line.get("text") or "",
            "norm": normalise_label(line.get("text") or ""),
            "left": float(box["left"]),
            "top": float(box["top"]),
            "width": float(box["width"]),
            "height": float(box["height"]),
        })

    for lines in by_page.values():
        lines.sort(key=lambda l: (l["top"], l["left"]))

    if not by_page:
        print("BDA output has no text_lines with bounding boxes - positions will be null")
    return by_page


def row_mates(line, lines):
    """Lines on the same printed row as `line`, left to right, including it."""
    tolerance = max(ROW_TOLERANCE, line["height"])
    same_row = [l for l in lines if abs(l["top"] - line["top"]) < tolerance]
    return sorted(same_row, key=lambda l: l["left"])


def field_box(anchor, row):
    """
    Derive the fill-in box from its label's line box.

    The box starts just right of the label and runs to the next thing on the
    same row (the other party's column, typically) or a default width.
    """
    x = anchor["left"] + anchor["width"] + FIELD_GAP
    right_neighbours = [l["left"] for l in row if l["left"] > anchor["left"] + anchor["width"]]
    if right_neighbours:
        width = min(right_neighbours) - x - FIELD_GAP
    else:
        width = DEFAULT_FIELD_WIDTH
    width = max(MIN_FIELD_WIDTH, min(width, 0.98 - x))
    return x, width


def label_matches(wanted, line):
    return line["norm"] == wanted or (len(wanted) > 3 and line["norm"].startswith(wanted))


class Placement:
    """
    Per-document state for anchoring fields to BDA lines.

    Tracks which lines are already taken, where each party's column is, and
    the last anchor placed for each party so label-less lines can be assigned
    in order.
    """

    def __init__(self, lines_by_page):
        self.lines_by_page = lines_by_page
        self.used_ids = set()
        self.column_x = {}        # (page, role) -> left edge of that party's block
        self.last_anchor = {}     # (page, role) -> last anchor line placed for that party

    def prepare(self, fields):
        """
        Resolve every party's column before any field is placed.

        Placement of one party depends on knowing where the *other* party is,
        so this cannot be done lazily in whatever order the model listed the
        fields - the first party placed would have nothing to keep away from.
        """
        for field in fields:
            page_lines = self.lines_by_page.get(field.get("page"))
            if page_lines:
                self.party_column(field, page_lines)

    def party_column(self, field, page_lines):
        """Left edge of the party's block, from its printed heading if BDA read it."""
        key = (field.get("page"), field.get("signer_role"))
        if key in self.column_x:
            return self.column_x[key]

        heading = normalise_label(field.get("party_heading"))
        x = None
        if heading:
            for line in page_lines:
                if line["norm"] and (line["norm"] == heading or heading in line["norm"]
                                     or line["norm"] in heading):
                    x = line["left"]
                    break
        self.column_x[key] = x
        return x

    def other_columns(self, field):
        page = field.get("page")
        return [x for (p, role), x in self.column_x.items()
                if p == page and role != field.get("signer_role") and x is not None]

    def choose(self, field, candidates):
        """
        Pick the best unused candidate anchor for a field.

        Preference: the candidate nearest the party's own column; failing a
        known column, the one farthest from any other party's column; failing
        both, the column hint from the model.
        """
        candidates = [c for c in candidates if c["id"] not in self.used_ids]
        if not candidates:
            return None

        own_x = self.column_x.get((field.get("page"), field.get("signer_role")))
        if own_x is not None:
            return min(candidates, key=lambda c: abs(c["left"] - own_x))

        others = self.other_columns(field)
        if others:
            return max(candidates, key=lambda c: min(abs(c["left"] - x) for x in others))

        column = field.get("column") or "single"
        ordered = sorted(candidates, key=lambda c: c["left"])
        if column == "left":
            return ordered[0]
        if column == "right":
            return ordered[-1]
        return next((c for c in candidates if c["norm"]), candidates[0])


def locate_field(field, placement, allow_fallback=True):
    """
    Anchor a detected field to a BDA text line and derive its box.

    Three ways to find the anchor, in order:
    1. A line whose text is the label.
    2. An empty-text line on the same printed row as such a line. BDA returns
       labels inside table cells this way - correct box, no text - which is how
       one party's whole column can come back blank.
    3. When no line carries the label at all (allow_fallback), the next unused
       empty line below the same party's previous field, preferring the same
       column; failing that, the nearest unused empty in that column. The
       document prints the labels in order, so the empties are in order too.

    Placement runs in two passes - labels BDA could read first, fallbacks
    second - so a fallback always has the party's real anchors to work from
    regardless of the order the model listed the fields in.

    Returns a position dict or None.
    """
    page_lines = placement.lines_by_page.get(field.get("page"))
    if not page_lines:
        return None

    wanted = normalise_label(field.get("label"))
    if not wanted:
        return None

    placement.party_column(field, page_lines)
    role_key = (field.get("page"), field.get("signer_role"))

    matches = [l for l in page_lines if label_matches(wanted, l)]
    candidates = []
    for match in matches:
        for mate in row_mates(match, page_lines):
            if mate is match or mate["norm"] == "" or label_matches(wanted, mate):
                if mate not in candidates:
                    candidates.append(mate)

    anchor = placement.choose(field, candidates)

    if anchor is None and not allow_fallback:
        return None

    if anchor is None:
        previous = placement.last_anchor.get(role_key)
        if previous is not None:
            empties = [
                l for l in page_lines
                if l["norm"] == "" and l["id"] not in placement.used_ids
                and previous["top"] < l["top"] < previous["top"] + 0.15
            ]
            same_column = [l for l in empties if abs(l["left"] - previous["left"]) < 0.03]
            pool = same_column or empties
            if pool:
                anchor = min(pool, key=lambda l: l["top"])
            else:
                # Nothing below: the nearest unused empty line in this party's
                # column anywhere on the page (a party-name cell at the top of
                # the block that BDA could not read, reported last by the model).
                same_column = [
                    l for l in page_lines
                    if l["norm"] == "" and l["id"] not in placement.used_ids
                    and abs(l["left"] - previous["left"]) < 0.03
                    and abs(l["top"] - previous["top"]) < 0.25
                ]
                if same_column:
                    anchor = min(same_column, key=lambda l: abs(l["top"] - previous["top"]))

    if anchor is None:
        return None

    placement.used_ids.add(anchor["id"])
    placement.last_anchor[role_key] = anchor
    # A party whose heading BDA could not read is pinned to the column of its
    # first placed field, so the rest of its fields stay on the same side.
    if placement.column_x.get(role_key) is None:
        placement.column_x[role_key] = anchor["left"]
    field["_anchor"] = anchor
    return box_from_anchor(field["field_type"], anchor, page_lines)


def box_from_anchor(field_type, anchor, page_lines):
    """The fill-in box for a field whose label sits on the given BDA line."""
    row = row_mates(anchor, page_lines)
    x, width = field_box(anchor, row)
    height = FIELD_HEIGHT.get(field_type, DEFAULT_FIELD_HEIGHT)

    # Do not run into the next printed line in the same column - "Name:" and
    # "Title:" are often stacked with almost no gap.
    below = [
        l["top"] for l in page_lines
        if l["top"] > anchor["top"] + anchor["height"] * 0.5
        and l["left"] < anchor["left"] + anchor["width"] + 0.02
        and l["left"] + l["width"] > anchor["left"]
    ]
    if below:
        gap = min(below) - anchor["top"]
        height = max(min(height, gap * 0.9), anchor["height"] * 1.2)

    # Centre a text box on the label line; hang a signature box from it so the
    # drawn signature sits on the printed line, not above it.
    y = anchor["top"] - (height - anchor["height"]) / 2
    if field_type in FIELD_HEIGHT:
        y = anchor["top"] - (height - anchor["height"]) * 0.6
    y = max(0.0, min(y, 1.0 - height))

    return {
        "x": round(x, 4),
        "y": round(y, 4),
        "width": round(width, 4),
        "height": round(height, 4),
    }


def mirror_missing_fields(fields, placement):
    """
    Give the other party the fields the model reported for only one of them.

    Signature blocks are symmetric: two columns, the same labels. When a
    placed field's printed row has an unused twin - the same label, or an
    empty-text cell BDA could not read - and no other party has that label on
    that page, the twin is that party's field. The model is asked to report
    both, but "Signature:" printed twice in a row still comes back once
    often enough that the geometry has to be the guarantee.
    """
    mirrored = []
    for field in list(fields):
        anchor = field.get("_anchor")
        page = field.get("page")
        if anchor is None or page is None:
            continue

        page_lines = placement.lines_by_page.get(page, [])
        wanted = normalise_label(field["label"])
        twins = [
            mate for mate in row_mates(anchor, page_lines)
            if mate is not anchor and mate["id"] not in placement.used_ids
            and (mate["norm"] == "" or label_matches(wanted, mate))
        ]
        if not twins:
            continue

        roles_on_page = {
            f.get("signer_role") for f in fields + mirrored
            if f.get("page") == page and f.get("signer_role")
        }
        other_roles = roles_on_page - {field.get("signer_role")}
        if len(other_roles) != 1:
            # No second party on this page, or more than one - do not guess.
            continue
        other_role = next(iter(other_roles))

        already = any(
            f.get("page") == page and f.get("signer_role") == other_role
            and normalise_label(f["label"]) == wanted
            for f in fields + mirrored
        )
        if already:
            continue

        twin = min(twins, key=lambda m: abs(m["left"] - anchor["left"]))
        placement.used_ids.add(twin["id"])
        mirrored.append({
            **{k: v for k, v in field.items() if k != "_anchor"},
            "signer_role": other_role,
            "column": "left" if twin["left"] < anchor["left"] else "right",
            "position": box_from_anchor(field["field_type"], twin, page_lines),
            "_anchor": twin,
        })
    return mirrored


# =============================================================
# Normalisation
# =============================================================
def normalise_detection(parsed):
    """Validate the classification response. Falls back to 'unknown', never fails."""
    if not isinstance(parsed, dict):
        print("Classification response unusable - recording 'unknown'")
        return {"template_type": "unknown", "rationale": None, "confidence": "low"}

    template_type = str(parsed.get("template_type", "")).strip().lower()
    if template_type not in TEMPLATE_TYPES:
        print(f"Invalid template_type {template_type!r} - recording 'unknown'")
        template_type = "unknown"

    rationale = str(parsed.get("rationale") or "").strip() or None
    confidence = str(parsed.get("confidence") or "").strip().lower()
    if confidence not in ("high", "medium", "low"):
        confidence = None

    return {"template_type": template_type, "rationale": rationale, "confidence": confidence}


def normalise_field(item):
    """Validate one detected field. Returns None if unusable."""
    if not isinstance(item, dict):
        return None

    field_type = str(item.get("field_type", "")).strip().lower()
    if field_type not in FIELD_TYPES:
        print(f"Dropping field with invalid type {field_type!r}: {item.get('label')!r}")
        return None

    label = str(item.get("label") or "").strip()
    if not label:
        print(f"Dropping field without a label: {item}")
        return None

    page = item.get("page")
    try:
        page = int(page) if page is not None else None
    except (TypeError, ValueError):
        page = None
    if page is not None and page < 1:
        page = None

    signer_role = str(item.get("signer_role") or "").strip().lower() or None
    party_heading = str(item.get("party_heading") or "").strip() or None
    column = str(item.get("column") or "single").strip().lower()
    if column not in COLUMNS:
        column = "single"

    is_required = item.get("is_required")
    if not isinstance(is_required, bool):
        is_required = True

    return {
        "field_type": field_type,
        "label": label[:200],
        "signer_role": signer_role,
        "party_heading": party_heading,
        "page": page,
        "column": column,
        "is_required": is_required,
        "position": None,
    }


# =============================================================
# Handler
# =============================================================
def analyse_text(contract_text, lines_by_page):
    """
    Run both model calls and resolve positions. Pure - no database access.

    Returns (detection, fields, raw, usage). Shared with the local evaluator so
    the code that runs in Lambda is exactly the code that gets measured.
    """
    if len(contract_text) > MAX_CONTRACT_CHARS:
        print(f"Contract text {len(contract_text)} chars, truncating to {MAX_CONTRACT_CHARS}")
        contract_text = contract_text[:MAX_CONTRACT_CHARS]

    calls = (
        ("classification", classification_prompt),
        ("fields", fields_prompt),
    )
    with ThreadPoolExecutor(max_workers=len(calls)) as pool:
        futures = {name: pool.submit(invoke_model, *builder(contract_text))
                   for name, builder in calls}
        results = {name: future.result() for name, future in futures.items()}

    raw = {}
    usage_total = {"inputTokens": 0, "outputTokens": 0}
    for name, (text, usage) in results.items():
        raw[name] = text
        usage_total["inputTokens"] += usage.get("inputTokens", 0)
        usage_total["outputTokens"] += usage.get("outputTokens", 0)

    detection = normalise_detection(parse_json(raw["classification"], "object"))
    print(f"Classified as {detection['template_type']} ({detection['confidence']})")

    fields = []
    placement = Placement(lines_by_page)
    parsed_fields = parse_json(raw["fields"], "array") or []
    print(f"Field detection returned {len(parsed_fields)} item(s)")
    for item in parsed_fields:
        field = normalise_field(item)
        if field is not None:
            fields.append(field)

    # Pass 1: labels BDA read. Pass 2: labels it did not, placed relative to
    # the anchors pass 1 established for the same party.
    placement.prepare(fields)
    for field in fields:
        field["position"] = locate_field(field, placement, allow_fallback=False)
    for field in fields:
        if field["position"] is None:
            field["position"] = locate_field(field, placement, allow_fallback=True)

    mirrored = mirror_missing_fields(fields, placement)
    if mirrored:
        print(f"{len(mirrored)} field(s) added by mirroring the other party's column")
        fields.extend(mirrored)

    for field in fields:
        field.pop("_anchor", None)

    located = sum(1 for f in fields if f["position"])
    print(f"{len(fields)} field(s) kept, {located} with positions")
    return detection, fields, raw, usage_total


def analyse(contract_id, extraction_id):
    contract_text, raw_key = fetch_extraction(extraction_id)
    run_id = start_run(contract_id, extraction_id)
    print(f"Run {run_id} started for contract {contract_id}")

    started = time.time()
    try:
        lines_by_page = load_text_lines(raw_key)
        detection, fields, raw, usage = analyse_text(contract_text, lines_by_page)

        latency_ms = int((time.time() - started) * 1000)
        insert_detection(run_id, contract_id, detection)
        inserted = insert_fields(run_id, contract_id, fields)
        complete_run(run_id, raw, usage, latency_ms)

        print(f"Run {run_id} succeeded: {detection['template_type']}, "
              f"{inserted} field(s) stored in {latency_ms} ms")
        return {"run_id": run_id, "template_type": detection["template_type"],
                "fields": inserted}

    except Exception as e:
        print(f"Run {run_id} failed: {e}")
        fail_run(run_id, str(e))
        raise


def lambda_handler(event, context):
    print(f"Event received: {json.dumps(event)[:500]}")

    contract_id = event.get("contract_id")
    extraction_id = event.get("extraction_id")

    if not contract_id or not extraction_id:
        raise ValueError("contract_id and extraction_id are required")

    return analyse(contract_id, extraction_id)

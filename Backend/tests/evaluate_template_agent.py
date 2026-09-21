"""
Exercise the template pre-population agent (JAAPESWM-23) locally.

Two measurements:

1. Classification on the corpus - each corpus document has a known base
   template, so the expected type is known (MSA-based documents are "msa",
   vendor and advisor agreements are "unknown").

2. Field detection and placement on real BDA output - result.json files
   produced by the bounding-box BDA project for the client's own templates.
   Every detected field is anchored to a BDA text line, and the resolved
   boxes are drawn onto the page images so placement can be checked by eye.
   That drawing is what goes in front of the client for manual validation.

Usage:
    python evaluate_template_agent.py                 # everything
    python evaluate_template_agent.py --fields-only   # skip corpus classification
    python evaluate_template_agent.py --pages DIR     # where page PNGs live, for overlays

Page images are BDA's rectified_image_N.png assets. They are not committed
(1-3 MB each); pass --pages pointing at a directory containing
<fixture-stem>_p<N>.png, e.g. vendor-agreement_p6.png.
"""

import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
CORPUS = os.path.join(HERE, "corpus")
FIXTURES = os.path.join(HERE, "bda_fixtures")

os.environ.setdefault("AURORA_CLUSTER_ARN", "unused-for-evaluation")
os.environ.setdefault("AURORA_SECRET_ARN", "unused-for-evaluation")
os.environ.setdefault("AURORA_DATABASE", "unused-for-evaluation")

sys.path.insert(0, os.path.join(HERE, "..", "lambdas", "agent_template_prepopulation"))
import handler  # noqa: E402

# Expected classification per corpus base template. The client's templates
# contain no purchase agreement or statement of work, so those two types are
# not covered here yet - see the note in the summary output.
EXPECTED_BY_BASE = {
    "template.md": "msa",
    "}[Judefly][Vendor Agreement].docx": "unknown",
    "advisor-agreement.docx": "unknown",
}

# What each fixture's signature blocks actually contain, from reading the
# documents. Used to report recall on field detection.
FIXTURE_EXPECTATIONS = {
    "vendor-agreement": {
        "type": "unknown",
        "signers": 2,
        "signature_lines": 2,   # page 6: "Signature:" x2
        "date_lines": 3,        # page 6: "Date:" x2, page 1: "Date (as of):"
        "min_fields": 12,
        # Which side of the page each party's execution block is on. The model's
        # own column hint has been wrong on this document; geometry decides.
        "sides": {"vendor": "left", "company": "right"},
    },
    "advisor-agreement": {
        "type": "unknown",
        "signers": 2,
        "signature_lines": 2,   # page 5: "By:" (company) and "(Signature)" (advisor)
        "date_lines": 0,
        "min_fields": 6,
        "sides": {},            # both blocks are stacked in the right half
    },
}


def page_text(document):
    """Same page-marker text the extraction Lambda stores in extracted_text."""
    pages = sorted(document.get("pages", []), key=lambda p: p.get("page_index", 0))
    blocks = []
    for page in pages:
        markdown = (page.get("representation") or {}).get("markdown")
        if isinstance(markdown, str) and markdown.strip():
            blocks.append(f"[page {page.get('page_index', 0) + 1}]\n{markdown.strip()}")
    return "\n\n".join(blocks)


def classify_corpus():
    with open(os.path.join(CORPUS, "manifest.json"), encoding="utf-8") as fh:
        manifest = json.load(fh)

    print("=" * 78)
    print("CLASSIFICATION - corpus documents")
    correct = 0
    for spec in manifest["documents"]:
        expected = EXPECTED_BY_BASE.get(spec["base_template"])
        with open(os.path.join(CORPUS, spec["file"]), encoding="utf-8") as fh:
            text = fh.read()[:handler.MAX_CONTRACT_CHARS]

        started = time.time()
        system, user = handler.classification_prompt(text)
        raw, _ = handler.invoke_model(system, user)
        detection = handler.normalise_detection(handler.parse_json(raw, "object"))
        ms = int((time.time() - started) * 1000)

        ok = detection["template_type"] == expected
        correct += ok
        mark = "ok " if ok else "XX "
        print(f"  {mark}{spec['id']:34} got {detection['template_type']:18} "
              f"expected {expected:8} [{detection['confidence']}] {ms} ms")
        if not ok:
            print(f"       rationale: {detection['rationale']}")

    print(f"  classification: {correct}/{len(manifest['documents'])} correct")
    return correct, len(manifest["documents"])


def draw_overlay(stem, page, fields, pages_dir):
    """Draw resolved boxes on the BDA page image, if it is available."""
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return None

    image_path = os.path.join(pages_dir, f"{stem}_p{page}.png")
    if not os.path.exists(image_path):
        return None

    image = Image.open(image_path).convert("RGB")
    width, height = image.size
    draw = ImageDraw.Draw(image)
    colours = {"signature": (220, 30, 30), "date": (30, 120, 220), "full_name": (30, 160, 60),
               "title": (200, 120, 0), "company": (120, 60, 180), "initial": (220, 30, 140),
               "text": (90, 90, 90)}

    for field in fields:
        pos = field.get("position")
        if not pos or field.get("page") != page:
            continue
        x0 = pos["x"] * width
        y0 = pos["y"] * height
        x1 = (pos["x"] + pos["width"]) * width
        y1 = (pos["y"] + pos["height"]) * height
        colour = colours.get(field["field_type"], (0, 0, 0))
        draw.rectangle([x0, y0, x1, y1], outline=colour, width=6)
        draw.text((x0 + 8, y0 - 34), f"{field['field_type']} / {field.get('signer_role')}",
                  fill=colour)

    out_path = os.path.join(pages_dir, f"{stem}_p{page}_overlay.png")
    image.save(out_path)
    return out_path


def evaluate_fixture(path, pages_dir):
    stem = os.path.basename(path).replace(".json", "")
    with open(path, encoding="utf-8") as fh:
        document = json.load(fh)

    text = page_text(document)
    lines_by_page = handler.index_text_lines(document)

    started = time.time()
    detection, fields, raw, usage = handler.analyse_text(text, lines_by_page)
    ms = int((time.time() - started) * 1000)
    tokens = usage.get("inputTokens", 0) + usage.get("outputTokens", 0)

    expect = FIXTURE_EXPECTATIONS.get(stem, {})
    print("=" * 78)
    print(f"{stem}  ({ms} ms, {tokens} tokens, {len(text)} chars, "
          f"{sum(len(v) for v in lines_by_page.values())} BDA lines)")
    type_mark = "ok " if detection["template_type"] == expect.get("type") else "?? "
    print(f"  {type_mark}type {detection['template_type']} [{detection['confidence']}] - "
          f"{detection['rationale']}")

    located = [f for f in fields if f["position"]]
    print(f"  fields: {len(fields)} detected, {len(located)} positioned "
          f"(expected at least {expect.get('min_fields', '?')})")

    signatures = [f for f in fields if f["field_type"] == "signature"]
    dates = [f for f in fields if f["field_type"] == "date"]
    roles = {f["signer_role"] for f in fields if f["signer_role"]}
    print(f"  signature lines {len(signatures)} (expected {expect.get('signature_lines', '?')}), "
          f"date lines {len(dates)} (expected {expect.get('date_lines', '?')}), "
          f"signer roles {sorted(roles)} (expected {expect.get('signers', '?')})")

    sides = expect.get("sides") or {}
    wrong_side = [
        f for f in located
        if f["signer_role"] in sides
        and (("left" if f["position"]["x"] < 0.5 else "right") != sides[f["signer_role"]])
    ]
    if sides:
        mark = "ok " if not wrong_side else "XX "
        print(f"  {mark}columns: {len(wrong_side)} field(s) on the wrong party's side")

    for field in sorted(fields, key=lambda f: (f["page"] or 0, (f["position"] or {}).get("y", 9),
                                               (f["position"] or {}).get("x", 9))):
        pos = field["position"]
        where = (f"x={pos['x']:.3f} y={pos['y']:.3f} w={pos['width']:.3f} h={pos['height']:.3f}"
                 if pos else "NO POSITION")
        print(f"    p{field['page']} {field['field_type']:10} {field['column']:6} "
              f"{(field['signer_role'] or '-'):10} {field['label'][:28]:28} {where}")

    pages = sorted({f["page"] for f in located if f["page"]})
    for page in pages:
        out = draw_overlay(stem, page, fields, pages_dir)
        if out:
            print(f"  overlay written: {out}")

    return fields


def main():
    args = sys.argv[1:]
    pages_dir = HERE
    if "--pages" in args:
        pages_dir = args[args.index("--pages") + 1]
    fields_only = "--fields-only" in args

    if not fields_only:
        classify_corpus()

    fixtures = sorted(
        os.path.join(FIXTURES, name) for name in os.listdir(FIXTURES) if name.endswith(".json")
    ) if os.path.isdir(FIXTURES) else []

    for path in fixtures:
        evaluate_fixture(path, pages_dir)

    print("=" * 78)
    print("NOTE: no purchase agreement or SOW sample exists in the client's templates yet, "
          "so those two classifications are untested. Eean owes MSA/SOW samples.")


if __name__ == "__main__":
    main()

"""
Measure the obligation tracking agent (JAAPESWM-24) against the corpus.

Three checks per document:

    grounded   - every obligation's quoted sentence is really in the contract
    dates      - due_date is only ever set when the quoted sentence (or the
                 document) actually contains a calendar date or a period tied
                 to one; and every obligation that mentions timing has
                 raw_date_text (the pair the UI relies on)
    expected   - obligations the document is known to contain were found,
                 matched by keywords in the description or quote

The corpus documents were built for the risk agent, but the vendor and MSA
templates carry real obligations (invoice within 24 hours, 30 days' notice,
return documents within 72 hours, 90-day non-renewal window, escalating
commission), which is what this checks for.

Usage:
    python evaluate_obligation_agent.py [document_id ...] [--show]
"""

import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
CORPUS = os.path.join(HERE, "corpus")

os.environ.setdefault("AURORA_CLUSTER_ARN", "unused-for-evaluation")
os.environ.setdefault("AURORA_SECRET_ARN", "unused-for-evaluation")
os.environ.setdefault("AURORA_DATABASE", "unused-for-evaluation")

sys.path.insert(0, os.path.join(HERE, "..", "lambdas", "agent_obligation_tracking"))
import handler  # noqa: E402

# Obligations each corpus document is known to state, as keyword sets. One
# obligation matches an expectation when every keyword appears in its
# description or quote (case-insensitive).
EXPECTED = {
    "01_vendor_site_and_systems": [("invoice", "24 hours"), ("30 days", "notice"), ("delay",)],
    "02_vendor_subcontractors_and_pii": [("invoice", "24 hours"), ("72 hours",), ("30 days", "notice")],
    "03_msa_marketing_and_brand": [("90 days",), ("renew",)],
    "04_msa_contacts_and_outreach": [("90 days",), ("60 days",)],
    "05_vendor_platform_and_training": [("invoice", "24 hours"), ("20 days",)],
    "06_vendor_customer_content": [("invoice", "24 hours"), ("30 days", "notice")],
    "07_msa_dates_and_thresholds": [("renew",), ("commission",), ("purchase order",), ("90 days",)],
    "08_control_advisor_agreement": [("12", "month"), ("30", "days"), ("one year",)],
}
CALENDAR_DATE = re.compile(r"\b(20\d\d-\d\d-\d\d|\d{1,2} \w+ 20\d\d|\w+ \d{1,2}, 20\d\d)\b")


def normalise(text):
    text = re.sub(r"<[^>]+>", "", text or "")
    text = re.sub(r"[*_`\[\]\\]", "", text)
    text = re.sub(r"[“”]", '"', text)
    text = re.sub(r"[‘’]", "'", text)
    return re.sub(r"\s+", " ", text).strip().lower()


def grounded(quote, text_norm):
    parts = [p for p in re.split(r"\.\.\.|…", quote or "") if normalise(p)]
    if len(parts) > 1:
        return all(grounded(p, text_norm) for p in parts)
    q = normalise(quote)
    if not q:
        return False
    if q in text_norm:
        return True
    words = q.split()
    if len(words) < 6:
        return False
    windows = [" ".join(words[i:i + 6]) for i in range(len(words) - 5)]
    return sum(1 for w in windows if w in text_norm) / len(windows) >= 0.8


def main():
    show = "--show" in sys.argv
    wanted = {a for a in sys.argv[1:] if not a.startswith("--")}
    with open(os.path.join(CORPUS, "manifest.json"), encoding="utf-8") as fh:
        manifest = json.load(fh)
    docs = [d for d in manifest["documents"] if not wanted or d["id"] in wanted]

    tot = {"obl": 0, "grounded": 0, "expected": 0, "found": 0, "bad_dates": 0, "no_raw": 0}

    for spec in docs:
        with open(os.path.join(CORPUS, spec["file"]), encoding="utf-8") as fh:
            text = fh.read()
        text_norm = normalise(text)
        doc_has_date = bool(CALENDAR_DATE.search(text))

        started = time.time()
        obligations, raw, usage = handler.analyse_text(text, signed_on=None)
        ms = int((time.time() - started) * 1000)
        tokens = usage.get("inputTokens", 0) + usage.get("outputTokens", 0)

        g = sum(1 for o in obligations if grounded(o["source_quote"], text_norm))
        # A due_date is only defensible when the document contains a calendar
        # date somewhere; with signed_on=None nothing else can anchor it.
        bad_dates = [o for o in obligations if o["due_date"] and not doc_has_date]
        timed = [o for o in obligations if re.search(r"\d|day|month|year|annual|quarter", o["description"].lower())]
        no_raw = [o for o in timed if not o["raw_date_text"]]

        expected = EXPECTED.get(spec["id"], [])
        found = []
        for keys in expected:
            hit = any(
                all(k.lower() in re.sub(r"\((\d+)\)", r"\1", " ".join(
                    [o["description"], o["source_quote"], o.get("raw_date_text") or ""])).lower()
                    for k in keys)
                for o in obligations)
            found.append(hit)

        tot["obl"] += len(obligations); tot["grounded"] += g
        tot["expected"] += len(expected); tot["found"] += sum(found)
        tot["bad_dates"] += len(bad_dates); tot["no_raw"] += len(no_raw)

        print("=" * 78)
        print(f"{spec['id']}  ({ms} ms, {tokens} tokens)")
        print(f"  {'ok ' if g == len(obligations) else 'XX '}grounded {g}/{len(obligations)}")
        print(f"  {'ok ' if not bad_dates else 'XX '}due dates without a calendar date in the document: {len(bad_dates)}")
        print(f"  {'ok ' if not no_raw else '?? '}timed obligations missing raw_date_text: {len(no_raw)}")
        print(f"  {'ok ' if all(found) else 'XX '}expected found {sum(found)}/{len(expected)}"
              + ("" if all(found) else "  MISSED: " + "; ".join("+".join(k) for k, f in zip(expected, found) if not f)))
        types = {}
        for o in obligations:
            types[o["obligation_type"]] = types.get(o["obligation_type"], 0) + 1
        print(f"  by type: {types}")
        if show:
            for o in obligations:
                mark = "ok " if grounded(o["source_quote"], text_norm) else "XX "
                page = f"p{o['source_page']}" if o["source_page"] else "  "
                due = o["due_date"] or "-"
                print(f"    {mark}{page} [{o['obligation_type']:10}] {(o['owner_party'] or '-'):9} "
                      f"due={due:10} rec={o['recurrence'] or '-':8} {o['description'][:70]}")
                if o["raw_date_text"]:
                    print(f"           when: {o['raw_date_text'][:90]}")

    print("=" * 78)
    if tot["obl"]:
        print(f"OVERALL grounded {tot['grounded']}/{tot['obl']} ({int(100 * tot['grounded'] / tot['obl'])}%) | "
              f"expected found {tot['found']}/{tot['expected']} | "
              f"unjustified due dates {tot['bad_dates']} | timed without wording {tot['no_raw']}")


if __name__ == "__main__":
    main()

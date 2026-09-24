"""
Measure the plain-language summary agent (JAAPESWM-25) against the corpus.

A summary cannot be scored for recall the way the risk agent can. What can be
measured is whether it is *faithful*:

    grounded   - a key point's quoted source sentence really is in the contract
    numbers    - every number, amount, percentage and period in the summary
                 text appears somewhere in the contract
    length     - the overview is within the target word range
    plainness  - a rough count of legalese the prompt asks to avoid

A key point whose quote is not in the document is the failure mode that
matters: it reads as fact and there is nothing on the page to check it
against.

Usage:
    python evaluate_summary_agent.py [document_id ...]
    python evaluate_summary_agent.py --show      # print the full summaries too
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

sys.path.insert(0, os.path.join(HERE, "..", "lambdas", "agent_summary"))
import handler  # noqa: E402

LEGALESE = ("shall", "hereinafter", "hereto", "thereof", "whereas", "notwithstanding",
            "indemnif", "pursuant", "aforementioned", "heretofore")


def normalise(text):
    text = re.sub(r"<[^>]+>", "", text or "")      # corpus docs carry <mark> highlights
    text = re.sub(r"[*_`\[\]\\]", "", text)
    text = re.sub(r"[“”]", '"', text)
    text = re.sub(r"[‘’]", "'", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip().lower()


def quote_in_text(quote, text_norm):
    """
    A quote is grounded if it appears in the document.

    Exact substring after normalisation first. Failing that, the quote is
    accepted when at least 80% of its 6-word windows appear - models
    sometimes drop a defined-term bracket or a stray comma mid-sentence.
    """
    # A quote joined with an ellipsis is two excerpts; each must be present.
    parts = [p for p in re.split(r"\.\.\.|…", quote or "") if normalise(p)]
    if len(parts) > 1:
        return all(quote_in_text(p, text_norm) for p in parts)

    q = normalise(quote)
    if not q:
        return False
    if q in text_norm:
        return True
    words = q.split()
    if len(words) < 6:
        return False
    windows = [" ".join(words[i:i + 6]) for i in range(len(words) - 5)]
    hits = sum(1 for w in windows if w in text_norm)
    return hits / len(windows) >= 0.8


NUMBER = re.compile(r"(?<![\w.])(\$?\d[\d,]*(?:\.\d+)?%?)(?![\w.])")
WORD_NUMBERS = ("one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
                "twelve", "thirty", "sixty", "ninety")


def numbers_in(text):
    found = set(m.group(1).strip("$%,.") for m in NUMBER.finditer(text))
    words = set(w for w in re.findall(r"[a-z]+", text.lower()) if w in WORD_NUMBERS)
    return {n for n in found if n}, words


def check_numbers(summary_text, contract_text):
    """Every numeric figure in the summary must appear in the contract, as digits or words."""
    digits, words = numbers_in(summary_text)
    contract_norm = normalise(contract_text)
    contract_digits, contract_words = numbers_in(contract_text)
    missing = []
    for n in digits:
        if n in contract_digits or n in contract_norm:
            continue
        # "12" in the summary may be "twelve" in the contract, and vice versa
        as_word = {"1": "one", "2": "two", "3": "three", "4": "four", "5": "five", "6": "six",
                   "7": "seven", "8": "eight", "9": "nine", "10": "ten", "12": "twelve",
                   "30": "thirty", "60": "sixty", "90": "ninety"}.get(n)
        if as_word and as_word in contract_words:
            continue
        missing.append(n)
    return missing


def evaluate(text, show):
    started = time.time()
    summary, raw, usage = handler.analyse_text(text)
    ms = int((time.time() - started) * 1000)
    tokens = usage.get("inputTokens", 0) + usage.get("outputTokens", 0)
    if summary is None:
        return None, ms, tokens

    text_norm = normalise(text)
    for kp in summary["key_points"]:
        kp["grounded"] = bool(kp["source_quote"]) and quote_in_text(kp["source_quote"], text_norm)
    summary["missing_numbers"] = check_numbers(summary["summary_text"], text)
    summary["legalese"] = [w for w in LEGALESE if w in summary["summary_text"].lower()]
    return summary, ms, tokens


def main():
    show = "--show" in sys.argv
    wanted = {a for a in sys.argv[1:] if not a.startswith("--")}

    with open(os.path.join(CORPUS, "manifest.json"), encoding="utf-8") as fh:
        manifest = json.load(fh)
    documents = [d for d in manifest["documents"] if not wanted or d["id"] in wanted]

    total_points = grounded_points = 0
    docs_with_bad_numbers = 0
    in_range = 0

    for spec in documents:
        with open(os.path.join(CORPUS, spec["file"]), encoding="utf-8") as fh:
            text = fh.read()

        summary, ms, tokens = evaluate(text, show)
        print("=" * 78)
        print(f"{spec['id']}  ({ms} ms, {tokens} tokens, {len(text)} chars)")
        if summary is None:
            print("  NO SUMMARY RETURNED")
            continue

        n = len(summary["key_points"])
        g = sum(1 for kp in summary["key_points"] if kp["grounded"])
        total_points += n
        grounded_points += g
        words = summary["word_count"]
        ok_len = handler.SUMMARY_MIN_WORDS <= words <= handler.SUMMARY_MAX_WORDS
        in_range += ok_len
        if summary["missing_numbers"]:
            docs_with_bad_numbers += 1

        print(f"  {'ok ' if ok_len else 'XX '}length {words} words "
              f"(target {handler.SUMMARY_MIN_WORDS}-{handler.SUMMARY_MAX_WORDS})")
        print(f"  {'ok ' if g == n else 'XX '}key points grounded {g}/{n}")
        print(f"  {'ok ' if not summary['missing_numbers'] else 'XX '}numbers not found in contract: "
              f"{summary['missing_numbers'] or 'none'}")
        print(f"  {'ok ' if not summary['legalese'] else '?? '}legalese: {summary['legalese'] or 'none'}")

        if show:
            print("\n  " + summary["summary_text"].replace("\n", "\n  "))
            print()
        for kp in summary["key_points"]:
            mark = "ok " if kp["grounded"] else "XX "
            page = f"p{kp['source_page']}" if kp["source_page"] else "  "
            print(f"    {mark}{page} {kp['point'][:96]}")
            if not kp["grounded"]:
                print(f"         quote not found: {str(kp['source_quote'])[:400]!r}")

    print("=" * 78)
    if total_points:
        print(f"OVERALL grounded key points {grounded_points}/{total_points} "
              f"({int(100 * grounded_points / total_points)}%) | "
              f"summaries in length range {in_range}/{len(documents)} | "
              f"documents with unverifiable numbers {docs_with_bad_numbers}/{len(documents)}")


if __name__ == "__main__":
    main()

"""
Measure the risk & clause agent against the test corpus (JAAPESWM-41).

Runs the agent's prompts over every corpus document and compares the playbook
rules it raises against the rules each document was built to trigger.

The SOW does not ask for an automated agent test suite - validation is manual,
with the client. This exists because prompt changes are otherwise judged by
eyeballing one document, which is how the first version of the playbook prompt
came to flag all eleven rules against an advisory agreement.

Reports:
    recall    - of the rules a document should raise, how many were raised
    false +   - rules raised that the document was not built to trigger
    control   - findings on the unmodified document, which should be near zero

Usage:
    python evaluate_risk_agent.py [document_id ...]
"""

import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
CORPUS = os.path.join(HERE, "corpus")

os.environ.setdefault("AURORA_CLUSTER_ARN", "unused-for-evaluation")
os.environ.setdefault("AURORA_SECRET_ARN", "unused-for-evaluation")
os.environ.setdefault("AURORA_DATABASE", "unused-for-evaluation")

sys.path.insert(0, os.path.join(HERE, "..", "lambdas", "agent_risk_clause"))
import handler  # noqa: E402


def evaluate(document, text):
    """Run both passes over one document and return findings plus timing."""
    result = {"playbook": [], "criteria": [], "ms": 0, "tokens": 0}
    started = time.time()

    for pass_name, builder, from_playbook in (
        ("playbook", handler.playbook_prompt, True),
        ("criteria", handler.criteria_prompt, False),
    ):
        system, user = builder(text)
        raw, usage = handler.invoke_model(system, user)
        result["tokens"] += usage.get("inputTokens", 0) + usage.get("outputTokens", 0)
        for item in handler.parse_findings(raw):
            row = handler.normalise(item, from_playbook)
            if row:
                result[pass_name].append(row)

    result["ms"] = int((time.time() - started) * 1000)
    return result


def main():
    with open(os.path.join(CORPUS, "manifest.json"), encoding="utf-8") as fh:
        manifest = json.load(fh)

    wanted = set(sys.argv[1:])
    documents = [
        d for d in manifest["documents"]
        if not wanted or d["id"] in wanted
    ]

    total_expected = 0
    total_found = 0
    total_false = 0

    for spec in documents:
        with open(os.path.join(CORPUS, spec["file"]), encoding="utf-8") as fh:
            text = fh.read()

        outcome = evaluate(spec, text)

        raised = {f["playbook_section"] for f in outcome["playbook"] if f["playbook_section"]}
        expected = set(spec["expects_rules"])
        hit = expected & raised
        missed = expected - raised
        extra = raised - expected

        total_expected += len(expected)
        total_found += len(hit)
        total_false += len(extra)

        is_control = not expected
        header = f"{spec['id']}  ({outcome['ms']} ms, {outcome['tokens']} tokens)"
        print("=" * 78)
        print(header)

        if is_control:
            print(f"  CONTROL - expected no playbook rules, raised {len(raised)}")
            if raised:
                print(f"    false positives: {', '.join(sorted(raised))}")
        else:
            pct = int(100 * len(hit) / len(expected))
            print(f"  recall {len(hit)}/{len(expected)} ({pct}%)")
            if missed:
                print(f"    MISSED: {', '.join(sorted(missed))}")
            if extra:
                print(f"    extra : {', '.join(sorted(extra))}")

        suggested = sum(1 for f in outcome["playbook"] if f["suggested_language"])
        print(f"  findings: {len(outcome['playbook'])} playbook "
              f"({suggested} with playbook wording), "
              f"{len(outcome['criteria'])} criteria")

        for finding in outcome["playbook"]:
            mark = "ok " if finding["playbook_section"] in expected else "?? "
            print(f"    {mark}[{finding['severity']:6}] {finding['playbook_section']} "
                  f"- {finding['title'][:52]}")

        if spec["expects_criteria"]:
            print(f"  criteria expected: {'; '.join(spec['expects_criteria'])}")
            for finding in outcome["criteria"]:
                print(f"       [{finding['severity']:6}] {finding['risk_category']:15} "
                      f"{finding['title'][:52]}")

    print("=" * 78)
    if total_expected:
        print(f"OVERALL recall {total_found}/{total_expected} "
              f"({int(100 * total_found / total_expected)}%) | "
              f"false positives across scenario docs: {total_false}")


if __name__ == "__main__":
    main()

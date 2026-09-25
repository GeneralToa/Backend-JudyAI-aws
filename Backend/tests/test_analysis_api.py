"""
Drive the analysis API handler locally with real HTTP API v2 events, against
the environment's database (JAAPESWM-43).

GET routes are read-only. The POST route is exercised in dry-run mode, which
creates the run rows exactly as production does and then marks them failed
("dry run - agent not invoked") instead of invoking the agents, so nothing is
left pending and no model runs.

Usage:
    python test_analysis_api.py [contract_id]

Environment: AWS_PROFILE=zq-appevo MSYS_NO_PATHCONV=1 PYTHONIOENCODING=utf-8
"""

import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))

os.environ.setdefault("AURORA_CLUSTER_ARN", "arn:aws:rds:us-west-2:580118073904:cluster:rag-app-prod-aurora-postgres")
os.environ.setdefault("AURORA_SECRET_ARN", "arn:aws:secretsmanager:us-west-2:580118073904:secret:/rds/rag-app-prod-judy-ai-writer/credentials-Zh88jo")
os.environ.setdefault("AURORA_DATABASE", "ragdb")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-west-2")
os.environ["ANALYSIS_API_DRY_RUN"] = "1"

sys.path.insert(0, os.path.join(HERE, "..", "lambdas", "analysis_api"))
import handler  # noqa: E402

DEFAULT_CONTRACT = "188e718e-00a9-4f32-a536-a1337bc31d5b"   # advisor-agreement.docx, analysed


def event(method, path, route, params, body=None, query=None):
    """The subset of an API Gateway HTTP API v2 event the handler reads."""
    return {
        "version": "2.0",
        "routeKey": f"{method} {route}",
        "rawPath": path,
        "pathParameters": params,
        "queryStringParameters": query,
        "body": body,
        "requestContext": {"http": {"method": method, "path": path},
                           "authorizer": {"jwt": {"claims": {"email": "test@local"}}}},
    }


def call(label, ev, expect):
    started = time.time()
    out = handler.lambda_handler(ev, None)
    ms = int((time.time() - started) * 1000)
    body = json.loads(out["body"])
    ok = out["statusCode"] == expect
    print(f"{'ok ' if ok else 'XX '}{label:58} -> {out['statusCode']} (expected {expect}) {ms} ms")
    return body, ok


def main():
    cid = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_CONTRACT
    base = f"/contracts/{cid}/analysis"
    results = []

    # --- status ---
    body, ok = call("GET status", event("GET", base, "/contracts/{contractId}/analysis", {"contractId": cid}), 200)
    results.append(ok)
    if ok:
        print(f"      extraction {body['extraction']['status']}, {body['extraction']['pageCount']} pages")
        for name, a in body["agents"].items():
            print(f"      {name:22} {a['status']:10} results={a['resultCount']}")
        assert set(body["agents"]) == {"riskClause", "templatePrepopulation", "summary", "obligationTracking"}

    # --- results per agent ---
    for route_agent, key in (("risk-clause", "risks"), ("template-prepopulation", "fields"),
                             ("summary", "keyPoints")):
        body, ok = call(f"GET results {route_agent}",
                        event("GET", f"{base}/{route_agent}", "/contracts/{contractId}/analysis/{agentType}",
                              {"contractId": cid, "agentType": route_agent}), 200)
        results.append(ok)
        if ok:
            items = body.get(key) or []
            print(f"      runId {body['runId'][:8]}… completedAt {body['completedAt']} | {key}: {len(items)}")
            if route_agent == "risk-clause" and items:
                r = items[0]
                assert {"id", "category", "severity", "title", "detail", "playbookSection",
                        "suggestedLanguage", "sourceQuote", "sourcePage"} <= set(r), r.keys()
                assert r["category"] in ("unusualTerm", "missingClause", "dateMismatch",
                                         "complianceGap", "trackedTerm"), r["category"]
                print(f"      first: [{r['severity']}] {r['category']} p{r['sourcePage']} {r['title']}")
            if route_agent == "template-prepopulation":
                assert body["detectedTemplateType"] in ("msa", "purchaseAgreement", "sow", "unknown")
                f = items[0]
                assert {"fieldType", "label", "signerRole", "page", "position", "isRequired"} <= set(f)
                assert f["fieldType"] in ("signature", "initial", "date", "fullName", "title", "company", "text")
                print(f"      type {body['detectedTemplateType']}; first field {f['fieldType']} '{f['label']}' "
                      f"{f['position']}")
            if route_agent == "summary":
                assert body["summaryText"] and isinstance(body["keyPoints"], list)
                print(f"      {body['wordCount']} words; first point: {body['keyPoints'][0][:80]}")

    # --- obligation tracking: not run yet -> 409 with status ---
    body, ok = call("GET results obligation-tracking (not run) -> 409",
                    event("GET", f"{base}/obligation-tracking", "/contracts/{contractId}/analysis/{agentType}",
                          {"contractId": cid, "agentType": "obligation-tracking"}), 409)
    results.append(ok and body["error"]["code"] == "ANALYSIS_NOT_READY")
    if ok:
        print(f"      {body['error']['code']} status={body['error'].get('status')}")

    # --- errors ---
    body, ok = call("GET results unknown agent -> 404",
                    event("GET", f"{base}/nope", "/contracts/{contractId}/analysis/{agentType}",
                          {"contractId": cid, "agentType": "nope"}), 404)
    results.append(ok)
    body, ok = call("GET status unknown contract -> 404",
                    event("GET", base, "/contracts/{contractId}/analysis",
                          {"contractId": "00000000-0000-0000-0000-000000000000"}), 404)
    results.append(ok and body["error"]["code"] == "CONTRACT_NOT_FOUND")
    body, ok = call("GET status bad id -> 400",
                    event("GET", base, "/contracts/{contractId}/analysis", {"contractId": "abc"}), 400)
    results.append(ok)

    # --- re-run (dry run: rows created then marked failed, agents not invoked) ---
    body, ok = call("POST re-run summary (dry run) -> 202",
                    event("POST", base, "/contracts/{contractId}/analysis", {"contractId": cid},
                          body=json.dumps({"agents": ["summary"]})), 202)
    results.append(ok)
    if ok:
        print(f"      runs: {[(r['agentType'], r['status'], r['runId'][:8]) for r in body['runs']]}")
    body, ok = call("POST re-run bad agent -> 400",
                    event("POST", base, "/contracts/{contractId}/analysis", {"contractId": cid},
                          body=json.dumps({"agents": ["chatbot"]})), 400)
    results.append(ok)
    body, ok = call("POST obligation on unsigned contract -> 422",
                    event("POST", base, "/contracts/{contractId}/analysis", {"contractId": cid},
                          body=json.dumps({"agents": ["obligationTracking"]})), 422)
    results.append(ok and body["error"]["code"] == "NOT_SIGNED")

    print("=" * 78)
    print(f"{sum(results)}/{len(results)} checks passed")
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()

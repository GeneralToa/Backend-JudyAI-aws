"""
Call the live analysis API the way the Judy interface does.

Signs in to Cognito with SRP (the app client allows no other flow), then hits
the three routes in Docs/AI_AGENTS_API.md through API Gateway with the id
token as the bare Authorization header, exactly like the website's
getIdToken(). Useful for demos, for checking a deployment, and for showing
the frontend team real responses.

Usage:
    python call_analysis_api.py                       # status + results for the default contract
    python call_analysis_api.py <contract_id>
    python call_analysis_api.py <contract_id> --rerun summary,riskClause
    python call_analysis_api.py <contract_id> --raw   # print full JSON bodies

Credentials: ~/.judy-app.env with JUDY_APP_USER and JUDY_APP_PASSWORD
(a Cognito user in rag-app-prod-pool). Requires: pip install pycognito
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request

from pycognito import Cognito

API = "https://6eqsnokjn9.execute-api.us-west-2.amazonaws.com"
USER_POOL = "us-west-2_XjAsraU5d"
CLIENT_ID = "4k1jo1ihv736i9m7qf2qf13vnk"
DEFAULT_CONTRACT = "188e718e-00a9-4f32-a536-a1337bc31d5b"   # advisor-agreement.docx


def sign_in():
    env = {}
    with open(os.path.expanduser("~/.judy-app.env"), encoding="utf-8") as fh:
        for line in fh:
            if "=" in line:
                k, v = line.strip().split("=", 1)
                env[k] = v
    user = Cognito(USER_POOL, CLIENT_ID, username=env["JUDY_APP_USER"])
    user.authenticate(password=env["JUDY_APP_PASSWORD"])
    return user.id_token


def call(token, method, path, body=None):
    req = urllib.request.Request(
        API + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": token, "Content-Type": "application/json"},
    )
    started = time.time()
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            return r.status, json.loads(r.read()), int((time.time() - started) * 1000)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}"), int((time.time() - started) * 1000)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    cid = args[0] if args else DEFAULT_CONTRACT
    raw = "--raw" in sys.argv
    rerun = None
    if "--rerun" in sys.argv:
        rerun = sys.argv[sys.argv.index("--rerun") + 1].split(",")

    token = sign_in()
    print(f"signed in; calling {API} for contract {cid}\n")

    if rerun:
        status, body, ms = call(token, "POST", f"/contracts/{cid}/analysis", {"agents": rerun})
        print(f"POST /analysis {rerun} -> {status} ({ms} ms)")
        print(json.dumps(body, indent=2) if raw else
              "  " + ", ".join(f"{r['agentType']}={r['status']} ({r['runId'][:8]})" for r in body.get("runs", [])))
        if status == 202:
            print("  waiting for the agents...")
            time.sleep(15)

    status, body, ms = call(token, "GET", f"/contracts/{cid}/analysis")
    print(f"GET /analysis -> {status} ({ms} ms)")
    if raw:
        print(json.dumps(body, indent=2))
    elif status == 200:
        ext = body["extraction"]
        print(f"  contract {body.get('contractStatus')} | extraction {ext['status']}, {ext.get('pageCount')} pages")
        for name, a in body["agents"].items():
            print(f"  {name:22} {a['status']:10} results={a['resultCount']}  {a.get('completedAt') or ''}")

    for agent, key in (("risk-clause", "risks"), ("template-prepopulation", "fields"),
                       ("summary", "keyPoints"), ("obligation-tracking", "obligations")):
        status, body, ms = call(token, "GET", f"/contracts/{cid}/analysis/{agent}")
        print(f"\nGET /analysis/{agent} -> {status} ({ms} ms)")
        if raw:
            print(json.dumps(body, indent=2))
            continue
        if status != 200:
            print(f"  {body.get('error', {}).get('code')}: {body.get('error', {}).get('message')}")
            continue
        items = body.get(key) or []
        if agent == "risk-clause":
            for r in items:
                print(f"  [{r['severity']:6}] {r['category']:14} p{r['sourcePage'] or '-'}  {r['title']}"
                      + (f"   (playbook: {r['playbookSection']})" if r["playbookSection"] else ""))
        elif agent == "template-prepopulation":
            print(f"  type {body['detectedTemplateType']}: {body['rationale']}")
            for f in items:
                p = f["position"] or {}
                if isinstance(p, str):   # older deploy returned JSONB as text; flag it loudly
                    p = json.loads(p)
                    print("  !! position arrived as a string - analysis-api needs redeploying from main")
                print(f"  p{f['page']} {f['fieldType']:10} {(f['signerRole'] or '-'):9} {f['label'][:24]:24} "
                      f"x={p.get('x')} y={p.get('y')}")
        elif agent == "summary":
            print(f"  {body['wordCount']} words\n  {body['summaryText']}\n")
            for kp in items:
                print(f"  - {kp}")
        else:
            for o in items:
                print(f"  [{o['obligationType']:10}] {(o['ownerParty'] or '-'):9} due={o['dueDate'] or '-':10} "
                      f"{o['description'][:70]}" + (f"   when: {o['rawDateText']}" if o["rawDateText"] else ""))


if __name__ == "__main__":
    main()

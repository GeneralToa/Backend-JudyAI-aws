"""
Live end-to-end demo: the whole SOW path on a real document, through the Judy
API, with the same sign-in a user has.

    1. upload       POST /upload, then PUT the file to the presigned URL
    2. analysis     extraction + risk, template and summary agents run on their own
    3. review       print what the reviewer would see before sending for signature
    4. route        POST /contracts/{id}/route   (to the signed-in user)
    5. sign         POST /contracts/{id}/sign    (click-to-confirm)
    6. obligations  the signing hook fires agent 4; print what it found

Every contract this creates is real and stays in the environment, named
"demo-<time>-<file>" so it is easy to recognise.

Usage:
    python demo_end_to_end.py                     # the client's vendor agreement template
    python demo_end_to_end.py <path-to-docx-or-pdf>
    python demo_end_to_end.py --no-sign           # stop after the review step

Credentials: ~/.judy-app.env (see call_analysis_api.py). Requires pycognito.
"""

import base64
import os
import sys
import time

from call_analysis_api import call, sign_in

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DOC = os.path.join(HERE, "..", "..", "..", "local_context", "Templates",
                           "}[Judefly][Vendor Agreement].docx")
# The presigned upload URL is signed for this content type; anything else is a 403.
UPLOAD_CONTENT_TYPE = "application/octet-stream"
# A 1x1 PNG: the SOW's signing model is click-to-confirm, so no real image is needed.
CONFIRM_PNG = "data:image/png;base64," + base64.b64encode(bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d4944415478da63f8ffff3f0300050001005ada4ff40000000049454e44ae426082")).decode()


def step(n, text):
    print(f"\n[{n}] {text}")


def wait(token, cid, done, limit, label):
    started = time.time()
    last = None
    while time.time() - started < limit:
        status, body, _ = call(token, "GET", f"/contracts/{cid}/analysis")
        if status == 200:
            state = done(body)
            if state is True:
                return body, int(time.time() - started)
            if state != last:
                print(f"    {int(time.time() - started):3}s  {state}")
                last = state
        time.sleep(4)
    raise SystemExit(f"    {label} did not finish within {limit}s")


def put_file(url, path):
    import urllib.request
    with open(path, "rb") as fh:
        req = urllib.request.Request(url, data=fh.read(), method="PUT",
                                     headers={"Content-Type": UPLOAD_CONTENT_TYPE})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.status


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    path = os.path.abspath(args[0] if args else DEFAULT_DOC)
    no_sign = "--no-sign" in sys.argv
    env_user = None
    with open(os.path.expanduser("~/.judy-app.env"), encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("JUDY_APP_USER="):
                env_user = line.strip().split("=", 1)[1]

    token = sign_in()
    t0 = time.time()
    name = f"demo-{time.strftime('%H%M')}-{os.path.basename(path).replace('}', '').replace('[', '').replace(']', '-')}"

    step(1, f"Upload {os.path.basename(path)} as {name}")
    status, body, _ = call(token, "POST", "/upload", {"filename": name})
    if status != 200:
        raise SystemExit(f"    upload request failed: {status} {body}")
    cid = body["contract_id"]
    code = put_file(body["upload_url"], path)
    print(f"    contract {cid}; file stored ({code})")

    step(2, "Extraction and the three pre-signing agents run automatically")
    def pre_signing_done(b):
        ext = b["extraction"]["status"]
        agents = b["agents"]
        if ext == "succeeded" and all(agents[a]["status"] == "succeeded"
                                      for a in ("riskClause", "templatePrepopulation", "summary")):
            return True
        return f"extraction {ext}; " + ", ".join(
            f"{a} {agents[a]['status']}" for a in ("riskClause", "templatePrepopulation", "summary"))
    body, secs = wait(token, cid, pre_signing_done, 240, "analysis")
    a = body["agents"]
    print(f"    done in {secs}s: {body['extraction']['pageCount']} pages, "
          f"{a['riskClause']['resultCount']} risks, {a['templatePrepopulation']['resultCount']} fields, "
          f"summary with {a['summary']['resultCount']} key points")

    step(3, "What the reviewer sees before sending for signature")
    _, risks, _ = call(token, "GET", f"/contracts/{cid}/analysis/risk-clause")
    for r in risks["risks"]:
        print(f"    [{r['severity']:6}] {r['category']:13} p{r['sourcePage'] or '-'}  {r['title']}"
              + (f"  (playbook: {r['playbookSection']})" if r["playbookSection"] else ""))
    _, summ, _ = call(token, "GET", f"/contracts/{cid}/analysis/summary")
    print(f"\n    Summary ({summ['wordCount']} words): {summ['summaryText'][:420]}...")
    _, tmpl, _ = call(token, "GET", f"/contracts/{cid}/analysis/template-prepopulation")
    roles = sorted({f["signerRole"] for f in tmpl["fields"] if f["signerRole"]})
    print(f"\n    Document type: {tmpl['detectedTemplateType']}. {len(tmpl['fields'])} fields placed "
          f"for {', '.join(roles)}.")

    if no_sign:
        print(f"\nStopped before signing. Contract {cid}. Total {int(time.time() - t0)}s.")
        return

    step(4, f"Route for signature to {env_user}")
    status, body, _ = call(token, "POST", f"/contracts/{cid}/route",
                           {"signers": [{"email": env_user, "role": "company"}]})
    print(f"    {status}: {body.get('status', body)}")

    step(5, "Sign (click-to-confirm)")
    status, body, _ = call(token, "POST", f"/contracts/{cid}/sign", {"signatureData": CONFIRM_PNG})
    print(f"    {status}: {body.get('message', body)}")

    step(6, "The signing hook runs obligation tracking")
    def obligations_done(b):
        s = b["agents"]["obligationTracking"]["status"]
        return True if s == "succeeded" else f"obligation tracking {s}"
    body, secs = wait(token, cid, obligations_done, 180, "obligation tracking")
    _, obl, _ = call(token, "GET", f"/contracts/{cid}/analysis/obligation-tracking")
    print(f"    {len(obl['obligations'])} obligations, {secs}s after signing:")
    for o in obl["obligations"]:
        when = f"  | when: {o['rawDateText']}" if o["rawDateText"] else ""
        print(f"    [{o['obligationType']:10}] {(o['ownerParty'] or '-'):8} {o['description'][:66]}{when[:70]}")

    print(f"\nWhole path in {int(time.time() - t0)}s. Contract {cid}.")


if __name__ == "__main__":
    main()

"""
Offline checks for the risk-flagged email trigger in agent_risk_clause.

No AWS calls: the database, the model and the Lambda client are replaced with
fakes. Checks that signature-workflow is asked to email the uploader only on
the automatic run after upload, only when a finding is high severity, never
on a re-run from analysis-api, and that a failed request never fails the
analysis.

Usage:
    python test_risk_notification.py
"""

import json
import os
import sys

for name, value in {
    "AWS_DEFAULT_REGION": "us-west-2",
    "AURORA_CLUSTER_ARN": "arn:aws:rds:us-west-2:000000000000:cluster:test",
    "AURORA_SECRET_ARN": "arn:aws:secretsmanager:us-west-2:000000000000:secret:test",
    "AURORA_DATABASE": "test",
}.items():
    os.environ.setdefault(name, value)

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lambdas", "agent_risk_clause"))
import handler  # noqa: E402
from botocore.exceptions import ClientError  # noqa: E402

CONTRACT = "11111111-1111-1111-1111-111111111111"


class FakeLambda:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    def invoke(self, **kwargs):
        if self.fail:
            raise ClientError({"Error": {"Code": "AccessDeniedException", "Message": "denied"}}, "Invoke")
        self.calls.append(kwargs)


def findings(*severities):
    return json.dumps([{"risk_category": "unusual_term", "severity": s, "title": f"t{i}", "detail": "d",
                        "source_quote": "q", "source_page": 1} for i, s in enumerate(severities)])


def run(severities, run_id=None, notify="rag-app-prod-signature-workflow", fail=False):
    """Run analyse() end to end with fakes; return the fake Lambda client."""
    handler.NOTIFY_FUNCTION_NAME = notify
    handler.lambda_client = FakeLambda(fail)
    handler.fetch_extraction = lambda extraction_id: "contract text"
    handler.start_run = lambda c, e, r: r or "run-1"
    handler.complete_run = lambda *a: None
    handler.fail_run = lambda *a: None
    handler.insert_risks = lambda run_id, contract_id, rows: len(rows)
    # Tagged prompts so the fake model knows which pass is asking: the criteria
    # pass returns the findings, the playbook pass returns none.
    handler.playbook_prompt = lambda text: ("PLAYBOOK", text)
    handler.criteria_prompt = lambda text: ("CRITERIA", text)
    handler.invoke_model = lambda system, user: (
        (findings(*severities), {}) if system == "CRITERIA" else ("[]", {}))
    out = handler.analyse(CONTRACT, "extraction-1", run_id)
    return handler.lambda_client, out


results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail and not ok else ""))


client, out = run(["high", "medium", "high"])
call = client.calls[0] if client.calls else {}
payload = json.loads(call.get("Payload", b"{}")) if call else {}
check("automatic run with high findings: one async invoke", len(client.calls) == 1 and call.get("InvocationType") == "Event", client.calls)
check("... to the configured function", call.get("FunctionName") == "rag-app-prod-signature-workflow", call.get("FunctionName"))
check("... with the agreed payload and the high count",
      payload == {"action": "notify_risk_flagged", "contract_id": CONTRACT, "high_count": 2}, payload)

client, _ = run(["medium", "low"])
check("automatic run, no high findings: no email", client.calls == [], client.calls)

client, _ = run(["high"], run_id="run-from-api")
check("re-run from analysis-api: no email", client.calls == [], client.calls)

client, _ = run(["high"], notify="")
check("NOTIFY_FUNCTION_NAME unset: no email", client.calls == [], client.calls)

try:
    client, out = run(["high"], fail=True)
    check("invoke denied: analysis still succeeds", out.get("risks") == 1, out)
except Exception as e:  # noqa: BLE001
    check("invoke denied: analysis still succeeds", False, repr(e))

print(f"\n{sum(results)}/{len(results)} checks passed")
sys.exit(0 if all(results) else 1)

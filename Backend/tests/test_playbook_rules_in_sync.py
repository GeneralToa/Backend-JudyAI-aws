"""
Check that the risk & clause agent ships the same playbook rules as the source.

Terraform packages Backend/lambdas/agent_risk_clause/ on its own, so the agent
loads the copy of playbook_rules.json beside its handler. build_rules.py writes
both files; this fails if they differ or the copy is missing, which would deploy
an agent with stale rules or none at all. Run it before every deploy.

Usage:
    python test_playbook_rules_in_sync.py
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SOURCE = os.path.join(HERE, "..", "playbook", "playbook_rules.json")
AGENT_COPY = os.path.join(HERE, "..", "lambdas", "agent_risk_clause", "playbook_rules.json")

if not os.path.exists(AGENT_COPY):
    sys.exit(f"FAIL  {os.path.normpath(AGENT_COPY)} is missing - run Backend/playbook/build_rules.py")

with open(SOURCE, encoding="utf-8") as a, open(AGENT_COPY, encoding="utf-8") as b:
    source, copy = json.load(a), json.load(b)

if source != copy:
    sys.exit("FAIL  the agent's playbook_rules.json differs from Backend/playbook/ - run build_rules.py")

print(f"PASS  agent ships the current playbook rules ({source['rule_count']} rules)")

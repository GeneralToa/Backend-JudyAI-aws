# Judy AI — Updating the Playbook

| | |
|---|---|
| Purpose | How to add a section to the playbook, or change one, so the risk & clause agent uses it |
| Audience | **Part A:** the person who writes the playbook. **Part B:** the developer or administrator who deploys it |
| Related | [`ADMIN_REFERENCE_AI.md`](ADMIN_REFERENCE_AI.md) §6.4 (summary) and §6.7 (test tools) |

The playbook is the Word document of contract positions the risk & clause agent checks every
contract against. The agent does not read the Word file directly. A converter turns it into rules
(`playbook_rules.json`), which are packaged with the agent. So a playbook change is a short
developer task: convert, check, test, deploy. It is not a self-service upload.

**Typical effort:** a new or changed section in the same layout as today takes a few hours,
including testing. A section in a different layout first needs the converter adjusted.

**What changes when:** an update applies to contracts analysed after the deploy. Contracts
already analysed keep their earlier results until analysis is re-run on them (Part B, step 7).

---

## Part A — For the playbook author

### A1. Where things go

The playbook is **one table**, and it must be the **first table** in the Word document. Keep the
header row. Each section is one row with three columns:

| Column | Header today | What goes in it |
|---|---|---|
| 1 | Purchasing Agreement Section | The section name, e.g. `Data Retention` |
| 2 | If supplier OBJECTS to the Following Sentence: | The standard sentence, then why it would be a problem |
| 3 | Then | What to do: replace or insert wording, attach an exhibit, ask for approval, or contact legal |

To add a section, add a row at the end of the table. To change a section, edit its row and
**keep the section name exactly as it is**. The name identifies the section; renaming it creates
a new section.

### A2. Column 2: the standard sentence and the condition

Write each scenario in this order, one item per paragraph:

1. The standard sentence, **in quotation marks**.
2. A paragraph with only the word **Because**.
3. The condition that makes the standard sentence a problem.

- A condition in two parts: put **AND** or **OR** alone on its own line between the parts.
- More than one scenario in the same section: separate them with a line of underscores
  (`_______`), then start the next scenario with its own quoted sentence.
- A note for the reviewer goes in curly braces, e.g. `{contact legal}`.

Example (column 2):

```
"Supplier will delete Customer Data within 30 days of termination."
Because
Supplier wants to keep Customer Data for more than 90 days after termination
```

### A3. Column 3: what to do

Each instruction goes in **curly braces**, with the wording it applies on the lines below it.
The converter recognises these instructions:

| Write | Meaning |
|---|---|
| `{Replace this section with the following:}`, `{Replace sentence with the following}`, `{Replace}` | Replace the contract's wording with the text below |
| `{Input the following}` | Add the text below to the contract |
| `{Attach the … exhibit}` | Attach the named exhibit |
| `{Contact legal}`, `{Connect to legal provider for appropriate language}` | Escalate to a legal professional |
| `{Request written confirmation from stakeholders}`, `{Multiple levels of approvals are suggested}` | Approval needed before signing |
| `{Identify contract in use}` | Check which agreement applies |
| `{Option add}`, `{Suggested option}` | Optional wording or step |
| `{Link}` | A link to be filled in, e.g. `{guidelines link}` |

Wording in braces that isn't in this list is not understood. The developer will see a warning
and come back to you. Use one of the instructions above, or say what you mean and the developer
can add it.

Example (column 3):

```
{Replace this section with the following:}
Data Retention. Supplier shall delete all Customer Data within thirty (30) days after
termination and certify the deletion in writing on request.
{Contact legal}
```

### A4. Before you send it

- **Proofread the wording.** The replacement and inserted wording is shown to users exactly as
  written, as the suggested contract language. Nobody edits it, and possible typos come back to
  you to confirm rather than being corrected silently.
- **Say which contract types the section applies to:** MSA, purchase agreement, SOW, or all
  three. If you don't say, it applies to all three.
- Send the updated Word file to the developer with a short note of what you added or changed.

### A5. Things to flag that aren't playbook clauses

Some checks aren't "replace this wording". The agent also flags anything with a date or number
attached (notice periods, renewal windows) and facility or access requirements. To add another
check of that kind, describe it in plain words, for example: "flag any limit on liability below
the contract value". The developer adds it to the agent's checking instructions (Part B, B8).

---

## Part B — For the developer or administrator

**You need:** the `Backend-JudyAI-aws` repository, Python 3.12 or later with `boto3`, AWS
credentials for the account with Bedrock access (for steps B5–B7), and the Terraform setup
described in `TECHNICAL_DOCUMENTATION.md`.
Run the commands from the repository root.

### B1. Convert the document

```bash
python Backend/playbook/build_rules.py path/to/Playbook.docx
```

This writes `Backend/playbook/playbook_rules.json` and an identical copy beside the agent
(`Backend/lambdas/agent_risk_clause/playbook_rules.json`). Check the printed rule, trigger and
action counts against what the author changed.

### B2. Deal with the warnings

| Warning | What to do |
|---|---|
| `No metadata for section '…' - using defaults` | A new section. Add it to `SECTION_METADATA` in `build_rules.py` with a stable id (lower case, underscores) and its contract types, then re-run B1 |
| `unrecognized marker {…}` | An instruction in braces the converter doesn't know. Ask the author to use one from A3, or add the phrase to `ACTION_MARKERS` |
| `no triggers parsed` / `no actions parsed` | The row doesn't follow the A2/A3 layout. Usually it's missing quotation marks, a missing **Because**, or a missing brace |
| `confirmed correction no longer matches the source` | The new document already contains one of the confirmed typo fixes. Harmless; the entry in `CLIENT_CONFIRMED_CORRECTIONS` can be removed |

### B3. Check the wording

Open `playbook_rules.json`, find the new or changed section, and compare its standard sentence,
condition and action text with the Word document word for word.

**Never edit the wording yourself.** If you find a typo, ask the author to confirm the correction
in writing. Then add the pair to `CLIENT_CONFIRMED_CORRECTIONS` in `build_rules.py` and re-run B1.
Every applied correction is recorded in the JSON.

### B4. Add a test case for a new section (recommended)

The test set is eight plain-text contracts in `Backend/tests/corpus/`, built from Judy's own
templates.

1. Add a short clause that should trigger the new section to one of them, inside the body of the
   agreement. Write the clause from scratch; don't copy the playbook's own sentence, or the test
   only proves the agent can match text against itself.
2. Add the section's id to that document's `expects_rules` in `Backend/tests/corpus/manifest.json`.

`build_corpus.py` regenerates the whole set from the original Word templates, so it needs those
templates beside the repository. If you use it, add the clause there too, so a rebuild keeps it.

### B5. Run the checks

```bash
python Backend/tests/test_playbook_rules_in_sync.py      # offline: the agent ships these rules
AWS_PROFILE=<profile> PYTHONIOENCODING=utf-8 \
  python Backend/tests/evaluate_risk_agent.py            # runs the agent's prompts on the test set
```

Every rule a test document is built to trigger should still be found, the new section included.
Compare the result with the previous run. A rule that stops firing usually means its row was
converted differently: look at what the agent is shown in `playbook_rules.json` before changing
anything else.

### B6. Commit and deploy

Commit both `playbook_rules.json` files, plus `build_rules.py` and the test-set files if you
changed them. Then deploy the agent:

```bash
cd IaC/6_lambda && terraform apply
```

Terraform packages the agent's folder, including its copy of the rules. Run the offline checks
in `ADMIN_REFERENCE_AI.md` §4.2 before every deploy.

### B7. Confirm it works live

Re-run the risk analysis on a contract that contains the new scenario, signed in as an
application user:

```http
POST /contracts/{contractId}/analysis
{ "agents": ["riskClause"] }
```

Results appear in about 10 seconds. The finding should show the new section's name and its
suggested wording. Re-runs never send notification emails. Re-run any already-analysed
contracts that should reflect the change the same way.

### B8. Adding a check that isn't a playbook clause (A5)

These checks live in the agent's second pass: `criteria_prompt()` in
`Backend/lambdas/agent_risk_clause/handler.py`.

1. Add the check there in plain words.
2. Raise `PROMPT_VERSION` (environment variable, default `risk-clause-v1`) so new and old
   results stay distinguishable.
3. Run `evaluate_risk_agent.py` before and after the change.
4. Deploy as in B6.

Findings must use one of the existing categories: `unusual_term`, `missing_clause`,
`date_mismatch`, `compliance_gap`, `tracked_term`. A new category needs a database change first.

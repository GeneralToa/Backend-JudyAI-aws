# Judy AI — Handover: the AI part (15 minutes)

| | |
|---|---|
| Purpose | The AI section of the 1-hour handoff session (SOW acceptance criterion 13) |
| Audience | Judy's users who review contracts, and the administrators who run the environment |
| Presenter | AI engineer |
| Reference | [`ADMIN_REFERENCE_AI.md`](ADMIN_REFERENCE_AI.md) for every detail behind this outline |

## Agenda

| Minutes | Part | Shown on screen |
|---|---|---|
| 0–2 | What the AI does, in one picture | Section 1 of the admin reference (the flow) |
| 2–8 | **For users:** reading the AI results | The live Judy interface |
| 8–13 | **For administrators:** keeping it running | AWS console, the admin reference |
| 13–15 | Questions | — |

---

## 1. What the AI does (2 min)

Say it in four lines:

- When a contract is uploaded, its text and layout are read automatically (Amazon Bedrock Data
  Automation).
- Three AI agents then check it before anyone signs: **risks** against Judy's playbook, the
  **signature fields** and who fills them, and a **plain-language summary**.
- When the last person signs, a fourth agent lists the **obligations**: deadlines, renewal windows,
  commitments.
- Everything runs inside Judy's own AWS account. Nothing trains a model on Judy's documents.

## 2. For users: reading the results (6 min)

Live, on a contract that is already analysed. Open it from **Contracts** to get the review screen.

| Show | Say |
|---|---|
| The status line under a file name ("N risks · N fields · summary ready") | Analysis takes about a minute after upload. Refresh to see it. |
| **Summary** and key points | A short overview for a non-lawyer. Every key point comes from a real sentence in the contract. |
| A **risk** card | Each risk has a type, a severity, the exact sentence and page it came from. Check the sentence, not just the title. |
| A risk with a **suggested wording** box | That wording is Judy's own playbook text, word for word. It is advice only; the AI never changes the document. |
| A **tracked term** | Not a problem — a date or a number the reviewer should see (notice periods, renewal windows, payment deadlines). |
| The dashed **field boxes** on the signature page | Where each party signs, dates and fills in details. Positions are measured from the page, not guessed. |
| **View Analysis → Obligations** on a signed contract | What must happen after signing, who owes it, and the contract's own timing words. A date appears only when the contract writes one out; the AI never calculates dates. |
| The risk count beside **Send for Signature** | Nobody sends a contract without seeing its risks first. |

Three things the AI does **not** do: it does not write or change contract language, it does not
chat or draft documents, and it does not send anything on its own.

Good to know:
- A **high-severity** risk on a new upload emails the person who uploaded it.
- Results can differ slightly between runs on borderline items. Every run is stored, so each result
  can be traced to the run that produced it.
- Each user sees only the contracts they uploaded or are asked to sign — and only those analyses.

## 3. For administrators: keeping it running (5 min)

| Topic | Where | What to know |
|---|---|---|
| Daily health | CloudWatch | Alarms email the team on any AI function error or a document stuck in the dead-letter queue. Replace the team's addresses with Judy's own (SNS topic `rag-app-prod-alarms`) and confirm them with `--authenticate-on-unsubscribe true`. |
| A document that failed | SQS dead-letter queue (14 days) | Read the error in the extraction log, fix the cause, redrive the message. Admin reference §6.3. |
| Re-running analysis | The API (`POST /contracts/{id}/analysis`) | For one contract or one agent. Re-runs never send emails. Admin reference §6.2. |
| Updating the playbook | `Backend/playbook/build_rules.py` | Regenerate the rules, run the two checks, deploy. Only wording Judy confirms in writing is ever corrected. Admin reference §6.4. |
| Changing the AI model | `MODEL_ID` on the agent functions | Any Bedrock inference profile ID; the permissions already allow other models. Run the evaluators before and after. Admin reference §6.5. |
| Cost | Bedrock usage on Judy's bill | About $0.05 of model use plus about $0.08 of extraction per contract, at list prices. |
| Limits | SOW | Text-based PDF and Word up to 20 pages / 10 MB; three template types (MSA, purchase agreement, SOW); no OCR or handwriting. |

## 4. Questions (2 min)

Likely ones, with short answers:

- **"Can we add our own playbook?"** Yes, if it follows the same structure (section, standard
  sentence, objection, replacement). A differently structured playbook needs converter changes —
  a next-phase item.
- **"Can it recognise vendor agreements as a type?"** Not as a fourth type in this scope; they are
  analysed and get their fields placed, reported as "not one of the three types".
- **"Can Judy draft or send things for us?"** Not in this proof of concept. That is the next phase,
  starting from Judy's 4 October guide.
- **"Where is everything documented?"** The administrator reference (this AI chapter plus the
  application and infrastructure chapters) and the API contract (`AI_AGENTS_API.md`).

## Before the session

- [ ] A contract already analysed, one already signed (with obligations), and one PDF, so nothing
      depends on a live upload.
- [ ] Sign in to the Judy interface and to the AWS console beforehand.
- [ ] Check the alarm topic has Judy's addresses, or note it as a follow-up.

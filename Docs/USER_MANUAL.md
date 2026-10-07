# Judy.ai - User Manual

## Table of Contents

1. [Overview](#overview)
2. [Getting Started](#getting-started)
3. [Login & Authentication](#login--authentication)
4. [Upload Documents](#upload-documents)
5. [Chat with Knowledge Base](#chat-with-knowledge-base)
6. [File Management](#file-management)
7. [Contracts](#contracts)
8. [Contract Review & AI Analysis](#contract-review--ai-analysis)
9. [Sending a Contract for Signature](#sending-a-contract-for-signature)
10. [Signing a Contract](#signing-a-contract)
11. [Batch Signing](#batch-signing)
12. [Dashboard](#dashboard)
13. [Export Chat History](#export-chat-history)
14. [Logout](#logout)
15. [Administration Guide](#administration-guide)

---

## Overview

Judy.ai is an AI-powered contract management platform. Upload PDF or DOCX contracts, let the AI analyse them for risks, fields, and obligations, then route them for digital signature — all in one place.

### Key Features

- Document upload (PDF, DOCX, up to 10 MB)
- AI contract analysis: risk detection, field identification, plain-language summary, obligation tracking
- Digital signature workflow with sequential or parallel signing
- Batch signing across multiple contracts
- AI chatbot for querying your uploaded documents
- Dashboard with pending reviews, flagged risks, and tracked obligations
- Admin panel for configuring approval templates and notification settings

---

## Getting Started

### System Requirements

- Modern web browser (Chrome, Firefox, Edge, Safari)
- Internet connection
- Valid user account (provided by your administrator)

### Accessing the Application

Navigate to the URL provided by your administrator:
```
https://d8xv5mej9ouxr.cloudfront.net
```

---

## Login & Authentication

1. When you access the application URL, you will be automatically redirected to the login page
2. Enter your **email address** and **password**
3. If this is your first login with a temporary password, you will be prompted to set a new permanent password
4. After successful authentication, you will be redirected back to the application

### Password Requirements

- Minimum 8 characters
- At least one uppercase letter
- At least one lowercase letter
- At least one number

### Session Duration

- Your login session lasts **1 hour**
- After 1 hour, you will be automatically redirected to the login page
- All chat sessions are cleared when the token expires

---

## Upload Documents

1. Click **Upload** in the left sidebar
2. Upload files using one of these methods:
   - **Drag and drop** files directly onto the drop zone
   - Click **Browse Files** to select files from your computer
3. Supported formats: **PDF**, **DOCX**
4. Maximum file size: **10 MB**
5. After upload, the file is automatically queued for AI analysis

### Upload Status Indicators

| Status | Meaning |
|--------|---------|
| Uploading (blue) | File is being transferred |
| Uploaded successfully (green) | File received, analysis will begin shortly |
| Error (red) | Upload failed — check file size and format |

### Important Notes

- After uploading, AI analysis takes about 40–60 seconds. Refresh the Contracts page to see results.
- Once analysis is complete, the contract status shows **ready for review**

---

## Chat with Knowledge Base

1. Click **Chat** in the left sidebar
2. Type your question in the input field at the bottom
3. Press **Enter** or click the send button
4. Wait for Judy's response

### How the AI Responds

The system processes your query through three stages:

1. **Safety Check** — Verifies your question doesn't contain harmful content
2. **Knowledge Retrieval** — Searches your uploaded documents for relevant information
3. **Response Formatting** — Structures the answer clearly with tables, bullet points, etc.

### Response Sources

- **From Knowledge Base**: The answer is based on your uploaded documents.
- **From General Knowledge**: If no relevant information is found in your documents, this is clearly indicated with: *"Based on general knowledge (not from your uploaded documents):"*

### Multi-Session Chat

- **Create new session**: Click the **+** button in the sessions panel
- **Switch sessions**: Click any session in the list
- Sessions are cleared on logout

---

## File Management

1. Click **Files** in the left sidebar
2. View all uploaded documents with their knowledge base status

### KB Status Values

| Status | Meaning |
|--------|---------|
| Loading (animated) | File is being processed and indexed |
| Ingested (green) | File is fully indexed and available for chat queries |
| Failed (red) | Ingestion failed — try re-uploading the file |
| Deleting (animated) | File is being removed |

### Deleting Files

1. **Right-click** on the file you want to delete
2. Select **Delete File** from the context menu
3. Confirm deletion in the dialog

---

## Contracts

Click **Contracts** in the left sidebar to manage your contract signing workflows.

### Contract Statuses

| Status | Meaning |
|--------|---------|
| uploaded | File received, analysis starting |
| analyzing | AI agents running |
| ready for review | Analysis complete — review before sending |
| routed for signature | Sent to signers, awaiting signatures |
| signed | All parties have signed |

### Actions by Status

| Status | Available Actions |
|--------|-------------------|
| ready for review | Send for Signature, View Analysis, Download |
| routed for signature | Sign (if it's your turn), View Signers, View Analysis, Cancel Routing |
| signed | View Signers, View Analysis, Download |

### Analysis Status Line

Under each contract filename you'll see a summary of the AI analysis, for example:
```
⚠ 3 risks · 8 fields · summary ready
```

Click the contract name to open the full review page.

### Refreshing

Click the **Refresh** button in the top-right corner to reload the latest contract statuses.

---

## Contract Review & AI Analysis

Click a contract name to open the review page. The review page shows the document alongside
the AI analysis in four tabs.

### Risks Tab

Each risk card shows:
- **Severity**: high (red), medium (orange), or low (green)
- **Category**: unusual term, missing clause, date mismatch, compliance gap, or tracked term
- **Detail**: explanation of the issue
- **Source quote**: the exact sentence from the contract, with the page number
- **Advisory suggestion** (where applicable): replacement wording from Judy's playbook — this is
  advice only; the AI never changes the document

A **tracked term** is not a problem — it is a date or number the reviewer should be aware of
(e.g. a renewal window or notice period).

### Fields Tab

Shows signature blocks and fill-in fields detected by the AI, with each field's:
- Type (signature, date, initial, text)
- Label and signer role
- Page number

### Summary Tab

A plain-language overview of the contract (120–260 words) and 5–10 key points, each traced to
a sentence in the document.

### Obligations Tab

Available only after the contract is signed. Shows post-signing commitments:
- Obligation type and description
- Owner (which party is responsible)
- Due date, if the contract writes one explicitly
- Source quote

### Download Analysis as PDF

Click **Download PDF** in the top-right of the analysis modal to export all four tabs as a
printable PDF.

---

## Sending a Contract for Signature

1. On the Contracts page, click **Send for Signature** on a `ready for review` contract
2. Enter the email address and role for each signer (up to 5 signers)
3. Click **Send for Signature** to confirm
4. The contract moves to `routed for signature`
5. Each signer receives an email notification with a link to sign

### Routing Types

Your administrator configures the approval template used when routing:

| Type | How it works |
|------|-------------|
| **Sequential** | Signers sign one at a time, in the order they were entered. All signers are notified by email when the contract is routed. |
| **Parallel** | All signers can sign simultaneously in any order. |

### Cancel Routing

If you need to change the signers after routing:
1. Click **Cancel Routing** on the contract
2. Confirm the cancellation
3. The contract returns to `ready for review`
4. Re-route with the correct signer details

---

## Signing a Contract

The **Sign** button appears only when it is your turn to sign:
- **Sequential**: the Sign button shows only for the current lowest-order pending signer
- **Parallel**: the Sign button shows for all pending signers

### Steps

1. Click **Sign** on a `routed for signature` contract
2. The signature modal opens — choose a method:
   - **Draw**: draw your signature on the canvas with your mouse or finger
   - **Type**: type your name and choose a cursive font style
3. Click **Sign** to submit
4. Your signature is recorded

### What happens next

- **Sequential contract**: the Sign button will appear for the next pending signer (all signers were already notified by email when the contract was routed)
- **Last signer**: the contract moves to `signed`, the uploader is notified, and obligation tracking begins automatically

---

## Batch Signing

Sign multiple contracts at once with a single signature.

1. On the Contracts page, tick the checkbox next to each contract you want to sign
   (checkboxes only appear when it is your turn to sign that contract)
2. The **Sign Selected (N)** button appears at the top
3. Click **Sign Selected**
4. Draw or type your signature once
5. Click **Sign** — the same signature is applied to all selected contracts
6. A results modal shows which contracts signed successfully and which (if any) failed

---

## Dashboard

Click **Dashboard** in the left sidebar for a summary view across all your contracts.

### Panels

| Panel | Shows |
|-------|-------|
| Pending Review | Contracts not yet sent for signature |
| Risks Flagged | High and medium risks across all analysed contracts |
| Obligations | Upcoming obligations from signed contracts, sorted by due date |

Click any item to go to the relevant contract's review page.

---

## Export Chat History

1. On the **Chat** page, click the **Export** button
2. A PDF is generated and downloaded automatically
3. The PDF contains the full conversation history with timestamps

---

## Logout

1. Click **Logout** at the bottom of the left sidebar
2. Confirm in the dialog
3. All session data is cleared and you are redirected to the login page

---

## Administration Guide

### Admin Panel

Click **Admin** in the left sidebar to access the admin panel.

#### Approval Templates

Approval templates define the signing workflow applied when a contract is sent for signature.

| Field | Description |
|-------|-------------|
| Name | Display name for the template |
| Routing Type | Sequential (one at a time in order) or Parallel (all at once) |
| Signer Roles | Comma-separated roles, e.g. `supplier, company`. Order matters for sequential. |
| Default | The default template is pre-selected in the route modal |

To edit a template:
1. Update the name, routing type, or roles
2. Click **Save**

To set a template as default:
- Click **Set as Default** on the template row

#### System Settings

| Setting | Description |
|---------|-------------|
| Application Name | Display name shown in the interface |
| Max File Size (MB) | Maximum upload size (frontend validation) |
| Email: Document assigned | Send email to signers when a contract is routed to them |
| Email: Signature completed | Send email to the uploader when all parties have signed |
| Email: Risk flagged | Send email to the uploader when the AI flags a high-severity risk |

Click **Save Settings** to apply changes.

---

### User Management

User management is performed through the AWS Management Console using Amazon Cognito.

#### Creating a New User

1. Log into the [AWS Console](https://console.aws.amazon.com)
2. Navigate to **Amazon Cognito** → **User pools** → select the Judy AI user pool
3. Go to the **Users** tab → click **Create user**
4. Enter the user's email address and set a temporary password
5. The user will receive an email with their temporary credentials and must set a new password on first login

Using AWS CLI:
```bash
aws cognito-idp admin-create-user \
  --user-pool-id <USER_POOL_ID> \
  --username user@example.com \
  --user-attributes Name=email,Value=user@example.com Name=email_verified,Value=true \
  --temporary-password TempPass123!
```

#### Resetting a User's Password

```bash
aws cognito-idp admin-set-user-password \
  --user-pool-id <USER_POOL_ID> \
  --username user@example.com \
  --password NewPassword123! \
  --permanent
```

#### Disabling a User

```bash
aws cognito-idp admin-disable-user \
  --user-pool-id <USER_POOL_ID> \
  --username user@example.com
```

#### Deleting a User

```bash
aws cognito-idp admin-delete-user \
  --user-pool-id <USER_POOL_ID> \
  --username user@example.com
```

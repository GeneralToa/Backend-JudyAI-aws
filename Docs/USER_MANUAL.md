# Judy.ai Knowledge Base - User Manual

## Table of Contents

1. [Overview](#overview)
2. [Getting Started](#getting-started)
3. [Login & Authentication](#login--authentication)
4. [Upload Documents](#upload-documents)
5. [Chat with Knowledge Base](#chat-with-knowledge-base)
6. [File Management](#file-management)
7. [Export Chat History](#export-chat-history)
8. [Logout](#logout)
9. [Administration Guide](#administration-guide)

---

## Overview

Judy.ai Knowledge Base is an AI-powered document analysis platform. Upload PDF or DOCX documents and ask questions about their content using natural language. The system uses Amazon Bedrock's Retrieval-Augmented Generation (RAG) technology to provide accurate answers grounded in your uploaded documents.

### Key Features

- Document upload (PDF, DOCX, TXT, up to 10 MB)
- AI chatbot powered by Amazon Bedrock Knowledge Bases
- Multi-session chat (create and switch between conversations)
- File management with knowledge base ingestion status tracking
- Chat history export as PDF
- Content safety filtering (harmful/inappropriate queries are blocked)

---

## Getting Started

### System Requirements

- Modern web browser (Chrome, Firefox, Edge, Safari)
- Internet connection
- Valid Cognito user account (provided by your administrator)

### Accessing the Application

Navigate to the CloudFront distribution URL provided by your administrator:
```
https://<distribution-id>.cloudfront.net
```

---

## Login & Authentication

1. When you access the application URL, you will be automatically redirected to the Cognito login page
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
5. After upload, the file is automatically queued for processing into the Knowledge Base

### Upload Status Indicators

| Status | Meaning |
|--------|---------|
| Uploading (blue) | File is being transferred to the server |
| Uploaded successfully (green) | File received, ingestion will begin shortly |
| Error (red) | Upload failed - check file size and format |

### Important Notes

- After uploading, it may take 1-2 minutes for the document to be fully ingested into the Knowledge Base
- You can check the ingestion status on the **Files** page
- Once status shows "Ingested", the document is available for querying

---

## Chat with Knowledge Base

1. Click **Chat** in the left sidebar
2. Type your question in the input field at the bottom
3. Press **Enter** or click the send button
4. Wait for Judy's response (a typing indicator shows while processing)

### How the AI Responds

The system processes your query through three stages:

1. **Safety Check** - Verifies your question doesn't contain harmful content
2. **Knowledge Retrieval** - Searches your uploaded documents for relevant information
3. **Response Formatting** - Structures the answer clearly with tables, bullet points, etc.

### Response Sources

- **From Knowledge Base**: The answer is based on your uploaded documents. Presented without disclaimers.
- **From General Knowledge**: If no relevant information is found in your documents, the system may use the AI's general knowledge. This is clearly indicated with: *"Based on general knowledge (not from your uploaded documents):"*

### Multi-Session Chat

- **Create new session**: Click the **+** button in the sessions panel (right side)
- **Switch sessions**: Click any session in the list to view its conversation
- **Session names**: Sessions are named by your first message in them
- Sessions are kept during your login session and cleared on logout

### Tips for Better Results

- Ask specific questions about content in your uploaded documents
- If you get no results, try rephrasing your question
- The system automatically rephrases and retries if the first search yields no results
- Use clear, concise language

---

## File Management

1. Click **Files** in the left sidebar
2. View all uploaded documents with their status

### File Information Displayed

| Column | Description |
|--------|-------------|
| File Name | Original name of the uploaded document |
| KB Status | Current status in the Knowledge Base |
| Size | File size in KB or MB |
| Uploaded By | Email of the user who uploaded the file |
| Upload Date | Date and time of upload |

### KB Status Values

| Status | Meaning |
|--------|---------|
| Loading (purple, animated) | File is being processed and ingested |
| Ingested (green) | File is fully indexed and available for queries |
| Failed (red) | Ingestion failed - try re-uploading the file |
| Deleting (orange, animated) | File is being removed from the Knowledge Base |

### Refreshing File Status

Click the **Refresh** button in the upper-right corner to reload the latest status from the server.

### Deleting Files

1. **Right-click** on the file you want to delete
2. Select **Delete File** from the context menu
3. A confirmation dialog will appear: "Are you sure you want to delete this file?"
4. Click **Delete** to confirm, or **Cancel** to abort
5. The file will be removed from S3 and the Knowledge Base will re-sync

### Uploading from the Files Page

Click the **Upload File** button in the upper-right corner to navigate to the upload page.

---

## Export Chat History

1. On the **Chat** page, click the **Export** button above the chat messages
2. A PDF file will be automatically generated and downloaded
3. The PDF contains:
   - Session name and export timestamp
   - Complete conversation history (your questions and Judy's responses)
4. The file is named: `judy-chat-<session-name>-<timestamp>.pdf`

---

## Logout

1. Click **Logout** at the bottom of the left sidebar
2. A confirmation dialog will appear: "Are you sure you want to log out?"
3. Click **Yes, Log Out** to confirm
4. All chat sessions and data are cleared from the browser
5. You will be redirected to the Cognito logout page

---

## Administration Guide

### User Management

User management is performed through the AWS Management Console using Amazon Cognito.

#### Accessing Cognito

1. Log into the [AWS Console](https://console.aws.amazon.com)
2. Navigate to **Amazon Cognito** > **User pools**
3. Select the user pool named `rag-app-dev-user-pool` (or your configured prefix)

#### Creating a New User

1. In the user pool, go to **Users** tab
2. Click **Create user**
3. Configure:
   - **Invitation message**: Send an email invitation (recommended)
   - **Email address**: Enter the user's email
   - **Temporary password**: Set a temporary password or let Cognito generate one
4. Click **Create user**
5. The user will receive an email with their temporary credentials
6. On first login, they will be prompted to set a permanent password

#### Using AWS CLI to Create Users

```bash
aws cognito-idp admin-create-user \
  --user-pool-id <USER_POOL_ID> \
  --username user@example.com \
  --user-attributes Name=email,Value=user@example.com Name=email_verified,Value=true \
  --temporary-password TempPass123!
```

#### Deleting a User

1. In the user pool, go to **Users** tab
2. Select the user to delete
3. Click **Actions** > **Delete user**
4. Confirm deletion

Using AWS CLI:
```bash
aws cognito-idp admin-delete-user \
  --user-pool-id <USER_POOL_ID> \
  --username user@example.com
```

#### Resetting a User's Password

```bash
aws cognito-idp admin-set-user-password \
  --user-pool-id <USER_POOL_ID> \
  --username user@example.com \
  --password NewPassword123! \
  --permanent
```

#### Disabling a User (without deletion)

```bash
aws cognito-idp admin-disable-user \
  --user-pool-id <USER_POOL_ID> \
  --username user@example.com
```

#### Viewing User Pool ID

The User Pool ID can be found in Terraform outputs:
```bash
cd terraform
terraform output cognito_user_pool_id
```

### Password Policy Configuration

The current password policy requires:
- Minimum 8 characters
- At least 1 uppercase letter
- At least 1 lowercase letter
- At least 1 number
- Symbols are NOT required

To modify the password policy, update `terraform/modules/cognito/main.tf` and run `terraform apply`.

### Monitoring

#### CloudWatch Logs

All Lambda functions write logs to CloudWatch Log Groups:
- `/aws/lambda/rag-app-dev-data-upload`
- `/aws/lambda/rag-app-dev-data-processor`
- `/aws/lambda/rag-app-dev-retrieve-and-generate`
- `/aws/lambda/rag-app-dev-ingestion-post-processor`

#### DLQ Alarm

A CloudWatch alarm is configured on the ingestion Dead Letter Queue. When messages land in the DLQ (indicating processing failures), the alarm transitions to ALARM state.

#### Knowledge Base Sync Status

Check ingestion job status:
```bash
aws bedrock-agent list-ingestion-jobs \
  --knowledge-base-id <KB_ID> \
  --data-source-id <DS_ID> \
  --region us-east-1
```

### Application URLs

After deployment, the key URLs are:
- **Application**: CloudFront distribution URL
- **Login**: Cognito hosted UI domain
- **API**: API Gateway invoke URL

All URLs are available via `terraform output`.

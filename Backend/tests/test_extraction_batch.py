"""
Offline checks for how document_extraction handles an SQS batch.

No AWS calls: process_document and the SQS and S3 clients are replaced with
fakes, so this tests only the batch logic - parallel processing, isolation of a
failing upload, and deleting the successes before the invocation fails so only
the failed upload is retried - and how file names BDA refuses are handled.

Usage:
    python test_extraction_batch.py
"""

import json
import os
import re
import sys
import threading
import time

for name, value in {
    "AWS_DEFAULT_REGION": "us-west-2",
    "AURORA_CLUSTER_ARN": "arn:aws:rds:us-west-2:000000000000:cluster:test",
    "AURORA_SECRET_ARN": "arn:aws:secretsmanager:us-west-2:000000000000:secret:test",
    "AURORA_DATABASE": "test",
    "BDA_PROJECT_ARN": "arn:aws:bedrock:us-west-2:000000000000:data-automation-project/test",
    "BDA_PROFILE_ARN": "arn:aws:bedrock:us-west-2:000000000000:data-automation-profile/test",
    "BDA_OUTPUT_BUCKET": "test-bucket",
}.items():
    os.environ.setdefault(name, value)

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lambdas", "document_extraction"))
import handler  # noqa: E402

QUEUE_ARN = "arn:aws:sqs:us-west-2:580118073904:rag-app-prod-document-extraction-sqs"
QUEUE_URL = "https://sqs.us-west-2.amazonaws.com/580118073904/rag-app-prod-document-extraction-sqs"
BUCKET = "rag-app-prod-landing-zone-580118073904-us-west-2-an"


def sqs_record(key, n):
    """An SQS record carrying an SNS envelope carrying an S3 event - the production shape."""
    s3_event = {"Records": [{"eventSource": "aws:s3", "s3": {"bucket": {"name": BUCKET}, "object": {"key": key}}}]}
    envelope = {"Type": "Notification", "Message": json.dumps(s3_event)}
    return {"messageId": f"m{n}", "receiptHandle": f"rh{n}", "eventSource": "aws:sqs",
            "eventSourceARN": QUEUE_ARN, "body": json.dumps(envelope)}


class FakeSQS:
    def __init__(self):
        self.deleted = []

    def delete_message(self, QueueUrl, ReceiptHandle):
        self.deleted.append((QueueUrl, ReceiptHandle))


processed, lock = [], threading.Lock()


def fake_process(bucket, key):
    time.sleep(1.0)  # stands in for the BDA wait
    with lock:
        processed.append(key)
    if "bad" in key:
        raise RuntimeError(f"BDA extraction failed for {key}")


handler.process_document = fake_process
results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail and not ok else ""))


def run(event):
    handler.sqs_client = FakeSQS()
    processed.clear()
    started = time.time()
    try:
        return handler.lambda_handler(event, None), None, time.time() - started
    except Exception as e:  # noqa: BLE001
        return None, e, time.time() - started


# 1. One bad upload in a batch of three
out, err, secs = run({"Records": [sqs_record("uploads/a/ok1.docx", 1), sqs_record("uploads/b/bad.docx", 2),
                                  sqs_record("uploads/c/ok2.docx", 3)]})
check("mixed batch: invocation fails so the bad upload is retried", isinstance(err, RuntimeError), repr(err))
check("mixed batch: error names the failed message", err is not None and "1 of 3" in str(err) and "m2" in str(err), str(err))
check("mixed batch: all three documents were attempted", sorted(processed) == ["uploads/a/ok1.docx", "uploads/b/bad.docx", "uploads/c/ok2.docx"], processed)
check("mixed batch: only the two successes deleted, from the right queue",
      sorted(handler.sqs_client.deleted) == [(QUEUE_URL, "rh1"), (QUEUE_URL, "rh3")], handler.sqs_client.deleted)
check("mixed batch: processed in parallel", secs < 2.0, f"{secs:.1f}s")

# 2. A full batch of ten, all good
out, err, secs = run({"Records": [sqs_record(f"uploads/{n}/doc{n}.docx", n) for n in range(10)]})
check("ten good uploads: no error", err is None, repr(err))
check("ten good uploads: all processed", out and "processed 10" in out["body"], out)
check("ten good uploads: nothing deleted by hand (Lambda deletes the batch)", handler.sqs_client.deleted == [], handler.sqs_client.deleted)
check("ten good uploads: about as long as one", secs < 2.0, f"{secs:.1f}s")

# 3. Direct invocation with a bare S3 event that fails: no queue to delete from
bare = {"Records": [{"eventSource": "aws:s3", "s3": {"bucket": {"name": BUCKET}, "object": {"key": "uploads/x/bad.docx"}}}]}
out, err, _ = run(bare)
check("bare S3 event: failure raises", isinstance(err, RuntimeError), repr(err))
check("bare S3 event: no delete attempted", handler.sqs_client.deleted == [], handler.sqs_client.deleted)

# 4. The one-off S3 test event: nothing to do, not an error
test_event = {"Records": [{"messageId": "t", "receiptHandle": "t", "eventSource": "aws:sqs", "eventSourceARN": QUEUE_ARN,
                           "body": json.dumps({"Type": "Notification", "Message": json.dumps({"Event": "s3:TestEvent"})})}]}
out, err, _ = run(test_event)
check("S3 test event: succeeds with nothing processed", err is None and "processed 0" in out["body"], (out, err))

# 5. URL-encoded keys are decoded (file names contain spaces)
objs = handler.objects_in_record(sqs_record("uploads/d/Mutual+NDA_Template.docx", 5))
check("encoded key decoded", objs == [(BUCKET, "uploads/d/Mutual NDA_Template.docx")], objs)

# 6. Queue URL from ARN
check("queue URL built from the ARN", handler.queue_url_from_arn(QUEUE_ARN) == QUEUE_URL, handler.queue_url_from_arn(QUEUE_ARN))


# 7. File names BDA refuses are extracted from a renamed copy
class FakeS3:
    def __init__(self):
        self.copies = []

    def copy_object(self, Bucket, Key, CopySource):
        self.copies.append((CopySource["Bucket"], CopySource["Key"], Bucket, Key))


# BDA's own validation pattern for inputConfiguration.s3Uri, copied from its error message.
BDA_URI = re.compile(r"s3://[a-z0-9][\.\-a-z0-9]{1,61}[a-z0-9](/[^\x00-\x1F\x7F\{^}%`\]\">\[~<#|]*)?")
CID = "1e6a673f-7af5-47eb-ba32-184b76ad34f1"
OUT = os.environ["BDA_OUTPUT_BUCKET"]

handler.s3_client = FakeS3()
plain = handler.bda_input_location(BUCKET, "uploads/d/Mutual NDA_Template (v2).docx", CID)
check("normal name: BDA reads the upload itself, no copy",
      plain == (BUCKET, "uploads/d/Mutual NDA_Template (v2).docx") and handler.s3_client.copies == [],
      (plain, handler.s3_client.copies))

eean = "uploads/824efddd-566c-4aa5-a50f-a0961fc958f3/}[Judefly][Vendor Agreement].docx"
check("the failing upload's key is refused by BDA's pattern", not BDA_URI.fullmatch(f"s3://{BUCKET}/{eean}"))
loc = handler.bda_input_location(BUCKET, eean, CID)
check("bracketed name: copied to a safe name under the BDA output prefix",
      loc == (OUT, f"bda-output/input/{CID}/__Judefly__Vendor Agreement_.docx"), loc)
check("bracketed name: copy made from the original upload",
      handler.s3_client.copies == [(BUCKET, eean, OUT, loc[1])], handler.s3_client.copies)
check("bracketed name: the copy's URI passes BDA's pattern", bool(BDA_URI.fullmatch(f"s3://{loc[0]}/{loc[1]}")), loc)
check("bracketed name: the copy is not under uploads/ (no re-trigger)", not loc[1].startswith("uploads/"), loc)

every = "uploads/e/a{b}c^d%e`f[g]h\"i<j>k~l#m|n\x01o\x7fp.pdf"
loc = handler.bda_input_location(BUCKET, every, CID)
check("every refused character replaced, extension kept",
      BDA_URI.fullmatch(f"s3://{loc[0]}/{loc[1]}") is not None and loc[1].endswith(".pdf"), loc)

print(f"\n{sum(results)}/{len(results)} checks passed")
sys.exit(0 if all(results) else 1)

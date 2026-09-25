"""
Show what the deployed AI agents produced for a contract, straight from Aurora.

This is the manual-validation view the SOW asks for: every finding with the
sentence it came from and its page, every detected field with its position.
It reads the same tables the analysis API will serve, so what it prints is
what the UI will show.

Usage:
    python show_contract_analysis.py                     # latest analysed contract
    python show_contract_analysis.py <contract_id>
    python show_contract_analysis.py <contract_id> --retrigger
        Re-puts the contract's S3 object so the real upload chain
        (S3 -> SNS -> SQS -> extraction -> agents) runs again, and waits for it.
    python show_contract_analysis.py <contract_id> --overlay DIR
        Downloads the BDA page image for each page with fields and draws the
        stored positions on it (needs Pillow).

Environment: AWS_PROFILE=zq-appevo MSYS_NO_PATHCONV=1 PYTHONIOENCODING=utf-8
"""

import json
import os
import sys
import time

import boto3

REGION = "us-west-2"
CLUSTER = "arn:aws:rds:us-west-2:580118073904:cluster:rag-app-prod-aurora-postgres"
SECRET = "arn:aws:secretsmanager:us-west-2:580118073904:secret:/rds/rag-app-prod-judy-ai-writer/credentials-Zh88jo"
DATABASE = "ragdb"

rds = boto3.client("rds-data", region_name=REGION)
s3 = boto3.client("s3", region_name=REGION)


def q(sql):
    result = rds.execute_statement(
        resourceArn=CLUSTER, secretArn=SECRET, database=DATABASE, sql=sql, formatRecordsAs="JSON"
    )
    return json.loads(result.get("formattedRecords", "[]"))


def latest_contract_id():
    rows = q("""
        select c.id::text as id from app.contracts c
        join judy_ai.agent_runs r on r.contract_id = c.id
        order by r.started_at desc limit 1
    """)
    return rows[0]["id"] if rows else None


def contract(cid):
    rows = q(f"select id::text, s3_bucket, s3_key, original_filename, status, uploaded_at::text "
             f"from app.contracts where id = '{cid}'")
    return rows[0] if rows else None


def latest_extraction(cid):
    rows = q(f"""
        select id::text, status, page_count, char_count, started_at::text, completed_at::text,
               raw_output_s3_key
          from judy_ai.document_extractions where contract_id = '{cid}'
         order by started_at desc limit 1
    """)
    return rows[0] if rows else None


def runs(extraction_id):
    return q(f"""
        select id::text, agent_type, status, latency_ms, input_tokens, output_tokens,
               error_message, started_at::text
          from judy_ai.agent_runs where extraction_id = '{extraction_id}' order by started_at
    """)


def hr(title):
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


def show(cid):
    c = contract(cid)
    if not c:
        print(f"No contract {cid}")
        return
    hr(f"CONTRACT  {c['original_filename']}")
    print(f"  id        {c['id']}")
    print(f"  uploaded  {c['uploaded_at']}   status {c['status']}")
    print(f"  s3        s3://{c['s3_bucket']}/{c['s3_key']}")

    ext = latest_extraction(cid)
    if not ext:
        print("\n  no extraction yet")
        return
    print(f"\n  EXTRACTION  {ext['status']}  {ext['page_count']} pages, {ext['char_count']} chars"
          f"   ({ext['started_at'][:19]} -> {(ext['completed_at'] or '')[:19]})")

    for run in runs(ext["id"]):
        print(f"  {run['agent_type']:24} {run['status']:10} {run['latency_ms'] or '-':>6} ms  "
              f"tokens {run['input_tokens'] or 0}+{run['output_tokens'] or 0}"
              + (f"   ERROR: {run['error_message'][:80]}" if run["error_message"] else ""))

    risks = q(f"""
        select r.risk_category, r.severity, r.title, r.detail, r.playbook_section,
               r.suggested_language, r.source_quote, r.source_page
          from judy_ai.contract_risks r join judy_ai.agent_runs a on a.id = r.run_id
         where a.extraction_id = '{ext['id']}'
         order by case r.severity when 'high' then 0 when 'medium' then 1 else 2 end,
                  r.risk_category, r.source_page nulls last
    """)
    hr(f"AGENT 1  RISK & CLAUSE  -  {len(risks)} finding(s)")
    for r in risks:
        page = f"p{r['source_page']}" if r["source_page"] else "  "
        tag = f"[{r['severity']:6}] {r['risk_category']:15}"
        print(f"\n  {tag} {page}  {r['title']}")
        print(f"      {r['detail']}")
        if r["source_quote"]:
            print(f"      \"{r['source_quote'][:160]}\"")
        if r["playbook_section"]:
            print(f"      playbook: {r['playbook_section']}")
            if r["suggested_language"]:
                print(f"      suggested wording (from the playbook, verbatim): "
                      f"{r['suggested_language'][:140].strip()}...")

    det = q(f"""
        select t.detected_template_type, t.rationale
          from judy_ai.template_detections t join judy_ai.agent_runs a on a.id = t.run_id
         where a.extraction_id = '{ext['id']}'
    """)
    fields = q(f"""
        select f.field_type, f.label, f.signer_role, f.page, f.position::text as position
          from judy_ai.contract_fields f join judy_ai.agent_runs a on a.id = f.run_id
         where a.extraction_id = '{ext['id']}'
         order by f.page, (f.position->>'y')::float nulls last, (f.position->>'x')::float
    """)
    hr(f"AGENT 2  TEMPLATE PRE-POPULATION  -  {len(fields)} field(s)")
    if det:
        print(f"  document type: {det[0]['detected_template_type']}")
        print(f"  because:       {det[0]['rationale']}")
    print()
    for f in fields:
        pos = json.loads(f["position"]) if f["position"] else None
        where = (f"x={pos['x']:.3f} y={pos['y']:.3f} w={pos['width']:.3f} h={pos['height']:.3f}"
                 if pos else "no position")
        print(f"  p{f['page']}  {f['field_type']:10} {(f['signer_role'] or '-'):10} "
              f"{f['label'][:26]:26} {where}")

    return ext, fields


def show_summary(extraction_id):
    rows = q(f"""
        select s.summary_text, s.key_points::text as key_points, s.word_count,
               a.raw_response::text as raw
          from judy_ai.contract_summaries s join judy_ai.agent_runs a on a.id = s.run_id
         where a.extraction_id = '{extraction_id}'
    """)
    hr("AGENT 3  PLAIN-LANGUAGE SUMMARY" + (f"  -  {rows[0]['word_count']} words" if rows else ""))
    if not rows:
        print("  no summary yet")
        return
    row = rows[0]
    print("  " + row["summary_text"].replace("\n", "\n  "))
    print()
    # The API serves key points as strings; the traces (quote + page) live in
    # raw_response so a reviewer can check each point against the document.
    traced = {}
    try:
        traced = {kp["point"]: kp for kp in json.loads(row["raw"]).get("key_points_traced", [])}
    except (json.JSONDecodeError, AttributeError, TypeError):
        pass
    for point in json.loads(row["key_points"] or "[]"):
        kp = traced.get(point, {})
        page = f"p{kp['source_page']}" if kp.get("source_page") else "  "
        print(f"  {page}  {point}")
        if kp.get("source_quote"):
            print(f"        \"{kp['source_quote'][:140]}\"")


def retrigger(cid):
    c = contract(cid)
    before = latest_extraction(cid)
    before_id = before["id"] if before else None
    started = time.time()
    s3.copy_object(
        Bucket=c["s3_bucket"], Key=c["s3_key"],
        CopySource={"Bucket": c["s3_bucket"], "Key": c["s3_key"]},
        MetadataDirective="REPLACE", Metadata={"retrigger": time.strftime("%Y-%m-%dT%H:%M:%S")},
    )
    print(f"re-put s3://{c['s3_bucket']}/{c['s3_key']}  -> S3 event fired; waiting for the chain")

    ext = None
    while time.time() - started < 240:
        ext = latest_extraction(cid)
        if ext and ext["id"] != before_id and ext["status"] in ("succeeded", "failed"):
            break
        time.sleep(4)
        print(f"  {int(time.time() - started):3}s  extraction "
              f"{(ext or {}).get('status', 'not started') if ext and ext['id'] != before_id else 'not started'}")
    if not ext or ext["id"] == before_id:
        print("extraction did not appear within 240 s")
        return
    print(f"  extraction {ext['status']} after {int(time.time() - started)} s")

    while time.time() - started < 360:
        rs = runs(ext["id"])
        if len(rs) >= 2 and all(r["status"] in ("succeeded", "failed") for r in rs):
            break
        time.sleep(4)
        print(f"  {int(time.time() - started):3}s  agents: "
              + ", ".join(f"{r['agent_type']}={r['status']}" for r in rs))
    print(f"  agents done after {int(time.time() - started)} s")


def overlay(cid, out_dir):
    from PIL import Image, ImageDraw

    ext, fields = show(cid) or (None, [])
    if not ext or not ext["raw_output_s3_key"]:
        return
    bucket = contract(cid)["s3_bucket"]
    base = ext["raw_output_s3_key"].rsplit("/result.json", 1)[0]
    colours = {"signature": (220, 30, 30), "date": (30, 120, 220), "full_name": (30, 160, 60),
               "title": (200, 120, 0), "company": (120, 60, 180), "text": (90, 90, 90)}
    os.makedirs(out_dir, exist_ok=True)
    for page in sorted({f["page"] for f in fields if f["page"] and f["position"]}):
        key = f"{base}/assets/rectified_image_{page - 1}.png"
        path = os.path.join(out_dir, f"{cid[:8]}_p{page}.png")
        s3.download_file(bucket, key, path)
        img = Image.open(path).convert("RGB")
        w, h = img.size
        draw = ImageDraw.Draw(img)
        for f in fields:
            if f["page"] != page or not f["position"]:
                continue
            p = json.loads(f["position"])
            box = [p["x"] * w, p["y"] * h, (p["x"] + p["width"]) * w, (p["y"] + p["height"]) * h]
            colour = colours.get(f["field_type"], (0, 0, 0))
            draw.rectangle(box, outline=colour, width=6)
            draw.text((box[0] + 8, box[1] - 34), f"{f['field_type']} / {f['signer_role']}", fill=colour)
        out = os.path.join(out_dir, f"{cid[:8]}_p{page}_overlay.png")
        img.save(out)
        print(f"\n  overlay written: {out}")


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    cid = args[0] if args else latest_contract_id()
    if not cid:
        print("No analysed contract found")
        return
    if "--retrigger" in sys.argv:
        retrigger(cid)
    if "--overlay" in sys.argv:
        overlay(cid, sys.argv[sys.argv.index("--overlay") + 1])
    else:
        show(cid)
    ext = latest_extraction(cid)
    if ext:
        show_summary(ext["id"])


if __name__ == "__main__":
    main()

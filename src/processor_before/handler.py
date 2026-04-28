"""Document processor - Traditional S3 API approach.

Downloads files from S3 to /tmp, processes them, uploads results back.
This is the "before" pattern that S3 Files eliminates.

Instrumented with Powertools for AWS Lambda (Logger, Tracer, Metrics).
"""

import json
import os
import time
from datetime import datetime, timezone

import boto3
from aws_lambda_powertools import Logger, Metrics, Tracer
from aws_lambda_powertools.metrics import MetricUnit

logger = Logger()
tracer = Tracer()
metrics = Metrics()

s3 = boto3.client("s3")

BUCKET = os.environ["BUCKET_NAME"]
INBOX = os.environ["INBOX_PREFIX"]
PROCESSED = os.environ["PROCESSED_PREFIX"]
REPORTS = os.environ["REPORTS_PREFIX"]


@logger.inject_lambda_context(log_event=False, correlation_id_path='requestContext.requestId')
@tracer.capture_lambda_handler
@metrics.log_metrics(capture_cold_start_metric=True)
def lambda_handler(event, context):
    mode = event.get("mode", "process")
    logger.append_keys(approach="before", mode=mode)

    if mode == "status":
        return get_status()

    return process_inbox()


@tracer.capture_method
def get_status():
    """List current state of all prefixes."""
    result = {}
    for prefix_name, prefix in [("inbox", INBOX), ("processed", PROCESSED), ("reports", REPORTS)]:
        response = s3.list_objects_v2(Bucket=BUCKET, Prefix=prefix)
        files = [
            obj["Key"].removeprefix(prefix)
            for obj in response.get("Contents", [])
            if not obj["Key"].endswith("/")
        ]
        result[prefix_name] = {"count": len(files), "files": files}

    return {"statusCode": 200, "approach": "before", "body": result}


@tracer.capture_method
def process_inbox():
    """Process all files in the inbox using S3 API calls."""
    timings = {"list_ms": 0, "download_ms": 0, "process_ms": 0, "upload_ms": 0, "cleanup_ms": 0}
    processed_files = []

    # List inbox files
    t0 = time.perf_counter()
    response = s3.list_objects_v2(Bucket=BUCKET, Prefix=INBOX)
    objects = [obj for obj in response.get("Contents", []) if not obj["Key"].endswith("/")]
    timings["list_ms"] = round((time.perf_counter() - t0) * 1000, 2)

    if not objects:
        logger.info("inbox empty")
        return {
            "statusCode": 200,
            "approach": "before",
            "message": "No files in inbox",
            "timings": timings,
        }

    logger.info("processing inbox", extra={"file_count": len(objects)})

    for obj in objects:
        key = obj["Key"]
        filename = key.removeprefix(INBOX)
        tmp_path = f"/tmp/{filename}"

        # Download from S3 to /tmp
        t1 = time.perf_counter()
        s3.download_file(BUCKET, key, tmp_path)
        timings["download_ms"] += round((time.perf_counter() - t1) * 1000, 2)

        # Process the file
        t2 = time.perf_counter()
        with open(tmp_path, "r") as f:
            content = f.read()

        result = {
            "filename": filename,
            "size_bytes": os.path.getsize(tmp_path),
            "word_count": len(content.split()),
            "line_count": content.count("\n") + 1,
            "char_count": len(content),
            "processed_at": datetime.now(timezone.utc).isoformat(),
        }
        timings["process_ms"] += round((time.perf_counter() - t2) * 1000, 2)

        # Upload processed file + metadata to processed/ prefix
        t3 = time.perf_counter()

        s3.copy_object(
            Bucket=BUCKET,
            CopySource={"Bucket": BUCKET, "Key": key},
            Key=f"{PROCESSED}{filename}",
        )

        s3.put_object(
            Bucket=BUCKET,
            Key=f"{PROCESSED}{filename}.meta.json",
            Body=json.dumps(result, indent=2),
            ContentType="application/json",
        )
        timings["upload_ms"] += round((time.perf_counter() - t3) * 1000, 2)

        # Delete from inbox (S3 has no rename - must copy then delete)
        t4 = time.perf_counter()
        s3.delete_object(Bucket=BUCKET, Key=key)
        timings["cleanup_ms"] += round((time.perf_counter() - t4) * 1000, 2)

        os.remove(tmp_path)
        processed_files.append(result)

    # Generate summary report
    t5 = time.perf_counter()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    summary = {
        "approach": "before",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "file_count": len(processed_files),
        "total_bytes": sum(f["size_bytes"] for f in processed_files),
        "total_words": sum(f["word_count"] for f in processed_files),
        "total_lines": sum(f["line_count"] for f in processed_files),
        "files": processed_files,
    }
    s3.put_object(
        Bucket=BUCKET,
        Key=f"{REPORTS}summary-before-{timestamp}.json",
        Body=json.dumps(summary, indent=2),
        ContentType="application/json",
    )
    timings["upload_ms"] += round((time.perf_counter() - t5) * 1000, 2)

    timings["total_ms"] = round(sum(timings.values()), 2)
    timings["file_count"] = len(processed_files)

    # Emit EMF metrics
    metrics.add_metric(name="FilesProcessed", unit=MetricUnit.Count, value=len(processed_files))
    metrics.add_metric(name="TotalProcessingMs", unit=MetricUnit.Milliseconds, value=timings["total_ms"])
    metrics.add_metric(name="DownloadMs", unit=MetricUnit.Milliseconds, value=timings["download_ms"])
    metrics.add_metric(name="UploadMs", unit=MetricUnit.Milliseconds, value=timings["upload_ms"])

    logger.info("finished processing", extra={"timings": timings})

    return {
        "statusCode": 200,
        "approach": "before",
        "message": f"Processed {len(processed_files)} files",
        "timings": timings,
    }

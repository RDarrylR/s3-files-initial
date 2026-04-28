"""Document processor - S3 Files mounted filesystem approach.

Reads and writes directly to /mnt/docs/ using standard filesystem operations.
No boto3 needed. No /tmp management. Rename is instant.

Instrumented with Powertools for AWS Lambda (Logger, Tracer, Metrics).
"""

import json
import os
import time
from datetime import datetime, timezone

from aws_lambda_powertools import Logger, Metrics, Tracer
from aws_lambda_powertools.metrics import MetricUnit

logger = Logger()
tracer = Tracer()
metrics = Metrics()

MOUNT_PATH = os.environ["MOUNT_PATH"]
INBOX = os.environ["INBOX_PREFIX"]
PROCESSED = os.environ["PROCESSED_PREFIX"]
REPORTS = os.environ["REPORTS_PREFIX"]


@logger.inject_lambda_context(log_event=False, correlation_id_path='requestContext.requestId')
@tracer.capture_lambda_handler
@metrics.log_metrics(capture_cold_start_metric=True)
def lambda_handler(event, context):
    mode = event.get("mode", "process")
    logger.append_keys(approach="after", mode=mode)

    if mode == "status":
        return get_status()

    return process_inbox()


@tracer.capture_method
def get_status():
    """List current state of all prefixes."""
    result = {}
    for prefix_name in [INBOX, PROCESSED, REPORTS]:
        dir_path = os.path.join(MOUNT_PATH, prefix_name)
        if os.path.isdir(dir_path):
            files = [f for f in os.listdir(dir_path) if not f.startswith(".")]
            result[prefix_name] = {"count": len(files), "files": files}
        else:
            result[prefix_name] = {"count": 0, "files": []}

    return {"statusCode": 200, "approach": "after", "body": result}


@tracer.capture_method
def process_inbox():
    """Process all files in the inbox using pure filesystem operations."""
    timings = {"list_ms": 0, "read_ms": 0, "process_ms": 0, "write_ms": 0, "move_ms": 0}
    processed_files = []

    inbox_path = os.path.join(MOUNT_PATH, INBOX)
    processed_path = os.path.join(MOUNT_PATH, PROCESSED)
    reports_path = os.path.join(MOUNT_PATH, REPORTS)

    os.makedirs(processed_path, exist_ok=True)
    os.makedirs(reports_path, exist_ok=True)

    # List inbox files - just os.listdir()
    t0 = time.perf_counter()
    files = [f for f in os.listdir(inbox_path) if not f.startswith(".")]
    timings["list_ms"] = round((time.perf_counter() - t0) * 1000, 2)

    if not files:
        timings["total_ms"] = round(sum(timings.values()), 2)
        timings["file_count"] = 0
        logger.info("inbox empty")
        return {
            "statusCode": 200,
            "approach": "after",
            "message": "No files in inbox",
            "timings": timings,
        }

    logger.info("processing inbox", extra={"file_count": len(files)})

    for filename in files:
        src_path = os.path.join(inbox_path, filename)

        if not os.path.isfile(src_path):
            continue

        # Read the file - just open()
        t1 = time.perf_counter()
        with open(src_path, "r") as f:
            content = f.read()
        timings["read_ms"] += round((time.perf_counter() - t1) * 1000, 2)

        # Process
        t2 = time.perf_counter()
        result = {
            "filename": filename,
            "size_bytes": os.path.getsize(src_path),
            "word_count": len(content.split()),
            "line_count": content.count("\n") + 1,
            "char_count": len(content),
            "processed_at": datetime.now(timezone.utc).isoformat(),
        }
        timings["process_ms"] += round((time.perf_counter() - t2) * 1000, 2)

        # Write metadata - just open() and write
        t3 = time.perf_counter()
        meta_path = os.path.join(processed_path, f"{filename}.meta.json")
        with open(meta_path, "w") as f:
            json.dump(result, f, indent=2)
        timings["write_ms"] += round((time.perf_counter() - t3) * 1000, 2)

        # Move file from inbox to processed - just os.rename()
        # This is instant with S3 Files. With S3 API you need copy + delete.
        t4 = time.perf_counter()
        dst_path = os.path.join(processed_path, filename)
        os.rename(src_path, dst_path)
        timings["move_ms"] += round((time.perf_counter() - t4) * 1000, 2)

        processed_files.append(result)

    # Summary report
    t5 = time.perf_counter()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    summary = {
        "approach": "after",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "file_count": len(processed_files),
        "total_bytes": sum(f["size_bytes"] for f in processed_files),
        "total_words": sum(f["word_count"] for f in processed_files),
        "total_lines": sum(f["line_count"] for f in processed_files),
        "files": processed_files,
    }
    report_path = os.path.join(reports_path, f"summary-after-{timestamp}.json")
    with open(report_path, "w") as f:
        json.dump(summary, f, indent=2)
    timings["write_ms"] += round((time.perf_counter() - t5) * 1000, 2)

    timings["total_ms"] = round(sum(timings.values()), 2)
    timings["file_count"] = len(processed_files)

    # Emit EMF metrics
    metrics.add_metric(name="FilesProcessed", unit=MetricUnit.Count, value=len(processed_files))
    metrics.add_metric(name="TotalProcessingMs", unit=MetricUnit.Milliseconds, value=timings["total_ms"])
    metrics.add_metric(name="ReadMs", unit=MetricUnit.Milliseconds, value=timings["read_ms"])
    metrics.add_metric(name="WriteMs", unit=MetricUnit.Milliseconds, value=timings["write_ms"])

    logger.info("finished processing", extra={"timings": timings})

    return {
        "statusCode": 200,
        "approach": "after",
        "message": f"Processed {len(processed_files)} files",
        "timings": timings,
    }

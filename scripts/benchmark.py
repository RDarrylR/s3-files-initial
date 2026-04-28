"""Benchmark comparing traditional S3 API vs S3 Files mounted Lambda processing."""

import argparse
import json
import random
import string
import time

import boto3

WORDS = [
    "the", "quick", "brown", "fox", "serverless", "lambda", "cloud",
    "infrastructure", "terraform", "deployment", "container", "kubernetes",
    "database", "network", "security", "pipeline", "workflow", "automation",
]


def generate_document(min_words=500, max_words=2000):
    word_count = random.randint(min_words, max_words)
    words = [random.choice(WORDS) for _ in range(word_count)]
    paragraphs = []
    i = 0
    while i < len(words):
        para_len = min(random.randint(20, 80), len(words) - i)
        para = " ".join(words[i : i + para_len])
        paragraphs.append(para.capitalize() + ".")
        i += para_len
    return "\n\n".join(paragraphs) + "\n"


def seed_inbox(s3, bucket, count, prefix="inbox/"):
    """Seed test files into the inbox."""
    for i in range(count):
        suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
        filename = f"bench-{suffix}.txt"
        content = generate_document()
        s3.put_object(Bucket=bucket, Key=f"{prefix}{filename}", Body=content, ContentType="text/plain")


def clear_prefix(s3, bucket, prefix):
    """Remove all objects under a prefix."""
    response = s3.list_objects_v2(Bucket=bucket, Prefix=prefix)
    for obj in response.get("Contents", []):
        if not obj["Key"].endswith("/"):
            s3.delete_object(Bucket=bucket, Key=obj["Key"])


def invoke_function(lambda_client, function_name):
    """Invoke a Lambda function and return parsed response."""
    t0 = time.perf_counter()
    response = lambda_client.invoke(
        FunctionName=function_name,
        InvocationType="RequestResponse",
        Payload=json.dumps({"mode": "process"}),
    )
    wall_ms = round((time.perf_counter() - t0) * 1000, 2)

    payload = json.loads(response["Payload"].read())
    return {
        "wall_ms": wall_ms,
        "lambda_response": payload,
    }


def main():
    parser = argparse.ArgumentParser(description="Benchmark S3 API vs S3 Files Lambda processing")
    parser.add_argument("--bucket", required=True, help="S3 bucket name")
    parser.add_argument("--before-function", required=True, help="Before Lambda function name")
    parser.add_argument("--after-function", required=True, help="After Lambda function name")
    parser.add_argument("--region", default="us-east-1", help="AWS region")
    parser.add_argument("--file-count", type=int, default=20, help="Files per run (default: 20)")
    parser.add_argument("--runs", type=int, default=3, help="Number of benchmark runs (default: 3)")
    parser.add_argument(
        "--nfs-propagation-wait",
        type=int,
        default=60,
        help="Seconds to wait after S3-seeding before invoking the after Lambda "
        "(S3 Files syncs S3-origin writes to NFS via EventBridge; allow time for propagation)",
    )
    args = parser.parse_args()

    s3 = boto3.client("s3", region_name=args.region)
    lambda_client = boto3.client("lambda", region_name=args.region)

    before_results = []
    after_results = []

    print(f"Benchmarking with {args.file_count} files x {args.runs} runs")
    print(f"  Before: {args.before_function}")
    print(f"  After:  {args.after_function}")
    print()

    for run in range(1, args.runs + 1):
        print(f"--- Run {run}/{args.runs} ---")

        # Clean up from any previous run - each approach has its own prefixes so
        # S3-origin directory ownership doesn't block the NFS-mounted "after" Lambda.
        clear_prefix(s3, args.bucket, "inbox-before/")
        clear_prefix(s3, args.bucket, "processed-before/")
        clear_prefix(s3, args.bucket, "inbox-after/")
        clear_prefix(s3, args.bucket, "processed-after/")

        # Seed "before" inbox
        print(f"  Seeding {args.file_count} files for before test...")
        seed_inbox(s3, args.bucket, args.file_count, prefix="inbox-before/")

        # Invoke "before" Lambda
        print(f"  Invoking {args.before_function}...")
        before = invoke_function(lambda_client, args.before_function)
        before_results.append(before)
        bt = before["lambda_response"].get("timings", {})
        print(f"    Wall: {before['wall_ms']}ms | Lambda total: {bt.get('total_ms', 'N/A')}ms")

        # Seed "after" inbox (separate prefix to avoid NFS ownership collisions)
        seed_inbox(s3, args.bucket, args.file_count, prefix="inbox-after/")

        # Files written via S3 API are visible through the NFS mount only after
        # S3 Files processes the corresponding EventBridge notifications. Wait
        # so the "after" Lambda sees the newly seeded files.
        if args.nfs_propagation_wait > 0:
            print(f"  Waiting {args.nfs_propagation_wait}s for S3->NFS propagation...")
            time.sleep(args.nfs_propagation_wait)

        # Invoke "after" Lambda
        print(f"  Invoking {args.after_function}...")
        after = invoke_function(lambda_client, args.after_function)
        after_results.append(after)
        at = after["lambda_response"].get("timings", {})
        print(f"    Wall: {after['wall_ms']}ms | Lambda total: {at.get('total_ms', 'N/A')}ms")
        if "total_ms" not in at:
            print(f"    [raw response] {json.dumps(after['lambda_response'])[:500]}")

        print()

    # Print comparison table
    print("=" * 70)
    print("RESULTS COMPARISON")
    print("=" * 70)
    print()

    def extract_timings(results, key):
        values = []
        for r in results:
            t = r["lambda_response"].get("timings", {})
            if key in t:
                values.append(t[key])
        return values

    def fmt_stats(values):
        if not values:
            return "N/A"
        avg = sum(values) / len(values)
        return f"{avg:>8.1f}ms (min: {min(values):.1f}, max: {max(values):.1f})"

    # Wall clock comparison
    before_walls = [r["wall_ms"] for r in before_results]
    after_walls = [r["wall_ms"] for r in after_results]
    print(f"{'Metric':<25} {'Before (S3 API)':<35} {'After (S3 Files)':<35}")
    print("-" * 95)
    print(f"{'Wall clock':<25} {fmt_stats(before_walls):<35} {fmt_stats(after_walls):<35}")

    # Lambda internal timing comparison
    before_keys = ["list_ms", "download_ms", "process_ms", "upload_ms", "cleanup_ms", "total_ms"]
    after_keys = ["list_ms", "read_ms", "process_ms", "write_ms", "move_ms", "total_ms"]
    labels = ["List files", "Read/Download", "Process", "Write/Upload", "Move/Cleanup", "Lambda total"]

    for label, bk, ak in zip(labels, before_keys, after_keys):
        bv = extract_timings(before_results, bk)
        av = extract_timings(after_results, ak)
        print(f"{label:<25} {fmt_stats(bv):<35} {fmt_stats(av):<35}")

    print()
    if before_walls and after_walls:
        before_avg = sum(before_walls) / len(before_walls)
        after_avg = sum(after_walls) / len(after_walls)
        if after_avg > 0:
            speedup = before_avg / after_avg
            print(f"S3 Files is {speedup:.1f}x {'faster' if speedup > 1 else 'slower'} (wall clock)")

    # Clean up
    clear_prefix(s3, args.bucket, "inbox-before/")
    clear_prefix(s3, args.bucket, "processed-before/")
    clear_prefix(s3, args.bucket, "inbox-after/")
    clear_prefix(s3, args.bucket, "processed-after/")
    print("\nCleanup complete.")


if __name__ == "__main__":
    main()

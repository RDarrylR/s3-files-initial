"""EC2-side benchmark runner for the three-way S3 API / S3 Files / Mountpoint comparison.

This script is uploaded to s3://$BUCKET/runner/benchmark.py and fetched onto
the EC2 instance by user-data (or `make deploy-runner`). The orchestrator
invokes it via SSM send-command and parses JSON from stdout.

Test types:
  - list-dir : enumerate a large flat directory and stat every entry
  - large-file : read N large binary files, SHA-256 hash them, write the digest back

Approaches:
  - s3api      : boto3 calls directly against the bucket
  - s3files    : filesystem at /mnt/s3files (NFS mount of S3 Files)
  - mountpoint : filesystem at /mnt/mountpoint (FUSE mount via mount-s3)

Seed modes (for populating test data from inside the VPC - much faster than
uploading from a laptop over the internet):
  - seed-list-dir : generate N tiny text files to list-dir-input/
  - seed-large-files : generate N random binary files of given size to large-file-input/
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import boto3

S3FILES_MOUNT = "/mnt/s3files"
MOUNTPOINT_MOUNT = "/mnt/mountpoint"

CHUNK_SIZE = 8 * 1024 * 1024  # 8 MiB streaming chunk

LIST_DIR_PREFIX = "list-dir-input/"
LARGE_FILE_INPUT_PREFIX = "large-file-input/"
LARGE_FILE_OUTPUT_PREFIX_FMT = "large-file-output-{approach}/"


# ---------- shared helpers ----------


def _ms(delta: float) -> float:
    return round(delta * 1000, 2)


def _root_for(approach: str) -> str:
    if approach == "s3files":
        return S3FILES_MOUNT
    if approach == "mountpoint":
        return MOUNTPOINT_MOUNT
    raise ValueError(f"no filesystem root for approach {approach!r}")


# ---------- list-dir test ----------


def list_dir_s3api(bucket: str, prefix: str) -> dict:
    s3 = boto3.client("s3")
    t0 = time.perf_counter()
    count = 0
    total_bytes = 0
    continuation = None
    while True:
        kwargs = {"Bucket": bucket, "Prefix": prefix}
        if continuation:
            kwargs["ContinuationToken"] = continuation
        resp = s3.list_objects_v2(**kwargs)
        for obj in resp.get("Contents", []):
            count += 1
            total_bytes += obj["Size"]
        if not resp.get("IsTruncated"):
            break
        continuation = resp["NextContinuationToken"]
    elapsed = time.perf_counter() - t0
    return {"list_ms": _ms(elapsed), "count": count, "total_bytes": total_bytes}


def list_dir_fs(root: str, prefix: str) -> dict:
    dir_path = os.path.join(root, prefix.rstrip("/"))
    t0 = time.perf_counter()
    names = os.listdir(dir_path)
    listdir_ms = _ms(time.perf_counter() - t0)

    t1 = time.perf_counter()
    count = 0
    total_bytes = 0
    for name in names:
        full = os.path.join(dir_path, name)
        try:
            st = os.stat(full)
        except OSError:
            continue
        count += 1
        total_bytes += st.st_size
    stat_ms = _ms(time.perf_counter() - t1)

    return {
        "list_ms": _ms((time.perf_counter() - t0)),
        "listdir_only_ms": listdir_ms,
        "stat_all_ms": stat_ms,
        "count": count,
        "total_bytes": total_bytes,
    }


def run_list_dir(approach: str, bucket: str) -> dict:
    if approach == "s3api":
        return list_dir_s3api(bucket, LIST_DIR_PREFIX)
    return list_dir_fs(_root_for(approach), LIST_DIR_PREFIX)


# ---------- large-file test ----------


def _hash_stream(stream) -> str:
    hasher = hashlib.sha256()
    while True:
        chunk = stream.read(CHUNK_SIZE)
        if not chunk:
            break
        hasher.update(chunk)
    return hasher.hexdigest()


def _large_files_s3api(bucket: str) -> dict:
    s3 = boto3.client("s3")
    # List input files
    t_list = time.perf_counter()
    resp = s3.list_objects_v2(Bucket=bucket, Prefix=LARGE_FILE_INPUT_PREFIX)
    inputs = [(o["Key"], o["Size"]) for o in resp.get("Contents", []) if o["Size"] > 0]
    list_ms = _ms(time.perf_counter() - t_list)

    output_prefix = LARGE_FILE_OUTPUT_PREFIX_FMT.format(approach="s3api")
    files = []
    for key, size in inputs:
        read_start = time.perf_counter()
        obj = s3.get_object(Bucket=bucket, Key=key)
        digest = _hash_stream(obj["Body"])
        read_ms = _ms(time.perf_counter() - read_start)

        write_start = time.perf_counter()
        out_key = output_prefix + os.path.basename(key) + ".sha256"
        s3.put_object(Bucket=bucket, Key=out_key, Body=digest.encode())
        write_ms = _ms(time.perf_counter() - write_start)

        files.append(
            {"key": key, "bytes": size, "read_ms": read_ms, "write_ms": write_ms, "sha256": digest}
        )

    return _summarize_large_files(files, list_ms)


def _large_files_fs(root: str, approach: str, bucket: str) -> dict:
    input_dir = os.path.join(root, LARGE_FILE_INPUT_PREFIX.rstrip("/"))
    output_dir = os.path.join(root, LARGE_FILE_OUTPUT_PREFIX_FMT.format(approach=approach).rstrip("/"))
    os.makedirs(output_dir, exist_ok=True)

    t_list = time.perf_counter()
    names = sorted(os.listdir(input_dir))
    list_ms = _ms(time.perf_counter() - t_list)

    files = []
    for name in names:
        src = os.path.join(input_dir, name)
        if not os.path.isfile(src):
            continue
        size = os.path.getsize(src)

        read_start = time.perf_counter()
        with open(src, "rb") as f:
            digest = _hash_stream(f)
        read_ms = _ms(time.perf_counter() - read_start)

        write_start = time.perf_counter()
        out_path = os.path.join(output_dir, name + ".sha256")
        with open(out_path, "w") as f:
            f.write(digest)
        write_ms = _ms(time.perf_counter() - write_start)

        files.append(
            {"key": name, "bytes": size, "read_ms": read_ms, "write_ms": write_ms, "sha256": digest}
        )

    return _summarize_large_files(files, list_ms)


def _summarize_large_files(files: list[dict], list_ms: float) -> dict:
    total_bytes = sum(f["bytes"] for f in files)
    total_read_ms = sum(f["read_ms"] for f in files)
    total_write_ms = sum(f["write_ms"] for f in files)
    # MB/s computed on total bytes over total read time (excludes list, write)
    read_throughput_mbps = (total_bytes / 1_048_576) / (total_read_ms / 1000) if total_read_ms else 0
    return {
        "list_ms": list_ms,
        "file_count": len(files),
        "total_bytes": total_bytes,
        "total_read_ms": round(total_read_ms, 2),
        "total_write_ms": round(total_write_ms, 2),
        "read_throughput_mib_s": round(read_throughput_mbps, 1),
        "files": files,
    }


def run_large_file(approach: str, bucket: str) -> dict:
    if approach == "s3api":
        return _large_files_s3api(bucket)
    return _large_files_fs(_root_for(approach), approach, bucket)


# ---------- seeders ----------


def _put_random_object(bucket: str, key: str, size: int) -> None:
    s3 = boto3.client("s3")
    # Use multipart for anything >= 8 MiB so we don't blow up RAM on 1 GiB files.
    if size < 8 * 1024 * 1024:
        s3.put_object(Bucket=bucket, Key=key, Body=secrets.token_bytes(size))
        return

    create = s3.create_multipart_upload(Bucket=bucket, Key=key)
    upload_id = create["UploadId"]
    parts = []
    remaining = size
    part_number = 1
    try:
        while remaining > 0:
            this_part = min(CHUNK_SIZE, remaining)
            resp = s3.upload_part(
                Bucket=bucket,
                Key=key,
                PartNumber=part_number,
                UploadId=upload_id,
                Body=secrets.token_bytes(this_part),
            )
            parts.append({"ETag": resp["ETag"], "PartNumber": part_number})
            part_number += 1
            remaining -= this_part
        s3.complete_multipart_upload(
            Bucket=bucket, Key=key, UploadId=upload_id, MultipartUpload={"Parts": parts}
        )
    except Exception:
        s3.abort_multipart_upload(Bucket=bucket, Key=key, UploadId=upload_id)
        raise


def seed_list_dir(bucket: str, count: int, concurrency: int = 32) -> dict:
    """Seed `count` small text objects under list-dir-input/."""
    t0 = time.perf_counter()

    def one(i: int) -> None:
        key = f"{LIST_DIR_PREFIX}file-{i:06d}.txt"
        body = f"entry {i}\n".encode()
        boto3.client("s3").put_object(Bucket=bucket, Key=key, Body=body)

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        list(pool.map(one, range(count)))

    return {"count": count, "elapsed_ms": _ms(time.perf_counter() - t0)}


def seed_large_files(bucket: str, count: int, size_mib: int, concurrency: int = 4) -> dict:
    """Seed `count` random binary files of size_mib MiB under large-file-input/."""
    size_bytes = size_mib * 1024 * 1024
    t0 = time.perf_counter()

    def one(i: int) -> tuple[str, int]:
        key = f"{LARGE_FILE_INPUT_PREFIX}blob-{i:03d}.bin"
        _put_random_object(bucket, key, size_bytes)
        return key, size_bytes

    results = []
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        for fut in as_completed([pool.submit(one, i) for i in range(count)]):
            results.append(fut.result())

    return {
        "count": count,
        "size_mib_each": size_mib,
        "total_mib": count * size_mib,
        "elapsed_ms": _ms(time.perf_counter() - t0),
    }


# ---------- cleanup ----------


def _delete_prefix(bucket: str, prefix: str) -> int:
    s3 = boto3.client("s3")
    deleted = 0
    continuation = None
    while True:
        kwargs = {"Bucket": bucket, "Prefix": prefix}
        if continuation:
            kwargs["ContinuationToken"] = continuation
        resp = s3.list_objects_v2(**kwargs)
        contents = resp.get("Contents", [])
        if not contents:
            break
        # delete_objects handles up to 1000 per call
        for i in range(0, len(contents), 1000):
            batch = contents[i : i + 1000]
            s3.delete_objects(
                Bucket=bucket,
                Delete={"Objects": [{"Key": o["Key"]} for o in batch]},
            )
            deleted += len(batch)
        if not resp.get("IsTruncated"):
            break
        continuation = resp.get("NextContinuationToken")
    return deleted


def cleanup(bucket: str) -> dict:
    out = {}
    for approach in ("s3api", "s3files", "mountpoint"):
        out[approach] = _delete_prefix(bucket, LARGE_FILE_OUTPUT_PREFIX_FMT.format(approach=approach))
    out["list_dir_input"] = _delete_prefix(bucket, LIST_DIR_PREFIX)
    out["large_file_input"] = _delete_prefix(bucket, LARGE_FILE_INPUT_PREFIX)
    return out


# ---------- CLI ----------


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--mode",
        required=True,
        choices=[
            "list-dir",
            "large-file",
            "seed-list-dir",
            "seed-large-files",
            "cleanup",
        ],
    )
    p.add_argument("--approach", choices=["s3api", "s3files", "mountpoint"])
    p.add_argument("--bucket", required=True)
    p.add_argument("--count", type=int, default=0, help="Seed count")
    p.add_argument("--size-mib", type=int, default=1024, help="Seed file size (MiB)")
    args = p.parse_args()

    start = time.perf_counter()
    if args.mode == "list-dir":
        if not args.approach:
            p.error("--approach required for list-dir")
        result = {"mode": args.mode, "approach": args.approach, **run_list_dir(args.approach, args.bucket)}
    elif args.mode == "large-file":
        if not args.approach:
            p.error("--approach required for large-file")
        result = {"mode": args.mode, "approach": args.approach, **run_large_file(args.approach, args.bucket)}
    elif args.mode == "seed-list-dir":
        result = {"mode": args.mode, **seed_list_dir(args.bucket, args.count)}
    elif args.mode == "seed-large-files":
        result = {"mode": args.mode, **seed_large_files(args.bucket, args.count, args.size_mib)}
    else:  # cleanup
        result = {"mode": args.mode, "deleted": cleanup(args.bucket)}

    result["wall_ms"] = _ms(time.perf_counter() - start)
    json.dump(result, sys.stdout, default=str)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()

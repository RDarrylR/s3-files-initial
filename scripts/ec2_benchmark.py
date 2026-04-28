"""Orchestrate the three-way EC2 benchmark via SSM send-command.

Usage examples:
    # Seed a 10K-file directory and 5 x 1 GiB random binaries, then run everything
    python scripts/ec2_benchmark.py --bucket my-bucket --instance-id i-xxx \\
        --seed-dir-count 10000 --seed-large-count 5 --seed-large-size-mib 1024

    # Just run the benchmarks (data already seeded)
    python scripts/ec2_benchmark.py --bucket my-bucket --instance-id i-xxx --skip-seed

    # Clean up all seeded + output data when done
    python scripts/ec2_benchmark.py --bucket my-bucket --instance-id i-xxx --cleanup-only
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time

import boto3

RUNNER_PATH = "/opt/runner/benchmark.py"
PYTHON_CMD = "/usr/local/bin/python3-bench"


def _ssm_run(ssm, instance_id: str, cmd: str, timeout_seconds: int = 3600) -> dict:
    """Run a shell command on the EC2 instance via SSM and return parsed JSON stdout."""
    resp = ssm.send_command(
        InstanceIds=[instance_id],
        DocumentName="AWS-RunShellScript",
        Parameters={"commands": [cmd], "executionTimeout": [str(timeout_seconds)]},
        TimeoutSeconds=60,
    )
    cmd_id = resp["Command"]["CommandId"]

    # Poll for completion
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        time.sleep(2)
        try:
            inv = ssm.get_command_invocation(CommandId=cmd_id, InstanceId=instance_id)
        except ssm.exceptions.InvocationDoesNotExist:
            continue
        status = inv["Status"]
        if status in ("Success", "Failed", "Cancelled", "TimedOut"):
            break
    else:
        raise TimeoutError(f"SSM command {cmd_id} did not complete in {timeout_seconds}s")

    if inv["Status"] != "Success":
        stderr = inv.get("StandardErrorContent", "")
        stdout = inv.get("StandardOutputContent", "")
        raise RuntimeError(f"SSM command failed ({inv['Status']}):\nstdout: {stdout}\nstderr: {stderr}")

    stdout = inv["StandardOutputContent"].strip()
    # The runner always ends with a single JSON line; tolerate extra log noise
    # by taking the last non-empty line.
    last_line = [line for line in stdout.splitlines() if line.strip()][-1]
    return json.loads(last_line)


def _cmd(bucket: str, mode: str, approach: str | None = None, **kwargs) -> str:
    parts = [PYTHON_CMD, RUNNER_PATH, "--mode", mode, "--bucket", bucket]
    if approach:
        parts += ["--approach", approach]
    for k, v in kwargs.items():
        parts += [f"--{k.replace('_', '-')}", str(v)]
    return " ".join(parts)


def seed(ssm, instance_id: str, bucket: str, dir_count: int, large_count: int, large_size_mib: int) -> None:
    if dir_count > 0:
        print(f"Seeding {dir_count} small files for the list-dir test...")
        t0 = time.perf_counter()
        result = _ssm_run(
            ssm,
            instance_id,
            _cmd(bucket, "seed-list-dir", count=dir_count),
            timeout_seconds=3600,
        )
        print(f"  seeded in {(time.perf_counter() - t0):.1f}s (runner reported {result['elapsed_ms']:.0f}ms)")

    if large_count > 0 and large_size_mib > 0:
        total_mib = large_count * large_size_mib
        print(f"Seeding {large_count} x {large_size_mib} MiB random binaries ({total_mib} MiB total)...")
        t0 = time.perf_counter()
        result = _ssm_run(
            ssm,
            instance_id,
            _cmd(bucket, "seed-large-files", count=large_count, size_mib=large_size_mib),
            timeout_seconds=7200,
        )
        print(f"  seeded in {(time.perf_counter() - t0):.1f}s (runner reported {result['elapsed_ms']:.0f}ms)")


def run_benchmark(
    ssm, instance_id: str, bucket: str, runs: int, skip_large: bool = False, skip_list: bool = False
) -> dict:
    approaches = ["s3api", "s3files", "mountpoint"]
    results: dict = {"list-dir": {a: [] for a in approaches}, "large-file": {a: [] for a in approaches}}

    for run_ix in range(1, runs + 1):
        print(f"\n--- Run {run_ix}/{runs} ---")
        if not skip_list:
            for approach in approaches:
                print(f"  list-dir / {approach}...", end=" ", flush=True)
                r = _ssm_run(
                    ssm, instance_id, _cmd(bucket, "list-dir", approach=approach), timeout_seconds=1800
                )
                print(f"{r['list_ms']:.0f}ms ({r['count']} entries)")
                results["list-dir"][approach].append(r)
        if not skip_large:
            for approach in approaches:
                print(f"  large-file / {approach}...", end=" ", flush=True)
                r = _ssm_run(
                    ssm, instance_id, _cmd(bucket, "large-file", approach=approach), timeout_seconds=3600
                )
                total_mb = r["total_bytes"] / 1_048_576
                print(
                    f"read {r['total_read_ms']:.0f}ms, write {r['total_write_ms']:.0f}ms, "
                    f"{r['read_throughput_mib_s']:.0f} MiB/s over {total_mb:.0f} MiB"
                )
                results["large-file"][approach].append(r)

    return results


def _fmt(values: list[float]) -> str:
    if not values:
        return "n/a"
    return f"{statistics.mean(values):.1f} (min {min(values):.1f}, max {max(values):.1f})"


def print_comparison(results: dict) -> None:
    print("\n" + "=" * 78)
    print("THREE-WAY COMPARISON (EC2 c7g.large, arm64)")
    print("=" * 78)

    # list-dir summary
    lst = results["list-dir"]
    if any(lst.values()):
        print("\n-- Large-directory walk (list + stat) --")
        print(f"{'Approach':<14}{'list_ms (mean)':<38}{'count':>10}")
        for approach in ("s3api", "s3files", "mountpoint"):
            runs = lst[approach]
            if not runs:
                continue
            list_ms = [r["list_ms"] for r in runs]
            count = runs[0]["count"]
            print(f"{approach:<14}{_fmt(list_ms):<38}{count:>10}")

    # large-file summary
    lf = results["large-file"]
    if any(lf.values()):
        print("\n-- Large-file hash+write --")
        print(
            f"{'Approach':<14}{'total_read_ms':<32}{'total_write_ms':<30}{'MiB/s (mean)':>12}"
        )
        for approach in ("s3api", "s3files", "mountpoint"):
            runs = lf[approach]
            if not runs:
                continue
            read_ms = [r["total_read_ms"] for r in runs]
            write_ms = [r["total_write_ms"] for r in runs]
            tp = [r["read_throughput_mib_s"] for r in runs]
            print(f"{approach:<14}{_fmt(read_ms):<32}{_fmt(write_ms):<30}{statistics.mean(tp):>12.0f}")
        # show file count + size from the first recorded run
        sample = next(iter(r for a in lf.values() for r in a), None)
        if sample:
            total_mib = sample["total_bytes"] / 1_048_576
            print(f"\n  ({sample['file_count']} files, {total_mib:.0f} MiB total per run)")


def cleanup(ssm, instance_id: str, bucket: str) -> None:
    print("Cleaning up seeded data and outputs...")
    result = _ssm_run(ssm, instance_id, _cmd(bucket, "cleanup"), timeout_seconds=1800)
    print(f"  deleted: {result['deleted']}")


def main() -> None:
    p = argparse.ArgumentParser(description="Three-way EC2 benchmark orchestrator")
    p.add_argument("--bucket", required=True)
    p.add_argument("--instance-id", required=True)
    p.add_argument("--region", default="us-east-1")
    p.add_argument("--runs", type=int, default=3)
    p.add_argument("--seed-dir-count", type=int, default=10000)
    p.add_argument("--seed-large-count", type=int, default=5)
    p.add_argument("--seed-large-size-mib", type=int, default=1024)
    p.add_argument("--skip-seed", action="store_true")
    p.add_argument("--skip-large", action="store_true")
    p.add_argument("--skip-list", action="store_true")
    p.add_argument("--cleanup-only", action="store_true")
    p.add_argument("--no-cleanup", action="store_true", help="Leave seeded data in the bucket")
    args = p.parse_args()

    ssm = boto3.client("ssm", region_name=args.region)

    if args.cleanup_only:
        cleanup(ssm, args.instance_id, args.bucket)
        return

    if not args.skip_seed:
        dir_count = 0 if args.skip_list else args.seed_dir_count
        large_count = 0 if args.skip_large else args.seed_large_count
        seed(ssm, args.instance_id, args.bucket, dir_count, large_count, args.seed_large_size_mib)

    results = run_benchmark(
        ssm,
        args.instance_id,
        args.bucket,
        args.runs,
        skip_large=args.skip_large,
        skip_list=args.skip_list,
    )
    print_comparison(results)

    if not args.no_cleanup:
        cleanup(ssm, args.instance_id, args.bucket)


if __name__ == "__main__":
    main()

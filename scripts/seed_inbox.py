"""Seed the S3 inbox with test document files."""

import argparse
import random
import string

import boto3

WORDS = [
    "the", "quick", "brown", "fox", "jumps", "over", "lazy", "dog",
    "lorem", "ipsum", "dolor", "sit", "amet", "consectetur", "adipiscing",
    "serverless", "lambda", "function", "storage", "bucket", "cloud",
    "infrastructure", "terraform", "deployment", "container", "kubernetes",
    "database", "network", "security", "encryption", "monitoring", "logging",
    "pipeline", "workflow", "automation", "integration", "microservice",
    "architecture", "scalable", "distributed", "resilient", "observability",
    "performance", "optimization", "throughput", "latency", "benchmark",
]

SIZE_CONFIGS = {
    "small": {"min_words": 50, "max_words": 200},
    "medium": {"min_words": 500, "max_words": 2000},
    "large": {"min_words": 5000, "max_words": 20000},
}


def generate_document(size: str) -> str:
    """Generate a realistic-looking text document."""
    config = SIZE_CONFIGS[size]
    word_count = random.randint(config["min_words"], config["max_words"])

    paragraphs = []
    words_remaining = word_count

    while words_remaining > 0:
        para_len = min(random.randint(20, 80), words_remaining)
        words = [random.choice(WORDS) for _ in range(para_len)]
        # Capitalize first word, add periods
        words[0] = words[0].capitalize()
        sentences = []
        i = 0
        while i < len(words):
            sent_len = random.randint(5, 15)
            sent_words = words[i : i + sent_len]
            if sent_words:
                sent_words[0] = sent_words[0].capitalize()
                sentences.append(" ".join(sent_words) + ".")
            i += sent_len
        paragraphs.append(" ".join(sentences))
        words_remaining -= para_len

    return "\n\n".join(paragraphs) + "\n"


def generate_filename() -> str:
    """Generate a realistic document filename."""
    prefixes = ["report", "memo", "analysis", "summary", "notes", "review", "spec", "draft"]
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
    return f"{random.choice(prefixes)}-{suffix}.txt"


def main():
    parser = argparse.ArgumentParser(description="Seed S3 inbox with test documents")
    parser.add_argument("--count", type=int, default=10, help="Number of files to seed (default: 10)")
    parser.add_argument(
        "--size",
        choices=["small", "medium", "large", "mixed"],
        default="medium",
        help="File size variant (default: medium)",
    )
    parser.add_argument("--bucket", required=True, help="S3 bucket name")
    parser.add_argument("--region", default="us-east-1", help="AWS region (default: us-east-1)")
    parser.add_argument("--prefix", default="inbox/", help="S3 prefix (default: inbox/)")
    args = parser.parse_args()

    s3 = boto3.client("s3", region_name=args.region)

    sizes = ["small", "medium", "large"] if args.size == "mixed" else [args.size]

    print(f"Seeding {args.count} {args.size} files to s3://{args.bucket}/{args.prefix}")

    for i in range(args.count):
        size = random.choice(sizes)
        filename = generate_filename()
        content = generate_document(size)
        key = f"{args.prefix}{filename}"

        s3.put_object(Bucket=args.bucket, Key=key, Body=content, ContentType="text/plain")
        print(f"  [{i + 1}/{args.count}] {key} ({len(content)} bytes, {size})")

    print(f"Done. Seeded {args.count} files.")


if __name__ == "__main__":
    main()

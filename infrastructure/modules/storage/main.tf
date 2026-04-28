data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  region = data.aws_region.current.region
}

# --- S3 Bucket (versioning mandatory for S3 Files) ---

resource "aws_s3_bucket" "docs" {
  bucket = "${var.project_name}-${var.environment}-docs"
}

resource "aws_s3_bucket_versioning" "docs" {
  bucket = aws_s3_bucket.docs.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "docs" {
  bucket = aws_s3_bucket.docs.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "docs" {
  bucket = aws_s3_bucket.docs.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Disable ACLs - bucket owner enforced. Default for new buckets, but explicit
# is better than relying on defaults readers may not understand.
resource "aws_s3_bucket_ownership_controls" "docs" {
  bucket = aws_s3_bucket.docs.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

# Clean up noncurrent object versions from repeated benchmark runs.
# Versioning is mandatory for S3 Files, so without lifecycle cleanup old
# versions accumulate silently.
resource "aws_s3_bucket_lifecycle_configuration" "docs" {
  bucket = aws_s3_bucket.docs.id

  rule {
    id     = "expire-noncurrent-versions"
    status = "Enabled"

    noncurrent_version_expiration {
      noncurrent_days = 7
    }
  }

  depends_on = [aws_s3_bucket_versioning.docs]
}

# Enforce TLS-only access. The confused-deputy guard (aws:ResourceAccount)
# prevents the S3 Files service role from being tricked into operating on
# buckets in other accounts.
resource "aws_s3_bucket_policy" "docs" {
  bucket = aws_s3_bucket.docs.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "DenyNonTLS"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:*"
        Resource = [
          aws_s3_bucket.docs.arn,
          "${aws_s3_bucket.docs.arn}/*"
        ]
        Condition = {
          Bool = {
            "aws:SecureTransport" = "false"
          }
        }
      },
    ]
  })

  depends_on = [aws_s3_bucket_public_access_block.docs]
}

# --- IAM Role for S3 Files service ---
# S3 Files assumes this role to read/write the S3 bucket on behalf of NFS clients.

resource "aws_iam_role" "s3files_service" {
  name = "${var.project_name}-${var.environment}-s3files-service"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid    = "AllowS3FilesAssumeRole"
      Effect = "Allow"
      Principal = {
        Service = "elasticfilesystem.amazonaws.com"
      }
      Action = "sts:AssumeRole"
      Condition = {
        StringEquals = {
          "aws:SourceAccount" = data.aws_caller_identity.current.account_id
        }
        ArnLike = {
          "aws:SourceArn" = "arn:aws:s3files:${local.region}:${data.aws_caller_identity.current.account_id}:file-system/*"
        }
      }
    }]
  })
}

resource "aws_iam_role_policy" "s3files_bucket_access" {
  name = "s3-bucket-access"
  role = aws_iam_role.s3files_service.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowS3BucketAccess"
        Effect = "Allow"
        Action = [
          "s3:ListBucket",
          "s3:ListBucketVersions",
          "s3:GetBucketLocation",
          "s3:GetBucketVersioning",
          "s3:AbortMultipartUpload",
          "s3:DeleteObject",
          "s3:DeleteObjectVersion",
          "s3:GetObject",
          "s3:GetObjectVersion",
          "s3:GetObjectTagging",
          "s3:GetObjectVersionTagging",
          "s3:ListMultipartUploadParts",
          "s3:PutObject",
          "s3:PutObjectTagging"
        ]
        Resource = [
          aws_s3_bucket.docs.arn,
          "${aws_s3_bucket.docs.arn}/*"
        ]
        Condition = {
          StringEquals = {
            "aws:ResourceAccount" = data.aws_caller_identity.current.account_id
          }
        }
      }
    ]
  })
}

# EventBridge permissions: S3 Files creates EventBridge rules to detect
# out-of-band bucket changes (PutObject, DeleteObject via the S3 API).
# Without these, S3-to-NFS synchronization doesn't work and S3-side writes
# never appear on the mount.
resource "aws_iam_role_policy" "s3files_eventbridge" {
  name = "eventbridge-sync"
  role = aws_iam_role.s3files_service.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "EventBridgeManage"
        Effect = "Allow"
        Action = [
          "events:PutRule",
          "events:PutTargets",
          "events:DeleteRule",
          "events:DisableRule",
          "events:EnableRule",
          "events:RemoveTargets",
        ]
        Resource = "arn:aws:events:${local.region}:${data.aws_caller_identity.current.account_id}:rule/DO-NOT-DELETE-S3-Files*"
        Condition = {
          StringEquals = {
            "events:ManagedBy" = "elasticfilesystem.amazonaws.com"
          }
        }
      },
      {
        Sid    = "EventBridgeRead"
        Effect = "Allow"
        Action = [
          "events:DescribeRule",
          "events:ListRules",
          "events:ListRuleNamesByTarget",
          "events:ListTargetsByRule",
        ]
        Resource = "arn:aws:events:${local.region}:${data.aws_caller_identity.current.account_id}:rule/*"
      },
    ]
  })
}

# Note: we deliberately do NOT pre-create `inbox/`, `processed/`, `reports/`
# as zero-byte S3 objects. When S3 Files mirrors S3-origin directories into
# the NFS view they come in with root ownership, which blocks the Lambda
# (running as the access point's UID/GID 1000:1000) from writing into them.
# The Lambda handlers call `os.makedirs(..., exist_ok=True)` over NFS, which
# creates the directories with the correct access-point ownership.

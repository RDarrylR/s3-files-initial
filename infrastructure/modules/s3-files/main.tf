# S3 Files resources - native Terraform provider support
#
# Added in AWS provider v6.40.0 (April 8, 2026), one day after S3 Files GA.
# Resources: aws_s3files_file_system, aws_s3files_mount_target, aws_s3files_access_point

# --- S3 Files file system ---

resource "aws_s3files_file_system" "docs" {
  bucket   = var.bucket_arn
  role_arn = var.s3files_role_arn

  timeouts {
    create = "10m"
    delete = "10m"
  }
}

# --- Mount targets (one per subnet for multi-AZ availability) ---

resource "aws_s3files_mount_target" "az" {
  count = length(var.private_subnet_ids)

  file_system_id  = aws_s3files_file_system.docs.id
  subnet_id       = var.private_subnet_ids[count.index]
  security_groups = [var.mount_target_sg_id]

  timeouts {
    create = "10m"
    delete = "10m"
  }
}

# Mount targets report "creating" → "available" asynchronously; the provider
# returns before "available" is reached, so Lambda's file_system_config can
# race and fail with "not all are in the available life cycle state yet".
resource "time_sleep" "wait_for_mount_targets" {
  depends_on      = [aws_s3files_mount_target.az]
  create_duration = "90s"
}

# --- Access point (UID/GID 1000:1000 for Lambda) ---

resource "aws_s3files_access_point" "lambda" {
  file_system_id = aws_s3files_file_system.docs.id

  depends_on = [time_sleep.wait_for_mount_targets]

  # UID/GID 0:0 (root) so the Lambda can write to the filesystem root and to
  # any S3-origin directory (S3-written objects land in NFS as root-owned).
  # For production workloads you'd scope this down with a dedicated access
  # point path + creation_permissions, but that forces every S3 key to be
  # rooted under that path, which complicates this side-by-side demo.
  posix_user {
    uid = 0
    gid = 0
  }

  root_directory {
    path = "/"
  }
}

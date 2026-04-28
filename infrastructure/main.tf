data "aws_caller_identity" "current" {}

# --- Storage: S3 bucket with versioning + S3 Files service role ---

module "storage" {
  source = "./modules/storage"

  project_name = var.project_name
  environment  = var.environment
}

# --- Networking: VPC, private subnets, VPC endpoints, security groups ---

module "networking" {
  source = "./modules/networking"

  project_name         = var.project_name
  environment          = var.environment
  vpc_cidr             = var.vpc_cidr
  availability_zones   = var.availability_zones
  private_subnet_cidrs = var.private_subnet_cidrs
  bucket_arn           = module.storage.bucket_arn
}

# --- S3 Files: file system, mount targets, access point ---

module "s3_files" {
  source = "./modules/s3-files"

  bucket_arn         = module.storage.bucket_arn
  s3files_role_arn   = module.storage.s3files_role_arn
  private_subnet_ids = module.networking.private_subnet_ids
  mount_target_sg_id = module.networking.mount_target_sg_id
}

# --- Lambda Before: traditional S3 API approach (no VPC) ---

module "lambda_before" {
  source = "./modules/lambda-before"

  project_name   = var.project_name
  environment    = var.environment
  bucket_name    = module.storage.bucket_name
  bucket_arn     = module.storage.bucket_arn
  lambda_memory  = var.lambda_memory
  lambda_timeout = var.lambda_timeout
}

# --- Lambda After: S3 Files mounted filesystem (VPC + NFS) ---

module "lambda_after" {
  source = "./modules/lambda-after"

  project_name       = var.project_name
  environment        = var.environment
  bucket_arn         = module.storage.bucket_arn
  access_point_arn   = module.s3_files.access_point_arn
  private_subnet_ids = module.networking.private_subnet_ids
  lambda_sg_id       = module.networking.lambda_sg_id
  lambda_memory      = var.lambda_memory
  lambda_timeout     = var.lambda_timeout
}

# --- S3 Files filesystem policy (deny-by-default, known principals only) ---
# Placed at root level because it references role ARNs from both the
# lambda-after and ec2-benchmark modules (avoiding circular dependencies).
resource "aws_s3files_file_system_policy" "docs" {
  file_system_id = module.s3_files.file_system_id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowMountFromKnownRoles"
        Effect = "Allow"
        Principal = {
          AWS = [
            module.lambda_after.execution_role_arn,
            module.ec2_benchmark.execution_role_arn,
          ]
        }
        Action = [
          "s3files:ClientMount",
          "s3files:ClientWrite",
        ]
        Resource = module.s3_files.file_system_arn
      },
      {
        Sid       = "EnforceTLS"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3files:*"
        Resource  = module.s3_files.file_system_arn
        Condition = {
          Bool = {
            "aws:SecureTransport" = "false"
          }
        }
      },
    ]
  })
}

# --- EC2 Benchmark: three-way comparison (S3 API + S3 Files + Mountpoint) ---
# Mountpoint for S3 is FUSE-based and does not work on Lambda, so the three-way
# comparison runs on a single EC2 host that mounts both S3 Files and Mountpoint.
module "ec2_benchmark" {
  source = "./modules/ec2-benchmark"

  project_name       = var.project_name
  environment        = var.environment
  vpc_id             = module.networking.vpc_id
  subnet_id          = module.networking.private_subnet_ids[0]
  mount_target_sg_id = module.networking.mount_target_sg_id
  bucket_name        = module.storage.bucket_name
  bucket_arn         = module.storage.bucket_arn
  file_system_id     = module.s3_files.file_system_id
  access_point_arn   = module.s3_files.access_point_arn
}

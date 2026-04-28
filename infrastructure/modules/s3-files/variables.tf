variable "bucket_arn" {
  description = "ARN of the S3 bucket to mount"
  type        = string
}

variable "s3files_role_arn" {
  description = "ARN of the IAM role for S3 Files service"
  type        = string
}

variable "private_subnet_ids" {
  description = "Subnet IDs for mount targets"
  type        = list(string)
}

variable "mount_target_sg_id" {
  description = "Security group ID for mount targets"
  type        = string
}


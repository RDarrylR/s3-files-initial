output "bucket_name" {
  description = "Name of the S3 bucket"
  value       = aws_s3_bucket.docs.id
}

output "bucket_arn" {
  description = "ARN of the S3 bucket"
  value       = aws_s3_bucket.docs.arn
}

output "bucket_id" {
  description = "ID of the S3 bucket"
  value       = aws_s3_bucket.docs.id
}

output "s3files_role_arn" {
  description = "ARN of the IAM role for S3 Files service"
  value       = aws_iam_role.s3files_service.arn
}

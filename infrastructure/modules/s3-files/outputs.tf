output "file_system_id" {
  description = "ID of the S3 Files file system"
  value       = aws_s3files_file_system.docs.id
}

output "file_system_arn" {
  description = "ARN of the S3 Files file system"
  value       = aws_s3files_file_system.docs.arn
}

output "access_point_id" {
  description = "ID of the S3 Files access point"
  value       = aws_s3files_access_point.lambda.id
}

output "access_point_arn" {
  description = "ARN of the S3 Files access point"
  value       = aws_s3files_access_point.lambda.arn
}

output "mount_target_ids" {
  description = "IDs of the mount targets"
  value       = aws_s3files_mount_target.az[*].id
}

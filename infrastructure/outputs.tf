output "bucket_name" {
  description = "Name of the S3 bucket"
  value       = module.storage.bucket_name
}

output "before_function_name" {
  description = "Name of the before (traditional) Lambda function"
  value       = module.lambda_before.function_name
}

output "after_function_name" {
  description = "Name of the after (S3 Files) Lambda function"
  value       = module.lambda_after.function_name
}

output "file_system_id" {
  description = "ID of the S3 Files file system"
  value       = module.s3_files.file_system_id
}

output "vpc_id" {
  description = "ID of the VPC"
  value       = module.networking.vpc_id
}

output "aws_region" {
  description = "AWS region"
  value       = var.aws_region
}

output "aws_account_id" {
  description = "AWS account ID"
  value       = data.aws_caller_identity.current.account_id
}

output "ec2_benchmark_instance_id" {
  description = "EC2 instance used for the three-way benchmark"
  value       = module.ec2_benchmark.instance_id
}


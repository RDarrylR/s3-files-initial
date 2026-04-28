output "vpc_id" {
  description = "ID of the VPC"
  value       = aws_vpc.main.id
}

output "private_subnet_ids" {
  description = "IDs of the private subnets"
  value       = aws_subnet.private[*].id
}

output "lambda_sg_id" {
  description = "Security group ID for the Lambda-after function"
  value       = aws_security_group.lambda_after.id
}

output "mount_target_sg_id" {
  description = "Security group ID for S3 Files mount targets"
  value       = aws_security_group.mount_target.id
}

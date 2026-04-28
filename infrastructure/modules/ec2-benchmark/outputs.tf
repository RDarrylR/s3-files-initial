output "instance_id" {
  description = "EC2 instance ID (use with aws ssm send-command)"
  value       = aws_instance.benchmark.id
}

output "instance_private_ip" {
  description = "Private IP of the benchmark instance"
  value       = aws_instance.benchmark.private_ip
}

output "security_group_id" {
  value = aws_security_group.ec2.id
}

output "execution_role_arn" {
  description = "ARN of the EC2 instance role"
  value       = aws_iam_role.ec2.arn
}

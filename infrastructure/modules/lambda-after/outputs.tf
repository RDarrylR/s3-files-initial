output "function_name" {
  description = "Name of the Lambda function"
  value       = aws_lambda_function.processor_after.function_name
}

output "function_arn" {
  description = "ARN of the Lambda function"
  value       = aws_lambda_function.processor_after.arn
}

output "execution_role_arn" {
  description = "ARN of the Lambda execution role"
  value       = aws_iam_role.execution.arn
}

output "function_name" {
  description = "Name of the Lambda function"
  value       = aws_lambda_function.processor_before.function_name
}

output "function_arn" {
  description = "ARN of the Lambda function"
  value       = aws_lambda_function.processor_before.arn
}

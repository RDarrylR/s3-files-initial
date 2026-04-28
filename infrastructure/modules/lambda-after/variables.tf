variable "project_name" {
  description = "Project name for resource naming"
  type        = string
}

variable "environment" {
  description = "Environment name"
  type        = string
}

variable "bucket_arn" {
  description = "ARN of the S3 bucket (for direct read policy)"
  type        = string
}

variable "access_point_arn" {
  description = "ARN of the S3 Files access point"
  type        = string
}

variable "private_subnet_ids" {
  description = "Subnet IDs for VPC-enabled Lambda"
  type        = list(string)
}

variable "lambda_sg_id" {
  description = "Security group ID for the Lambda function"
  type        = string
}

variable "lambda_memory" {
  description = "Lambda memory size in MB"
  type        = number
}

variable "lambda_timeout" {
  description = "Lambda timeout in seconds"
  type        = number
}

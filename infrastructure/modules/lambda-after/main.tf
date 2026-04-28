data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

# Latest AWS-published Powertools for Lambda (Python) layer for python3.14/arm64.
# Dynamically resolved from SSM so we always pick up the latest release.
data "aws_ssm_parameter" "powertools_layer_arn" {
  name = "/aws/service/powertools/python/arm64/python3.14/latest"
}

# --- Lambda package ---

data "archive_file" "function" {
  type        = "zip"
  source_dir  = "${path.module}/../../../src/processor_after"
  output_path = "${path.module}/.build/processor_after.zip"
}

# --- IAM Role ---

resource "aws_iam_role" "execution" {
  name = "${var.project_name}-${var.environment}-after-lambda"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        Service = "lambda.amazonaws.com"
      }
      Action = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "logging" {
  name = "cloudwatch-logs"
  role = aws_iam_role.execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "logs:CreateLogGroup",
        "logs:CreateLogStream",
        "logs:PutLogEvents"
      ]
      Resource = "${aws_cloudwatch_log_group.function.arn}:*"
    }]
  })
}

resource "aws_iam_role_policy" "vpc_networking" {
  name = "vpc-networking"
  role = aws_iam_role.execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "ec2:CreateNetworkInterface",
        "ec2:DescribeNetworkInterfaces",
        "ec2:DeleteNetworkInterface",
        "ec2:AssignPrivateIpAddresses",
        "ec2:UnassignPrivateIpAddresses"
      ]
      Resource = "*"
    }]
  })
}

# X-Ray tracing (Powertools Tracer writes segments via the X-Ray SDK)
resource "aws_iam_role_policy_attachment" "xray" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/AWSXRayDaemonWriteAccess"
}

resource "aws_iam_role_policy" "s3files_mount" {
  name = "s3files-mount"
  role = aws_iam_role.execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "s3files:ClientMount",
        "s3files:ClientWrite"
      ]
      Resource = var.access_point_arn
    }]
  })
}

# S3 direct read access (for >=512MB memory direct read optimization)
resource "aws_iam_role_policy" "s3_direct_read" {
  name = "s3-direct-read"
  role = aws_iam_role.execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "s3:GetObject",
        "s3:GetObjectVersion"
      ]
      Resource = "${var.bucket_arn}/*"
    }]
  })
}

# --- CloudWatch Log Group ---

resource "aws_cloudwatch_log_group" "function" {
  name              = "/aws/lambda/${var.project_name}-${var.environment}-after"
  retention_in_days = 14
}

# --- Lambda Function ---

resource "aws_lambda_function" "processor_after" {
  function_name    = "${var.project_name}-${var.environment}-after"
  filename         = data.archive_file.function.output_path
  source_code_hash = data.archive_file.function.output_base64sha256
  handler          = "handler.lambda_handler"
  runtime          = "python3.14"
  architectures    = ["arm64"]
  memory_size      = var.lambda_memory
  timeout          = var.lambda_timeout

  role = aws_iam_role.execution.arn

  layers = [data.aws_ssm_parameter.powertools_layer_arn.value]

  tracing_config {
    mode = "Active"
  }

  vpc_config {
    subnet_ids         = var.private_subnet_ids
    security_group_ids = [var.lambda_sg_id]
  }

  file_system_config {
    arn              = var.access_point_arn
    local_mount_path = "/mnt/docs"
  }

  environment {
    variables = {
      MOUNT_PATH       = "/mnt/docs"
      INBOX_PREFIX     = "inbox-after"
      PROCESSED_PREFIX = "processed-after"
      REPORTS_PREFIX   = "reports-after"
      APPROACH         = "after"
      # Powertools configuration
      POWERTOOLS_SERVICE_NAME      = "s3-files-demo"
      POWERTOOLS_METRICS_NAMESPACE = "S3FilesDemo"
      LOG_LEVEL                    = "INFO"
    }
  }

  depends_on = [aws_cloudwatch_log_group.function]
}

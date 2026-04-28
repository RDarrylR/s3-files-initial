data "aws_region" "current" {}

# --- VPC ---

resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = {
    Name = "${var.project_name}-${var.environment}-vpc"
  }
}

# --- Private Subnets (no public subnets, no NAT gateway) ---

resource "aws_subnet" "private" {
  count = length(var.availability_zones)

  vpc_id            = aws_vpc.main.id
  cidr_block        = var.private_subnet_cidrs[count.index]
  availability_zone = var.availability_zones[count.index]

  tags = {
    Name = "${var.project_name}-${var.environment}-private-${var.availability_zones[count.index]}"
  }
}

resource "aws_route_table" "private" {
  vpc_id = aws_vpc.main.id

  tags = {
    Name = "${var.project_name}-${var.environment}-private-rt"
  }
}

resource "aws_route_table_association" "private" {
  count = length(aws_subnet.private)

  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private.id
}

# --- VPC Endpoint: S3 Gateway (free, routes S3 API traffic within VPC) ---

resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.main.id
  service_name      = "com.amazonaws.${data.aws_region.current.region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_route_table.private.id]

  # Scoped endpoint policy (defense-in-depth alongside IAM):
  # 1. Full CRUD on our project bucket.
  # 2. Read-only on the specific AWS-owned buckets the private-subnet EC2 needs
  #    to bootstrap: AL2023 dnf repos, SSM agent updates, and the Mountpoint
  #    release bucket. Without this, user-data's package installs 403.
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "ProjectBucketFullAccess"
        Effect    = "Allow"
        Principal = "*"
        Action = [
          "s3:GetObject",
          "s3:GetObjectVersion",
          "s3:PutObject",
          "s3:DeleteObject",
          "s3:ListBucket",
          "s3:ListBucketVersions",
          "s3:AbortMultipartUpload",
          "s3:ListMultipartUploadParts",
        ]
        Resource = [var.bucket_arn, "${var.bucket_arn}/*"]
      },
      {
        Sid       = "BootstrapReadOnly"
        Effect    = "Allow"
        Principal = "*"
        Action    = ["s3:GetObject", "s3:ListBucket"]
        Resource = [
          # AL2023 dnf repos (per-region bucket)
          "arn:aws:s3:::al2023-repos-${data.aws_region.current.region}-*",
          "arn:aws:s3:::al2023-repos-${data.aws_region.current.region}-*/*",
          # SSM agent and patch manager updates
          "arn:aws:s3:::aws-ssm-${data.aws_region.current.region}",
          "arn:aws:s3:::aws-ssm-${data.aws_region.current.region}/*",
          "arn:aws:s3:::amazon-ssm-${data.aws_region.current.region}",
          "arn:aws:s3:::amazon-ssm-${data.aws_region.current.region}/*",
          "arn:aws:s3:::amazon-ssm-packages-${data.aws_region.current.region}",
          "arn:aws:s3:::amazon-ssm-packages-${data.aws_region.current.region}/*",
          # Mountpoint for S3 release bucket
          "arn:aws:s3:::mountpoint-s3-release",
          "arn:aws:s3:::mountpoint-s3-release/*",
        ]
      },
    ]
  })

  tags = {
    Name = "${var.project_name}-${var.environment}-s3-endpoint"
  }
}

# --- SSM Interface Endpoints (lets the private-subnet EC2 be managed by SSM) ---
#
# The EC2 benchmark host has no NAT Gateway and no public IP. For the SSM
# agent to register and for `aws ssm send-command` / `aws ssm start-session`
# to work, we need these three AWS-service interface endpoints inside the VPC.
# Gateway endpoints (like the S3 one above) don't work for SSM - it's all
# PrivateLink interface endpoints.

resource "aws_security_group" "vpc_endpoints" {
  name_prefix = "${var.project_name}-${var.environment}-vpce-"
  vpc_id      = aws_vpc.main.id
  description = "Allow HTTPS from inside the VPC to AWS service endpoints"

  ingress {
    description = "HTTPS from VPC"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = [aws_vpc.main.cidr_block]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "${var.project_name}-${var.environment}-vpce-sg"
  }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_vpc_endpoint" "ssm" {
  for_each = toset(["ssm", "ssmmessages", "ec2messages"])

  vpc_id              = aws_vpc.main.id
  service_name        = "com.amazonaws.${data.aws_region.current.region}.${each.key}"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = aws_subnet.private[*].id
  security_group_ids  = [aws_security_group.vpc_endpoints.id]
  private_dns_enabled = true

  tags = {
    Name = "${var.project_name}-${var.environment}-${each.key}-endpoint"
  }
}

# --- Security Groups ---

# Lambda-after security group: egress to NFS mount targets and HTTPS endpoints
resource "aws_security_group" "lambda_after" {
  name_prefix = "${var.project_name}-${var.environment}-lambda-after-"
  vpc_id      = aws_vpc.main.id
  description = "Security group for Lambda function with S3 Files mount"

  tags = {
    Name = "${var.project_name}-${var.environment}-lambda-after-sg"
  }

  lifecycle {
    create_before_destroy = true
  }
}

# S3 Files mount target security group: ingress from Lambda on NFS port
resource "aws_security_group" "mount_target" {
  name_prefix = "${var.project_name}-${var.environment}-mount-target-"
  vpc_id      = aws_vpc.main.id
  description = "Security group for S3 Files mount targets"

  tags = {
    Name = "${var.project_name}-${var.environment}-mount-target-sg"
  }

  lifecycle {
    create_before_destroy = true
  }
}

# Lambda -> mount target: NFS (TCP 2049)
resource "aws_vpc_security_group_egress_rule" "lambda_to_nfs" {
  security_group_id            = aws_security_group.lambda_after.id
  referenced_security_group_id = aws_security_group.mount_target.id
  from_port                    = 2049
  to_port                      = 2049
  ip_protocol                  = "tcp"
  description                  = "NFS to S3 Files mount targets"
}

# Lambda -> HTTPS (for VPC endpoints and AWS APIs)
resource "aws_vpc_security_group_egress_rule" "lambda_to_https" {
  security_group_id = aws_security_group.lambda_after.id
  cidr_ipv4         = "0.0.0.0/0"
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"
  description       = "HTTPS to AWS service endpoints"
}

# Mount target ingress: NFS from Lambda
resource "aws_vpc_security_group_ingress_rule" "nfs_from_lambda" {
  security_group_id            = aws_security_group.mount_target.id
  referenced_security_group_id = aws_security_group.lambda_after.id
  from_port                    = 2049
  to_port                      = 2049
  ip_protocol                  = "tcp"
  description                  = "NFS from Lambda"
}

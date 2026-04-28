data "aws_region" "current" {}

# Latest Amazon Linux 2023 arm64 AMI (matches the Lambda arm64 architecture)
data "aws_ami" "al2023_arm" {
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["al2023-ami-2023.*-arm64"]
  }

  filter {
    name   = "architecture"
    values = ["arm64"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

# --- IAM: instance profile with SSM, S3, and S3 Files mount access ---

resource "aws_iam_role" "ec2" {
  name = "${var.project_name}-${var.environment}-ec2-benchmark"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

# SSM Session Manager + run-command (no SSH key needed)
resource "aws_iam_role_policy_attachment" "ssm_core" {
  role       = aws_iam_role.ec2.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

# S3 bucket access (read/write/list/multipart) - used by the S3 API approach
# and by Mountpoint (which calls S3 under the hood).
resource "aws_iam_role_policy" "s3_access" {
  name = "s3-access"
  role = aws_iam_role.ec2.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "s3:ListBucket",
        "s3:ListBucketVersions",
        "s3:GetBucketLocation",
        "s3:AbortMultipartUpload",
        "s3:ListMultipartUploadParts",
        "s3:GetObject",
        "s3:GetObjectVersion",
        "s3:PutObject",
        "s3:DeleteObject",
      ]
      Resource = [var.bucket_arn, "${var.bucket_arn}/*"]
    }]
  })
}

# S3 Files mount permissions (EC2 uses the access point via amazon-efs-utils)
resource "aws_iam_role_policy" "s3files_mount" {
  name = "s3files-mount"
  role = aws_iam_role.ec2.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["s3files:ClientMount", "s3files:ClientWrite"]
      Resource = var.access_point_arn
    }]
  })
}

resource "aws_iam_instance_profile" "ec2" {
  name = "${var.project_name}-${var.environment}-ec2-benchmark"
  role = aws_iam_role.ec2.name
}

# --- Security group ---

resource "aws_security_group" "ec2" {
  name        = "${var.project_name}-${var.environment}-ec2-benchmark"
  description = "Benchmark EC2 instance: outbound to S3, NFS, HTTPS (SSM)"
  vpc_id      = var.vpc_id

  egress {
    description = "All outbound (SSM over 443, S3 Gateway endpoint, NFS 2049)"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "${var.project_name}-${var.environment}-ec2-benchmark"
  }
}

# Let the EC2 instance reach S3 Files mount targets on NFS port
resource "aws_vpc_security_group_ingress_rule" "nfs_from_ec2" {
  security_group_id            = var.mount_target_sg_id
  referenced_security_group_id = aws_security_group.ec2.id
  from_port                    = 2049
  to_port                      = 2049
  ip_protocol                  = "tcp"
  description                  = "NFS from benchmark EC2 instance"
}

# --- Instance ---

resource "aws_instance" "benchmark" {
  ami                    = data.aws_ami.al2023_arm.id
  instance_type          = var.instance_type
  subnet_id              = var.subnet_id
  vpc_security_group_ids = [aws_security_group.ec2.id]
  iam_instance_profile   = aws_iam_instance_profile.ec2.name

  root_block_device {
    volume_size = var.root_volume_size_gb
    volume_type = "gp3"
    encrypted   = true
  }

  metadata_options {
    http_tokens   = "required"
    http_endpoint = "enabled"
  }

  user_data_replace_on_change = true

  user_data = templatefile("${path.module}/user_data.sh", {
    file_system_id = var.file_system_id
    bucket_name    = var.bucket_name
    region         = data.aws_region.current.region
  })

  tags = {
    Name = "${var.project_name}-${var.environment}-ec2-benchmark"
  }

  # Mount targets must be in the "available" state before we try to NFS-mount
  # them in user-data. The ingress rule authorizing NFS from this SG is
  # an implicit dependency; making it explicit so Terraform waits.
  depends_on = [aws_vpc_security_group_ingress_rule.nfs_from_ec2]
}

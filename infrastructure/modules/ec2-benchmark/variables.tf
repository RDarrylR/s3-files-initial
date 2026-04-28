variable "project_name" {
  type = string
}

variable "environment" {
  type = string
}

variable "vpc_id" {
  type = string
}

variable "subnet_id" {
  description = "Private subnet in the same AZ as an S3 Files mount target"
  type        = string
}

variable "mount_target_sg_id" {
  description = "Security group attached to S3 Files mount targets (this module adds an ingress rule to it)"
  type        = string
}

variable "bucket_name" {
  type = string
}

variable "bucket_arn" {
  type = string
}

variable "file_system_id" {
  type = string
}

variable "access_point_arn" {
  type = string
}

variable "instance_type" {
  type    = string
  default = "c7g.large"
}

variable "root_volume_size_gb" {
  description = "Root volume size - 1GB x N files plus OS overhead"
  type        = number
  default     = 30
}

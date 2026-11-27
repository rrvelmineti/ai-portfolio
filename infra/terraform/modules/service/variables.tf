variable "name" {
  description = "Name prefix."
  type        = string
}

variable "vpc_id" {
  description = "VPC ID."
  type        = string
}

variable "vpc_cidr_block" {
  description = "VPC range: the database lives here and the ALB forwards from here."
  type        = string
}

variable "public_subnet_ids" {
  description = "Subnets for the load balancer."
  type        = list(string)
}

variable "private_subnet_ids" {
  description = "Subnets for the tasks."
  type        = list(string)
}

variable "image" {
  description = "Image URI, pinned by digest (repo@sha256:...)."
  type        = string
}

variable "certificate_arn" {
  description = "ACM certificate for the HTTPS listener."
  type        = string
}

variable "access_logs_bucket" {
  description = "S3 bucket for load balancer access logs."
  type        = string
}

variable "kms_key_arn" {
  description = "KMS key for log groups and secrets."
  type        = string
}

variable "secret_arns" {
  description = "Environment variable name => Secrets Manager ARN."
  type        = map(string)
}

variable "environment" {
  description = "Plain (non-secret) environment variables."
  type        = map(string)
  default     = {}
}

variable "cpu" {
  description = "Fargate CPU units."
  type        = number
  default     = 1024
}

variable "memory_mb" {
  description = "Fargate memory; the app peaks at about 1.5 GB with both guard models loaded."
  type        = number
  default     = 3072
}

variable "desired_count" {
  description = "Number of tasks."
  type        = number
  default     = 2
}

variable "requests_per_5_minutes_per_ip" {
  description = "WAF rate limit per client IP."
  type        = number
  default     = 1000
}

variable "log_retention_days" {
  description = "Log retention."
  type        = number
  default     = 365
}

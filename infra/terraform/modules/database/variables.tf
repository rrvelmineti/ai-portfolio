variable "name" {
  description = "Name prefix."
  type        = string
}

variable "vpc_id" {
  description = "VPC for the database security group."
  type        = string
}

variable "subnet_ids" {
  description = "Private subnets for the database."
  type        = list(string)
}

variable "app_security_group_id" {
  description = "Security group of the app tasks (the only allowed client)."
  type        = string
}

variable "kms_key_arn" {
  description = "KMS key for storage, the master secret and Performance Insights."
  type        = string
}

variable "engine_version" {
  description = "Postgres major.minor version."
  type        = string
  default     = "16.9"
}

variable "instance_class" {
  description = "RDS instance size."
  type        = string
  default     = "db.t4g.medium"
}

variable "database_name" {
  description = "Initial database name."
  type        = string
  default     = "portfolio"
}

variable "allocated_storage_gb" {
  description = "Initial storage; autoscaling allows up to 4x."
  type        = number
  default     = 20
}

variable "multi_az" {
  description = "Standby in a second AZ."
  type        = bool
  default     = true
}

variable "backup_retention_days" {
  description = "Automated backup retention."
  type        = number
  default     = 14
}

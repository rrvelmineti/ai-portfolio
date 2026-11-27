variable "name" {
  description = "Name prefix for every resource."
  type        = string
}

variable "cidr_block" {
  description = "VPC address range."
  type        = string
  default     = "10.40.0.0/16"
}

variable "availability_zones" {
  description = "Two availability zones in the region, by name."
  type        = list(string)

  validation {
    condition     = length(var.availability_zones) == 2
    error_message = "Give exactly two availability zones."
  }
}

variable "kms_key_arn" {
  description = "KMS key that encrypts the flow-log group."
  type        = string
}

variable "log_retention_days" {
  description = "How long to keep VPC flow logs."
  type        = number
  default     = 365
}

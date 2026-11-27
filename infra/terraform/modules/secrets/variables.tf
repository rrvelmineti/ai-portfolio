variable "name" {
  description = "Path prefix for the secrets."
  type        = string
}

variable "kms_key_arn" {
  description = "KMS key that encrypts the secrets."
  type        = string
}

variable "secret_names" {
  description = "Environment variable names stored as secrets (one secret each)."
  type        = list(string)
}

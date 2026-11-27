variable "region" {
  description = "AWS region."
  type        = string
  default     = "eu-west-2"
}

variable "name" {
  description = "Name prefix for every resource."
  type        = string
  default     = "ai-portfolio"
}

variable "image" {
  description = "App image pinned by digest, e.g. <account>.dkr.ecr.<region>.amazonaws.com/ai-portfolio@sha256:..."
  type        = string
}

variable "certificate_arn" {
  description = "ACM certificate for the public domain."
  type        = string
}

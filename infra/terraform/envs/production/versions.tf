terraform {
  required_version = ">= 1.13.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.73"
    }
  }

  # Partial configuration: bucket, key and region come from `terraform init
  # -backend-config=...` in the (future) apply pipeline. CI only validates, with
  # `init -backend=false`, and never applies.
  backend "s3" {
    use_lockfile = true
    encrypt      = true
  }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project     = "ai-portfolio"
      Environment = "production"
      ManagedBy   = "terraform"
    }
  }
}

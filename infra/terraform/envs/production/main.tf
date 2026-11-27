# AWS production path (ADR 0008). Validated in CI (fmt, validate, tflint, checkov) and
# never applied from this repository yet. The free demo runs elsewhere; this is where
# the same image goes when the platform needs production guarantees.

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  # Secret env vars the app reads; values are set in Secrets Manager outside Terraform.
  app_secrets = [
    "DATABASE_URL", # the restricted app role, ai_portfolio_app
    "JWT_SECRET",
    "ANTHROPIC_API_KEY",
    "GEMINI_API_KEY",
    "GROQ_API_KEY",
    "LANGSMITH_API_KEY",
  ]
}

# ---------------------------------------------------------------- encryption key

data "aws_iam_policy_document" "kms" {
  # The account-root statement is AWS's default key policy: it delegates access to IAM
  # policies in this account (a key policy's resource is always the key itself).
  #checkov:skip=CKV_AWS_111:Default KMS key policy; "*" means this key only
  #checkov:skip=CKV_AWS_356:Default KMS key policy; "*" means this key only
  #checkov:skip=CKV_AWS_109:Default KMS key policy; "*" means this key only
  statement {
    sid       = "AccountAdministration"
    actions   = ["kms:*"]
    resources = ["*"]
    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::${local.account_id}:root"]
    }
  }

  statement {
    sid = "CloudWatchLogs"
    actions = [
      "kms:Encrypt*",
      "kms:Decrypt*",
      "kms:ReEncrypt*",
      "kms:GenerateDataKey*",
      "kms:Describe*",
    ]
    resources = ["*"]
    principals {
      type        = "Service"
      identifiers = ["logs.${data.aws_region.current.region}.amazonaws.com"]
    }
    condition {
      test     = "ArnLike"
      variable = "kms:EncryptionContext:aws:logs:arn"
      values   = ["arn:aws:logs:${data.aws_region.current.region}:${local.account_id}:*"]
    }
  }
}

resource "aws_kms_key" "this" {
  description             = "${var.name}: logs, secrets, database, images"
  enable_key_rotation     = true
  deletion_window_in_days = 30
  policy                  = data.aws_iam_policy_document.kms.json
}

resource "aws_kms_alias" "this" {
  name          = "alias/${var.name}"
  target_key_id = aws_kms_key.this.key_id
}

# ---------------------------------------------------------------- modules

module "network" {
  source             = "../../modules/network"
  name               = var.name
  availability_zones = ["${var.region}a", "${var.region}b"]
  kms_key_arn        = aws_kms_key.this.arn
}

module "ecr" {
  source      = "../../modules/ecr"
  name        = var.name
  kms_key_arn = aws_kms_key.this.arn
}

module "secrets" {
  source       = "../../modules/secrets"
  name         = var.name
  kms_key_arn  = aws_kms_key.this.arn
  secret_names = local.app_secrets
}

module "service" {
  source             = "../../modules/service"
  name               = var.name
  vpc_id             = module.network.vpc_id
  vpc_cidr_block     = module.network.vpc_cidr_block
  public_subnet_ids  = module.network.public_subnet_ids
  private_subnet_ids = module.network.private_subnet_ids
  image              = var.image
  certificate_arn    = var.certificate_arn
  access_logs_bucket = aws_s3_bucket.access_logs.id
  kms_key_arn        = aws_kms_key.this.arn
  secret_arns        = module.secrets.arns
  environment = {
    APP_ENV   = "production"
    LOG_LEVEL = "INFO"
  }
}

module "database" {
  source                = "../../modules/database"
  name                  = var.name
  vpc_id                = module.network.vpc_id
  subnet_ids            = module.network.private_subnet_ids
  app_security_group_id = module.service.app_security_group_id
  kms_key_arn           = aws_kms_key.this.arn
}

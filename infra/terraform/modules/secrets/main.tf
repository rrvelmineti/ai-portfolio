# Secrets Manager entries for the app. Terraform creates the containers only: values are
# set out of band (console, CLI or a CI job), so no secret ever enters Terraform state or
# this repository.

resource "aws_secretsmanager_secret" "this" {
  # Provider API keys and the JWT secret are rotated by hand (runbook in ADR 0008); the
  # database owner secret is rotated by RDS itself (manage_master_user_password).
  #checkov:skip=CKV2_AWS_57:Third-party API keys can't be rotated by a Lambda
  for_each                = toset(var.secret_names)
  name                    = "${var.name}/${each.key}"
  description             = "ai-portfolio ${each.key} (value set outside Terraform)"
  kms_key_id              = var.kms_key_arn
  recovery_window_in_days = 30
}

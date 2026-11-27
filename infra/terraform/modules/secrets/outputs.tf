output "arns" {
  description = "Secret ARN per environment variable name."
  value       = { for k, s in aws_secretsmanager_secret.this : k => s.arn }
}

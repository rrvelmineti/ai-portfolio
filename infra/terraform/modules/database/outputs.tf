output "address" {
  description = "Database host name."
  value       = aws_db_instance.this.address
}

output "port" {
  description = "Database port."
  value       = aws_db_instance.this.port
}

output "database_name" {
  description = "Database name."
  value       = aws_db_instance.this.db_name
}

output "master_secret_arn" {
  description = "Secrets Manager secret holding the owner credentials (CI only)."
  value       = aws_db_instance.this.master_user_secret[0].secret_arn
}

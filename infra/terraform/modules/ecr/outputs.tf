output "repository_url" {
  description = "Push and pull URL of the repository."
  value       = aws_ecr_repository.this.repository_url
}

output "repository_arn" {
  description = "Repository ARN (for pull permissions)."
  value       = aws_ecr_repository.this.arn
}

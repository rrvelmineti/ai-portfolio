output "load_balancer_dns_name" {
  description = "Point the domain's DNS at this name."
  value       = module.service.load_balancer_dns_name
}

output "ecr_repository_url" {
  description = "Push the app image here."
  value       = module.ecr.repository_url
}

output "database_address" {
  description = "Postgres host for DATABASE_URL."
  value       = module.database.address
}

output "database_owner_secret_arn" {
  description = "Owner credentials for CI migrations (never given to the app)."
  value       = module.database.master_secret_arn
}

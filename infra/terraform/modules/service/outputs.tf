output "load_balancer_dns_name" {
  description = "Point the domain's DNS at this name."
  value       = aws_lb.this.dns_name
}

output "app_security_group_id" {
  description = "Security group of the tasks (the database allows only this one)."
  value       = aws_security_group.app.id
}

output "cluster_name" {
  description = "ECS cluster."
  value       = aws_ecs_cluster.this.name
}

output "service_name" {
  description = "ECS service."
  value       = aws_ecs_service.this.name
}

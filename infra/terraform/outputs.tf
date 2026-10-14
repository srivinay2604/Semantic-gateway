output "alb_dns_name" {
  description = "Public DNS name of the Application Load Balancer"
  value       = aws_lb.main.dns_name
}

output "alb_http_url" {
  description = "HTTP endpoint for the CacheGate gateway"
  value       = "http://${aws_lb.main.dns_name}"
}

output "ecr_repository_url" {
  description = "URL of the ECR repository"
  value       = aws_ecr_repository.gateway.repository_url
}

output "ecs_cluster_name" {
  description = "Name of the ECS Cluster"
  value       = aws_ecs_cluster.main.name
}

output "ecs_service_name" {
  description = "Name of the ECS Service"
  value       = aws_ecs_service.gateway.name
}

output "cloudwatch_log_group" {
  description = "CloudWatch Log Group name for gateway logs"
  value       = aws_cloudwatch_log_group.gateway.name
}

output "secrets_manager_arn" {
  description = "ARN of the Secrets Manager secret"
  value       = aws_secretsmanager_secret.app_secrets.arn
}

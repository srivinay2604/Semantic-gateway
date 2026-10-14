resource "aws_ecs_cluster" "main" {
  name = "${var.app_name}-cluster-${var.environment}"

  setting {
    name  = "containerInsights"
    value = "enabled"
  }

  tags = {
    Name = "${var.app_name}-cluster"
  }
}

resource "aws_ecs_task_definition" "gateway" {
  family                   = "${var.app_name}-task-${var.environment}"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.task_cpu
  memory                   = var.task_memory
  execution_role_arn       = aws_iam_role.ecs_execution_role.arn
  task_role_arn            = aws_iam_role.ecs_task_role.arn

  container_definitions = jsonencode([
    {
      name      = "gateway"
      image     = "${aws_ecr_repository.gateway.repository_url}:${var.image_tag}"
      essential = true

      portMappings = [
        {
          containerPort = var.container_port
          hostPort      = var.container_port
          protocol      = "tcp"
        }
      ]

      environment = [
        { name = "APP_ENV", value = var.environment },
        { name = "LOG_LEVEL", value = "INFO" },
        { name = "REDIS_URL", value = "redis://redis.cachegate.local:6379/0" },
        { name = "CACHE_ENABLED", value = "true" },
        { name = "CACHE_SCHEMA_VERSION", value = "v1" },
        { name = "CACHE_TTL_SECONDS", value = "86400" },
        { name = "SEMANTIC_THRESHOLD", value = "0.92" },
        { name = "SEMANTIC_TOP_K", value = "3" },
        { name = "RATE_LIMIT_DEFAULT_RPS", value = "10.0" },
        { name = "RATE_LIMIT_DEFAULT_BURST", value = "30" },
        { name = "SINGLEFLIGHT_ENABLED", value = "true" }
      ]

      secrets = [
        {
          name      = "ADMIN_API_KEY"
          valueFrom = "${aws_secretsmanager_secret.app_secrets.arn}:ADMIN_API_KEY::"
        },
        {
          name      = "OPENAI_API_KEY"
          valueFrom = "${aws_secretsmanager_secret.app_secrets.arn}:OPENAI_API_KEY::"
        },
        {
          name      = "ANTHROPIC_API_KEY"
          valueFrom = "${aws_secretsmanager_secret.app_secrets.arn}:ANTHROPIC_API_KEY::"
        }
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.gateway.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "gateway"
        }
      }

      healthCheck = {
        command     = ["CMD-SHELL", "curl -f http://localhost:8000/healthz || exit 1"]
        interval    = 15
        timeout     = 5
        retries     = 3
        startPeriod = 30
      }
    }
  ])
}

resource "aws_ecs_service" "gateway" {
  name            = "${var.app_name}-service"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.gateway.arn
  desired_count   = var.min_capacity
  launch_type     = "FARGATE"

  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200

  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.ecs.id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.gateway.arn
    container_name   = "gateway"
    container_port   = var.container_port
  }

  depends_on = [aws_lb_listener.http, aws_ecs_service.redis]
}

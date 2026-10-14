resource "aws_ecr_repository" "gateway" {
  name                 = "${var.app_name}-gateway"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = {
    Name = "${var.app_name}-ecr-repository"
  }
}

resource "aws_ecr_lifecycle_policy" "gateway_policy" {
  repository = aws_ecr_repository.gateway.name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Keep last 10 container images to control storage costs"
        selection = {
          tagStatus   = "any"
          countType   = "imageCountMoreThan"
          countNumber = 10
        }
        action = {
          type = "expire"
        }
      }
    ]
  })
}

resource "random_password" "admin_api_key" {
  length  = 32
  special = false
}

resource "aws_secretsmanager_secret" "app_secrets" {
  name        = "${var.app_name}-secrets-${var.environment}"
  description = "API keys and sensitive credentials for CacheGate"

  recovery_window_in_days = 0

  tags = {
    Name = "${var.app_name}-secrets"
  }
}

resource "aws_secretsmanager_secret_version" "app_secrets_val" {
  secret_id = aws_secretsmanager_secret.app_secrets.id

  secret_string = jsonencode({
    ADMIN_API_KEY     = random_password.admin_api_key.result
    OPENAI_API_KEY    = ""
    ANTHROPIC_API_KEY = ""
  })
}

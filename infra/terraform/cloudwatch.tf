resource "aws_cloudwatch_log_group" "gateway" {
  name              = "/ecs/${var.app_name}-${var.environment}"
  retention_in_days = 14

  tags = {
    Name = "${var.app_name}-logs"
  }
}

resource "aws_cloudwatch_log_group" "redis" {
  name              = "/ecs/${var.app_name}-redis-${var.environment}"
  retention_in_days = 7

  tags = {
    Name = "${var.app_name}-redis-logs"
  }
}

# -----------------------------------------------------------------------------
# CloudWatch Metric Alarms (Spec §18.2)
# -----------------------------------------------------------------------------

# Alarm: High 5xx Error Rate
resource "aws_cloudwatch_metric_alarm" "high_5xx_errors" {
  alarm_name          = "${var.app_name}-high-5xx-rate-${var.environment}"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "HTTPCode_Target_5XX_Count"
  namespace           = "AWS/ApplicationELB"
  period              = 60
  statistic           = "Sum"
  threshold           = 10
  alarm_description   = "Alarm when gateway returns more than 10 5XX responses in 2 consecutive minutes"
  treat_missing_data  = "notBreaching"

  dimensions = {
    LoadBalancer = aws_lb.main.arn_suffix
  }
}

# Alarm: High Latency (p95 > 2.0s)
resource "aws_cloudwatch_metric_alarm" "high_latency" {
  alarm_name          = "${var.app_name}-high-p95-latency-${var.environment}"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  metric_name         = "TargetResponseTime"
  namespace           = "AWS/ApplicationELB"
  period              = 60
  extended_statistic  = "p95"
  threshold           = 2.0
  alarm_description   = "Alarm when target p95 latency exceeds 2.0 seconds"
  treat_missing_data  = "notBreaching"

  dimensions = {
    LoadBalancer = aws_lb.main.arn_suffix
  }
}

# Alarm: Unhealthy Hosts in Target Group
resource "aws_cloudwatch_metric_alarm" "unhealthy_hosts" {
  alarm_name          = "${var.app_name}-unhealthy-hosts-${var.environment}"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "UnHealthyHostCount"
  namespace           = "AWS/ApplicationELB"
  period              = 60
  statistic           = "Maximum"
  threshold           = 0
  alarm_description   = "Alarm when any target host fails health check"
  treat_missing_data  = "breaching"

  dimensions = {
    TargetGroup  = aws_lb_target_group.gateway.arn_suffix
    LoadBalancer = aws_lb.main.arn_suffix
  }
}

# Alarm: High ECS Task CPU Utilization
resource "aws_cloudwatch_metric_alarm" "high_cpu" {
  alarm_name          = "${var.app_name}-high-cpu-${var.environment}"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "CPUUtilization"
  namespace           = "AWS/ECS"
  period              = 60
  statistic           = "Average"
  threshold           = 80
  alarm_description   = "Alarm when ECS task CPU exceeds 80%"
  treat_missing_data  = "notBreaching"

  dimensions = {
    ClusterName = aws_ecs_cluster.main.name
    ServiceName = aws_ecs_service.gateway.name
  }
}

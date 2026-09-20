# -----------------------------------------------------------------------------
# CloudWatch : surveillance de l'infrastructure AWS (le cluster, les nodes, etc.)
# en complément de Prometheus/Grafana qui surveille l'applicatif.
# -----------------------------------------------------------------------------

# Groupe de logs dédié à l'agent DevOps lui-même
resource "aws_cloudwatch_log_group" "agent_logs" {
  name              = "/${var.project_name}/agent-logs"
  retention_in_days = 30

  tags = {
    Project     = var.project_name
    Environment = var.environment
  }
}


# Alarme simple : alerte si la CPU moyenne du cluster dépasse 90% (cf. US 1.2)
resource "aws_cloudwatch_metric_alarm" "high_cpu" {
  alarm_name          = "${var.project_name}-high-cpu"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "CPUUtilization"
  namespace           = "AWS/EC2"
  period              = 300
  statistic           = "Average"
  threshold           = 90
  alarm_description   = "Déclenché quand la CPU moyenne dépasse 90% pendant 5 minutes"
  treat_missing_data  = "notBreaching"

  tags = {
    Project     = var.project_name
    Environment = var.environment
  }
}

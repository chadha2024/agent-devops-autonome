# -----------------------------------------------------------------------------
# SQS : file d'attente utilisée pour découpler les modules de l'agent
# (ex: détection -> diagnostic -> remédiation) via des événements JSON.
# -----------------------------------------------------------------------------

# Dead Letter Queue : récupère les messages qui échouent plusieurs fois
resource "aws_sqs_queue" "agent_events_dlq" {
  name                      = "${var.project_name}-events-dlq"
  message_retention_seconds = 1209600 # 14 jours

  tags = {
    Project     = var.project_name
    Environment = var.environment
  }
}

# Queue principale : reçoit les événements "anomalie détectée", "diagnostic prêt", etc.
resource "aws_sqs_queue" "agent_events" {
  name                       = "${var.project_name}-events"
  visibility_timeout_seconds = 120 # doit couvrir le temps de traitement d'un message
  message_retention_seconds  = 345600 # 4 jours

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.agent_events_dlq.arn
    maxReceiveCount      = 5
  })

  tags = {
    Project     = var.project_name
    Environment = var.environment
  }
}

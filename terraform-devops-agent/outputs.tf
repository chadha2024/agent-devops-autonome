output "eks_cluster_name" {
  description = "Nom du cluster EKS"
  value       = module.eks.cluster_name
}

output "eks_cluster_endpoint" {
  description = "Endpoint de l'API Kubernetes"
  value       = module.eks.cluster_endpoint
}

output "configure_kubectl" {
  description = "Commande pour connecter kubectl au cluster"
  value       = "aws eks update-kubeconfig --region ${var.aws_region} --name ${module.eks.cluster_name}"
}

output "sqs_queue_url" {
  description = "URL de la queue SQS principale"
  value       = aws_sqs_queue.agent_events.id
}

output "sqs_queue_arn" {
  description = "ARN de la queue SQS principale"
  value       = aws_sqs_queue.agent_events.arn
}

output "rds_endpoint" {
  description = "Endpoint de connexion à la base PostgreSQL"
  value       = aws_db_instance.agent_state_db.endpoint
  sensitive   = true
}

output "cloudwatch_agent_log_group" {
  description = "Nom du groupe de logs CloudWatch pour l'agent"
  value       = aws_cloudwatch_log_group.agent_logs.name
}

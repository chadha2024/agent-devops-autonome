variable "aws_region" {
  description = "Région AWS où déployer l'infrastructure"
  type        = string
  default     = "eu-west-3" # Paris
}

variable "project_name" {
  description = "Nom du projet, utilisé comme préfixe pour les ressources"
  type        = string
  default     = "devops-agent"
}

variable "environment" {
  description = "Environnement de déploiement (stage/test uniquement, pas de prod client)"
  type        = string
  default     = "staging"
}

variable "cluster_version" {
  description = "Version de Kubernetes pour le cluster EKS"
  type        = string
  default     = "1.33"
}

variable "node_instance_type" {
  description = "Type d'instance EC2 pour les nodes du cluster EKS"
  type        = string
  default     = "t3.medium"
}

variable "node_desired_size" {
  description = "Nombre de nodes souhaité"
  type        = number
  default     = 2
}

variable "db_username" {
  description = "Nom d'utilisateur pour la base PostgreSQL"
  type        = string
  default     = "agentadmin"
}

variable "db_password" {
  description = "Mot de passe pour la base PostgreSQL (à surcharger via terraform.tfvars, jamais commité)"
  type        = string
  sensitive   = true
}

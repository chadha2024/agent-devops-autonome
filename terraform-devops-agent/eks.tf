# -----------------------------------------------------------------------------
# EKS : le cluster Kubernetes managé où tourneront les pods surveillés par
# l'agent, et sur lesquels il exécutera ses actions de remédiation.
# -----------------------------------------------------------------------------
module "eks" {
  source  = "terraform-aws-modules/eks/aws"
  version = "~> 20.0"

  cluster_name    = "${var.project_name}-cluster"
  cluster_version = var.cluster_version

  vpc_id     = module.vpc.vpc_id
  subnet_ids = module.vpc.private_subnets

  # Permet à l'agent (ou à toi en local) d'atteindre l'API Kubernetes
  cluster_endpoint_public_access = true
enable_cluster_creator_admin_permissions = true

  eks_managed_node_groups = {
    default = {
      instance_types = [var.node_instance_type]
      min_size       = 1
      max_size       = 4
      desired_size   = var.node_desired_size
    }
  }

  # Active les logs de contrôle (utile pour le debug et l'observabilité)
  cluster_enabled_log_types = ["api", "audit", "authenticator", "controllerManager", "scheduler"]

  tags = {
    Project     = var.project_name
    Environment = var.environment
  }
}

# -----------------------------------------------------------------------------
# RDS PostgreSQL : mémoire persistante de l'agent (historique des incidents,
# actions prises, compteurs de redémarrage par pod pour le rate limiting).
# -----------------------------------------------------------------------------

resource "aws_db_subnet_group" "agent_db_subnet_group" {
  name       = "${var.project_name}-db-subnet-group"
  subnet_ids = module.vpc.private_subnets

  tags = {
    Project     = var.project_name
    Environment = var.environment
  }
}

resource "aws_security_group" "agent_db_sg" {
  name        = "${var.project_name}-db-sg"
  description = "Autorise le trafic PostgreSQL depuis le cluster EKS uniquement"
  vpc_id      = module.vpc.vpc_id

  ingress {
    description     = "PostgreSQL depuis les nodes EKS"
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [module.eks.node_security_group_id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Project     = var.project_name
    Environment = var.environment
  }
}

resource "aws_db_instance" "agent_state_db" {
  identifier     = "${var.project_name}-state-db"
  engine         = "postgres"
    instance_class = "db.t3.micro" # éligible free tier, suffisant pour staging

  allocated_storage = 20
  storage_type      = "gp3"

  db_name  = "agent_state"
  username = var.db_username
  password = var.db_password

  db_subnet_group_name   = aws_db_subnet_group.agent_db_subnet_group.name
  vpc_security_group_ids = [aws_security_group.agent_db_sg.id]

  publicly_accessible = false
  multi_az            = false # pas nécessaire en staging, à activer en prod
  skip_final_snapshot = true  # OK pour du staging/test, à retirer en prod

  backup_retention_period = 7

  tags = {
    Project     = var.project_name
    Environment = var.environment
  }
}

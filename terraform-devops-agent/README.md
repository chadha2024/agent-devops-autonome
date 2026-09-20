# Infrastructure Terraform — Agent DevOps Autonome

Ce projet provisionne les 4 briques du ticket "Configurer l'environnement cloud
et les services managés" :

1. **Cluster EKS** (`eks.tf`, `vpc.tf`) — Kubernetes managé
2. **CloudWatch** (`cloudwatch.tf`) — monitoring et logs
3. **SQS** (`sqs.tf`) — message queue entre les modules de l'agent
4. **RDS PostgreSQL** (`rds.tf`) — état persistant de l'agent

## Prérequis

- Terraform >= 1.5.0 installé (https://developer.hashicorp.com/terraform/install)
- AWS CLI configuré avec tes accès (`aws configure`)
- kubectl installé (pour te connecter au cluster après déploiement)

## Étapes

1. **Configure tes variables sensibles**
   ```bash
   cp terraform.tfvars.example terraform.tfvars
   # édite terraform.tfvars et mets un vrai mot de passe DB
   ```

2. **Initialise Terraform** (télécharge les providers et modules)
   ```bash
   terraform init
   ```

3. **Vérifie ce qui va être créé** (très important avant d'appliquer)
   ```bash
   terraform plan
   ```

4. **Applique** (crée réellement les ressources sur AWS — prend ~15-20 min,
   principalement à cause du cluster EKS)
   ```bash
   terraform apply
   ```

5. **Connecte kubectl à ton nouveau cluster**
   ```bash
   aws eks update-kubeconfig --region eu-west-3 --name devops-agent-cluster
   kubectl get nodes
   ```

## Détruire l'infrastructure

⚠️ Comme c'est un environnement de staging/test, pense à détruire les
ressources quand tu ne t'en sers pas, pour ne pas payer inutilement
(EKS + RDS + NAT Gateway coûtent même sans trafic) :

```bash
terraform destroy
```

## Ordre de création (géré automatiquement par Terraform)

```
VPC → Subnets → EKS Cluster → Node Group
                             → Security Group RDS → RDS PostgreSQL
    → SQS Queues (indépendant)
    → CloudWatch Log Groups + Alarme (indépendant)
```

## Coûts estimés (ordre de grandeur, eu-west-3)

- EKS control plane : ~0,10 $/h (~73 $/mois)
- 2x EC2 t3.medium (nodes) : ~0,08 $/h chacune
- NAT Gateway : ~0,05 $/h + trafic
- RDS db.t3.micro : éligible free tier la 1ère année
- SQS + CloudWatch : quasi gratuit à faible volume

👉 **Pense à faire `terraform destroy` en fin de journée si tu es en phase
de test**, pour éviter une facture surprise.

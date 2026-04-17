terraform {
  required_version = ">= 1.5"
  required_providers {
    aws        = { source = "hashicorp/aws", version = "~> 5.60" }
    kubernetes = { source = "hashicorp/kubernetes", version = "~> 2.30" }
    helm       = { source = "hashicorp/helm", version = "~> 2.14" }
  }
}

variable "aws_region" { default = "ap-northeast-2" }
variable "cluster_name" { default = "vllm-orchestrator" }
variable "vllm_endpoint" { type = string }
variable "api_keys" {
  type      = string
  sensitive = true
}
variable "domain_name" { type = string }

provider "aws" {
  region = var.aws_region
}

# VPC
module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "~> 5.8"

  name = "${var.cluster_name}-vpc"
  cidr = "10.0.0.0/16"
  azs  = ["${var.aws_region}a", "${var.aws_region}b", "${var.aws_region}c"]

  private_subnets = ["10.0.1.0/24", "10.0.2.0/24", "10.0.3.0/24"]
  public_subnets  = ["10.0.101.0/24", "10.0.102.0/24", "10.0.103.0/24"]

  enable_nat_gateway   = true
  single_nat_gateway   = true
  enable_dns_hostnames = true
  enable_dns_support   = true
}

# EKS cluster
module "eks" {
  source  = "terraform-aws-modules/eks/aws"
  version = "~> 20.17"

  cluster_name    = var.cluster_name
  cluster_version = "1.30"
  subnet_ids      = module.vpc.private_subnets
  vpc_id          = module.vpc.vpc_id
  cluster_endpoint_public_access = true

  eks_managed_node_groups = {
    default = {
      min_size     = 2
      max_size     = 10
      desired_size = 2
      instance_types = ["t3.medium"]
      labels = { role = "orchestrator" }
    }
  }
  enable_cluster_creator_admin_permissions = true
}

# ElastiCache Redis (Managed)
resource "aws_elasticache_subnet_group" "redis" {
  name       = "${var.cluster_name}-redis"
  subnet_ids = module.vpc.private_subnets
}

resource "aws_security_group" "redis" {
  name        = "${var.cluster_name}-redis"
  description = "Redis access from EKS nodes"
  vpc_id      = module.vpc.vpc_id

  ingress {
    from_port = 6379
    to_port   = 6379
    protocol  = "tcp"
    security_groups = [module.eks.node_security_group_id]
  }
  egress {
    from_port = 0
    to_port   = 0
    protocol  = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_elasticache_cluster" "redis" {
  cluster_id           = "${var.cluster_name}-redis"
  engine               = "redis"
  node_type            = "cache.t4g.small"
  num_cache_nodes      = 1
  parameter_group_name = "default.redis7"
  engine_version       = "7.1"
  port                 = 6379
  subnet_group_name    = aws_elasticache_subnet_group.redis.name
  security_group_ids   = [aws_security_group.redis.id]
  apply_immediately    = true
}

# Secrets Manager
resource "aws_secretsmanager_secret" "orchestrator" {
  name = "${var.cluster_name}/credentials"
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret_version" "orchestrator" {
  secret_id     = aws_secretsmanager_secret.orchestrator.id
  secret_string = jsonencode({
    LLM_API_KEY = "replace-me"
    API_KEYS    = var.api_keys
    LLM_BASE_URL = var.vllm_endpoint
    REDIS_URL   = "redis://${aws_elasticache_cluster.redis.cache_nodes[0].address}:6379/0"
  })
}

# IRSA: orchestrator ServiceAccount can read its secret
data "aws_iam_policy_document" "orchestrator_assume" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [module.eks.oidc_provider_arn]
    }
    condition {
      test     = "StringEquals"
      variable = "${replace(module.eks.cluster_oidc_issuer_url, "https://", "")}:sub"
      values   = ["system:serviceaccount:vllm-orchestrator:orchestrator"]
    }
  }
}

resource "aws_iam_role" "orchestrator_irsa" {
  name               = "${var.cluster_name}-irsa"
  assume_role_policy = data.aws_iam_policy_document.orchestrator_assume.json
}

data "aws_iam_policy_document" "read_secret" {
  statement {
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_secretsmanager_secret.orchestrator.arn]
  }
}

resource "aws_iam_role_policy" "read_secret" {
  role   = aws_iam_role.orchestrator_irsa.id
  policy = data.aws_iam_policy_document.read_secret.json
}

# Outputs
output "cluster_endpoint" { value = module.eks.cluster_endpoint }
output "redis_host"       { value = aws_elasticache_cluster.redis.cache_nodes[0].address }
output "secret_arn"       { value = aws_secretsmanager_secret.orchestrator.arn }
output "irsa_role_arn"    { value = aws_iam_role.orchestrator_irsa.arn }

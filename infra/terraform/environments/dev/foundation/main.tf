# dev / foundation — AWS resources for the Apollo platform, composed from ../../../modules.
# Kubernetes-level components (Helm) live in ../platform.

# ---------------------------------------------------------------- Network

module "vpc" {
  source = "../../../modules/vpc"

  name = var.cluster_name
  cidr = var.vpc_cidr
  azs  = var.azs

  public_subnet_tags = {
    "kubernetes.io/role/elb"                    = "1" # internet-facing NLB placement
    "kubernetes.io/cluster/${var.cluster_name}" = "shared"
  }
}

# ---------------------------------------------------------------- EKS control plane

module "eks" {
  source = "../../../modules/eks-cluster"

  name                         = var.cluster_name
  kubernetes_version           = var.kubernetes_version
  subnet_ids                   = module.vpc.public_subnet_ids
  endpoint_public_access_cidrs = var.cluster_endpoint_public_cidrs
  admin_principal_arns         = var.cluster_admin_principal_arns
}

# Needed before any node can become Ready.
module "addons_core" {
  source = "../../../modules/eks-addons"

  cluster_name       = module.eks.name
  kubernetes_version = module.eks.kubernetes_version
  addons = {
    vpc-cni                = { version = var.addon_versions["vpc-cni"] }
    kube-proxy             = { version = var.addon_versions["kube-proxy"] }
    eks-pod-identity-agent = { version = var.addon_versions["eks-pod-identity-agent"] }
  }
}

# ---------------------------------------------------------------- Node groups

# Platform services: Argo CD, MLflow, Model Portal, Grafana stack, ARC controller, LB controller.
# Spot + single AZ so EBS-backed PVCs can always re-attach after an interruption.
module "node_group_system" {
  source = "../../../modules/eks-node-group"

  cluster_name   = module.eks.name
  name           = "system"
  subnet_ids     = [module.vpc.public_subnet_ids_by_az[var.system_node_az]]
  ami_type       = "AL2023_x86_64_STANDARD"
  capacity_type  = "SPOT"
  instance_types = var.system_instance_types
  min_size       = var.system_node_count.min
  desired_size   = var.system_node_count.desired
  max_size       = var.system_node_count.max
  disk_size_gb   = 40
  labels         = { role = "system" }
  tags           = local.tags

  depends_on = [module.addons_core]
}

# NVIDIA L4: TensorRT engine build (ARC runner) + Triton + DCGM. On-demand for stable benchmarks.
# Starts at 0; infra/scripts/gpu-up.sh / gpu-down.sh change the desired size.
module "node_group_gpu" {
  source = "../../../modules/eks-node-group"

  cluster_name   = module.eks.name
  name           = "gpu-l4"
  subnet_ids     = module.vpc.public_subnet_ids
  ami_type       = "AL2023_x86_64_NVIDIA"
  capacity_type  = "ON_DEMAND"
  instance_types = [var.gpu_instance_type]
  min_size       = 0
  desired_size   = 0
  max_size       = var.gpu_node_max
  disk_size_gb   = var.gpu_disk_gb
  labels = {
    role                     = "gpu"
    "nvidia.com/gpu.present" = "true" # matched by the NVIDIA device plugin's node affinity
  }
  taints = [{ key = "nvidia.com/gpu", value = "true", effect = "NO_SCHEDULE" }]
  tags   = local.tags

  depends_on = [module.addons_core]
}

# Need schedulable nodes; their pods would otherwise leave the add-on DEGRADED.
module "addons" {
  source = "../../../modules/eks-addons"

  cluster_name       = module.eks.name
  kubernetes_version = module.eks.kubernetes_version
  addons = {
    coredns        = { version = var.addon_versions["coredns"] }
    metrics-server = { version = var.addon_versions["metrics-server"] }
    aws-ebs-csi-driver = {
      version = var.addon_versions["aws-ebs-csi-driver"]
      # Creates a default gp3 StorageClass for PVCs (MLflow Postgres, Grafana stack).
      configuration_values = jsonencode({ defaultStorageClass = { enabled = true } })
      pod_identity = {
        role_arn        = module.pod_identity_ebs_csi.role_arn
        service_account = "ebs-csi-controller-sa"
      }
    }
  }

  depends_on = [module.node_group_system]
}

# ---------------------------------------------------------------- Registry & storage

module "ecr" {
  source   = "../../../modules/ecr-repository"
  for_each = toset(var.ecr_repositories)

  name             = each.value
  keep_last_images = var.ecr_keep_last_images
  force_delete     = var.force_destroy_data
}

# Org SCP denies s3:PutBucketVersioning / DeleteObject / DeleteBucket in this account:
# no versioning, artifacts cannot be deleted, and the bucket survives `terraform destroy`.
module "mlflow_bucket" {
  source = "../../../modules/s3-bucket"

  name               = "${var.project}-mlflow-${local.account_id}-${var.region}"
  versioning_enabled = false
  force_destroy      = false
}

# ---------------------------------------------------------------- Cost guardrail

# Scoped to the platform region: the account is shared, and other teams' spend elsewhere
# would dominate an account-wide budget.
module "budget" {
  source = "../../../modules/budget"

  name         = "${var.cluster_name}-${var.region}-monthly"
  limit_usd    = var.budget_limit_usd
  regions      = [var.region]
  alert_emails = var.budget_alert_emails
}

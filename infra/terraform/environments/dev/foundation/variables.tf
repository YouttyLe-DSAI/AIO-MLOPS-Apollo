variable "environment" {
  type    = string
  default = "dev"
}

variable "region" {
  description = "Single region for the whole platform. ap-northeast-2: L4 (g6.xlarge) is absent in ap-southeast-1 and was out of capacity in ap-southeast-2."
  type        = string
  default     = "ap-northeast-2"
}

variable "project" {
  type    = string
  default = "apollo"
}

variable "cluster_name" {
  type    = string
  default = "apollo-mlops"
}

variable "kubernetes_version" {
  type    = string
  default = "1.36"
}

# ---------------- Network ----------------

variable "vpc_cidr" {
  type    = string
  default = "10.42.0.0/16"
}

variable "azs" {
  type    = list(string)
  default = ["ap-northeast-2a", "ap-northeast-2c", "ap-northeast-2d"]
}

variable "cluster_endpoint_public_cidrs" {
  description = "CIDRs allowed to reach the public Kubernetes API endpoint (IAM auth still required)."
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

# ---------------- Nodes ----------------

variable "system_node_az" {
  description = "System nodes stay in one AZ so EBS-backed PVCs can always re-attach after a spot interruption."
  type        = string
  default     = "ap-northeast-2a"
}

variable "system_instance_types" {
  description = "Spot pool for platform services; non-burstable to avoid T-family unlimited-credit charges."
  type        = list(string)
  default     = ["m5.large", "m5a.large", "m6i.large", "m7i.large"]
}

variable "system_node_count" {
  type = object({ min = number, desired = number, max = number })
  default = {
    min     = 2
    desired = 2
    max     = 3
  }
}

variable "gpu_instance_type" {
  type    = string
  default = "g6.xlarge" # 1x NVIDIA L4 24 GB, 4 vCPU, 16 GiB
}

variable "gpu_node_max" {
  description = "Upper bound for the GPU node group. Desired size is driven by infra/scripts/gpu-up.sh / gpu-down.sh."
  type        = number
  default     = 1
}

variable "gpu_disk_gb" {
  description = "Root volume for GPU nodes; Triton and TensorRT images are ~15 GB each."
  type        = number
  default     = 200
}

# ---------------- Access ----------------

variable "cluster_admin_principal_arns" {
  description = "Extra IAM users/roles that get cluster-admin. Do NOT list the identity that creates the cluster; it is admin already."
  type        = list(string)
  default     = []
}

# ---------------- Workloads that call AWS APIs (EKS Pod Identity) ----------------

variable "mlflow_service_account" {
  description = "Kubernetes namespace/service account of the MLflow tracking server (S3 artifact store access)."
  type        = object({ namespace = string, name = string })
  default = {
    namespace = "mlflow"
    name      = "mlflow"
  }
}

variable "gpu_runner_service_account" {
  description = "Namespace/service account of the ARC GPU runner that builds TensorRT engines and pushes images to ECR."
  type        = object({ namespace = string, name = string })
  default = {
    namespace = "arc-runners"
    name      = "arc-gpu-runner"
  }
}

# ---------------- Registry & storage ----------------

variable "ecr_repositories" {
  type    = list(string)
  default = ["resnet18-triton"]
}

variable "ecr_keep_last_images" {
  type    = number
  default = 15
}

variable "force_destroy_data" {
  description = "Allow `terraform destroy` to delete ECR repos that still hold images. Keep false except at final teardown. (S3 buckets cannot be deleted in this account: org SCP.)"
  type        = bool
  default     = false
}

# ---------------- Cost guardrail ----------------

variable "budget_limit_usd" {
  type    = number
  default = 300
}

variable "budget_alert_emails" {
  description = "Recipients of budget alerts. Set in secrets.auto.tfvars (git-ignored: the repo is public)."
  type        = list(string)
}

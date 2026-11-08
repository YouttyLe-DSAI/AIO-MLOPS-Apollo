variable "environment" {
  type    = string
  default = "dev"
}

variable "region" {
  type    = string
  default = "ap-northeast-2"
}

variable "cluster_name" {
  type    = string
  default = "apollo-mlops"
}

# Chart versions are pinned; bump deliberately and re-run plan.
variable "chart_versions" {
  type = object({
    aws_load_balancer_controller = string
    nvidia_device_plugin         = string
    argo_cd                      = string
  })
  default = {
    aws_load_balancer_controller = "3.6.2"   # app v3.6.2 — must match the IAM policy file in 10-foundation/policies
    nvidia_device_plugin         = "0.20.1"  # app 0.20.1
    argo_cd                      = "10.14.0" # app v3.6.1
  }
}

variable "argocd_apps_chart_version" {
  type    = string
  default = "2.0.7"
}

variable "gitops_repo" {
  description = "Private GitOps repository that Argo CD syncs (read-only deploy key)."
  type        = object({ owner = string, name = string, revision = string, apps_path = string })
  default = {
    owner     = "YouttyLe-DSAI"
    name      = "AIO-MLOPS-Apollo-gitops"
    revision  = "main"
    apps_path = "apps/dev"
  }
}

variable "mlflow_namespace" {
  type    = string
  default = "mlflow"
}

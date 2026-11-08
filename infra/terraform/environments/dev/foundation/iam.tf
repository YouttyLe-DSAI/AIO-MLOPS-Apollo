# IAM for workloads that call AWS APIs, wired to Kubernetes service accounts via EKS Pod Identity.
# Charts must use exactly these namespace/service-account names.

# ---------------------------------------------------------------- Policy documents

data "aws_iam_policy_document" "mlflow" {
  statement {
    sid       = "ListArtifactBucket"
    actions   = ["s3:ListBucket", "s3:GetBucketLocation"]
    resources = [module.mlflow_bucket.arn]
  }
  # No s3:DeleteObject: the org SCP denies it anyway, so MLflow artifact GC will not work.
  statement {
    sid       = "ReadWriteArtifacts"
    actions   = ["s3:GetObject", "s3:PutObject"]
    resources = ["${module.mlflow_bucket.arn}/*"]
  }
}

data "aws_iam_policy_document" "gpu_runner" {
  statement {
    sid       = "EcrLogin"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }
  statement {
    sid = "PushTritonImages"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:BatchGetImage",
      "ecr:GetDownloadUrlForLayer",
      "ecr:InitiateLayerUpload",
      "ecr:UploadLayerPart",
      "ecr:CompleteLayerUpload",
      "ecr:PutImage",
      "ecr:DescribeImages",
    ]
    resources = [for r in module.ecr : r.arn]
  }
  statement {
    sid       = "ReadOnnxFromMlflow"
    actions   = ["s3:ListBucket", "s3:GetObject"]
    resources = [module.mlflow_bucket.arn, "${module.mlflow_bucket.arn}/*"]
  }
}

# ---------------------------------------------------------------- Roles

# The aws-ebs-csi-driver add-on creates its own association (see module.addons), so role only.
module "pod_identity_ebs_csi" {
  source = "../../../modules/pod-identity"

  name                = "${var.cluster_name}-ebs-csi"
  cluster_name        = module.eks.name
  managed_policy_arns = ["arn:${local.partition}:iam::aws:policy/service-role/AmazonEBSCSIDriverPolicy"]
}

module "pod_identity_lb_controller" {
  source = "../../../modules/pod-identity"

  name            = "${var.cluster_name}-aws-load-balancer-controller"
  cluster_name    = module.eks.name
  namespace       = "kube-system"
  service_account = "aws-load-balancer-controller"
  # Upstream policy; keep the file version in step with the chart in ../platform.
  inline_policies = {
    AWSLoadBalancerController = file("${path.module}/policies/aws-load-balancer-controller-v3.6.2.json")
  }
}

module "pod_identity_mlflow" {
  source = "../../../modules/pod-identity"

  name            = "${var.cluster_name}-mlflow"
  cluster_name    = module.eks.name
  namespace       = var.mlflow_service_account.namespace
  service_account = var.mlflow_service_account.name
  inline_policies = { MlflowArtifacts = data.aws_iam_policy_document.mlflow.json }
}

module "pod_identity_gpu_runner" {
  source = "../../../modules/pod-identity"

  name            = "${var.cluster_name}-gpu-runner"
  cluster_name    = module.eks.name
  namespace       = var.gpu_runner_service_account.namespace
  service_account = var.gpu_runner_service_account.name
  inline_policies = { BuildAndPushTriton = data.aws_iam_policy_document.gpu_runner.json }
}

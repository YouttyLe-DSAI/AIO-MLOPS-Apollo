# Cluster-level platform components installed once by Terraform.
# Application workloads (Triton, MLflow, Model Portal, Grafana stack, ARC) are delivered by Argo CD
# from the GitOps repository, not from here.

# ---------------- AWS Load Balancer Controller: provisions the NLB in front of Triton ----------------
# IAM comes from the pod identity association created in 10-foundation (kube-system/aws-load-balancer-controller).
resource "helm_release" "aws_load_balancer_controller" {
  name       = "aws-load-balancer-controller"
  namespace  = "kube-system"
  repository = "https://aws.github.io/eks-charts"
  chart      = "aws-load-balancer-controller"
  version    = var.chart_versions.aws_load_balancer_controller

  values = [yamlencode({
    clusterName    = var.cluster_name
    region         = var.region
    vpcId          = data.aws_eks_cluster.this.vpc_config[0].vpc_id
    serviceAccount = { create = true, name = "aws-load-balancer-controller" }
    nodeSelector   = { role = "system" }
  })]

  wait    = true
  timeout = 600
}

# ---------------- NVIDIA device plugin: exposes nvidia.com/gpu on the L4 node ----------------
# The AL2023 NVIDIA AMI ships the driver but not the device plugin. The chart's default node
# affinity matches the nvidia.com/gpu.present=true label set on the gpu-l4 node group and its
# default tolerations cover the nvidia.com/gpu taint.
resource "helm_release" "nvidia_device_plugin" {
  name             = "nvidia-device-plugin"
  namespace        = "nvidia-device-plugin"
  create_namespace = true
  repository       = "https://nvidia.github.io/k8s-device-plugin"
  chart            = "nvidia-device-plugin"
  version          = var.chart_versions.nvidia_device_plugin

  # The GPU node group usually sits at 0, so there is nothing to wait for.
  wait = false
}

# ---------------- Argo CD: GitOps engine that syncs the GitOps repository ----------------
resource "helm_release" "argo_cd" {
  name             = "argocd"
  namespace        = "argocd"
  create_namespace = true
  repository       = "oci://ghcr.io/argoproj/argo-helm" # OCI mirror: GitHub release downloads were flaky (504)
  chart            = "argo-cd"
  version          = var.chart_versions.argo_cd

  values = [yamlencode({
    global = {
      domain       = local.hosts.argocd
      nodeSelector = { role = "system" }
    }
    dex = { enabled = false } # no SSO; log in with the initial admin secret
    # TLS terminates at Traefik; argocd-server speaks plain HTTP behind it (avoids redirect loops).
    configs = { params = { "server.insecure" = true } }
    server = {
      ingress = {
        enabled          = true
        ingressClassName = "traefik"
        annotations      = { "cert-manager.io/cluster-issuer" = local.cluster_issuer }
        tls              = true
      }
    }
  })]

  wait    = true
  timeout = 900

  # The LB controller registers a webhook on Services; it must be serving before Argo CD creates any.
  depends_on = [helm_release.aws_load_balancer_controller, helm_release.traefik]
}

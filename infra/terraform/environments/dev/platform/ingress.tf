# Public HTTPS entry point for platform UIs (MLflow, Argo CD, Grafana):
#   Internet -> NLB (static EIP, one subnet) -> Traefik -> Ingress -> Service
# Hostnames come from sslip.io (<name>.<ip-with-dashes>.sslip.io), certificates from Let's Encrypt
# via cert-manager. The ClusterIssuers themselves live in the GitOps repo (envs/dev/cluster-config)
# because their CRDs only exist after cert-manager is installed.

# Owned by ../foundation (ingress.tf); looked up by tag so no remote state is needed.
data "aws_eip" "ingress" {
  filter {
    name   = "tag:Name"
    values = ["${var.cluster_name}-ingress"]
  }
}

data "aws_subnet" "ingress" {
  filter {
    name   = "tag:Name"
    values = ["${var.cluster_name}-public-${var.ingress_az}"]
  }
}

locals {
  base_domain    = "${replace(data.aws_eip.ingress.public_ip, ".", "-")}.sslip.io"
  cluster_issuer = "letsencrypt-prod"
  hosts = {
    argocd  = "argocd.${local.base_domain}"
    mlflow  = "mlflow.${local.base_domain}"
    grafana = "grafana.${local.base_domain}"
  }
}

resource "helm_release" "cert_manager" {
  name             = "cert-manager"
  namespace        = "cert-manager"
  create_namespace = true
  repository       = "https://charts.jetstack.io"
  chart            = "cert-manager"
  version          = var.chart_versions.cert_manager

  values = [yamlencode({
    crds            = { enabled = true }
    nodeSelector    = { role = "system" }
    webhook         = { nodeSelector = { role = "system" } }
    cainjector      = { nodeSelector = { role = "system" } }
    startupapicheck = { nodeSelector = { role = "system" } }
  })]

  wait    = true
  timeout = 600
}

resource "helm_release" "traefik" {
  name             = "traefik"
  namespace        = "traefik"
  create_namespace = true
  repository       = "https://traefik.github.io/charts"
  chart            = "traefik"
  version          = var.chart_versions.traefik

  values = [yamlencode({
    ingressClass = { enabled = true, isDefaultClass = true, name = "traefik" }
    nodeSelector = { role = "system" } # same AZ as the single-subnet NLB
    ports = {
      web = {
        # Plain HTTP only redirects: MLflow basic-auth credentials must never travel unencrypted.
        http = { redirections = { entryPoint = { to = "websecure", scheme = "https", permanent = true } } }
        # Let cert-manager's HTTP-01 solver routes win over the redirect (certificate renewals).
        allowACMEByPass = true
      }
    }
    service = {
      annotations = {
        "service.beta.kubernetes.io/aws-load-balancer-type"            = "external"
        "service.beta.kubernetes.io/aws-load-balancer-scheme"          = "internet-facing"
        "service.beta.kubernetes.io/aws-load-balancer-nlb-target-type" = "ip"
        "service.beta.kubernetes.io/aws-load-balancer-subnets"         = data.aws_subnet.ingress.id
        "service.beta.kubernetes.io/aws-load-balancer-eip-allocations" = data.aws_eip.ingress.id
      }
    }
  })]

  wait    = true
  timeout = 600

  # The NLB is provisioned by the AWS Load Balancer Controller.
  depends_on = [helm_release.aws_load_balancer_controller, helm_release.cert_manager]
}

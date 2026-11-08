# Wire Argo CD to the private GitOps repository and create the root "app of apps".
# Everything below apps/dev in that repo is then deployed and kept in sync by Argo CD.

locals {
  gitops_repo_url = "git@github.com:${var.gitops_repo.owner}/${var.gitops_repo.name}.git"
}

# Read-only deploy key: Argo CD can clone the repo but never push.
resource "tls_private_key" "argocd_gitops" {
  algorithm = "ED25519"
}

resource "github_repository_deploy_key" "argocd" {
  repository = var.gitops_repo.name
  title      = "argocd-${var.cluster_name}-${var.environment}"
  key        = tls_private_key.argocd_gitops.public_key_openssh
  read_only  = true
}

resource "kubernetes_secret_v1" "argocd_repo_gitops" {
  metadata {
    name      = "repo-${lower(var.gitops_repo.name)}"
    namespace = helm_release.argo_cd.namespace
    labels = {
      "argocd.argoproj.io/secret-type" = "repository"
    }
  }

  data = {
    type          = "git"
    url           = local.gitops_repo_url
    sshPrivateKey = tls_private_key.argocd_gitops.private_key_openssh
  }
}

# Root application: one Argo CD Application per file in apps/<env>.
resource "helm_release" "argocd_apps" {
  name       = "argocd-apps"
  namespace  = helm_release.argo_cd.namespace
  repository = "oci://ghcr.io/argoproj/argo-helm" # OCI mirror: GitHub release downloads were flaky (504)
  chart      = "argocd-apps"
  version    = var.argocd_apps_chart_version

  values = [yamlencode({
    applications = {
      "apollo-${var.environment}" = {
        namespace  = helm_release.argo_cd.namespace
        finalizers = ["resources-finalizer.argocd.argoproj.io"]
        project    = "default"
        source = {
          repoURL        = local.gitops_repo_url
          targetRevision = var.gitops_repo.revision
          path           = var.gitops_repo.apps_path
        }
        destination = {
          server    = "https://kubernetes.default.svc"
          namespace = helm_release.argo_cd.namespace
        }
        syncPolicy = {
          automated = { prune = true, selfHeal = true }
        }
      }
    }
  })]

  depends_on = [
    kubernetes_secret_v1.argocd_repo_gitops,
    github_repository_deploy_key.argocd,
    # Workload prerequisites that must exist before Argo CD syncs the child apps.
    kubernetes_secret_v1.mlflow_db,
    kubernetes_secret_v1.mlflow_admin,
    helm_release.cert_manager, # ClusterIssuer CRDs for envs/dev/cluster-config
  ]
}

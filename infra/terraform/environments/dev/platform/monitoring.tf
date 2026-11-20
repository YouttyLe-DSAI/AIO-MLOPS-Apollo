# Prerequisites for the observability stack delivered by Argo CD (GitOps repo: apps/dev/monitoring-*):
# namespace + Grafana admin credentials, so no password is committed to Git.
resource "kubernetes_namespace_v1" "monitoring" {
  metadata {
    name   = "monitoring"
    labels = { "app.kubernetes.io/part-of" = "apollo" }
  }
}

resource "random_password" "grafana_admin" {
  length  = 24
  special = false
}

resource "kubernetes_secret_v1" "grafana_admin" {
  metadata {
    name      = "grafana-admin"
    namespace = kubernetes_namespace_v1.monitoring.metadata[0].name
  }

  data = {
    admin-user     = "admin"
    admin-password = random_password.grafana_admin.result
  }
}

output "argocd_login" {
  value = <<-EOT
    kubectl -n argocd port-forward svc/argocd-server 8080:443
    # user: admin   password:
    kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d
  EOT
}

output "mlflow_access" {
  value = <<-EOT
    kubectl -n mlflow port-forward svc/mlflow 5000:80
    export MLFLOW_TRACKING_URI=http://localhost:5000
  EOT
}

output "gitops_repo" {
  value = "https://github.com/${var.gitops_repo.owner}/${var.gitops_repo.name}"
}

output "urls" {
  value = { for k, h in local.hosts : k => "https://${h}" }
}

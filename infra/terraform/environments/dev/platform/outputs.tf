output "argocd_login" {
  value = <<-EOT
    kubectl -n argocd port-forward svc/argocd-server 8080:443
    # user: admin   password:
    kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d
  EOT
}

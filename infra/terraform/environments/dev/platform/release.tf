# Release namespace: the GitHub Actions release workflow creates one Job here per Model version.
# The Job (TensorRT Engine build on the L4 + Serving image build) runs as release-builder, whose
# IAM role (ECR push, MLflow artifact read) comes from the Pod Identity association in ../foundation.
resource "kubernetes_namespace_v1" "release" {
  metadata {
    name   = var.release_namespace
    labels = { "app.kubernetes.io/part-of" = "apollo" }
  }
}

resource "kubernetes_service_account_v1" "release_builder" {
  metadata {
    name      = "release-builder"
    namespace = kubernetes_namespace_v1.release.metadata[0].name
  }
}

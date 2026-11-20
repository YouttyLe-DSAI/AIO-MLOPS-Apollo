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

# Dataset cache for the benchmark Job (CIFAR-10 test batch, ~30 MB). The official mirror is slow
# from Seoul (~120 KB/s, ~20 min for the archive), so it is downloaded once and reused.
# WaitForFirstConsumer: the EBS volume is created in the AZ of the first benchmark pod (system nodes).
resource "kubernetes_persistent_volume_claim_v1" "benchmark_cache" {
  metadata {
    name      = "benchmark-cache"
    namespace = kubernetes_namespace_v1.release.metadata[0].name
    labels    = { "app.kubernetes.io/part-of" = "apollo" }
  }
  spec {
    access_modes       = ["ReadWriteOnce"]
    storage_class_name = "ebs-csi-default-sc"
    resources {
      requests = { storage = "1Gi" }
    }
  }
  wait_until_bound = false
}

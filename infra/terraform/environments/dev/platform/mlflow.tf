# Prerequisites for the MLflow app delivered by Argo CD (GitOps repo: apps/dev/mlflow.yaml).
# The namespace and DB credentials live here so no secret is ever committed to Git.
# The S3 permissions come from the Pod Identity association mlflow/mlflow in ../foundation.

resource "kubernetes_namespace_v1" "mlflow" {
  metadata {
    name = var.mlflow_namespace
    labels = {
      "app.kubernetes.io/part-of" = "apollo"
    }
  }
}

# Alphanumeric only: the chart embeds the password in the SQLAlchemy connection URI.
resource "random_password" "mlflow_db" {
  length  = 32
  special = false
}

resource "kubernetes_secret_v1" "mlflow_db" {
  metadata {
    name      = "mlflow-db"
    namespace = kubernetes_namespace_v1.mlflow.metadata[0].name
  }

  data = {
    username = "mlflow"
    password = random_password.mlflow_db.result
  }
}

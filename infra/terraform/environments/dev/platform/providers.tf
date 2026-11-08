provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project     = "apollo"
      Environment = var.environment
      Cluster     = var.cluster_name
      Stack       = "platform"
      ManagedBy   = "terraform"
    }
  }
}

# The cluster is owned by ../foundation and read here through a data source, so the Helm provider
# is never configured from a resource created in the same apply.
data "aws_eks_cluster" "this" {
  name = var.cluster_name
}

provider "helm" {
  kubernetes = {
    host                   = data.aws_eks_cluster.this.endpoint
    cluster_ca_certificate = base64decode(data.aws_eks_cluster.this.certificate_authority[0].data)
    exec = {
      api_version = "client.authentication.k8s.io/v1beta1"
      command     = "aws"
      args        = ["eks", "get-token", "--cluster-name", var.cluster_name, "--region", var.region]
    }
  }
}

provider "kubernetes" {
  host                   = data.aws_eks_cluster.this.endpoint
  cluster_ca_certificate = base64decode(data.aws_eks_cluster.this.certificate_authority[0].data)
  exec {
    api_version = "client.authentication.k8s.io/v1beta1"
    command     = "aws"
    args        = ["eks", "get-token", "--cluster-name", var.cluster_name, "--region", var.region]
  }
}

# Token from the environment: GITHUB_TOKEN=$(gh auth token) terraform apply
provider "github" {
  owner = var.gitops_repo.owner
}

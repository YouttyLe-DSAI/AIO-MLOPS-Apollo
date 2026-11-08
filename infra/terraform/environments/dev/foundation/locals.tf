data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  partition  = data.aws_partition.current.partition

  tags = {
    Project     = var.project
    Environment = var.environment
    Cluster     = var.cluster_name
    Stack       = "foundation"
    ManagedBy   = "terraform"
  }
}

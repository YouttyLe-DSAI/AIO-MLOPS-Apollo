output "region" {
  value = var.region
}

output "cluster_name" {
  value = module.eks.name
}

output "cluster_endpoint" {
  value = module.eks.endpoint
}

output "vpc_id" {
  value = module.vpc.vpc_id
}

output "gpu_node_group_name" {
  value = module.node_group_gpu.node_group_name
}

output "ecr_repository_urls" {
  value = { for k, r in module.ecr : k => r.repository_url }
}

output "mlflow_artifact_root" {
  value = "s3://${module.mlflow_bucket.bucket}"
}

output "eks_addon_versions" {
  value = merge(module.addons_core.addon_versions, module.addons.addon_versions)
}

output "kubeconfig_command" {
  value = "aws eks update-kubeconfig --name ${module.eks.name} --region ${var.region} --alias ${module.eks.name}"
}

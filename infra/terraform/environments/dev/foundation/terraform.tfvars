# dev environment — committed. Secrets/personal values go in secrets.auto.tfvars (git-ignored).
environment        = "dev"
region             = "ap-northeast-2"
cluster_name       = "apollo-mlops"
kubernetes_version = "1.36"

vpc_cidr = "10.42.0.0/16"
azs      = ["ap-northeast-2a", "ap-northeast-2c", "ap-northeast-2d"] # g6.xlarge is not offered in 2b

system_node_az        = "ap-northeast-2a"
system_instance_types = ["m5.large", "m5a.large", "m6i.large", "m7i.large"] # offered in ap-northeast-2a
system_node_count     = { min = 2, desired = 2, max = 3 }

gpu_instance_type = "g6.xlarge"
gpu_node_max      = 1
gpu_disk_gb       = 200

ecr_repositories     = ["resnet18-triton"]
ecr_keep_last_images = 15

budget_limit_usd = 300

# Pinned EKS add-on versions (2026-11-08). Unpinned add-ons would upgrade on unrelated applies.
addon_versions = {
  vpc-cni                = "v1.23.2-eksbuild.12"
  kube-proxy             = "v1.36.0-eksbuild.65"
  eks-pod-identity-agent = "v1.4.1-eksbuild.3"
  coredns                = "v1.14.7-eksbuild.35"
  metrics-server         = "v0.9.0-eksbuild.14"
  aws-ebs-csi-driver     = "v1.67.2-eksbuild.1"
}

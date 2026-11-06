# dev environment — committed. Secrets/personal values go in secrets.auto.tfvars (git-ignored).
environment        = "dev"
region             = "ap-southeast-2"
cluster_name       = "apollo-mlops"
kubernetes_version = "1.36"

vpc_cidr = "10.42.0.0/16"
azs      = ["ap-southeast-2a", "ap-southeast-2b", "ap-southeast-2c"]

system_node_az        = "ap-southeast-2a"
system_instance_types = ["m5.large", "m5a.large", "m6i.large", "m6a.large", "m7i.large", "m7a.large"]
system_node_count     = { min = 2, desired = 2, max = 3 }

gpu_instance_type = "g6.xlarge"
gpu_node_max      = 1
gpu_disk_gb       = 200

ecr_repositories     = ["resnet18-triton"]
ecr_keep_last_images = 15

budget_limit_usd = 300
